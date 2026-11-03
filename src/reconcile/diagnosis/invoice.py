"""Invoice-primary rules: F06 (docs/evidence-contract.md section 4.1) and the
legitimate split-payment and due-date variants (section 5).

Every amount in a calculation is read back from the evidence item it cites, so
a reported number cannot differ from the cited record. Only rules the contract
defines are implemented; every other shape is `out_of_scope` or an abstention,
never `legitimate`.
"""

from dataclasses import dataclass, field

from reconcile.diagnosis.guard import guarded
from reconcile.diagnosis.ledger import Ledger, is_effective, req, val
from reconcile.diagnosis.policy import quantum
from reconcile.domain.contracts import (
    Calculation,
    Diagnosis,
    Disposition,
    EvidencePacket,
    Finding,
    FindingState,
    InvestigationRequest,
    Recommendation,
)
from reconcile.domain.money import Money
from reconcile.retrieval.invoice import InvoiceFacts
from reconcile.retrieval.snapshot import Reader

F06 = "F06"
SPLIT = "legitimate_split_payment"
DUE_DATE = "legitimate_due_date_payment"


@dataclass
class _Leg:
    pay_ev: str
    alloc_ev: str
    effective: bool
    debit_ev: str | None = None
    evidence: list[str] = field(default_factory=list)  # bank rows or search
    done: bool = False  # bank side fully examined


def _missing_debit_finding(legs: list[_Leg]) -> Finding | None:
    """Contract 4.1 / principle 5: an effective payment with no posted debit
    stays visible, with its bounded search, in every packet that touched it."""
    unproven = [
        leg for leg in legs if leg.effective and leg.done and leg.debit_ev is None
    ]
    if not unproven:
        return None
    return Finding(
        "allocating_payment_without_posted_debit",
        FindingState.SUPPORTED,
        tuple(e for leg in unproven for e in (leg.pay_ev, *leg.evidence)),
    )


def _abstain(
    led: Ledger, legs: list[_Leg], reason: str, missing: str
) -> EvidencePacket:
    finding = _missing_debit_finding(legs)
    return led.abstain(reason, missing, (finding,) if finding else ())


