"""Evidence ledger shared by the investigators: one packet's evidence,
calculations and day counts, with amounts read back from the cited records."""

from functools import reduce

from reconcile.diagnosis.policy import SCOPE_LIMITATION
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


class FieldMissing(ValueError):
    """A required value is absent from the data. Not a malformed record: the
    export simply does not carry it, so the pipeline abstains."""

    def __init__(self, column: str) -> None:
        super().__init__(f"{column} is null")
        self.column = column


def req(row: Row, key: str) -> str:
    v = row[key]
    if v is None:
        raise FieldMissing(key)
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
        old = self.items.get(evidence_id)
        if old is not None:
            # Re-citing is allowed when it is the same record with the same values
            # and timestamps; the first relationship text is kept (a label, not a
            # fact). Anything else is an id collision, which must not silently
            # reuse the first item's figures.
            same = (
                old.record == RecordRef(self.request.snapshot, table, record_id)
                and dict(old.values) == values
                and dict(old.timestamps) == (timestamps or {})
            )
            if not same:
                raise ValueError(f"evidence id {evidence_id!r} re-recorded differently")
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

    def journal(
        self,
        journal: str,
        lines: list[Row],
        relationship: str,
        source: str,
        gl_type: str,
    ) -> str | None:
        """Cite a journal that is balanced as a whole (total debit = total credit
        > 0, one currency) and whose lines of `gl_type` sourced to `source` are
        themselves balanced. The evidence id is `journal:<journal_id>:<source>:
        <gl_type>`: the amount depends on the attribution, so each attribution is
        its own item. Its `amount` is the total debit of those attributable
        lines, the figure to compare with the source record; `journal_total` is
        the whole journal's total debit. None if either check fails."""
        currencies = {req(x, "currency") for x in lines}
        if len(currencies) != 1:
            return None
        cur = currencies.pop()
        total = self._sides(lines, cur)
        own = self._sides(
            [
                x
                for x in lines
                if x["source_transaction_id"] == source
                and x["transaction_type"] == gl_type
            ],
            cur,
        )
        if total[0] != total[1] or total[0].amount <= 0:
            return None
        if own[0] != own[1] or own[0].amount <= 0:
            return None
        return self.record(
            f"journal:{journal}:{source}:{gl_type}",
            "gl_entries",
            journal,
            relationship,
            {
                "amount": str(own[0]),
                "journal_total": str(total[0]),
                "currency": cur,
                "source": source,
                "gl_type": gl_type,
            },
            {"posting_date": max(req(x, "posting_date") for x in lines)},
        )

    @staticmethod
    def _sides(lines: list[Row], cur: str) -> tuple[Money, Money]:
        zero = Money.parse("0", cur)
        debit = reduce(
            lambda a, b: a + b, (Money.parse(req(x, "debit"), cur) for x in lines), zero
        )
        credit = reduce(
            lambda a, b: a + b,
            (Money.parse(req(x, "credit"), cur) for x in lines),
            zero,
        )
        return debit, credit

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
        category = diagnosis.category or ""
        all_clear = category.startswith("legitimate_") or category == "no_exception"
        if (
            diagnosis.disposition is Disposition.ANSWERED
            and all_clear
            and SCOPE_LIMITATION not in limitations
        ):
            limitations = (*limitations, SCOPE_LIMITATION)
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
