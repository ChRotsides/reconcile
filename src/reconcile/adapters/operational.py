"""Operational input: rows in contract column names to a prepared snapshot.

The investigator reads one snapshot layout (`reconcile.adapters.finrca.TABLES`).
FinRCA reaches it through tokens and a secret key. A real export does not need
that: the analyst must be able to find a record in their own system by the ID
the report shows, and the investigator only tests IDs for equality. So source
record IDs are stored unchanged here.

Two kinds of column are normalised, because the investigator compares their
values across tables and a stray space or letter case would make equal
references look different: bank references (`payments.reference_number`,
`bank_transactions.payment_reference`) and the account columns they are
compared with (`payments.bank_account_id`, `bank_transactions.counterparty_token`).
The original text is not kept in the snapshot; the ID leads back to it in the
source file, whose digest is in the manifest.
"""

import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from reconcile.adapters.finrca import (
    INDEXES,
    KEYS,
    MANIFEST_FILE,
    QUALITY_FILE,
    SNAPSHOT_FILE,
    TABLES,
    Col,
    _MoneyStats,
    _write_quality,
)
from reconcile.diagnosis.policy import CLEARING_LIMITS
from reconcile.domain.money import MAX_DIGITS
from reconcile.store.snapshot import Row, write_snapshot

ADAPTER_VERSION = "operational-1"

# The investigator's checks read these (retrieval/ and diagnosis/ query nothing
# else). approval_events is read by every all-clear, but a missing table only
# stops all-clears, so it may be omitted.
REQUIRED_TABLES = (
    "invoices",
    "payments",
    "payment_allocations",
    "bank_transactions",
    "gl_entries",
)

# Columns that may be NULL. Measured on the public fixtures (setting each column
# NULL in a prepared snapshot and re-running every scenario): these either
# changed no verdict or turned an answer into an explicit abstained, failed or
# out_of_scope packet. Every other column of a checked table is required,
# because NULL there silently changed verdicts (for example a NULL
# counterparty_token turned an abstention into an F12 answer).
_OPTIONAL = {
    "invoices": (
        "vendor_id po_id invoice_date received_date due_date subtotal tax "
        "shipping payment_terms status"
    ),
    "payments": "vendor_id created_at payment_method reference_number",
    "payment_allocations": "",
    "bank_transactions": "payment_reference bank_account_id transaction_date",
    "gl_entries": "accounting_period gl_account",
    "approval_events": "approver_role previous_status",
}


def optional_columns(table: str) -> frozenset[str]:
    """Columns of `table` that may be empty. Tables the checks never read have
    nothing required beyond their key."""
    if table in _OPTIONAL:
        return frozenset(_OPTIONAL[table].split())
    return frozenset(c.name for c in TABLES[table].columns) - set(KEYS[table])


# Values the diagnosis code compares against. A value outside these sets would
# make a row match nothing, so it is rejected, naming the allowed values.
# 'failed' and 'returned' appear in docs/data-audit.md section 8 (RCA_001631,
# RCA_002223); the rest are the values the diagnosis code tests.
CLOSED_VOCABULARY: dict[tuple[str, str], frozenset[str]] = {
    ("payments", "payment_status"): frozenset(
        {"completed", "submitted", "pending", "failed", "reversed"}
    ),
    ("payments", "settlement_status"): frozenset(
        {"settled", "pending", "failed", "returned", "reversed"}
    ),
    ("bank_transactions", "status"): frozenset({"posted", "returned"}),
    ("bank_transactions", "direction"): frozenset({"debit", "credit"}),
}

# Open vocabularies: other values are normal ("ACH debit", "card settlement"), so
# they pass and are listed in the manifest. The values below are the ones the
# diagnosis code compares by exact text. A different spelling of one of them
# ("Bank Fee", "bank_fee" in the bank table, "ach") is rejected, because it would
# silently turn a fee row into an ordinary debit or a payment method into an
# unknown one.
_SPECIAL: dict[tuple[str, str], tuple[str, ...]] = {
    ("payments", "payment_method"): tuple(CLEARING_LIMITS),
    ("bank_transactions", "transaction_type"): ("bank fee", "payment reversal"),
    ("gl_entries", "transaction_type"): ("payment", "bank_fee", "payment_reversal"),
    ("invoices", "status"): ("paid",),
    ("approval_events", "action"): ("approved", "auto_approved"),
    ("approval_events", "new_status"): ("approved",),
}