def diagnose_invoice(
    request: InvestigationRequest,
    facts: InvoiceFacts,
    provenance: tuple[tuple[str, str], ...],
    reader: Reader,
) -> EvidencePacket:
    led = Ledger(request, provenance)
    inv_tok = request.primary_entity.record_id
    cur = req(facts.invoice, "currency")
    q = quantum(cur)
    # No invoice_date or received_date anywhere (leakage-test residual R4).
    # due_date is a contract term of the due-date variant, not an age.
    inv_ev = led.record(
        "inv",
        "invoices",
        inv_tok,
        "primary",
        {
            "invoice_total": str(Money.parse(req(facts.invoice, "invoice_total"), cur)),
            "currency": cur,
            "due_date": val(facts.invoice, "due_date"),
            "status": val(facts.invoice, "status"),
        },
    )

    legs: list[_Leg] = []
    for a in facts.allocations:
        pid = req(a, "payment_id")
        pay = facts.payments.get(pid)
        if pay is None:
            return _abstain(
                led,
                legs,
                "required_records_missing",
                "an allocating payment is not in the snapshot at the cutoff",
            )
        pcur = req(pay, "payment_currency")
        effective = is_effective(pay)
        pay_ev = led.payment(pay, pid, "allocating_payment")
        if pcur != cur:
            return _abstain(
                led,
                legs,
                "unsupported_fx",
                "a documented conversion path between currencies",
            )
        alloc_ev = led.record(
            f"alloc:{pid}.{inv_tok}",
            "payment_allocations",
            f"{pid}|{inv_tok}",
            "allocation_of_invoice",
            {"allocated_amount": str(Money.parse(req(a, "allocated_amount"), pcur))},
            {"allocation_date": req(a, "allocation_date")},
        )
        leg = _Leg(pay_ev, alloc_ev, effective)
        legs.append(leg)
        if not effective:
            continue

        ref = req(pay, "reference_number")
        if facts.reference_counts[ref] > 1:
            led.search(
                "payments",
                f"reference_number = '{ref}'",
                facts.reference_counts[ref],
                "reference_uniqueness",
            )
            return _abstain(
                led,
                legs,
                "ambiguous_reference",
                "a payment reference unique to one payment",
            )
        rows = facts.bank_rows[ref]
        debits: list[str] = []
        for r in rows:
            bcur = req(r, "currency")
            b_ev = led.bank_row(r, "payment_reference")
            leg.evidence.append(b_ev)
            if bcur != cur:
                return _abstain(
                    led,
                    legs,
                    "unsupported_fx",
                    "a documented conversion path between currencies",
                )
            if r["direction"] == "debit" and r["status"] == "posted":
                debits.append(b_ev)
        if len(debits) > 1:
            return _abstain(
                led,
                legs,
                "ambiguous_reference",
                "which one bank debit settles the payment",
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
        # An allocation is committed only where the bank debit supports the
        # payment amount (contract 4.1, note of 2026-11): a debit far below the
        # payment would otherwise count the whole allocation as paid out.
        for leg in committed_legs:
            assert leg.debit_ev is not None
            # Checked without a calculation on the answered path (the debit and
            # payment are cited records); the abstention records the difference.
            gap = led.money(leg.debit_ev, "amount") - led.money(
                leg.pay_ev, "payment_amount"
            )
            if abs(gap).amount > q.amount:
                _debit_minus_payment(led, leg, q)
                return _abstain(
                    led,
                    legs,
                    "debit_differs_from_payment",
                    "an explanation of the debit difference",
                )
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
        return _abstain(
            led,
            legs,
            "commitment_not_proven_at_cutoff",
            "a posted bank debit for each over-allocating payment at the cutoff",
        )

    # --- legitimate variants, section 5 ----------------------------------
    # Full allocation (docs/evidence-contract.md section 5): an under-allocated
    # invoice is a partial payment, which no variant covers.
    fully_allocated = abs(total_minus_allocated.result).amount <= q.amount
    is_split = fully_allocated and len(live) >= 2
    due = val(facts.invoice, "due_date")
    on_due = [
        leg
        for leg in live
        if fully_allocated
        and dict(led.items[leg.pay_ev].timestamps)["payment_date"] == due
    ]
    if not is_split and not on_due:
        return _out_of_scope(led, "no in-scope invoice pattern applies")

    if any(leg.debit_ev is None for leg in live):
        return _abstain(
            led,
            legs,
            "required_records_missing",
            "a posted bank debit for each payment",
        )
    debit_calcs: list[str] = []
    for leg in live:
        assert leg.debit_ev is not None
        diff = _debit_minus_payment(led, leg, q)
        if abs(diff.result).amount > q.amount:
            return _abstain(
                led,
                legs,
                "debit_differs_from_payment",
                "an explanation of the debit difference",
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
    packet = led.packet(
        diagnosis,
        limitations=(
            "Legitimacy rests on arithmetic only; scheduling intent is not recorded.",
        ),
    )
    pids = [leg.pay_ev.removeprefix("pay:") for leg in live]
    return guarded(led, reader, pids, packet)


def _debit_minus_payment(led: Ledger, leg: _Leg, q: Money) -> Calculation:
    assert leg.debit_ev is not None
    pid = leg.pay_ev.removeprefix("pay:")
    return led.calc(
        f"debit_minus_payment:{pid}",
        "difference",
        [(leg.debit_ev, "amount"), (leg.pay_ev, "payment_amount")],
        q,
    )


def _f06(led: Ledger, live: list[_Leg], committed: list[_Leg]) -> EvidencePacket:
    cited = tuple(led.items)
    findings = [
        Finding(
            "committed_overpayment",
            FindingState.SUPPORTED,
            tuple(e for e in cited if not e.startswith("search_")),
            ("allocated", "committed", "invoice_total_minus_committed"),
        )
    ]
    unproven = _missing_debit_finding(live)
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


def _out_of_scope(led: Ledger, why: str) -> EvidencePacket:
    diagnosis = Diagnosis(Disposition.OUT_OF_SCOPE, None, reason="no_in_scope_pattern")
    return led.packet(
        diagnosis,
        limitations=(f"{why}. This is not a finding that the invoice is correct.",),
    )
