"""Payment-primary rules when the payment has bank rows: F14 (docs/evidence-
contract.md section 4.3), net-fee, clearing, batch and reversal variants
(section 5), the FX abstention, and the answered "no exception" outcome.

Evidence is cited only where a conclusion rests on it. Shapes the contract does
not define abstain with their own reason; none is called legitimate.
"""

from dataclasses import dataclass
from datetime import date

from reconcile.diagnosis.ledger import Ledger, is_effective, req
from reconcile.diagnosis.policy import CLEARING_LIMITS, clearing_policy, quantum
from reconcile.domain.calendar import business_days, spans_weekend
from reconcile.domain.contracts import (
    Diagnosis,
    Disposition,
    EvidencePacket,
    Finding,
    FindingState,
    InvestigationRequest,
    Recommendation,
)
from reconcile.domain.money import Money
from reconcile.retrieval import payment as rt
from reconcile.retrieval.invoice import gather_invoice
from reconcile.retrieval.snapshot import Reader, Row

F14 = "F14"
NET_FEE = "legitimate_net_fee_accounting"
WEEKEND = "legitimate_weekend_clearing"
CHECK = "legitimate_check_clearing"
BATCH = "legitimate_batch_payment"
REVERSAL = "legitimate_payment_reversal"
NO_EXCEPTION = "no_exception"
NO_ALLOCATION = "payment_without_invoice_allocation"


def _supported(
    check: str, evidence: tuple[str, ...], calcs: tuple[str, ...] = ()
) -> Finding:
    unique = tuple(dict.fromkeys(evidence))  # several sources may cite one record
    return Finding(check, FindingState.SUPPORTED, unique, tuple(dict.fromkeys(calcs)))


def diagnose_settled(
    request: InvestigationRequest,
    reader: Reader,
    led: Ledger,
    pay: Row,
    rows: list[Row],
) -> EvidencePacket:
    pid = req(pay, "payment_id")
    cur = req(pay, "payment_currency")
    pay_ev = f"pay:{pid}"
    row_evs = {
        req(r, "bank_transaction_id"): led.bank_row(r, "payment_reference")
        for r in rows
    }
    if any(req(r, "currency") != cur for r in rows):
        return led.abstain(
            "unsupported_fx", "a documented conversion path between currencies"
        )
    if pay["payment_status"] == "reversed" or pay["settlement_status"] == "reversed":
        return _reversal(request, reader, led, pay, rows, row_evs)
    if not is_effective(pay):
        return led.out_of_scope(
            "no_in_scope_pattern", "the payment is not completed and settled"
        )
    debits = [r for r in rows if r["direction"] == "debit" and r["status"] == "posted"]
    if not debits:
        return led.abstain(
            "bank_row_not_a_posted_debit", "a posted bank debit for the payment"
        )
    if len(debits) > 1:
        return led.abstain(
            "ambiguous_reference", "which one bank debit settles the payment"
        )
    d = debits[0]
    d_id = req(d, "bank_transaction_id")
    d_ev = row_evs[d_id]
    q = quantum(cur)
    delta = led.calc(
        "delta", "difference", [(pay_ev, "payment_amount"), (d_ev, "amount")], q
    )
    if delta.result.amount < -q.amount:
        return led.abstain(
            "bank_amount_above_payment",
            "an explanation of a bank amount above the payment",
        )
    if delta.result.amount > q.amount:
        return _shortfall(request, reader, led, pay, pay_ev, d_ev, d_id, cur)
    return _matched(request, reader, led, pay, pay_ev, d, d_ev)


