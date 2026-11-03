"""ISO 20022 CAMT.053 XML statements read into bank rows plus coverage.

One bank row per booked `Ntry` (an entry is never split into its `TxDtls`, so
amounts are never divided). The mapping and its interpretations are in
docs/input-mapping.md sections 3.5, 3.6 and 10.

XML is read with expat directly so that a DOCTYPE (and with it every entity
declaration) is refused: a statement has no reason to carry one, and refusing
is simpler than limiting entity expansion.

Coverage (the period each statement states) decides `bank_data_from` and
`bank_data_through`: per account the periods must follow each other without a
gap, and overlapping statements are refused unless one is an exact repeat of
another. Overlap would repeat entries; with position-based IDs that fakes a
second, identical debit. A partly covered first or last day is not counted as
covered.

A statement that cannot be shown complete is refused, because the investigator
reads "no bank row" as evidence: every entry needs a recognised status, a stated
entry count must match the entries present, and opening plus booked credits
minus booked debits must equal the closing balance when both are stated.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.parsers import expat

from reconcile.adapters.inputs import Problems

NAMESPACE_PREFIX = "urn:iso:std:iso:20022:tech:xsd:camt.053.001."
REFERENCE_KINDS = ("EndToEndId", "InstrId", "TxId", "PmtInfId", "Ustrd", "CdtrRefInf")
# The Westpac table lists a fee as domain CAMT, family ACCB, sub-family CHRG
# (docs/input-mapping.md 3.5). One bank's table: others may differ.
DEFAULT_FEE_CODES = (("CAMT", "ACCB", "CHRG"),)
_AMOUNT = re.compile(r"[0-9]+(\.[0-9]+)?", re.ASCII)
_NOT_PROVIDED = "NOTPROVIDED"
ENTRY_STATUSES = ("BOOK", "PDNG", "INFO")
# A statement period given as date-times covers its first day from here ...
_DAY_STARTS_BY = time(0, 0, 59)
# ... and its last day from here, so a day cut off earlier is not fully covered.
_DAY_ENDS_FROM = time(23, 59)
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
    first: date  # as stated
    last: date
    source: str  # "FrToDt" or "balance date"
    covered_first: date  # the first and last day the statement covers whole
    covered_last: date


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
    bank_data_from: date | None = None
    bank_data_through: date | None = None
    extras: dict[str, Any] = field(default_factory=dict)


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


def _clock(text: str, what: str) -> time | None:
    """Wall-clock time of a date-time as written (no zone conversion); None for
    a plain date."""
    if len(text) == 10:
        return None
    try:
        return datetime.fromisoformat(text).time()
    except ValueError:
        raise _Bad(f"{what} {text!r} is not an ISO date") from None


def _period(stmt: _El) -> Period | None:
    window = stmt.find("FrToDt")
    if window is not None:
        a, b = window.value("FrDtTm"), window.value("ToDtTm")
        if a and b:
            first, last = _day(a, "FrDtTm"), _day(b, "ToDtTm")
            start, end = _clock(a, "FrDtTm"), _clock(b, "ToDtTm")
            return Period(
                first,
                last,
                "FrToDt",
                first
                if start is None or start <= _DAY_STARTS_BY
                else first + timedelta(days=1),
                last
                if end is None or end >= _DAY_ENDS_FROM
                else last - timedelta(days=1),
            )
        raise _Bad("FrToDt lacks FrDtTm or ToDtTm")
    for bal in stmt.findall("Bal"):
        if bal.value("Tp", "CdOrPrtry", "Cd") == "CLBD":
            d = _date_of(bal.find("Dt"), "closing balance Dt")
            if d:
                return Period(d, d, "balance date", d, d)
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
    credit. Never a name. Known only when every TxDtls names an account and they
    all agree; one without an account, or two accounts, give None, so the
    investigator treats the counterparty as unknown rather than as the one seen."""
    acct = "CdtrAcct" if direction == "DBIT" else "DbtrAcct"
    found = [
        d.value("RltdPties", acct, "Id", "IBAN")
        for nd in entry.findall("NtryDtls")
        for d in nd.findall("TxDtls")
    ]
    accounts = {re.sub(r"\s+", "", v).upper() if v else None for v in found}
    if not found or len(accounts) != 1 or None in accounts:
        return None
    return accounts.pop()


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


def _statements(path: Path, problems: Problems) -> list[Statement]:
    name = path.name
    try:
        root = parse_xml(path.read_bytes())
    except (OSError, ValueError) as e:
        problems.add(str(e), name)
        return []
    container = root.find("BkToCstmrStmt")
    if container is None:
        problems.add("no BkToCstmrStmt element (is this a CAMT.053 statement?)", name)
        return []
    out = []
    for stmt in container.findall("Stmt"):
        sid = stmt.value("Id")
        if not sid:
            problems.add("Stmt has no Id", f"{name} line {stmt.line}")
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
            problems.add(str(e), where)
    if not out:
        problems.add("contains no Stmt", name)
    return out


