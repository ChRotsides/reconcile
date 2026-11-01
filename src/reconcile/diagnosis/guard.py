"""Prerequisites of every all-clear (docs/evidence-contract.md section 5).

Before a payment is called legitimate or "no exception", three generic finance
rules must hold, all read from allowlisted snapshot tables:

1. Invoice totals. No invoice the payment is allocated to is over-allocated by
   the effective payments (this is also the duplicate check), in one currency.
2. Approval. Each invoice the payment is allocated to has an approval that is
   effective on the payment date: the latest approval-workflow event dated on
   or before the payment date is a final approval, i.e. `action` approved or
   auto_approved with `new_status` `approved` (the workflow's terminal state) at
   the highest `approval_level` reached by that date. A later rejection,
   cancellation or reopening on or before the payment date revokes it; events
   after the payment date do not matter. Events with the same timestamp have no
   order, so the approval is effective only if every one of them is final.
3. GL posting. The payment has a balanced ERP payment journal in the payment's
   currency whose amount equals the payment amount within one minor unit.

If any cannot be confirmed the case abstains with a specific reason. The guard
runs last, so every other reason keeps its precedence.
"""

from dataclasses import replace
from datetime import date, datetime

from reconcile.diagnosis.ledger import Ledger, req
from reconcile.diagnosis.policy import quantum
from reconcile.diagnosis.settled import alloc_evs_of, invoice_balances
from reconcile.domain.contracts import (
    Disposition,
    EvidencePacket,
    Finding,
    FindingState,
)
from reconcile.retrieval import payment as rt
from reconcile.retrieval.snapshot import Reader, Row

NOT_COMPLETED = "invoice_approval_not_completed"
NOT_EFFECTIVE = "invoice_approval_not_effective"
BEFORE_APPROVAL = "payment_before_approval"
JOURNAL_MISSING = "payment_journal_missing"
JOURNAL_UNBALANCED = "payment_journal_unbalanced"
JOURNAL_CURRENCY = "payment_journal_currency_differs"
JOURNAL_AMOUNT = "payment_journal_amount_differs"
APPROVED = ("approved", "auto_approved")


def _supported(
    check: str, evidence: tuple[str, ...], calcs: tuple[str, ...] = ()
) -> Finding:
    return Finding(check, FindingState.SUPPORTED, tuple(dict.fromkeys(evidence)), calcs)


def _events(reader: Reader, invoice: str, cutoff: date) -> list[Row]:
    # The order only fixes the order of citations; no decision reads it.
    return reader.rows(
        "SELECT approval_event_id, approval_level, approver_role, action,"
        " event_timestamp, previous_status, new_status FROM approval_events"
        " WHERE invoice_id = ? AND substr(event_timestamp, 1, 10) <= ?"
        " ORDER BY event_timestamp, approval_level, action, new_status,"
        " approval_event_id",
        (invoice, cutoff.isoformat()),
    )


def _stamp(e: Row) -> datetime:
    return datetime.fromisoformat(req(e, "event_timestamp"))


def _is_final(e: Row, top: int) -> bool:
    return (
        e["action"] in APPROVED
        and e["new_status"] == "approved"
        and int(req(e, "approval_level")) >= top
    )


def _cite_event(led: Ledger, e: Row) -> str:
    eid = req(e, "approval_event_id")
    return led.record(
        f"approval:{eid}",
        "approval_events",
        eid,
        "invoice_approval",
        {
            "approval_level": req(e, "approval_level"),
            "approver_role": e["approver_role"] or "",
            "action": e["action"] or "",
            "previous_status": e["previous_status"] or "",
            "new_status": e["new_status"] or "",
        },
        {"event_timestamp": req(e, "event_timestamp")},
    )


def _approval(
    led: Ledger, reader: Reader, pid: str, invoices: list[str], cutoff: date
) -> list[Finding] | EvidencePacket:
    pay_ev = f"pay:{pid}"
    paid = dict(led.items[pay_ev].timestamps)["payment_date"]
    evidence: list[str] = []
    for inv in invoices:
        events = _events(reader, inv, cutoff)
        upto = [e for e in events if req(e, "event_timestamp")[:10] <= paid]
        top = max((int(req(e, "approval_level")) for e in upto), default=0)
        finals = [e for e in upto if _is_final(e, top)]
        newest = max((_stamp(e) for e in upto), default=None)
        latest = [e for e in upto if _stamp(e) == newest]
        if latest and all(_is_final(e, top) for e in latest):
            evidence.extend(_cite_event(led, e) for e in latest)
            continue
        if finals:  # approved once, but the newest event is not a final approval
            cited = [_cite_event(led, e) for e in (finals[-1], *latest)]
            return led.abstain(
                NOT_EFFECTIVE,
                "an approval that was still effective on the payment date",
                (_supported(NOT_EFFECTIVE, (pay_ev, *cited)),),
            )
        top_all = max((int(req(e, "approval_level")) for e in events), default=0)
        later = [
            e
            for e in events
            if req(e, "event_timestamp")[:10] > paid and _is_final(e, top_all)
        ]
        if later:
            ev = _cite_event(led, later[0])
            return led.abstain(
                BEFORE_APPROVAL,
                "a final approval recorded on or before the payment date",
                (_supported(BEFORE_APPROVAL, (pay_ev, ev)),),
            )
        cited = [_cite_event(led, e) for e in upto]
        search = led.search(
            "approval_events",
            f"invoice_id = '{inv}' AND action IN ('approved', 'auto_approved')"
            " AND new_status = 'approved'"
            f" AND CAST(approval_level AS INTEGER) >= {top}"
            f" AND substr(event_timestamp, 1, 10) <= '{paid}'",
            0,
            "final_approval_search",
        )
        return led.abstain(
            NOT_COMPLETED,
            "a completed final approval of each invoice the payment settles",
            (_supported(NOT_COMPLETED, (pay_ev, *cited, search)),),
        )
    return [_supported("final_approval_before_payment", (pay_ev, *evidence))]


