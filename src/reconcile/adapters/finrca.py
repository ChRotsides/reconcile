"""FinRCA input transform: `full/*.csv` to a prepared, read-only SQLite snapshot.

Implements the allowlist of docs/data-audit.md section 3.1. Only the columns
listed in TABLES reach the snapshot. Any other column is dropped, and so are
whole tables that TABLES does not list (`audit_log.csv` and `employees.csv`
are never opened). Identifiers and references become opaque tokens; rows are
sorted by token; amounts are validated through `Money.parse`.

Token key
---------
Tokens are HMAC-SHA256(key, namespace + value), cut to 128 bits and written as
hex. The key is a random secret stored in a file *outside* the prepared-inputs
directory. Inference code gets the snapshot directory only, so it cannot
recompute or invert tokens (raw IDs are guessable sequences). Evaluator-side
code keeps the key file so a later preparation reproduces the same snapshot.
The key is never written into the snapshot or the manifest.
"""

import csv
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

from reconcile.domain.money import MAX_DIGITS, Money
from reconcile.store.snapshot import Indexes, Row, write_snapshot

ADAPTER_VERSION = "1"
SNAPSHOT_FILE = "snapshot.sqlite"
MANIFEST_FILE = "manifest.json"
QUALITY_FILE = "quality.json"
QUALITY_VERSION = 1
SAMPLE_LIMIT = 5
DROPPED_TABLES = ("audit_log", "employees")
TOKEN_HEX_CHARS = 32


class Col(NamedTuple):
    name: str
    # keep | remap | poly_remap | bank_token_value | currency | money | money_of
    kind: str
    # remap: namespace; money: currency column; money_of: "<namespace>:<fk column>"
    arg: str = ""


class Table(NamedTuple):
    columns: tuple[Col, ...]
    cutoff_column: str = ""  # rows dated after the cutoff are excluded
    registers_currency: tuple[str, str, str] = (
        "",
        "",
        "",
    )  # (id column, currency column, ns)


def _keep(*names: str) -> tuple[Col, ...]:
    return tuple(Col(n, "keep") for n in names)


# Order matters twice: parents come before the children that look up their
# currency, and the column order here is the snapshot column order.
TABLES: dict[str, Table] = {
    "vendors": Table(
        (
            Col("vendor_id", "remap", "vendor"),
            Col("bank_account_token", "remap", "bank_acct_token"),
            *_keep("vendor_type", "country"),
            Col("currency", "currency"),
            *_keep(
                "payment_terms",
                "default_payment_method",
                "vendor_status",
                "created_at",
                "updated_at",
            ),
        )
    ),
    "purchase_orders": Table(
        (
            Col("po_id", "remap", "po"),
            Col("vendor_id", "remap", "vendor"),
            Col("po_date", "keep"),
            Col("currency", "currency"),
            Col("po_total", "money", "currency"),
            Col("status", "keep"),
        ),
        registers_currency=("po_id", "currency", "po"),
    ),
    "po_lines": Table(
        (
            Col("po_id", "remap", "po"),
            Col("po_line_id", "remap", "po_line"),
            Col("quantity", "keep"),
            Col("unit_price", "money_of", "po:po_id"),
            Col("line_amount", "money_of", "po:po_id"),
        )
    ),
    "invoices": Table(
        (
            Col("invoice_id", "remap", "invoice"),
            Col("vendor_id", "remap", "vendor"),
            Col("po_id", "remap", "po"),
            *_keep("invoice_date", "received_date", "due_date"),
            Col("currency", "currency"),
            Col("subtotal", "money", "currency"),
            Col("tax", "money", "currency"),
            Col("shipping", "money", "currency"),
            Col("invoice_total", "money", "currency"),
            *_keep("payment_terms", "status"),
        ),
        registers_currency=("invoice_id", "currency", "invoice"),
    ),
    "invoice_lines": Table(
        (
            Col("invoice_id", "remap", "invoice"),
            Col("invoice_line_id", "remap", "invoice_line"),
            Col("po_line_id", "remap", "po_line"),
            Col("quantity", "keep"),
            Col("unit_price", "money_of", "invoice:invoice_id"),
            Col("line_amount", "money_of", "invoice:invoice_id"),
        )
    ),
    "payments": Table(
        (
            Col("payment_id", "remap", "payment"),
            Col("vendor_id", "remap", "vendor"),
            Col("bank_account_id", "remap", "bank_acct_token"),
            Col("reference_number", "remap", "payment_ref"),
            *_keep("payment_date", "payment_method"),
            Col("payment_currency", "currency"),
            Col("payment_amount", "money", "payment_currency"),
            *_keep("payment_status", "settlement_status", "created_at"),
        ),
        registers_currency=("payment_id", "payment_currency", "payment"),
    ),
    "payment_allocations": Table(
        (
            Col("payment_id", "remap", "payment"),
            Col("invoice_id", "remap", "invoice"),
            Col("allocated_amount", "money_of", "payment:payment_id"),
            Col("allocation_date", "keep"),
        )
    ),
    "bank_transactions": Table(
        (
            Col("bank_transaction_id", "remap", "bank_txn"),
            Col("counterparty_token", "remap", "bank_acct_token"),
            Col("payment_reference", "remap", "payment_ref"),
            *_keep(
                "bank_account_id", "transaction_date", "posted_date", "transaction_type"
            ),
            Col("amount", "money", "currency"),
            Col("currency", "currency"),
            *_keep("direction", "status"),
        ),
        cutoff_column="posted_date",
    ),
    "bank_statements": Table(
        (
            Col("bank_statement_id", "remap", "statement"),
            *_keep("bank_account_id", "statement_date"),
        )
    ),
    "vendor_change_log": Table(
        (
            Col("change_id", "remap", "change"),
            Col("vendor_id", "remap", "vendor"),
            *_keep("field_changed", "changed_at"),
            Col("old_value", "bank_token_value"),
            Col("new_value", "bank_token_value"),
        )
    ),
    "approval_events": Table(
        (
            Col("approval_event_id", "remap", "approval"),
            Col("invoice_id", "remap", "invoice"),
            *_keep(
                "approval_level",
                "approver_role",
                "action",
                "event_timestamp",
                "previous_status",
                "new_status",
            ),
        )
    ),
    "gl_entries": Table(
        (
            Col("journal_id", "remap", "journal"),
            Col("journal_line_id", "remap", "journal_line"),
            Col("source_transaction_id", "poly_remap"),
            *_keep(
                "transaction_type", "posting_date", "accounting_period", "gl_account"
            ),
            Col("debit", "money", "currency"),
            Col("credit", "money", "currency"),
            Col("currency", "currency"),
        ),
        cutoff_column="posting_date",
    ),
}

