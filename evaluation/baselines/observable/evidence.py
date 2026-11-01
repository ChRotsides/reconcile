"""Deterministic evidence retrieval for the baseline (PLAN.md M2: "give both
baselines deterministic evidence retrieval and report templates").

For the category the classifier predicted, retrieve from the snapshot the
records and absence counts that `docs/evidence-contract.md` requires for that
category and check that each requirement is met. This is the same list of
requirements the evaluator applies to the investigator's packets
(`evaluation/dev_run.py` `required`, lines 389-599), re-implemented here
against the snapshot state instead of against cited packet items.

What this does and does not show. A requirement is met when the required
records exist (non-empty retrieval) and, for absence searches, the count the
contract demands holds. So a wrong category usually fails its own retrieval
(a bank row exists, so "F12" cannot cite a count-0 reference search), and a
right category passes when the snapshot supports it. It does not check claim
text, calculations or citation ids: the baseline emits no packet. It is
therefore a retrieval-completeness check, weaker than the packet check applied
to the investigator in two ways (no calculation or day-count item is
cited, and no claim is rendered); in two ways it is the same (same record
lists, same absence counts). The dev_run check also requires the packet's
absence-search counts to reproduce; that holds here by construction because
the counts are computed from the snapshot.
"""

from datetime import date
from decimal import Decimal

from features import CLEARING_LIMITS, Snapshot, business_days, effective


def retrieve(
    s: Snapshot, category: str, table: str, record_id: str
) -> list[tuple[str, bool]]:
    """Requirements (name, met) for `category` on this primary entity; an empty
    list means the contract defines nothing for it (treated as incomplete)."""
    out: list[tuple[str, bool]] = []

    def add(name: str, ok: bool) -> None:
        out.append((name, bool(ok)))

    if table == "bank_transactions":
        if category != "legitimate_accounted_bank_fee":
            return []
        row = s.bank.get(record_id)
        pays = s.pay_by_ref.get(row["payment_reference"] or "", []) if row else []
        add("fee_row", row is not None)
        add("fee_payment", len(pays) == 1)
        add("fee_journal", bool(s.balanced_journals(record_id, "bank_fee")))
        add(
            "payment_allocations",
            len(pays) == 1 and bool(s.alloc_by_pay.get(pays[0]["payment_id"])),
        )
        return out

    if table == "invoices":
        inv = s.invoices.get(record_id)
        if inv is None or category not in (
            "F06",
            "legitimate_split_payment",
            "legitimate_due_date_payment",
        ):
            return []
        allocs = s.alloc_by_inv.get(record_id, [])
        pays = [
            s.payments[a["payment_id"]] for a in allocs if a["payment_id"] in s.payments
        ]
        add("invoice", True)
        add("allocations", bool(allocs))
        if category == "F06":
            add("allocating_payments", bool(pays))
            committed = [
                p
                for p in pays
                if effective(p)
                and p["payment_currency"] == inv["currency"]
                and s.posted_debits(p["reference_number"])
            ]
            add("committed_debits", bool(committed))
            alloc_sum = sum(
                (Decimal(a["allocated_amount"]) for a in allocs), Decimal(0)
            )
            add("overpayment_calculation", alloc_sum > Decimal(inv["invoice_total"]))
        else:
            eff = [p for p in pays if effective(p)]
            add("effective_payments", bool(eff))
            add(
                "posted_debits",
                bool(eff) and all(s.posted_debits(p["reference_number"]) for p in eff),
            )
        return out

    pay = s.payments.get(record_id) if table == "payments" else None
    if pay is None:
        return []
    ref = pay["reference_number"]
    allocs = s.alloc_by_pay.get(record_id, [])
    debits = s.posted_debits(ref)
    principal = s.principal(ref)
    add("payment", True)

    if category in ("F12", "legitimate_pending_settlement"):
        add("allocations", bool(allocs))
        add("payment_journal", bool(s.balanced_journals(record_id, "payment")))
        add("search_a1_no_bank_row", len(principal) == 0)
        add("search_a2_unique_reference", len(s.pay_by_ref.get(ref, [])) == 1)
        add("search_a3_no_candidate", len(s.a3_candidates(pay)) == 0)
        limit = CLEARING_LIMITS.get(pay["payment_method"])
        age = business_days(date.fromisoformat(pay["payment_date"][:10]), s.cutoff)
        add(
            "business_day_age",
            limit is not None and ((age > limit) == (category == "F12")),
        )
    elif category == "F14":
        add("posted_debit", len(debits) == 1)
        add(
            "search_no_fee_journal",
            len(debits) == 1
            and not s.balanced_journals(debits[0]["bank_transaction_id"], "bank_fee"),
        )
    elif category in ("legitimate_weekend_clearing", "legitimate_check_clearing"):
        add("posted_debit", len(debits) == 1)
        add("allocations", bool(allocs))
    elif category == "legitimate_batch_payment":
        add("allocations", bool(allocs))
        add(
            "allocated_invoices",
            bool(allocs) and all(a["invoice_id"] in s.invoices for a in allocs),
        )
        add("posted_debit", bool(debits))
    elif category == "legitimate_payment_reversal":
        add("posted_debit", bool(debits))
        add(
            "posted_credit",
            any(
                b["direction"] == "credit" and b["status"] == "posted"
                for b in principal
            ),
        )
        add(
            "reversal_journal", bool(s.balanced_journals(record_id, "payment_reversal"))
        )
        add(
            "allocated_invoices",
            bool(allocs) and all(a["invoice_id"] in s.invoices for a in allocs),
        )
    elif category == "legitimate_net_fee_accounting":
        add("posted_debit", len(debits) == 1)
        add(
            "fee_journals",
            len(debits) == 1
            and bool(s.balanced_journals(debits[0]["bank_transaction_id"], "bank_fee")),
        )
        add("allocations", bool(allocs))
    else:
        return []
    return out


def complete(s: Snapshot, category: str, table: str, record_id: str) -> bool:
    reqs = retrieve(s, category, table, record_id)
    return bool(reqs) and all(ok for _, ok in reqs)