def _shortfall(
    request: InvestigationRequest,
    reader: Reader,
    led: Ledger,
    pay: Row,
    pay_ev: str,
    d_ev: str,
    d_id: str,
    cur: str,
) -> EvidencePacket:
    cutoff = request.cutoff
    lines = rt.journals(reader, d_id, "bank_fee", cutoff)
    if not lines:  # section 4.3: explained = 0 by a bounded absence search
        g1 = led.search(
            "gl_entries",
            f"transaction_type = 'bank_fee' AND source_transaction_id = '{d_id}'"
            f" AND posting_date <= '{cutoff.isoformat()}'",
            0,
            "fee_journal_search",
        )
        finding = _supported("unexplained_shortfall", (pay_ev, d_ev, g1), ("delta",))
        return led.packet(
            Diagnosis(Disposition.ANSWERED, F14, (finding,)),
            (
                Recommendation(
                    "review_bank_shortfall",
                    ("unexplained_shortfall",),
                    prerequisites=("Ask the bank or processor for the debit detail.",),
                ),
            ),
            ("The cause of the shortfall is not observable and is not asserted.",),
        )
    evs = [
        e
        for j, ls in lines.items()
        if (e := led.journal(j, ls, "fee_journal", d_id, "bank_fee"))
    ]
    if len(evs) != len(lines):
        return led.abstain(
            "fee_journal_invalid", "balanced single-currency fee journals"
        )
    q = quantum(cur)
    led.calc("explained", "sum", [(e, "amount") for e in evs])
    unexplained = led.calc(
        "unexplained",
        "difference",
        [(pay_ev, "payment_amount"), (d_ev, "amount"), *[(e, "amount") for e in evs]],
        q,
    )
    if abs(unexplained.result).amount > q.amount:
        # Partial or oversized fee journal: neither F14 (explained is not 0) nor legit.
        return led.abstain(
            "fee_journal_does_not_match_difference",
            "an explanation of the part of the shortfall the fee journal leaves",
        )
    allocs = require_allocation(led, reader, pay, cutoff)
    if isinstance(allocs, EvidencePacket):
        return allocs
    finding = _supported(
        "fee_explains_shortfall",
        (pay_ev, d_ev, *evs),
        ("delta", "explained", "unexplained"),
    )
    return led.packet(
        Diagnosis(
            Disposition.ANSWERED,
            NET_FEE,
            (finding, allocated_finding(led, pay, allocs)),
        )
    )


@dataclass
class _Balance:
    calc_id: str | None  # None when no effective allocation exists
    evidence: list[str]
    status: str
    others: list[str]  # effective payments other than the primary one


def _balance(
    led: Ledger, reader: Reader, inv: str, cur: str, cutoff: date, primary: str
) -> _Balance | str:
    """Cite an invoice with every effective allocation to it and compute its open
    balance (total minus effective allocations). Returns an abstention reason
    when the invoice cannot be assessed or is over-allocated."""
    facts = gather_invoice(reader, inv, cutoff)
    if req(facts.invoice, "currency") != cur:
        return "unsupported_fx"
    inv_ev = led.record(
        f"inv:{inv}",
        "invoices",
        inv,
        "allocated_invoice",
        {
            "invoice_total": str(Money.parse(req(facts.invoice, "invoice_total"), cur)),
            "currency": cur,
            "status": facts.invoice["status"] or "",
        },
    )
    cited, allocs, others = [inv_ev], [], []
    for a in facts.allocations:
        pid = req(a, "payment_id")
        other = facts.payments.get(pid)
        if other is None:
            return "required_records_missing"
        if req(other, "payment_currency") != cur:
            return "unsupported_fx"
        if not is_effective(other):
            continue
        cited.append(led.payment(other, pid, "allocating_payment"))
        allocs.append(
            led.record(
                f"alloc:{pid}.{inv}",
                "payment_allocations",
                f"{pid}|{inv}",
                "allocation_of_invoice",
                {"allocated_amount": str(Money.parse(req(a, "allocated_amount"), cur))},
                {"allocation_date": req(a, "allocation_date")},
            )
        )
        if pid != primary:
            others.append(pid)
    cited.extend(allocs)
    status = facts.invoice["status"] or ""
    if not allocs:
        return _Balance(None, cited, status, others)
    q = quantum(cur)
    calc = led.calc(
        f"invoice_open_balance:{inv}",
        "difference",
        [(inv_ev, "invoice_total"), *[(e, "allocated_amount") for e in allocs]],
        q,
    )
    if calc.result.amount < -q.amount:
        return "invoice_over_allocated"
    return _Balance(calc.calculation_id, cited, status, others)


def invoice_balances(
    led: Ledger, reader: Reader, invoices: list[str], cur: str, cutoff: date, pid: str
) -> list[_Balance] | EvidencePacket:
    out: list[_Balance] = []
    for inv in invoices:
        res = _balance(led, reader, inv, cur, cutoff, pid)
        if isinstance(res, str):
            return led.abstain(res, "a consistent, complete set of invoice allocations")
        out.append(res)
    return out


def alloc_evs_of(led: Ledger, pid: str, allocs: list[Row], cur: str) -> list[str]:
    return [
        led.record(
            f"alloc:{pid}.{req(a, 'invoice_id')}",
            "payment_allocations",
            f"{pid}|{req(a, 'invoice_id')}",
            "allocation_of_payment",
            {"allocated_amount": str(Money.parse(req(a, "allocated_amount"), cur))},
            {"allocation_date": req(a, "allocation_date")},
        )
        for a in allocs
    ]


