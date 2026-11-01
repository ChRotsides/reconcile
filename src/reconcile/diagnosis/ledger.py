"""Evidence ledger shared by the investigators: one packet's evidence,
calculations and day counts, with amounts read back from the cited records."""

from functools import reduce

from reconcile.domain.contracts import (
    AbsenceSearch,
    Calculation,
    DayCount,
    Diagnosis,
    Disposition,
    EvidenceItem,
    EvidencePacket,
    Finding,
    InvestigationRequest,
    Operand,
    Recommendation,
    RecordRef,
)
from reconcile.domain.money import Money
from reconcile.retrieval.snapshot import Row


def req(row: Row, key: str) -> str:
    v = row[key]
    if v is None:
        raise ValueError(f"{key} is null")
    return v


def val(row: Row, key: str) -> str:
    return row[key] or ""


def is_effective(payment: Row) -> bool:
    """Contract section 3: completed and settled."""
    return (
        payment["payment_status"] == "completed"
        and payment["settlement_status"] == "settled"
    )


class Ledger:
    """Collects evidence and calculations for one packet."""

    def __init__(
        self, request: InvestigationRequest, provenance: tuple[tuple[str, str], ...]
    ) -> None:
        self.request = request
        self.provenance = provenance
        self.items: dict[str, EvidenceItem] = {}
        self.calcs: dict[str, Calculation] = {}
        self.counts: dict[str, DayCount] = {}

    def record(
        self,
        evidence_id: str,
        table: str,
        record_id: str,
        relationship: str,
        values: dict[str, str],
        timestamps: dict[str, str] | None = None,
    ) -> str:
        if evidence_id in self.items:  # first citation wins (keeps its relationship)
            return evidence_id
        self.items[evidence_id] = EvidenceItem(
            evidence_id,
            RecordRef(self.request.snapshot, table, record_id),
            None,
            relationship,
            tuple(values.items()),
            tuple((timestamps or {}).items()),
        )
        return evidence_id

    def payment(self, pay: Row, pid: str, relationship: str) -> str:
        cur = req(pay, "payment_currency")
        return self.record(
            f"pay:{pid}",
            "payments",
            pid,
            relationship,
            {
                "payment_amount": str(Money.parse(req(pay, "payment_amount"), cur)),
                "payment_currency": cur,
                "payment_method": val(pay, "payment_method"),
                "payment_status": val(pay, "payment_status"),
                "settlement_status": val(pay, "settlement_status"),
            },
            {"payment_date": req(pay, "payment_date")},
        )

    def bank_row(self, row: Row, relationship: str) -> str:
        bid, cur = req(row, "bank_transaction_id"), req(row, "currency")
        return self.record(
            f"bank:{bid}",
            "bank_transactions",
            bid,
            relationship,
            {
                "amount": str(Money.parse(req(row, "amount"), cur)),
                "currency": cur,
                "direction": val(row, "direction"),
                "status": val(row, "status"),
                "transaction_type": val(row, "transaction_type"),
            },
            {"posted_date": req(row, "posted_date")},
        )

    def journal(self, journal: str, lines: list[Row], relationship: str) -> str | None:
        """Cite a balanced journal (total debit = total credit > 0); None if it
        is not balanced or mixes currencies. Its amount is the total debit."""
        currencies = {req(x, "currency") for x in lines}
        if len(currencies) != 1:
            return None
        cur = currencies.pop()
        debit = reduce(
            lambda a, b: a + b, (Money.parse(req(x, "debit"), cur) for x in lines)
        )
        credit = reduce(
            lambda a, b: a + b, (Money.parse(req(x, "credit"), cur) for x in lines)
        )
        if debit != credit or debit.amount <= 0:
            return None
        return self.record(
            f"journal:{journal}",
            "gl_entries",
            journal,
            relationship,
            {"amount": str(debit), "currency": cur},
            {"posting_date": max(req(x, "posting_date") for x in lines)},
        )

    def search(self, table: str, predicate: str, count: int, relationship: str) -> str:
        evidence_id = f"search_{len(self.items)}"
        self.items[evidence_id] = EvidenceItem(
            evidence_id,
            None,
            AbsenceSearch(
                self.request.snapshot, table, predicate, self.request.cutoff, count
            ),
            relationship,
        )
        return evidence_id

    def money(self, evidence_id: str, field_name: str) -> Money:
        return Money.from_str(dict(self.items[evidence_id].values)[field_name])

    def calc(
        self,
        calc_id: str,
        operation: str,
        cited: list[tuple[str, str]],
        tolerance: Money | None = None,
    ) -> Calculation:
        operands = tuple(Operand(e, f, self.money(e, f)) for e, f in cited)
        amounts = [o.amount for o in operands]
        if operation == "sum":
            result = reduce(lambda a, b: a + b, amounts)
        elif operation == "abs_difference":
            result = abs(amounts[0] - amounts[1])
        else:
            result = reduce(lambda a, b: a - b, amounts)
        self.calcs[calc_id] = Calculation(
            calc_id, operation, operands, result, tolerance
        )
        return self.calcs[calc_id]

    def day_count(
        self,
        calc_id: str,
        start: tuple[str, str],
        end: tuple[str, str] | None,
        result: int,
        limit: int,
        limit_policy: str,
    ) -> DayCount:
        self.counts[calc_id] = DayCount(
            calc_id,
            start[0],
            start[1],
            end[0] if end else None,
            end[1] if end else None,
            result,
            limit,
            limit_policy,
        )
        return self.counts[calc_id]

    def packet(
        self,
        diagnosis: Diagnosis,
        recommendations: tuple[Recommendation, ...] = (),
        limitations: tuple[str, ...] = (),
    ) -> EvidencePacket:
        return EvidencePacket(
            self.request,
            diagnosis,
            tuple(self.items.values()),
            tuple(self.calcs.values()),
            recommendations,
            limitations,
            self.provenance,
            tuple(self.counts.values()),
        )

    def abstain(
        self, reason: str, missing: str, findings: tuple[Finding, ...] = ()
    ) -> EvidencePacket:
        return self.packet(
            Diagnosis(
                Disposition.ABSTAINED,
                None,
                findings=findings,
                reason=reason,
                missing_information=(missing,),
            )
        )

    def out_of_scope(self, reason: str, why: str) -> EvidencePacket:
        return self.packet(
            Diagnosis(Disposition.OUT_OF_SCOPE, None, reason=reason),
            limitations=(f"{why}. This is not a finding that the case is correct.",),
        )