REFERENCE_RULE = "trim, collapse runs of whitespace to one space, uppercase"
ACCOUNT_RULE = (
    "remove all whitespace, uppercase (so printed and electronic IBANs agree)"
)


def _reference(v: str) -> str:
    return re.sub(r"\s+", " ", v.strip()).upper()


def _account(v: str) -> str:
    return re.sub(r"\s+", "", v).upper()


_NORMALISED: dict[tuple[str, str], Callable[[str], str]] = {
    ("payments", "reference_number"): _reference,
    ("bank_transactions", "payment_reference"): _reference,
    ("payments", "bank_account_id"): _account,
    ("bank_transactions", "counterparty_token"): _account,
}


def _squash(v: str) -> str:
    return re.sub(r"[\s_]+", "", v).lower()


def _iso_date(v: str) -> None:
    # fromisoformat also accepts "20260601"; the investigator compares dates as
    # text, so only the extended form is allowed.
    if date.fromisoformat(v).isoformat() != v:
        raise ValueError


def _iso_timestamp(v: str) -> None:
    parsed = datetime.fromisoformat(v)
    # The investigator compares the first ten characters as a date and subtracts
    # parsed values; a UTC offset would make the two disagree or fail.
    if parsed.tzinfo is not None or parsed.date().isoformat() != v[:10]:
        raise ValueError


def _check_value(table: str, col: str, v: str) -> str | None:
    """A reason `v` is unusable in this column, or None."""
    key = (table, col)
    allowed = CLOSED_VOCABULARY.get(key)
    if allowed is not None and v not in allowed:
        return f"{v!r} is not one of {sorted(allowed)}"
    for known in _SPECIAL.get(key, ()):
        if v != known and _squash(v) == _squash(known):
            return f"{v!r} must be written exactly {known!r}"
    return None


def prepare_operational_snapshot(
    tables: Mapping[str, Sequence[Mapping[str, str | None]]],
    out_dir: Path,
    *,
    cutoff: date,
    bank_data_through: date,
    source_format: str,
    source_files: Mapping[str, str],
    extras: Mapping[str, Any] | None = None,
    locate: Callable[[str, int], str] | None = None,
) -> dict[str, Any]:
    """Write `snapshot.sqlite`, `manifest.json` and `quality.json` into the new
    directory `out_dir` and return the manifest.

    `tables` maps table names (those of `finrca.TABLES`) to rows keyed by
    contract column names. `locate(table, n)` names the n-th row (from 1) of a
    table in errors; the default is "row n".
    """
    if cutoff > bank_data_through:
        raise ValueError(
            f"cutoff {cutoff} is after bank_data_through {bank_data_through}: "
            "payments made after the bank data ends would look missing from the bank"
        )
    unknown = sorted(set(tables) - set(TABLES))
    if unknown:
        raise ValueError(f"unknown tables: {unknown}")
    absent = [t for t in REQUIRED_TABLES if t not in tables]
    if absent:
        raise ValueError(f"required tables not supplied: {absent}")
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} already exists; prepare into a new directory")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    # Build in a sibling directory and rename it into place in one step, so a
    # reader sees either nothing or a complete snapshot and manifest pair.
    work = Path(tempfile.mkdtemp(prefix=".prepare-", dir=out_dir.parent))
    try:
        manifest = _prepare(
            tables,
            work,
            cutoff,
            bank_data_through,
            source_format,
            dict(source_files),
            dict(extras or {}),
            locate or (lambda _table, n: f"row {n}"),
        )
        os.chmod(work, 0o755)
        os.rename(work, out_dir)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return manifest