def require_allocation(
    led: Ledger, reader: Reader, pay: Row, cutoff: date
) -> list[Row] | EvidencePacket:
    """No invoice, no all-clear (docs/evidence-contract.md section 5): a payment
    must have an allocation to an invoice in its own currency. Returns the cited
    allocation rows, or an abstention that keeps the missing allocation visible."""
    pid, cur = req(pay, "payment_id"), req(pay, "payment_currency")
    pay_ev = led.payment(pay, pid, "primary")
    allocs = rt.allocations_of_payment(reader, pid, cutoff)
    if not allocs:
        search = led.search(
            "payment_allocations",
            f"payment_id = '{pid}' AND allocation_date <= '{cutoff.isoformat()}'",
            0,
            "allocation_search",
        )
        finding = _supported(NO_ALLOCATION, (pay_ev, search))
        return led.abstain(
            NO_ALLOCATION,
            "an allocation of the payment to an invoice in the payment's currency",
            (finding,),
        )
    alloc_evs_of(led, pid, allocs, cur)
    currencies = [
        reader.rows(
            "SELECT currency FROM invoices WHERE invoice_id = ?",
            (req(a, "invoice_id"),),
        )
        for a in allocs
    ]
    if not any(r and r[0]["currency"] == cur for r in currencies):
        return led.abstain(
            "unsupported_fx", "a documented conversion path between currencies"
        )
    return allocs


def allocated_finding(led: Ledger, pay: Row, allocs: list[Row]) -> Finding:
    pid, cur = req(pay, "payment_id"), req(pay, "payment_currency")
    return _supported(
        "payment_allocated_to_invoice",
        (f"pay:{pid}", *alloc_evs_of(led, pid, allocs, cur)),
    )


def _batch(
    led: Ledger,
    pay: Row,
    pay_ev: str,
    d_ev: str,
    allocs: list[Row],
    alternatives: tuple[str, ...],
) -> EvidencePacket:
    pid, cur = req(pay, "payment_id"), req(pay, "payment_currency")
    q = quantum(cur)
    alloc_evs = alloc_evs_of(led, pid, allocs, cur)
    led.calc("allocated", "sum", [(e, "allocated_amount") for e in alloc_evs])
    gap = led.calc(
        "payment_minus_allocated",
        "difference",
        [(pay_ev, "payment_amount"), *[(e, "allocated_amount") for e in alloc_evs]],
        q,
    )
    if abs(gap.result).amount > q.amount:
        return led.abstain(
            "allocations_differ_from_payment",
            "an explanation of allocations that differ from the payment",
        )
    findings = (
        _supported(
            "allocations_equal_payment",
            (pay_ev, *alloc_evs),
            ("allocated", "payment_minus_allocated"),
        ),
        _supported(
            "bank_matches_payment", (pay_ev, d_ev), ("delta", "business_day_lag")
        ),
    )
    return led.packet(
        Diagnosis(Disposition.ANSWERED, BATCH, findings, alternatives=alternatives)
    )


def _no_exception(
    led: Ledger, reader: Reader, pay: Row, pay_ev: str, d_ev: str, allocs: list[Row]
) -> EvidencePacket:
    pid, cur = req(pay, "payment_id"), req(pay, "payment_currency")
    alloc_evs = alloc_evs_of(led, pid, allocs, cur)
    led.calc("allocated", "sum", [(e, "allocated_amount") for e in alloc_evs])
    findings = [
        _supported("debit_matches_payment", (pay_ev, d_ev), ("delta",)),
        _supported("cleared_within_limit", (pay_ev, d_ev), ("business_day_lag",)),
        _supported(
            "payment_allocated_to_invoice", (pay_ev, *alloc_evs), ("allocated",)
        ),
    ]
    twins = rt.equal_twins(reader, pay)
    if twins:
        twin_evs = [led.payment(t, req(t, "payment_id"), "equal_twin") for t in twins]
        findings.append(_supported("equal_payment_kept_distinct", (pay_ev, *twin_evs)))
    return led.packet(
        Diagnosis(Disposition.ANSWERED, NO_EXCEPTION, tuple(findings)),
        limitations=(
            "No exception was found by the checks this tool has (F06, F12, F14 and the "
            "listed legitimate variants); this is not a general clean bill.",
        ),
    )