def _totals(
    led: Ledger,
    reader: Reader,
    pid: str,
    invoices: list[str],
    allocs: list[Row],
    cutoff: date,
) -> Finding | EvidencePacket:
    pay_ev = f"pay:{pid}"
    cur = dict(led.items[pay_ev].values)["payment_currency"]
    bals = invoice_balances(led, reader, invoices, cur, cutoff, pid)
    if isinstance(bals, EvidencePacket):
        return bals
    # Every allocation of the payment is relied on (its invoice is approved and
    # totalled below), so each is cited whether or not the payment is effective.
    return _supported(
        "invoice_totals_not_exceeded",
        (
            pay_ev,
            *alloc_evs_of(led, pid, allocs, cur),
            *[e for b in bals for e in b.evidence],
        ),
        tuple(b.calc_id for b in bals if b.calc_id),
    )


def _journal(
    led: Ledger, reader: Reader, pid: str, cutoff: date
) -> list[Finding] | EvidencePacket:
    pay_ev = f"pay:{pid}"
    cur = dict(led.items[pay_ev].values)["payment_currency"]
    lines = rt.journals(reader, pid, "payment", cutoff)
    if not lines:
        search = led.search(
            "gl_entries",
            f"transaction_type = 'payment' AND source_transaction_id = '{pid}'"
            f" AND posting_date <= '{cutoff.isoformat()}'",
            0,
            "payment_journal_search",
        )
        return led.abstain(
            JOURNAL_MISSING,
            "a balanced payment journal sourced to the payment",
            (_supported(JOURNAL_MISSING, (pay_ev, search)),),
        )
    evs: list[str] = []
    for jid, ls in lines.items():
        ev = led.journal(jid, ls, "payment_journal")
        if ev is None:
            return led.abstain(
                JOURNAL_UNBALANCED,
                "a balanced single-currency payment journal",
            )
        evs.append(ev)
    if any(dict(led.items[e].values)["currency"] != cur for e in evs):
        return led.abstain(
            JOURNAL_CURRENCY, "a payment journal in the payment's currency"
        )
    q = quantum(cur)
    diff = led.calc(
        f"payment_minus_payment_journals:{pid}",
        "difference",
        [(pay_ev, "payment_amount"), *[(e, "amount") for e in evs]],
        q,
    )
    if abs(diff.result).amount > q.amount:
        finding = _supported(JOURNAL_AMOUNT, (pay_ev, *evs), (diff.calculation_id,))
        return led.abstain(
            JOURNAL_AMOUNT,
            "an explanation of a payment journal that differs from the payment",
            (finding,),
        )
    return [
        _supported(
            "payment_journal_matches_payment", (pay_ev, *evs), (diff.calculation_id,)
        )
    ]


def guard_payment(
    led: Ledger, reader: Reader, pid: str, cutoff: date
) -> list[Finding] | EvidencePacket:
    """Findings that show all three prerequisites, or the abstention. The payment
    must already be cited in `led` as `pay:<pid>`."""
    allocs = rt.allocations_of_payment(reader, pid, cutoff)
    invoices = sorted({req(a, "invoice_id") for a in allocs})
    totals = _totals(led, reader, pid, invoices, allocs, cutoff)
    if isinstance(totals, EvidencePacket):
        return totals
    approval = _approval(led, reader, pid, invoices, cutoff)
    if isinstance(approval, EvidencePacket):
        return approval
    journal = _journal(led, reader, pid, cutoff)
    if isinstance(journal, EvidencePacket):
        return journal
    return [totals, *approval, *journal]


def guarded(
    led: Ledger, reader: Reader, pids: list[str], packet: EvidencePacket
) -> EvidencePacket:
    """Pass an answered all-clear packet through the guard of each payment it
    rests on; anything else is returned unchanged. Findings of several payments
    are merged by check, so a split invoice keeps one finding per prerequisite."""
    d = packet.diagnosis
    category = d.category or ""
    if d.disposition is not Disposition.ANSWERED or not (
        category.startswith("legitimate_") or category == "no_exception"
    ):
        return packet
    merged: dict[str, Finding] = {}
    for pid in pids:
        result = guard_payment(led, reader, pid, led.request.cutoff)
        if isinstance(result, EvidencePacket):
            return result
        for f in result:
            old = merged.get(f.check_id)
            merged[f.check_id] = (
                f
                if old is None
                else _supported(
                    f.check_id,
                    (*old.evidence_ids, *f.evidence_ids),
                    tuple(dict.fromkeys((*old.calculation_ids, *f.calculation_ids))),
                )
            )
    diagnosis = replace(d, findings=(*d.findings, *merged.values()))
    return led.packet(diagnosis, packet.recommendations, packet.limitations)