def _clean(
    name: str,
    raw: Mapping[str, str | None],
    where: str,
    bank_data_through: date,
) -> dict[str, str | None]:
    """One row with empty values as None, the required columns present, values
    inside the vocabulary and the reference columns normalised."""
    columns = {c.name: c for c in TABLES[name].columns}
    extra = sorted(set(raw) - set(columns))
    if extra:
        raise ValueError(f"{name} {where}: unknown columns {extra}")
    optional = optional_columns(name)
    row: dict[str, str | None] = {}
    for col in columns:
        v = raw.get(col)
        if v is not None and not isinstance(v, str):
            raise TypeError(f"{name} {where}: {col} must be text, not {type(v)}")
        if v is None or not v.strip():
            if col not in optional:
                raise ValueError(f"{name} {where}: {col} is empty")
            row[col] = None
            continue
        problem = _check_value(name, col, v)
        if problem:
            raise ValueError(f"{name} {where}: {col}: {problem}")
        fn = _NORMALISED.get((name, col))
        row[col] = fn(v) if fn else v
        if fn and not row[col]:
            raise ValueError(f"{name} {where}: {col} is empty")
    for col, spec in columns.items():
        v = row[col]
        if v is None:
            continue
        try:
            if spec.kind == "date":
                _iso_date(v)
            elif spec.kind == "timestamp":
                _iso_timestamp(v)
        except ValueError:
            raise ValueError(
                f"{name} {where}: {col} {v!r} is not an ISO {spec.kind}"
            ) from None
    if name == "bank_transactions":
        posted = row["posted_date"]
        assert posted is not None  # required
        if date.fromisoformat(posted) > bank_data_through:
            raise ValueError(
                f"{name} {where}: posted_date {posted} is after "
                f"bank_data_through {bank_data_through}"
            )
    return row


def _money(
    stats: _MoneyStats, v: str | None, currency: str | None, what: str
) -> str | None:
    if v is None:
        return None
    if currency is None:
        raise ValueError(f"{what}: has an amount but no currency")
    return stats.amount(v, currency)


def _prepare(
    tables: Mapping[str, Sequence[Mapping[str, str | None]]],
    out_dir: Path,
    cutoff: date,
    bank_data_through: date,
    source_format: str,
    source_files: dict[str, str],
    extras: dict[str, Any],
    locate: Callable[[str, int], str],
) -> dict[str, Any]:
    stats = _MoneyStats()
    currency_of: dict[str, dict[str, str]] = {}
    snapshot_tables: dict[str, tuple[list[str], list[Row]]] = {}
    report: dict[str, Any] = {}
    not_supplied: list[str] = []
    vocabulary: dict[str, Counter[str]] = {k: Counter() for k in _OPEN_LISTED}

    for name, table in TABLES.items():
        names = [c.name for c in table.columns]
        if name not in tables:
            not_supplied.append(name)
            snapshot_tables[name] = (names, [])
            continue
        source_rows = tables[name]
        rows: list[Row] = []
        seen: dict[tuple[str | None, ...], tuple[int, tuple[str | None, ...]]] = {}
        cut = dupes = 0
        for n, raw in enumerate(source_rows, start=1):
            where = locate(name, n)
            row = _clean(name, raw, where, bank_data_through)
            if table.cutoff_column:
                day = row[table.cutoff_column]
                assert day is not None  # required in checked tables
                if date.fromisoformat(day) > cutoff:
                    cut += 1
                    continue
            ident = tuple(row[c] for c in KEYS[name])
            content = tuple(row[c] for c in names)
            if ident in seen:
                first_line, first = seen[ident]
                if first != content:
                    raise ValueError(
                        f"{name}: conflicting rows for id {ident} "
                        f"({locate(name, first_line)} and {where})"
                    )
                dupes += 1  # exact repeat of an earlier row: a repeated import
                continue
            seen[ident] = (n, content)
            try:
                rows.append(_transform(name, table.columns, row, stats, currency_of))
            except ValueError as e:
                raise ValueError(f"{name} {where}: {e}") from e
            id_col, cur_col, ns = table.registers_currency
            if ns:
                currency = row[cur_col]
                assert currency is not None
                currency_of.setdefault(ns, {})[str(row[id_col])] = currency
            for key, counter in vocabulary.items():
                t, c = key.split(".")
                if t == name and row[c] is not None:
                    counter[str(row[c])] += 1
        rows.sort(key=lambda r: tuple("" if v is None else v for v in r))
        snapshot_tables[name] = (names, rows)
        report[name] = {
            "rows_before": len(source_rows),
            "rows_after": len(rows),
            "dropped_by_cutoff": cut,
            "exact_duplicates_removed": dupes,
            "columns_kept": names,
        }

    content_sha, file_sha = write_snapshot(
        out_dir / SNAPSHOT_FILE, snapshot_tables, INDEXES
    )
    quality_sha = _write_quality(out_dir)
    manifest: dict[str, Any] = {
        "adapter_version": ADAPTER_VERSION,
        "format": source_format,
        "cutoff": cutoff.isoformat(),
        "bank_data_through": bank_data_through.isoformat(),
        "sqlite_version": sqlite3.sqlite_version,
        "source_file_sha256": source_files,
        "identifiers": "source record IDs stored unchanged (no tokenising)",
        "normalisation": {
            "references": REFERENCE_RULE,
            "accounts": ACCOUNT_RULE,
            "columns": sorted(f"{t}.{c}" for t, c in _NORMALISED),
            "original_text_kept": False,
        },
        "tables_not_supplied": not_supplied,
        "tables": report,
        "vocabulary": {k: dict(sorted(v.items())) for k, v in vocabulary.items()},
        "unrecognised_payment_methods": {
            v: n
            for v, n in sorted(vocabulary["payments.payment_method"].items())
            if v not in CLEARING_LIMITS
        },
        "reference_quality": _reference_quality(snapshot_tables),
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
        "extras": extras,
    }
    manifest_path = out_dir / MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o444)
    return manifest