# Raw columns that identify a row. Two rows with the same identity are a
# repeated import if their allowlisted values match, an ingestion error if not.
# Distinct identities with equal values (a genuine double payment) both stay.
KEYS: dict[str, tuple[str, ...]] = {
    "vendors": ("vendor_id",),
    "purchase_orders": ("po_id",),
    "po_lines": ("po_line_id",),
    "invoices": ("invoice_id",),
    "invoice_lines": ("invoice_line_id",),
    "payments": ("payment_id",),
    "payment_allocations": ("payment_id", "invoice_id"),
    "bank_transactions": ("bank_transaction_id",),
    "bank_statements": ("bank_statement_id",),
    "vendor_change_log": ("change_id",),
    "approval_events": ("approval_event_id",),
    "gl_entries": ("journal_line_id",),
}

# Lookup and join columns of docs/evidence-contract.md and of the quality report
# below; nothing else is indexed.
INDEXES: Indexes = {
    # sections 3, 4.1 (payments by ID, allocating payments), quality FK checks
    "payments": ("payment_id", "reference_number"),  # reference: sections 3, 4.2 A1/A2
    "invoices": ("invoice_id",),  # section 4.1 invoice by ID; allocation parent
    "payment_allocations": ("payment_id", "invoice_id"),  # sections 4.1, 4.2, 5
    "bank_transactions": (
        "bank_transaction_id",  # citations resolve by ID; GL source (4.3)
        "payment_reference",  # sections 3 and 4.2 A1: principal bank row
        "posted_date",  # section 2 rule 1: observable (cutoff) rows
        "counterparty_token",  # section 4.2 A3: unreferenced candidate search
    ),
    "gl_entries": (
        "source_transaction_id",  # sections 4.2, 4.3: journals sourced to a record
        "journal_id",  # section 3: balanced journal of type T
    ),
    "vendors": ("vendor_id",),  # quality FK checks only
    "purchase_orders": ("po_id",),  # quality FK checks only
    "po_lines": ("po_line_id",),  # quality FK checks only
}

# gl_entries.source_transaction_id points at different entities; the prefix of
# the raw ID says which namespace the token belongs to. An unknown prefix fails.
_POLY_PREFIX = {"INV_": "invoice", "PAY_": "payment", "BT_": "bank_txn"}


