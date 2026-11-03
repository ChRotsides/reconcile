"""ISO 20022 CAMT.053 XML statements read into bank rows plus coverage.

One bank row per booked `Ntry` (an entry is never split into its `TxDtls`, so
amounts are never divided). The mapping and its interpretations are in
docs/input-mapping.md sections 3.5, 3.6 and 10.

XML is read with expat directly so that a DOCTYPE (and with it every entity
declaration) is refused: a statement has no reason to carry one, and refusing
is simpler than limiting entity expansion.

Coverage (the period each statement states) decides `bank_data_through`:
per account the periods must follow each other without a gap, and overlapping
statements are refused unless one is an exact repeat of another. Overlap would
repeat entries; with position-based IDs that fakes a second, identical debit.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.parsers import expat

PROBLEM_CAP = 25
NAMESPACE_PREFIX = "urn:iso:std:iso:20022:tech:xsd:camt.053.001."
REFERENCE_KINDS = ("EndToEndId", "InstrId", "TxId", "PmtInfId", "Ustrd", "CdtrRefInf")
# The Westpac table lists a fee as domain CAMT, family ACCB, sub-family CHRG
# (docs/input-mapping.md 3.5). One bank's table: others may differ.
DEFAULT_FEE_CODES = (("CAMT", "ACCB", "CHRG"),)
_AMOUNT = re.compile(r"[0-9]+(\.[0-9]+)?", re.ASCII)
_NOT_PROVIDED = "NOTPROVIDED"
_SEP = " "  # namespace separator in expat names: "<uri> <local>"


class _El:
    """A parsed element: local name, attributes, own text, children."""

    __slots__ = ("attrs", "children", "line", "tag", "text")

    def __init__(self, tag: str, attrs: dict[str, str], line: int) -> None:
        self.tag = tag
        self.attrs = attrs
        self.line = line
        self.text = ""
        self.children: list[_El] = []

    def find(self, *path: str) -> "_El | None":
        node: _El | None = self
        for name in path:
            if node is None:
                return None
            node = next((c for c in node.children if c.tag == name), None)
        return node

    def findall(self, name: str) -> "list[_El]":
        return [c for c in self.children if c.tag == name]

    def value(self, *path: str) -> str | None:
        node = self.find(*path)
        text = node.text.strip() if node is not None else ""
        return text or None

    def canonical(self) -> tuple[Any, ...]:
        return (
            self.tag,
            tuple(sorted(self.attrs.items())),
            self.text.strip(),
            tuple(c.canonical() for c in self.children),
        )


def parse_xml(data: bytes) -> _El:
    """Root element of a CAMT.053 document. Refuses a DOCTYPE, any other root
    and any namespace other than camt.053.001.*. Names are local names."""
    parser = expat.ParserCreate(namespace_separator=_SEP)
    parser.buffer_text = True
    stack: list[_El] = []
    root: list[_El] = []

    def refuse_doctype(*_args: object) -> None:
        raise ValueError("a DOCTYPE or entity declaration is not allowed")

    def start(name: str, attrs: dict[str, str]) -> None:
        uri, _, local = name.rpartition(_SEP)
        if not uri.startswith(NAMESPACE_PREFIX):
            raise ValueError(
                f"element {local!r} is in namespace {uri or '(none)'!r}, not "
                f"{NAMESPACE_PREFIX}*"
            )
        if not stack and local != "Document":
            raise ValueError(f"root element is {local!r}, not 'Document'")
        node = _El(
            local,
            {k.rpartition(_SEP)[2]: v for k, v in attrs.items()},
            parser.CurrentLineNumber,
        )
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)

    def end(_name: str) -> None:
        stack.pop()

    def text(data: str) -> None:
        stack[-1].text += data

    parser.StartDoctypeDeclHandler = refuse_doctype
    parser.EntityDeclHandler = refuse_doctype
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    try:
        parser.Parse(data, True)
    except expat.ExpatError as e:
        raise ValueError(f"not well-formed XML: {e}") from None
    if not root:
        raise ValueError("no root element")
    return root[0]


@dataclass(frozen=True)
class Period:
    first: date
    last: date
    source: str  # "FrToDt" or "balance date"


@dataclass
class Statement:
    file: str
    stmt_id: str
    account: str
    currency: str | None
    period: Period | None
    node: _El = field(repr=False)

    @property
    def label(self) -> str:
        return f"{self.file} Stmt {self.stmt_id!r}"


@dataclass
class CamtData:
    rows: list[dict[str, str | None]] = field(default_factory=list)
    where: list[str] = field(default_factory=list)
    bank_data_through: date | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class _Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, where: str, message: str) -> None:
        self.items.append(f"{where}: {message}")

    def raise_if_any(self) -> None:
        if not self.items:
            return
        shown = "\n  ".join(self.items[:PROBLEM_CAP])
        more = len(self.items) - PROBLEM_CAP
        tail = f"\n  ... and {more} more" if more > 0 else ""
        raise ValueError(
            f"{len(self.items)} problem(s) in the CAMT.053 files; nothing was "
            f"prepared (no entry is dropped or guessed):\n  {shown}{tail}"
        )


class _Bad(Exception):
    pass


def _day(text: str, what: str) -> date:
    """Date part of an ISO date or date-time as written (no zone conversion)."""
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        return datetime.fromisoformat(text).date()
    except ValueError:
        raise _Bad(f"{what} {text!r} is not an ISO date") from None


def _date_of(node: _El | None, what: str) -> date | None:
    """The value of a `Dt` / `DtTm` choice element, or None when absent."""
    if node is None:
        return None
    for name in ("Dt", "DtTm"):
        v = node.value(name)
        if v:
            return _day(v, what)
    return None


def _period(stmt: _El) -> Period | None:
    window = stmt.find("FrToDt")
    if window is not None:
        a, b = window.value("FrDtTm"), window.value("ToDtTm")
        if a and b:
            return Period(_day(a, "FrDtTm"), _day(b, "ToDtTm"), "FrToDt")
        raise _Bad("FrToDt lacks FrDtTm or ToDtTm")
    for bal in stmt.findall("Bal"):
        if bal.value("Tp", "CdOrPrtry", "Cd") == "CLBD":
            d = _date_of(bal.find("Dt"), "closing balance Dt")
            if d:
                return Period(d, d, "balance date")
    return None


def _account(stmt: _El) -> str:
    acct = stmt.find("Acct", "Id")
    if acct is not None:
        v = acct.value("IBAN") or acct.value("Othr", "Id")
        if v:
            return re.sub(r"\s+", "", v).upper()
    raise _Bad("Stmt/Acct/Id has neither IBAN nor Othr/Id")


def _status(entry: _El) -> str | None:
    return entry.value("Sts") or entry.value("Sts", "Cd")


def _fee_code(entry: _El) -> tuple[str, ...]:
    return (
        entry.value("BkTxCd", "Domn", "Cd") or "",
        entry.value("BkTxCd", "Domn", "Fmly", "Cd") or "",
        entry.value("BkTxCd", "Domn", "Fmly", "SubFmlyCd") or "",
    )


def _ref_values(details: _El, kind: str) -> list[str]:
    paths = {
        "EndToEndId": ("Refs", "EndToEndId"),
        "InstrId": ("Refs", "InstrId"),
        "TxId": ("Refs", "TxId"),
        "PmtInfId": ("Refs", "PmtInfId"),
    }
    if kind in paths:
        v = details.value(*paths[kind])
        return [v] if v else []
    rmt = details.find("RmtInf")
    if rmt is None:
        return []
    if kind == "Ustrd":
        return [t for n in rmt.findall("Ustrd") if (t := n.text.strip())]
    out = []
    for strd in rmt.findall("Strd"):
        v = strd.value("CdtrRefInf", "Ref")
        if v:
            out.append(v)
    return out


def _reference(entry: _El, fields: Sequence[str], used: Counter[str]) -> str | None:
    details = [d for nd in entry.findall("NtryDtls") for d in nd.findall("TxDtls")]
    if len(details) == 1:
        for kind in fields:
            values = [v for v in _ref_values(details[0], kind) if v != _NOT_PROVIDED]
            if values:
                used[kind] += 1
                return _SEP.join(values)
        used["none"] += 1
        return None
    # No TxDtls, or several: one row stands for all of them, so only a reference
    # the whole batch shares can describe it.
    batch = next(
        (v for nd in entry.findall("NtryDtls") if (v := nd.value("Btch", "PmtInfId"))),
        None,
    )
    if batch and batch != _NOT_PROVIDED:
        used["Btch/PmtInfId"] += 1
        return batch
    used["none_several_TxDtls" if details else "none"] += 1
    return None


def _counterparty(entry: _El, direction: str) -> str | None:
    """The other party's account: the creditor's for a debit, the debtor's for a
    credit. Never a name. Several TxDtls with different accounts give None."""
    acct = "CdtrAcct" if direction == "DBIT" else "DbtrAcct"
    found = {
        v
        for nd in entry.findall("NtryDtls")
        for d in nd.findall("TxDtls")
        if (v := d.value("RltdPties", acct, "Id", "IBAN"))
    }
    return found.pop() if len(found) == 1 else None


def _entry_row(
    stmt: Statement,
    entry: _El,
    position: int,
    fields: Sequence[str],
    fee_codes: frozenset[tuple[str, ...]],
    used_ref: Counter[str],
    id_used: Counter[str],
) -> dict[str, str | None]:
    amt = entry.find("Amt")
    if amt is None or not amt.text.strip():
        raise _Bad("no Amt")
    amount = amt.text.strip()
    if amount.startswith("-"):
        raise _Bad(f"Amt {amount!r} is negative; CdtDbtInd carries the sign")
    if not _AMOUNT.fullmatch(amount):
        raise _Bad(f"Amt {amount!r} is not a plain decimal")
    currency = amt.attrs.get("Ccy")
    if not currency:
        raise _Bad("Amt has no Ccy attribute")
    if stmt.currency and currency != stmt.currency:
        raise _Bad(
            f"Amt currency {currency} differs from Stmt/Acct/Ccy {stmt.currency}"
        )
    indicator = entry.value("CdtDbtInd")
    if indicator not in ("CRDT", "DBIT"):
        raise _Bad(f"CdtDbtInd {indicator!r} is not CRDT or DBIT")
    booked = _date_of(entry.find("BookgDt"), "BookgDt")
    if booked is None:
        raise _Bad("no BookgDt")
    if stmt.period and not stmt.period.first <= booked <= stmt.period.last:
        raise _Bad(
            f"BookgDt {booked} is outside the statement period "
            f"{stmt.period.first} to {stmt.period.last}"
        )
    value_date = _date_of(entry.find("ValDt"), "ValDt")
    reversal = (entry.value("RvslInd") or "").lower() in ("true", "1", "yes")
    is_fee = _fee_code(entry) in fee_codes
    if reversal and is_fee:
        raise _Bad("is both a fee code and a reversal (RvslInd); no mapping defined")
    if reversal:
        kind = "payment reversal"
    elif is_fee:
        kind = "bank fee"
    else:
        kind = "bank debit" if indicator == "DBIT" else "bank credit"
    for key, source in (("AcctSvcrRef", "AcctSvcrRef"), ("NtryRef", "NtryRef")):
        ident = entry.value(key)
        if ident:
            id_used[source] += 1
            break
    else:
        ident = f"{stmt.stmt_id}:{position}"
        id_used["Stmt/Id:position"] += 1
    return {
        "bank_transaction_id": ident,
        "counterparty_token": _counterparty(entry, indicator),
        "payment_reference": _reference(entry, fields, used_ref),
        "bank_account_id": stmt.account,
        "transaction_date": value_date.isoformat() if value_date else None,
        "posted_date": booked.isoformat(),
        "transaction_type": kind,
        "amount": amount,
        "currency": currency,
        "direction": "debit" if indicator == "DBIT" else "credit",
        "status": "posted",
    }


def _statements(path: Path, problems: _Problems) -> list[Statement]:
    name = path.name
    try:
        root = parse_xml(path.read_bytes())
    except (OSError, ValueError) as e:
        problems.add(name, str(e))
        return []
    container = root.find("BkToCstmrStmt")
    if container is None:
        problems.add(name, "no BkToCstmrStmt element (is this a CAMT.053 statement?)")
        return []
    out = []
    for stmt in container.findall("Stmt"):
        sid = stmt.value("Id")
        if not sid:
            problems.add(f"{name} line {stmt.line}", "Stmt has no Id")
            continue
        where = f"{name} Stmt {sid!r}"
        try:
            out.append(
                Statement(
                    name,
                    sid,
                    _account(stmt),
                    stmt.value("Acct", "Ccy"),
                    _period(stmt),
                    stmt,
                )
            )
        except _Bad as e:
            problems.add(where, str(e))
    if not out:
        problems.add(name, "contains no Stmt")
    return out


def _coverage(
    statements: Sequence[Statement],
    declared: date | None,
    problems: _Problems,
) -> tuple[date | None, dict[str, Any]]:
    """`bank_data_through` and a description of the periods used."""
    groups: dict[tuple[str, str | None], list[Statement]] = {}
    unstated: list[Statement] = []
    for s in statements:
        if s.period is None:
            unstated.append(s)
        else:
            groups.setdefault((s.account, s.currency), []).append(s)
    for s in unstated:
        if declared is None:
            problems.add(
                s.label,
                "states no period (no FrToDt and no closing balance date); give "
                "bank_data_through in the config to say how far the bank data runs",
            )
    lasts: dict[str, str] = {}
    periods: dict[str, list[dict[str, str]]] = {}
    for (account, currency), group in sorted(
        groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")
    ):
        group.sort(key=lambda s: (s.period.first, s.period.last))  # type: ignore[union-attr]
        key = f"{account} {currency or '(no currency)'}"
        periods[key] = [
            {
                "statement": s.label,
                "from": s.period.first.isoformat(),  # type: ignore[union-attr]
                "to": s.period.last.isoformat(),  # type: ignore[union-attr]
                "stated_by": s.period.source,  # type: ignore[union-attr]
            }
            for s in group
        ]
        for a, b in zip(group, group[1:], strict=False):
            assert a.period and b.period
            if b.period.first <= a.period.last:
                problems.add(
                    b.label,
                    f"period {b.period.first} to {b.period.last} overlaps "
                    f"{a.label} ({a.period.first} to {a.period.last}); overlapping "
                    "statements would repeat entries. Remove one, or export "
                    "non-overlapping periods",
                )
            elif b.period.first > a.period.last + timedelta(days=1):
                problems.add(
                    b.label,
                    f"a gap follows {a.label} (last day {a.period.last}); this "
                    f"statement starts {b.period.first}. Entries in the gap would "
                    "look missing from the bank",
                )
        lasts[key] = max(s.period.last for s in group if s.period).isoformat()
    computed = min(date.fromisoformat(v) for v in lasts.values()) if lasts else None
    if declared is not None and computed is not None and declared > computed:
        problems.add(
            "config",
            f"bank_data_through {declared} is later than the statements show "
            f"({computed}, the earliest last day covered across accounts)",
        )
    through = declared if declared is not None else computed
    return through, {
        "periods_by_account": periods,
        "last_day_by_account": lasts,
        "statements_without_period": [s.label for s in unstated],
        "bank_data_through_declared_in_config": declared.isoformat()
        if declared
        else None,
        "bank_data_through_from_statements": computed.isoformat() if computed else None,
    }


def read_camt053(
    files: Sequence[Path],
    *,
    reference_fields: Sequence[str] = ("EndToEndId",),
    fee_codes: Sequence[Sequence[str]] = DEFAULT_FEE_CODES,
    bank_data_through: date | None = None,
) -> CamtData:
    """Bank rows for `bank_transactions` and the date the statements run to."""
    bad = [f for f in reference_fields if f not in REFERENCE_KINDS]
    if bad or not reference_fields:
        raise ValueError(
            f"bank_reference_fields must be a non-empty list of {list(REFERENCE_KINDS)}"
        )
    if not files:
        raise ValueError("no CAMT.053 file given")
    codes = frozenset(tuple(c) for c in fee_codes)
    problems = _Problems()
    seen: dict[str, Statement] = {}
    unique: list[Statement] = []
    repeats: list[str] = []
    for path in files:
        for s in _statements(path, problems):
            first = seen.get(s.stmt_id)
            if first is None:
                seen[s.stmt_id] = s
                unique.append(s)
            elif first.node.canonical() == s.node.canonical():
                repeats.append(f"{s.label} repeats {first.label}")
            else:
                problems.add(
                    s.label,
                    f"has the same Stmt/Id as {first.label} but different content",
                )
    through, coverage = _coverage(unique, bank_data_through, problems)
    out = CamtData(bank_data_through=through)
    excluded: Counter[str] = Counter()
    used_ref: Counter[str] = Counter()
    id_used: Counter[str] = Counter()
    ids: dict[str, str] = {}
    for stmt in unique:
        for position, entry in enumerate(stmt.node.findall("Ntry"), start=1):
            where = f"{stmt.label} Ntry #{position} (line {entry.line})"
            sts = _status(entry)
            if sts != "BOOK":
                excluded[str(sts)] += 1
                continue
            try:
                row = _entry_row(
                    stmt, entry, position, reference_fields, codes, used_ref, id_used
                )
            except _Bad as e:
                problems.add(where, str(e))
                continue
            ident = row["bank_transaction_id"] or ""
            if ident in ids:
                problems.add(
                    where,
                    f"bank_transaction_id {ident!r} is also used by {ids[ident]}; "
                    "two entries must not share an ID",
                )
                continue
            ids[ident] = where
            out.rows.append(row)
            out.where.append(where)
    problems.raise_if_any()
    types = Counter(r["transaction_type"] for r in out.rows)
    out.extras = {
        "files": len(files),
        "statements_read": len(unique),
        "statements_dropped_as_exact_repeats": repeats,
        "entries_excluded_not_booked": dict(sorted(excluded.items())),
        "bank_transaction_id_source": dict(sorted(id_used.items())),
        "payment_reference_source": dict(sorted(used_ref.items())),
        "reference_fields": list(reference_fields),
        "fee_codes": [list(c) for c in sorted(codes)],
        "transaction_types": dict(sorted((str(k), v) for k, v in types.items())),
        "coverage": coverage,
        "limitation": (
            "A payment's own bank account (Xero) is not compared with the "
            "statement account (IBAN); the two are not checked against each other."
        ),
    }
    return out