def _coverage(
    statements: Sequence[Statement],
    declared_from: date | None,
    declared: date | None,
    problems: Problems,
) -> tuple[date | None, date | None, dict[str, Any]]:
    """`bank_data_from`, `bank_data_through` and a description of the periods
    used. The data is complete only where every account is covered, so the start
    is the latest first covered day and the end the earliest last covered day."""
    groups: dict[tuple[str, str | None], list[Statement]] = {}
    unstated: list[Statement] = []
    for s in statements:
        if s.period is None:
            unstated.append(s)
        else:
            groups.setdefault((s.account, s.currency), []).append(s)
    if unstated and (declared is None or declared_from is None):
        for s in unstated:
            problems.add(
                "states no period (no FrToDt and no closing balance date); give "
                "bank_data_from and bank_data_through in the config to say what the "
                "bank data covers",
                s.label,
            )
    firsts: dict[str, str] = {}
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
                    f"period {b.period.first} to {b.period.last} overlaps "
                    f"{a.label} ({a.period.first} to {a.period.last}); overlapping "
                    "statements would repeat entries. Remove one, or export "
                    "non-overlapping periods",
                    b.label,
                )
            elif b.period.covered_first > a.period.covered_last + timedelta(days=1):
                problems.add(
                    f"a gap follows {a.label} (last day covered "
                    f"{a.period.covered_last}); this statement covers whole days "
                    f"from {b.period.covered_first}. Entries in the gap would "
                    "look missing from the bank",
                    b.label,
                )
        assert group[0].period
        firsts[key] = group[0].period.covered_first.isoformat()
        lasts[key] = max(s.period.covered_last for s in group if s.period).isoformat()
    computed = min(date.fromisoformat(v) for v in lasts.values()) if lasts else None
    computed_from = (
        max(date.fromisoformat(v) for v in firsts.values()) if firsts else None
    )
    if declared is not None and computed is not None and declared > computed:
        problems.add(
            f"bank_data_through {declared} is later than the statements show "
            f"({computed}, the earliest last day covered across accounts)",
            "config",
        )
    if declared_from is not None and computed_from is not None:
        if declared_from < computed_from:
            problems.add(
                f"bank_data_from {declared_from} is earlier than the statements "
                f"show ({computed_from}, the latest first day covered across "
                "accounts)",
                "config",
            )
    through = declared if declared is not None else computed
    start = declared_from if declared_from is not None else computed_from
    if start is not None and through is not None and start > through:
        problems.add(
            f"the statements cover no whole day: coverage would start {start} "
            f"and end {through}",
            "config",
        )
    return (
        start,
        through,
        {
            "periods_by_account": periods,
            "first_day_by_account": firsts,
            "last_day_by_account": lasts,
            "statements_without_period": [s.label for s in unstated],
            "bank_data_from_declared_in_config": declared_from.isoformat()
            if declared_from
            else None,
            "bank_data_through_declared_in_config": declared.isoformat()
            if declared
            else None,
            "bank_data_from_from_statements": computed_from.isoformat()
            if computed_from
            else None,
            "bank_data_through_from_statements": computed.isoformat()
            if computed
            else None,
        },
    )


def _signed(bal: _El, currency: str, problems: Problems, label: str) -> Decimal | None:
    """A balance as a signed amount, or None when it is in another currency or
    cannot be read (the latter is a problem)."""
    amt = bal.find("Amt")
    indicator = bal.value("CdtDbtInd")
    text = amt.text.strip() if amt is not None else ""
    if amt is None or amt.attrs.get("Ccy") != currency:
        return None
    if not _AMOUNT.fullmatch(text) or indicator not in ("CRDT", "DBIT"):
        problems.add(
            f"balance {text!r} {indicator!r} is not a plain amount with CRDT or DBIT",
            label,
        )
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        problems.add(f"balance {text!r} is not a number", label)
        return None
    return value if indicator == "CRDT" else -value


def _check_balances(
    stmt: Statement, credits: Decimal, debits: Decimal, problems: Problems
) -> None:
    """Opening plus booked credits minus booked debits must equal the closing
    balance, when the statement states both in its currency."""
    found: dict[str, _El] = {}
    for bal in stmt.node.findall("Bal"):
        code = bal.value("Tp", "CdOrPrtry", "Cd")
        if code in ("OPBD", "PRCD", "CLBD"):
            found.setdefault(code, bal)
    opening = found.get("OPBD") or found.get("PRCD")
    closing = found.get("CLBD")
    if opening is None or closing is None:
        return
    close_amt = closing.find("Amt")
    currency = stmt.currency or (close_amt.attrs.get("Ccy") if close_amt else None)
    if not currency:
        return
    before = _signed(opening, currency, problems, stmt.label)
    after = _signed(closing, currency, problems, stmt.label)
    if before is None or after is None:
        return
    expected = before + credits - debits
    if expected != after:
        problems.add(
            f"opening {before} + booked credits {credits} - booked debits {debits} "
            f"= {expected}, but the closing balance is {after} ({currency}); the "
            "statement is incomplete or its entries are wrong",
            stmt.label,
        )


