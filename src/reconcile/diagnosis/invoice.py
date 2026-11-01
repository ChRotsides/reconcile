"""Invoice-primary rules: F06 (docs/evidence-contract.md section 4.1) and the
legitimate split-payment and due-date variants (section 5).

Every amount in a calculation is read back from the evidence item it cites, so
a reported number cannot differ from the cited record. Only rules the contract
defines are implemented; every other shape is `out_of_scope` or an abstention,
never `legitimate`.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from functools import reduce

from reconcile.domain.contracts import (
    AbsenceSearch,
    Calculation,
    Diagnosis,
    Disposition,
    EvidenceItem,
    EvidencePacket,
    Finding,
    FindingState,
    InvestigationRequest,
    Operand,
    Recommendation,
    RecordRef,
)
from reconcile.domain.money import Money
from reconcile.retrieval.invoice import InvoiceFacts
from reconcile.retrieval.snapshot import Row

F06 = "F06"
SPLIT = "legitimate_split_payment"
DUE_DATE = "legitimate_due_date_payment"


def _req(row: Row, key: str) -> str:
    v = row[key]
    if v is None:
        raise ValueError(f"{key} is null")
    return v


def _val(row: Row, key: str) -> str:
    return row[key] or ""


@dataclass
class _Leg:
    pay_ev: str
    alloc_ev: str
    effective: bool
    debit_ev: str | None = None
    evidence: list[str] = field(default_factory=list)  # bank rows or search
    done: bool = False  # bank side fully examined


class _Ledger:
    """Collects evidence and calculations for one packet."""

    def __init__(
        self, request: InvestigationRequest, provenance: tuple[tuple[str, str], ...]
    ) -> None:
        self.request = request
        self.provenance = provenance
        self.items: dict[str, EvidenceItem] = {}
        self.calcs: dict[str, Calculation] = {}
        self.legs: list[_Leg] = []

    def record(
        self,
        evidence_id: str,
        table: str,
        record_id: str,
        relationship: str,
        values: dict[str, str],
        timestamps: dict[str, str] | None = None,
    ) -> str:
        self.items[evidence_id] = EvidenceItem(
            evidence_id,
            RecordRef(self.request.snapshot, table, record_id),
            None,
            relationship,
            tuple(values.items()),
            tuple((timestamps or {}).items()),
        )
        return evidence_id

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
        else:
            result = reduce(lambda a, b: a - b, amounts)
        self.calcs[calc_id] = Calculation(
            calc_id, operation, operands, result, tolerance
        )
        return self.calcs[calc_id]

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
        )

    def missing_debit_finding(self) -> Finding | None:
        """Contract 4.1 / principle 5: an effective payment with no posted debit
        stays visible, with its bounded search, in every packet that touched it."""
        unproven = [
            leg
            for leg in self.legs
            if leg.effective and leg.done and leg.debit_ev is None
        ]
        if not unproven:
            return None
        return Finding(
            "allocating_payment_without_posted_debit",
            FindingState.SUPPORTED,
            tuple(e for leg in unproven for e in (leg.pay_ev, *leg.evidence)),
        )

    def abstain(self, reason: str, missing: str) -> EvidencePacket:
        finding = self.missing_debit_finding()
        diagnosis = Diagnosis(
            Disposition.ABSTAINED,
            None,
            findings=(finding,) if finding else (),
            reason=reason,
            missing_information=(missing,),
        )
        return self.packet(diagnosis)


def _quantum(currency: str) -> Money:
    return Money(Decimal((0, (1,), -Money.parse("0", currency).scale)), currency)


def diagnose_invoice(
    request: InvestigationRequest,
    facts: InvoiceFacts,
    provenance: tuple[tuple[str, str], ...],
) -> EvidencePacket:
    led = _Ledger(request, provenance)
    inv_tok = request.primary_entity.record_id
    cur = _req(facts.invoice, "currency")
    q = _quantum(cur)
    # No invoice_date or received_date anywhere (leakage-test residual R4).
    # due_date is a contract term of the due-date variant, not an age.
    inv_ev = led.record(
        "inv",
        "invoices",
        inv_tok,
        "primary",
        {
            "invoice_total": str(
                Money.parse(_req(facts.invoice, "invoice_total"), cur)
            ),
            "currency": cur,
            "due_date": _val(facts.invoice, "due_date"),
            "status": _val(facts.invoice, "status"),
        },
    )

    legs: list[_Leg] = []
    for a in facts.allocations:
        pid = _req(a, "payment_id")
        pay = facts.payments.get(pid)
        if pay is None:
            return led.abstain(
                "required_records_missing",
                "an allocating payment is not in the snapshot at the cutoff",
            )
        pcur = _req(pay, "payment_currency")
        effective = (
            pay["payment_status"] == "completed"
            and pay["settlement_status"] == "settled"
        )
        pay_ev = led.record(
            f"pay:{pid}",
            "payments",
            pid,
            "allocating_payment",
            {
                "payment_amount": str(Money.parse(_req(pay, "payment_amount"), pcur)),
                "payment_currency": pcur,
                "payment_method": _val(pay, "payment_method"),
                "payment_status": _val(pay, "payment_status"),
                "settlement_status": _val(pay, "settlement_status"),
            },
            {"payment_date": _req(pay, "payment_date")},
        )
        if pcur != cur:
            return led.abstain(
                "unsupported_fx", "a documented conversion path between currencies"
            )
        alloc_ev = led.record(
            f"alloc:{pid}.{inv_tok}",
            "payment_allocations",
            f"{pid}|{inv_tok}",
            "allocation_of_invoice",
            {"allocated_amount": str(Money.parse(_req(a, "allocated_amount"), pcur))},
            {"allocation_date": _req(a, "allocation_date")},
        )
        leg = _Leg(pay_ev, alloc_ev, effective)
        legs.append(leg)
        led.legs.append(leg)
        if not effective:
            continue

        ref = _req(pay, "reference_number")
        if facts.reference_counts[ref] > 1:
            led.search(
                "payments",
                f"reference_number = '{ref}'",
                facts.reference_counts[ref],
                "reference_uniqueness",
            )
            return led.abstain(
                "ambiguous_reference", "a payment reference unique to one payment"
            )
        rows = facts.bank_rows[ref]
        debits: list[str] = []
        for r in rows:
            bid = _req(r, "bank_transaction_id")
            bcur = _req(r, "currency")
            b_ev = led.record(
                f"bank:{bid}",
                "bank_transactions",
                bid,
                "payment_reference",
                {
                    "amount": str(Money.parse(_req(r, "amount"), bcur)),
                    "currency": bcur,
                    "direction": _val(r, "direction"),
                    "status": _val(r, "status"),
                    "transaction_type": _val(r, "transaction_type"),
                },
                {"posted_date": _req(r, "posted_date")},
            )
            leg.evidence.append(b_ev)
            if bcur != cur:
                return led.abstain(
                    "unsupported_fx", "a documented conversion path between currencies"
                )
            if r["direction"] == "debit" and r["status"] == "posted":
                debits.append(b_ev)
        if len(debits) > 1:
            return led.abstain(
                "ambiguous_reference", "which one bank debit settles the payment"
            )
        if not debits:
            # Bounded search for the missing counterpart, also when only
            # returned or credit rows exist (contract 4.1).
            leg.evidence.append(
                led.search(
                    "bank_transactions",
                    f"payment_reference = '{ref}' AND transaction_type <> 'bank fee'"
                    f" AND direction = 'debit' AND status = 'posted'"
                    f" AND posted_date <= '{request.cutoff.isoformat()}'",
                    0,
                    "reference_search",
                )
            )
        leg.debit_ev = debits[0] if debits else None
        leg.done = True

    live = [leg for leg in legs if leg.effective]
    if not live:
        return _out_of_scope(led, "no effective payment allocated to this invoice")

    total = ("inv", "invoice_total")
    allocated = led.calc(
        "allocated", "sum", [(leg.alloc_ev, "allocated_amount") for leg in live]
    )
    total_minus_allocated = led.calc(
        "invoice_total_minus_allocated",
        "difference",
        [total, *[(leg.alloc_ev, "allocated_amount") for leg in live]],
        q,
    )

    # --- F06, section 4.1 ------------------------------------------------
    if -total_minus_allocated.result.amount > q.amount:
        committed_legs = [leg for leg in live if leg.debit_ev is not None]
        if committed_legs:
            led.calc(
                "committed",
                "sum",
                [(leg.alloc_ev, "allocated_amount") for leg in committed_legs],
            )
            overpaid = led.calc(
                "invoice_total_minus_committed",
                "difference",
                [
                    total,
                    *[(leg.alloc_ev, "allocated_amount") for leg in committed_legs],
                ],
                q,
            )
            if -overpaid.result.amount > q.amount:
                return _f06(led, live, committed_legs)
        return led.abstain(
            "commitment_not_proven_at_cutoff",
            "a posted bank debit for each over-allocating payment at the cutoff",
        )

    # --- legitimate variants, section 5 ----------------------------------
    # Full allocation (docs/evidence-contract.md section 5): an under-allocated
    # invoice is a partial payment, which no variant covers.
    fully_allocated = abs(total_minus_allocated.result).amount <= q.amount
    is_split = fully_allocated and len(live) >= 2
    due = _val(facts.invoice, "due_date")
    on_due = [
        leg
        for leg in live
        if fully_allocated
        and dict(led.items[leg.pay_ev].timestamps)["payment_date"] == due
    ]
    if not is_split and not on_due:
        return _out_of_scope(led, "no in-scope invoice pattern applies")

    if any(leg.debit_ev is None for leg in live):
        return led.abstain(
            "required_records_missing", "a posted bank debit for each payment"
        )
    debit_calcs: list[str] = []
    for leg in live:
        assert leg.debit_ev is not None
        pid = leg.pay_ev.removeprefix("pay:")
        diff = led.calc(
            f"debit_minus_payment:{pid}",
            "difference",
            [(leg.debit_ev, "amount"), (leg.pay_ev, "payment_amount")],
            q,
        )
        if abs(diff.result).amount > q.amount:
            return led.abstain(
                "debit_differs_from_payment", "an explanation of the debit difference"
            )
        debit_calcs.append(diff.calculation_id)
    debit_ev = [e for leg in live for e in (leg.pay_ev, leg.debit_ev) if e]
    debits_match = Finding(
        "debits_match_payments",
        FindingState.SUPPORTED,
        tuple(debit_ev),
        tuple(debit_calcs),
    )
    sums = Finding(
        "allocations_equal_invoice_total",
        FindingState.SUPPORTED,
        (inv_ev, *[leg.alloc_ev for leg in live]),
        (allocated.calculation_id, total_minus_allocated.calculation_id),
    )
    findings = [sums, debits_match]
    if on_due:
        findings.append(
            Finding(
                "payment_on_due_date",
                FindingState.SUPPORTED,
                (inv_ev, *[leg.pay_ev for leg in on_due]),
            )
        )
    category = SPLIT if is_split else DUE_DATE
    others = (DUE_DATE,) if is_split and on_due else ()
    diagnosis = Diagnosis(
        Disposition.ANSWERED, category, tuple(findings), alternatives=others
    )
    return led.packet(
        diagnosis,
        limitations=(
            "Legitimacy rests on arithmetic only; scheduling intent is not recorded.",
        ),
    )


def _f06(led: _Ledger, live: list[_Leg], committed: list[_Leg]) -> EvidencePacket:
    cited = tuple(led.items)
    findings = [
        Finding(
            "committed_overpayment",
            FindingState.SUPPORTED,
            tuple(e for e in cited if not e.startswith("search_")),
            ("allocated", "committed", "invoice_total_minus_committed"),
        )
    ]
    unproven = led.missing_debit_finding()
    if unproven:  # contract 4.1: its own finding, not counted as committed
        findings.append(unproven)
    diagnosis = Diagnosis(
        Disposition.ANSWERED,
        F06,
        tuple(findings),
        missing_information=(),
    )
    rec = Recommendation(
        "review_possible_duplicate_payment",
        ("committed_overpayment",),
        prerequisites=("Confirm both debits on the bank statement with the vendor.",),
        unresolved_assumptions=(
            "Snapshot is complete through the cutoff (contract P7).",
        ),
    )
    return led.packet(
        diagnosis,
        (rec,),
        (
            "Instalments, prepayments and intentional overpayment cannot be told "
            "apart from a duplicate except by allocation arithmetic (contract 4.1).",
        ),
    )


def _out_of_scope(led: _Ledger, why: str) -> EvidencePacket:
    diagnosis = Diagnosis(Disposition.OUT_OF_SCOPE, None, reason="no_in_scope_pattern")
    return led.packet(
        diagnosis,
        limitations=(f"{why}. This is not a finding that the invoice is correct.",),
    )