def _reversal(
    request: InvestigationRequest,
    reader: Reader,
    led: Ledger,
    pay: Row,
    rows: list[Row],
    row_evs: dict[str, str],
) -> EvidencePacket:
    cutoff = request.cutoff
    pid, cur = req(pay, "payment_id"), req(pay, "payment_currency")
    pay_ev, q = f"pay:{pid}", quantum(cur)
    debits = [r for r in rows if r["direction"] == "debit" and r["status"] == "posted"]
    credits = [
        r
        for r in rows
        if r["direction"] == "credit"
        and r["status"] == "posted"
        and r["transaction_type"] == "payment reversal"
    ]
    if len(debits) != 1 or len(credits) != 1:
        return led.abstain(
            "required_records_missing",
            "one posted debit and one posted reversal credit for the payment",
        )
    d_ev = row_evs[req(debits[0], "bank_transaction_id")]
    c_ev = row_evs[req(credits[0], "bank_transaction_id")]
    lines = rt.journals(reader, pid, "payment_reversal", cutoff)
    evs = [
        e
        for j, ls in lines.items()
        if (e := led.journal(j, ls, "reversal_journal", pid, "payment_reversal"))
    ]
    if len(lines) != 1 or len(evs) != 1:
        return led.abstain(
            "required_records_missing", "one balanced payment_reversal journal"
        )
    j_ev = evs[0]
    calcs = [
        led.calc(
            "debit_minus_credit", "difference", [(d_ev, "amount"), (c_ev, "amount")], q
        ),
        led.calc(
            "credit_minus_journal",
            "difference",
            [(c_ev, "amount"), (j_ev, "amount")],
            q,
        ),
        led.calc(
            "journal_minus_payment",
            "difference",
            [(j_ev, "amount"), (pay_ev, "payment_amount")],
            q,
        ),
    ]
    if any(abs(c.result).amount > q.amount for c in calcs):
        return led.abstain(
            "reversal_amounts_differ",
            "an explanation of the reversal amount differences",
        )
    if req(credits[0], "posted_date") < req(debits[0], "posted_date"):
        return led.abstain(
            "reversal_credit_before_debit",
            "an explanation of a credit before the debit",
        )
    allocs = rt.allocations_of_payment(reader, pid, cutoff)
    if not allocs:
        return led.abstain("required_records_missing", "an allocation of the payment")
    alloc_evs = alloc_evs_of(led, pid, allocs, cur)
    invoices = sorted({req(a, "invoice_id") for a in allocs})
    bals = invoice_balances(led, reader, invoices, cur, cutoff, pid)
    if isinstance(bals, EvidencePacket):
        return bals
    for b in bals:
        if b.status == "paid":  # the contract needs the invoice reopened
            reason = (
                "reversal_followed_by_repayment"
                if b.others
                else "reversal_invoice_still_paid"
            )
            return led.abstain(
                reason, "practitioner input on a reversal whose invoice is paid again"
            )
    findings = (
        _supported(
            "reversal_fully_booked",
            (pay_ev, d_ev, c_ev, j_ev),
            tuple(c.calculation_id for c in calcs),
        ),
        _supported(
            "invoice_not_paid",
            (*alloc_evs, *[e for b in bals for e in b.evidence]),
        ),
    )
    return led.packet(Diagnosis(Disposition.ANSWERED, REVERSAL, findings))


def _matched(
    request: InvestigationRequest,
    reader: Reader,
    led: Ledger,
    pay: Row,
    pay_ev: str,
    d: Row,
    d_ev: str,
) -> EvidencePacket:
    """Debit equals payment: batch, clearing variants, or no exception."""
    cutoff = request.cutoff
    method = req(pay, "payment_method")
    policy = clearing_policy(method)
    if policy is None:
        return led.abstain(
            "unknown_payment_method", "a declared clearing limit for the payment method"
        )
    limit = CLEARING_LIMITS[method]
    paid = date.fromisoformat(req(pay, "payment_date"))
    posted = date.fromisoformat(req(d, "posted_date"))
    if posted < paid:
        return led.abstain(
            "bank_row_before_payment",
            "an explanation of a debit dated before the payment",
        )
    lag = business_days(paid, posted)
    led.day_count(
        "business_day_lag",
        (pay_ev, "payment_date"),
        (d_ev, "posted_date"),
        lag,
        limit,
        policy,
    )
    if lag > limit:
        return led.abstain(
            "clearing_lag_exceeds_limit",
            "an explanation of a debit posted beyond the clearing limit",
        )
    allocs = require_allocation(led, reader, pay, cutoff)
    if isinstance(allocs, EvidencePacket):
        return allocs
    invoices = sorted({req(a, "invoice_id") for a in allocs})
    base = (pay_ev, d_ev)
    calcs = ("delta", "business_day_lag")
    # Every variant whose contract holds on these records; the first one wins.
    holds = []
    if len(invoices) >= 2:
        holds.append(BATCH)
    if method == "check":
        holds.append(CHECK)
    if method == "ACH" and spans_weekend(paid, posted):
        holds.append(WEEKEND)
    if holds and holds[0] == BATCH:
        return _batch(led, pay, pay_ev, d_ev, allocs, tuple(holds[1:]))
    if holds:
        check_id = (
            "cleared_within_check_limit"
            if holds[0] == CHECK
            else "cleared_across_weekend"
        )
        findings = (
            _supported(check_id, base, calcs),
            allocated_finding(led, pay, allocs),
        )
        return led.packet(Diagnosis(Disposition.ANSWERED, holds[0], findings))
    return _no_exception(led, reader, pay, pay_ev, d_ev, allocs)