class _Tokens:
    def __init__(self, key: bytes) -> None:
        self._key = key
        self._seen: dict[tuple[str, str], str] = {}

    def token(self, namespace: str, value: str) -> str:
        msg = f"{namespace}\x00{value}".encode()
        tok = hmac.new(self._key, msg, hashlib.sha256).hexdigest()[:TOKEN_HEX_CHARS]
        if self._seen.setdefault((namespace, tok), value) != value:
            raise ValueError(f"token collision in namespace {namespace!r}")
        return tok


class _MoneyStats:
    def __init__(self) -> None:
        self.currencies: set[str] = set()
        self.max_digits = 0
        self.amounts = 0

    def currency(self, code: str) -> None:
        Money.parse("0", code)  # raises on an unknown currency
        self.currencies.add(code)

    def amount(self, text: str, code: str) -> str:
        self.currency(code)
        m = Money.parse(text, code)
        self.amounts += 1
        self.max_digits = max(self.max_digits, len(m.amount.as_tuple().digits))
        return f"{m.amount:f}"


def create_key_file(path: Path) -> None:
    """Create a new random key file (hex, mode 0600). Refuses to overwrite."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_hex(32) + "\n")


def _load_key(path: Path) -> bytes:
    key = bytes.fromhex(path.read_text().strip())
    if len(key) < 32:
        raise ValueError("key must be at least 32 bytes")
    return key


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_finrca_snapshot(
    source_dir: Path,
    out_dir: Path,
    *,
    key_path: Path,
    source_revision: str,
    cutoff: date,
) -> dict[str, Any]:
    """Read `<source_dir>/<table>.csv` for each allowlisted table and write
    `snapshot.sqlite` and `manifest.json` into the new directory `out_dir`
    (which must not exist yet) and return the manifest.
    """
    if out_dir.resolve() in key_path.resolve().parents:
        raise ValueError("key file must be outside the prepared-inputs directory")
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} already exists; prepare into a new directory")
    key = _load_key(key_path)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    # Build in a sibling directory and rename it into place in one step, so a
    # reader sees either nothing or a complete snapshot and manifest pair.
    work = Path(tempfile.mkdtemp(prefix=".prepare-", dir=out_dir.parent))
    try:
        manifest = _prepare(source_dir, work, key, source_revision, cutoff)
        os.chmod(work, 0o755)
        os.rename(work, out_dir)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return manifest


def _read_rows(path: Path) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """Strictly parse a CSV: bad quoting, duplicate headers and rows with the
    wrong number of fields raise instead of silently losing or padding rows."""
    out: list[tuple[int, dict[str, str]]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f, strict=True)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"{path.name}: empty file")
        if len(set(header)) != len(header):
            raise ValueError(f"{path.name}: duplicate column names in header")
        for row in reader:
            if not row:  # blank line
                continue
            if len(row) != len(header):
                raise ValueError(
                    f"{path.name} line {reader.line_num}: {len(row)} fields, "
                    f"expected {len(header)}"
                )
            out.append((reader.line_num, dict(zip(header, row, strict=True))))
    return header, out


def _prepare(
    source_dir: Path, out_dir: Path, key: bytes, source_revision: str, cutoff: date
) -> dict[str, Any]:
    tokens = _Tokens(key)
    stats = _MoneyStats()
    currency_of: dict[str, dict[str, str]] = {}
    snapshot_tables: dict[str, tuple[list[str], list[Row]]] = {}
    report: dict[str, Any] = {}
    file_digests: dict[str, str] = {}

    for name, table in TABLES.items():
        src = source_dir / f"{name}.csv"
        file_digests[name] = _sha256_file(src)
        header, source_rows = _read_rows(src)
        needed = [c.name for c in table.columns]
        if table.cutoff_column:
            needed.append(table.cutoff_column)
        missing = sorted(set(needed) - set(header))
        if missing:
            raise ValueError(f"{name}.csv lacks allowlisted columns: {missing}")
        rows: list[Row] = []
        # key -> (line, allowlisted source values) of the first row seen
        seen: dict[tuple[str, ...], tuple[int, tuple[str, ...]]] = {}
        cut = dupes = 0
        for n, raw in source_rows:
            if table.cutoff_column and (
                date.fromisoformat(raw[table.cutoff_column]) > cutoff
            ):
                cut += 1
                continue
            ident = tuple(raw[c] for c in KEYS[name])
            content = tuple(raw[c] for c in needed)
            if ident in seen:
                first_line, first = seen[ident]
                if first != content:
                    raise ValueError(
                        f"{name}.csv: conflicting rows for id {ident} "
                        f"(lines {first_line} and {n})"
                    )
                dupes += 1  # exact repeat of an earlier row: a repeated import
                continue
            seen[ident] = (n, content)
            try:
                rows.append(_transform(table, raw, tokens, stats, currency_of))
            except (KeyError, ValueError) as e:
                raise ValueError(f"{name}.csv line {n}: {e!r}") from e
            id_col, cur_col, ns = table.registers_currency
            if ns:
                currency_of.setdefault(ns, {})[raw[id_col]] = raw[cur_col]
        rows.sort(key=lambda r: tuple("" if v is None else v for v in r))
        snapshot_tables[name] = ([c.name for c in table.columns], rows)
        report[name] = {
            "rows_before": len(source_rows),
            "rows_after": len(rows),
            "dropped_by_cutoff": cut,
            "exact_duplicates_removed": dupes,
            "columns_kept": [c.name for c in table.columns],
            "source_columns_dropped": sorted(set(header) - set(needed)),
        }

    content_sha, file_sha = write_snapshot(
        out_dir / SNAPSHOT_FILE, snapshot_tables, INDEXES
    )
    quality_sha = _write_quality(out_dir)
    manifest: dict[str, Any] = {
        "adapter_version": ADAPTER_VERSION,
        "source_revision": source_revision,
        "cutoff": cutoff.isoformat(),
        "key_id": hmac.new(key, b"key-id", hashlib.sha256).hexdigest()[:8],
        "sqlite_version": sqlite3.sqlite_version,
        "source_file_sha256": file_digests,
        "dropped_tables": list(DROPPED_TABLES),
        "tables": report,
        "money": {
            "amounts_validated": stats.amounts,
            "currencies": sorted(stats.currencies),
            "max_digits": stats.max_digits,
            "max_digits_allowed": MAX_DIGITS,
        },
        "snapshot": {
            "file": SNAPSHOT_FILE,
            "content_sha256": content_sha,
            "file_sha256": file_sha,
        },
        "quality": {"file": QUALITY_FILE, "sha256": quality_sha},
    }
    manifest_path = out_dir / MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o444)
    return manifest


def _transform(
    table: Table,
    raw: dict[str, str],
    tokens: _Tokens,
    stats: _MoneyStats,
    currency_of: dict[str, dict[str, str]],
) -> Row:
    out: list[str | None] = []
    for col in table.columns:
        v = raw[col.name]
        if col.kind == "keep":
            out.append(v)
        elif col.kind == "remap":
            out.append(tokens.token(col.arg, v) if v else None)
        elif col.kind == "poly_remap":
            ns = next((n for p, n in _POLY_PREFIX.items() if v.startswith(p)), None)
            if ns is None:
                raise ValueError(f"{col.name}: unknown reference prefix")
            out.append(tokens.token(ns, v))
        elif col.kind == "bank_token_value":
            # Free-form old/new values are dropped unless they are bank tokens.
            is_token = raw["field_changed"] == "bank_account_token" and v
            out.append(tokens.token("bank_acct_token", v) if is_token else None)
        elif col.kind == "currency":
            stats.currency(v)
            out.append(v)
        elif col.kind == "money":
            out.append(stats.amount(v, raw[col.arg]))
        elif col.kind == "money_of":
            ns, fk = col.arg.split(":")
            out.append(stats.amount(v, currency_of[ns][raw[fk]]))
        else:
            raise AssertionError(col.kind)
    return tuple(out)


# (name, rows-in-gap SQL tail, sampled expression). Facts only: a gap can be
# the very exception under investigation, so nothing here is an error or a
# failure class. Dangling references are counted per referring row; samples are
# distinct tokens of the sampled expression, sorted, at most SAMPLE_LIMIT.
_EFFECTIVE = "p.payment_status = 'completed' AND p.settlement_status = 'settled'"
_NO_BANK_ROW = (
    "FROM payments p WHERE NOT EXISTS (SELECT 1 FROM bank_transactions b "
    "WHERE b.payment_reference = p.reference_number "
    "AND b.transaction_type <> 'bank fee')"
)


def _dangling(child: str, col: str, parent: str, pcol: str) -> tuple[str, str]:
    return (
        f'FROM "{child}" c WHERE c."{col}" IS NOT NULL AND NOT EXISTS '
        f'(SELECT 1 FROM "{parent}" t WHERE t."{pcol}" = c."{col}")',
        f'c."{col}"',
    )


_GAPS: dict[str, tuple[str, str]] = {
    "allocation_payment_missing": _dangling(
        "payment_allocations", "payment_id", "payments", "payment_id"
    ),
    "allocation_invoice_missing": _dangling(
        "payment_allocations", "invoice_id", "invoices", "invoice_id"
    ),
    "bank_payment_reference_unmatched": _dangling(
        "bank_transactions", "payment_reference", "payments", "reference_number"
    ),
    "bank_without_payment_reference": (
        "FROM bank_transactions c WHERE c.payment_reference IS NULL",
        "c.bank_transaction_id",
    ),
    "payment_without_bank_row": (_NO_BANK_ROW, "p.payment_id"),
    "effective_payment_without_bank_row": (
        f"{_NO_BANK_ROW} AND {_EFFECTIVE}",
        "p.payment_id",
    ),
    "payment_shares_reference": (
        "FROM payments p WHERE (SELECT COUNT(*) FROM payments q "
        "WHERE q.reference_number = p.reference_number) > 1",
        "p.payment_id",
    ),
    "payment_without_allocation": (
        "FROM payments p WHERE NOT EXISTS (SELECT 1 FROM payment_allocations a "
        "WHERE a.payment_id = p.payment_id)",
        "p.payment_id",
    ),
    "invoice_without_allocation": (
        "FROM invoices i WHERE NOT EXISTS (SELECT 1 FROM payment_allocations a "
        "WHERE a.invoice_id = i.invoice_id)",
        "i.invoice_id",
    ),
    "gl_source_unresolved": (
        "FROM gl_entries g WHERE NOT EXISTS (SELECT 1 FROM invoices t "
        "WHERE t.invoice_id = g.source_transaction_id) AND NOT EXISTS "
        "(SELECT 1 FROM payments t WHERE t.payment_id = g.source_transaction_id) "
        "AND NOT EXISTS (SELECT 1 FROM bank_transactions t "
        "WHERE t.bank_transaction_id = g.source_transaction_id)",
        "g.source_transaction_id",
    ),
    "invoice_line_po_line_unresolved": _dangling(
        "invoice_lines", "po_line_id", "po_lines", "po_line_id"
    ),
    "invoice_line_invoice_missing": _dangling(
        "invoice_lines", "invoice_id", "invoices", "invoice_id"
    ),
    "po_line_po_missing": _dangling("po_lines", "po_id", "purchase_orders", "po_id"),
    "invoice_po_unresolved": _dangling("invoices", "po_id", "purchase_orders", "po_id"),
    "invoice_vendor_unresolved": _dangling(
        "invoices", "vendor_id", "vendors", "vendor_id"
    ),
    "payment_vendor_unresolved": _dangling(
        "payments", "vendor_id", "vendors", "vendor_id"
    ),
    "approval_invoice_unresolved": _dangling(
        "approval_events", "invoice_id", "invoices", "invoice_id"
    ),
    "currency_allocation_vs_invoice": (
        "FROM payment_allocations a JOIN payments p ON p.payment_id = a.payment_id "
        "JOIN invoices i ON i.invoice_id = a.invoice_id "
        "WHERE p.payment_currency <> i.currency",
        "a.payment_id",
    ),
    "currency_bank_vs_payment": (
        "FROM bank_transactions b JOIN payments p "
        "ON p.reference_number = b.payment_reference "
        "WHERE b.transaction_type <> 'bank fee' AND b.currency <> p.payment_currency",
        "b.bank_transaction_id",
    ),
    "currency_invoice_vs_po": (
        "FROM invoices i JOIN purchase_orders o ON o.po_id = i.po_id "
        "WHERE i.currency <> o.currency",
        "i.invoice_id",
    ),
}


def quality_report(snapshot: Path) -> dict[str, Any]:
    """Relationship gaps in a prepared snapshot, from its contents alone.

    Rows after the cutoff are already absent from the snapshot, so "no bank
    row" means none by the cutoff.
    """
    con = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        counts = {
            name: con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for name in TABLES
        }
        gaps: dict[str, Any] = {}
        for name, (tail, sample) in _GAPS.items():
            count = con.execute(f"SELECT COUNT(*) {tail}").fetchone()[0]
            samples = [
                r[0]
                for r in con.execute(
                    f"SELECT DISTINCT {sample} {tail} ORDER BY 1 LIMIT {SAMPLE_LIMIT}"
                )
            ]
            gaps[name] = {"count": count, "sample_tokens": samples}
    finally:
        con.close()
    return {"quality_version": QUALITY_VERSION, "row_counts": counts, "gaps": gaps}


def _write_quality(out_dir: Path) -> str:
    path = out_dir / QUALITY_FILE
    path.write_text(
        json.dumps(quality_report(out_dir / SNAPSHOT_FILE), indent=2, sort_keys=True)
        + "\n"
    )
    os.chmod(path, 0o444)
    return _sha256_file(path)
