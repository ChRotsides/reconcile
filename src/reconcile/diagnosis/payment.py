"""Payment- and bank-row-primary entry points, and the "no bank row" family:
F12 (docs/evidence-contract.md section 4.2), pending settlement (section 5),
the accounted bank fee variant and the abstentions that guard them.

A search is cited only where the conclusion rests on it. Known contract gaps
(one bank debit for several payments, a reference that fails to match with a
nearby debit) are detected and abstain with their own reason.
"""

from datetime import date
from itertools import combinations

from reconcile.diagnosis.guard import guarded
from reconcile.diagnosis.ledger import Ledger, is_effective, req, val
from reconcile.diagnosis.policy import CLEARING_LIMITS, clearing_policy, quantum
from reconcile.diagnosis.settled import (
    allocated_finding,
    diagnose_settled,
    require_allocation,
)
from reconcile.domain.calendar import business_days
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
from reconcile.retrieval.snapshot import Reader, Row

F12 = "F12"
PENDING = "legitimate_pending_settlement"
ACCOUNTED_FEE = "legitimate_accounted_bank_fee"
MAX_BATCH_CANDIDATES = 12  # subset search is 2**n; beyond this we abstain


def _a1_predicate(ref: str, cutoff: date) -> str:
    return (
        f"payment_reference = '{ref}' AND transaction_type <> 'bank fee'"
        f" AND posted_date <= '{cutoff.isoformat()}'"
    )


def _a3_predicate(pay: Row, cutoff: date) -> str:
    return (
        "direction = 'debit' AND transaction_type <> 'bank fee'"
        f" AND currency = '{val(pay, 'payment_currency')}'"
        f" AND amount = '{val(pay, 'payment_amount')}'"
        f" AND counterparty_token = '{val(pay, 'bank_account_id')}'"
        f" AND posted_date <= '{cutoff.isoformat()}'"
        " AND payment_reference matches no payment"
    )


def diagnose_payment(
    request: InvestigationRequest,
    reader: Reader,
    provenance: tuple[tuple[str, str], ...],
) -> EvidencePacket:
    led = Ledger(request, provenance)
    cutoff = request.cutoff
    pid = request.primary_entity.record_id
    pay = rt.payment(reader, pid, cutoff)
    if pay is None:
        return led.abstain(
            "primary_entity_not_in_snapshot",
            "the primary record, absent from the snapshot at the cutoff",
        )
    ref = req(pay, "reference_number")
    pay_ev = led.payment(pay, pid, "primary")
    same_ref = rt.payments_with_reference(reader, ref)
    rows = rt.principal_rows(reader, ref, cutoff)

    if len(same_ref) > 1:  # A2 not empty: matching is ambiguous
        for other in same_ref:
            oid = req(other, "payment_id")
            if oid != pid:
                led.payment(other, oid, "same_reference")
        led.search("payments", f"reference_number = '{ref}'", len(same_ref), "a2")
        if rows:
            for r in rows:
                led.bank_row(r, "payment_reference")
        else:
            led.search("bank_transactions", _a1_predicate(ref, cutoff), 0, "a1")
            _age(led, pay, pay_ev, cutoff)
        return led.abstain(
            "ambiguous_reference", "a payment reference unique to one payment"
        )
    packet = (
        diagnose_settled(request, reader, led, pay, rows)
        if rows
        else _no_bank_row(request, reader, led, pay)
    )
    return guarded(led, reader, [pid], packet)


def _age(led: Ledger, pay: Row, pay_ev: str, cutoff: date) -> tuple[int, int] | None:
    """Business-day age at the cutoff and the declared limit, as a day count."""
    method = val(pay, "payment_method")
    policy = clearing_policy(method)
    if policy is None:
        return None
    age = business_days(date.fromisoformat(req(pay, "payment_date")), cutoff)
    limit = CLEARING_LIMITS[method]
    led.day_count(
        "business_day_age", (pay_ev, "payment_date"), None, age, limit, policy
    )
    return age, limit