def _check_count(stmt: Statement, problems: Problems) -> None:
    stated = stmt.node.value("TxsSummry", "TtlNtries", "NbOfNtries")
    if stated is None:
        return
    present = len(stmt.node.findall("Ntry"))
    if not (stated.isascii() and stated.isdigit()):
        problems.add(f"NbOfNtries {stated!r} is not a count", stmt.label)
    elif int(stated) != present:
        problems.add(
            f"states {int(stated)} entries (TxsSummry/TtlNtries/NbOfNtries) but "
            f"contains {present}; the statement is incomplete",
            stmt.label,
        )


def _read(
    files: Sequence[Path],
    reference_fields: Sequence[str],
    fee_codes: Sequence[Sequence[str]],
    declared_from: date | None,
    declared: date | None,
    *,
    complete: bool,
) -> CamtData:
    bad = [f for f in reference_fields if f not in REFERENCE_KINDS]
    if bad or not reference_fields:
        raise ValueError(
            f"bank_reference_fields must be a non-empty list of {list(REFERENCE_KINDS)}"
        )
    if not files:
        raise ValueError("no CAMT.053 file given")
    codes = frozenset(tuple(c) for c in fee_codes)
    problems = Problems("CAMT.053 files", "entry")
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
                    f"has the same Stmt/Id as {first.label} but different content",
                    s.label,
                )
    start = through = None
    coverage: dict[str, Any] = {}
    if complete:
        start, through, coverage = _coverage(unique, declared_from, declared, problems)
        for stmt in unique:
            _check_count(stmt, problems)
    out = CamtData(bank_data_from=start, bank_data_through=through)
    excluded: Counter[str] = Counter()
    used_ref: Counter[str] = Counter()
    id_used: Counter[str] = Counter()
    ids: dict[str, str] = {}
    for stmt in unique:
        credits = debits = Decimal(0)
        readable = True
        for position, entry in enumerate(stmt.node.findall("Ntry"), start=1):
            where = f"{stmt.label} Ntry #{position} (line {entry.line})"
            sts = _status(entry)
            if sts not in ENTRY_STATUSES:
                readable = False
                problems.add(
                    f"Sts is {sts!r}, not one of {list(ENTRY_STATUSES)}; an entry "
                    "of unknown status cannot be counted in or out",
                    where,
                )
                continue
            if sts != "BOOK":
                excluded[sts] += 1
                continue
            try:
                row = _entry_row(
                    stmt, entry, position, reference_fields, codes, used_ref, id_used
                )
            except _Bad as e:
                readable = False
                problems.add(str(e), where)
                continue
            amount = Decimal(row["amount"] or "0")
            if row["direction"] == "credit":
                credits += amount
            else:
                debits += amount
            ident = row["bank_transaction_id"] or ""
            if ident in ids:
                problems.add(
                    f"bank_transaction_id {ident!r} is also used by {ids[ident]}; "
                    "two entries must not share an ID",
                    where,
                )
                continue
            ids[ident] = where
            out.rows.append(row)
            out.where.append(where)
        if complete and readable:
            _check_balances(stmt, credits, debits, problems)
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


def read_camt053(
    files: Sequence[Path],
    *,
    reference_fields: Sequence[str] = ("EndToEndId",),
    fee_codes: Sequence[Sequence[str]] = DEFAULT_FEE_CODES,
    bank_data_from: date | None = None,
    bank_data_through: date | None = None,
) -> CamtData:
    """Bank rows for `bank_transactions` and the days the statements cover. A
    statement that is not shown complete (entry count, balances) is refused."""
    return _read(
        files,
        reference_fields,
        fee_codes,
        bank_data_from,
        bank_data_through,
        complete=True,
    )


def read_entries(
    files: Sequence[Path],
    *,
    reference_fields: Sequence[str] = ("EndToEndId",),
    fee_codes: Sequence[Sequence[str]] = DEFAULT_FEE_CODES,
) -> CamtData:
    """The entries only, as `read_camt053` maps them, without the checks that a
    statement is complete and without coverage. For excerpts and samples: rows
    from it must not be used as evidence that a bank row is absent."""
    return _read(files, reference_fields, fee_codes, None, None, complete=False)