# Open vocabularies listed in the manifest, as "table.column".
_OPEN_LISTED = (
    "payments.payment_method",
    "bank_transactions.transaction_type",
    "gl_entries.transaction_type",
    "invoices.status",
    "approval_events.action",
    "approval_events.new_status",
)


def _transform(
    table: str,
    columns: Sequence[Col],
    row: dict[str, str | None],
    stats: _MoneyStats,
    currency_of: dict[str, dict[str, str]],
) -> Row:
    out: list[str | None] = []
    for col in columns:
        v = row[col.name]
        if col.kind == "currency":
            if v is not None:
                stats.currency(v)
            out.append(v)
        elif col.kind == "money":
            if (table, col.name) == ("bank_transactions", "amount") and (v or "")[
                :1
            ] == "-":
                raise ValueError("amount is negative; direction carries the sign")
            out.append(_money(stats, v, row[col.arg], col.name))
        elif col.kind == "money_of":
            ns, fk = col.arg.split(":")
            parent = row[fk]
            currency = currency_of.get(ns, {}).get(parent) if parent else None
            if v is not None and currency is None:
                raise ValueError(
                    f"{col.name}: no {ns} {parent!r} to take a currency from"
                )
            out.append(_money(stats, v, currency, col.name))
        else:
            out.append(v)  # keep, remap, date, timestamp, poly_remap: as cleaned
    return tuple(out)


def _reference_quality(
    snapshot_tables: Mapping[str, tuple[list[str], list[Row]]],
) -> dict[str, dict[str, int]]:
    """How usable references are, as counts: a payment without a unique reference
    cannot be matched to a bank row by it."""
    pcols, prows = snapshot_tables["payments"]
    ref = pcols.index("reference_number")
    refs = Counter(r[ref] for r in prows if r[ref])
    bcols, brows = snapshot_tables["bank_transactions"]
    direction, bank_ref = bcols.index("direction"), bcols.index("payment_reference")
    debits = [r for r in brows if r[direction] == "debit"]
    return {
        "payments": {
            "total": len(prows),
            "with_reference": sum(refs.values()),
            "with_unique_reference": sum(1 for n in refs.values() if n == 1),
        },
        "bank_debits": {
            "total": len(debits),
            "with_reference": sum(1 for r in debits if r[bank_ref]),
        },
    }