def _no_bank_row(
    request: InvestigationRequest, reader: Reader, led: Ledger, pay: Row
) -> EvidencePacket:
    cutoff = request.cutoff
    pid, ref = req(pay, "payment_id"), req(pay, "reference_number")
    pay_ev = f"pay:{pid}"
    cur = req(pay, "payment_currency")
    amount = Money.parse(req(pay, "payment_amount"), cur)
    a1 = led.search("bank_transactions", _a1_predicate(ref, cutoff), 0, "a1")
    a2 = led.search("payments", f"reference_number = '{ref}'", 1, "a2")
    aged = _age(led, pay, pay_ev, cutoff)
    if aged is None:
        return led.abstain(
            "unknown_payment_method", "a declared clearing limit for the payment method"
        )
    age, limit = aged
    effective = is_effective(pay)
    not_final = pay["payment_status"] in ("submitted", "pending") or (
        pay["settlement_status"] == "pending"
    )
    if not (effective or not_final):
        return led.out_of_scope(
            "no_in_scope_pattern", "the payment is neither settled nor pending"
        )

    allocs = rt.allocations_of_payment(reader, pid, cutoff)
    alloc_evs = [
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
    journal_evs = [
        ev
        for jid, lines in rt.journals(reader, pid, "payment", cutoff).items()
        if (ev := led.journal(jid, lines, "payment_journal", pid, "payment"))
        is not None
    ]
    if not alloc_evs or not journal_evs:
        return led.abstain(
            "required_records_missing",
            "an allocation and a balanced payment journal for the payment",
        )

    # The A3 search needs the counterparty on both sides. Without it "found
    # nothing" would be a search that never ran, so do not draw on it.
    if pay["bank_account_id"] is None:
        return led.abstain(
            "counterparty_unavailable",
            "the counterparty account of this payment, for the unmatched-debit search",
        )
    if rt.debits_without_counterparty(reader, cur, cutoff):
        return led.abstain(
            "counterparty_unavailable",
            "the counterparty of every bank debit that could settle this payment",
        )
    acct = req(pay, "bank_account_id")
    pool = rt.unreferenced_debits(reader, acct, cur, cutoff)
    equal = [r for r in pool if Money.parse(req(r, "amount"), cur) == amount]
    a3 = led.search("bank_transactions", _a3_predicate(pay, cutoff), len(equal), "a3")
    if equal:
        for r in equal:
            led.bank_row(r, "unreferenced_candidate")
        return led.abstain(
            "reference_unmatched_candidate_bank_row",
            "confirmation that the candidate bank row settles this payment",
        )
    gap = _reference_gap(request, reader, led, pay, pool, limit)
    if gap is not None:
        return gap

    base = (pay_ev, *alloc_evs, *journal_evs, a1, a2, a3)
    if effective and age > limit:
        finding = Finding(
            "payment_missing_from_bank",
            FindingState.SUPPORTED,
            base,
            ("business_day_age",),
        )
        return led.packet(
            Diagnosis(Disposition.ANSWERED, F12, (finding,)),
            (
                Recommendation(
                    "investigate_missing_bank_row",
                    ("payment_missing_from_bank",),
                    prerequisites=(
                        "Check the bank statement and the payment file for this run.",
                    ),
                    unresolved_assumptions=(
                        "Snapshot is complete through the cutoff (contract P7).",
                    ),
                ),
            ),
            ("A missing row is not proof of non-payment; do not pay again unchecked.",),
        )
    if age > limit:
        return led.abstain(
            "pending_payment_beyond_clearing_limit",
            "the status of a not-yet-final payment past its clearing limit",
        )
    # Pending settlement is an all-clear too: it needs an invoice in its currency.
    in_currency = require_allocation(led, reader, pay, cutoff)
    if isinstance(in_currency, EvidencePacket):
        return in_currency
    findings = (
        Finding(
            "within_clearing_window",
            FindingState.SUPPORTED,
            (pay_ev, a1, a2, a3),
            ("business_day_age",),
        ),
        Finding(
            "payment_records_present",
            FindingState.SUPPORTED,
            (pay_ev, *alloc_evs, *journal_evs),
        ),
    )
    return led.packet(
        Diagnosis(Disposition.ANSWERED, PENDING, findings),
        limitations=("The payment may become an exception if no row posts later.",),
    )


def _reference_gap(
    request: InvestigationRequest,
    reader: Reader,
    led: Ledger,
    pay: Row,
    pool: list[Row],
    limit: int,
) -> EvidencePacket | None:
    """Known contract gaps: no debit carries the reference and none equals the
    amount (A3 empty), yet a debit in the clearing window exists. A batch debit
    or a truncated reference with a netted fee would be called F12 wrongly."""
    cutoff = request.cutoff
    cur = req(pay, "payment_currency")
    paid = date.fromisoformat(req(pay, "payment_date"))
    near = [
        r
        for r in pool
        if paid <= date.fromisoformat(req(r, "posted_date"))
        and business_days(paid, date.fromisoformat(req(r, "posted_date"))) <= limit
    ]
    if not near:
        return None
    pid = req(pay, "payment_id")
    others = [
        p
        for p in rt.unbanked_payments(reader, req(pay, "bank_account_id"), cur, cutoff)
        if req(p, "payment_id") != pid
    ]
    if len(others) > MAX_BATCH_CANDIDATES:
        return led.abstain(
            "batch_search_limit_exceeded",
            f"a batch check over more than {MAX_BATCH_CANDIDATES} unbanked payments",
        )
    for row in near:
        target = Money.parse(req(row, "amount"), cur)
        for n in range(1, len(others) + 1):
            for members in combinations(others, n):
                total = Money.parse(req(pay, "payment_amount"), cur)
                for m in members:
                    total = total + Money.parse(req(m, "payment_amount"), cur)
                if total == target:
                    return _batch_gap(led, pay, members, row)
    pay_ev = f"pay:{pid}"
    for row in near:
        b_ev = led.bank_row(row, "nearby_debit")
        led.calc(
            f"nearby_debit_amount_difference:{req(row, 'bank_transaction_id')}",
            "abs_difference",
            [(pay_ev, "payment_amount"), (b_ev, "amount")],
            quantum(cur),
        )
    return led.abstain(
        "reference_unmatched_nearby_debit",
        "confirmation whether a nearby debit with another amount settles this payment",
    )


def _batch_gap(
    led: Ledger, pay: Row, members: tuple[Row, ...], row: Row
) -> EvidencePacket:
    pay_ev = f"pay:{req(pay, 'payment_id')}"
    member_evs = [led.payment(m, req(m, "payment_id"), "batch_member") for m in members]
    b_ev = led.bank_row(row, "unreferenced_batch_debit")
    led.calc(
        "sum_of_payments",
        "sum",
        [(pay_ev, "payment_amount")] + [(e, "payment_amount") for e in member_evs],
    )
    led.calc("bank_debit", "sum", [(b_ev, "amount")])
    return led.abstain(
        "bank_batch_grouping_unmodelled",
        "the bank batch's member list (one debit settles several payments)",
    )


def diagnose_bank_row(
    request: InvestigationRequest,
    reader: Reader,
    provenance: tuple[tuple[str, str], ...],
) -> EvidencePacket:
    """Bank-row primary: only the accounted-bank-fee variant (section 5)."""
    led = Ledger(request, provenance)
    cutoff = request.cutoff
    bid = request.primary_entity.record_id
    row = rt.bank_row(reader, bid, cutoff)
    if row is None:
        return led.abstain(
            "primary_entity_not_in_snapshot",
            "the primary record, absent from the snapshot at the cutoff",
        )
    if row["transaction_type"] != "bank fee":
        return led.out_of_scope(
            "bank_row_type_not_supported_yet",
            "only bank fee rows are investigated as a bank-row primary",
        )
    cur = req(row, "currency")
    fee_ev = led.bank_row(row, "primary")
    ref = row["payment_reference"]
    resolved = rt.payments_with_reference(reader, ref) if ref else []
    if ref:
        led.search("payments", f"reference_number = '{ref}'", len(resolved), "a2")
    if len(resolved) != 1:
        for p in resolved:
            led.payment(p, req(p, "payment_id"), "same_reference")
        reason = "ambiguous_reference" if resolved else "fee_reference_unresolved"
        return led.abstain(
            reason, "a fee reference that resolves to exactly one payment"
        )
    pay_ev = led.payment(resolved[0], req(resolved[0], "payment_id"), "fee_payment")
    if req(resolved[0], "payment_currency") != cur:
        return led.abstain(
            "unsupported_fx", "a documented conversion path between currencies"
        )
    lines = rt.journals(reader, bid, "bank_fee", cutoff)
    evs = [
        e
        for j, ls in lines.items()
        if (e := led.journal(j, ls, "fee_journal", bid, "bank_fee"))
    ]
    if not lines:
        led.search(
            "gl_entries",
            f"transaction_type = 'bank_fee' AND source_transaction_id = '{bid}'"
            f" AND posting_date <= '{cutoff.isoformat()}'",
            0,
            "fee_journal_search",
        )
        return led.abstain(
            "accounted_fee_journal_missing", "a balanced bank_fee journal for the fee"
        )
    if len(evs) != len(lines):
        return led.abstain(
            "fee_journal_invalid", "balanced single-currency fee journals"
        )
    if any(dict(led.items[e].values)["currency"] != cur for e in evs):
        return led.abstain(
            "unsupported_fx", "a documented conversion path between currencies"
        )
    q = quantum(cur)
    diff = led.calc(
        "fee_row_minus_journals",
        "difference",
        [(fee_ev, "amount"), *[(e, "amount") for e in evs]],
        q,
    )
    if abs(diff.result).amount > q.amount:
        return led.abstain(
            "fee_journal_amount_differs", "an explanation of the fee journal difference"
        )
    allocs = require_allocation(led, reader, resolved[0], cutoff)
    if isinstance(allocs, EvidencePacket):
        return allocs
    finding = Finding(
        "fee_booked_in_ledger",
        FindingState.SUPPORTED,
        (fee_ev, pay_ev, *evs),
        ("fee_row_minus_journals",),
    )
    packet = led.packet(
        Diagnosis(
            Disposition.ANSWERED,
            ACCOUNTED_FEE,
            (finding, allocated_finding(led, resolved[0], allocs)),
        )
    )
    return guarded(led, reader, [req(resolved[0], "payment_id")], packet)
