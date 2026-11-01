"""Validate docs/evidence-contract.md on the train and validation splits.

Evaluation-side only: it reads development labels, so it must never be imported
by src/reconcile/. Standard library only. It reads the raw upstream
data/benchmark/full tables and applies the section 3.1 allowlist and the
section 6 cutoff rule logically (it only touches allowlisted columns, drops
bank rows posted after the cutoff and GL lines posted after the cutoff).
Opaque remapping is not applied: the contract is about structure, not names.

Usage:
    python3 -I evaluation/evidence_contract_check.py --upstream PATH

Only train/rca_ground_truth.jsonl and validation/rca_ground_truth.jsonl are
opened. No other split directory is touched.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

CUTOFF = date(2026, 6, 30)  # contract section 5; docs/data-audit.md section 6
CLEARING_BDAYS = {"ACH": 3, "wire": 1, "check": 10, "virtual_card": 2}
SPLITS = ("train", "validation")  # the only label directories this script opens
FEE_ROW = "bank fee"
REVERSAL_ROW = "payment reversal"

# Allowlist (docs/data-audit.md section 3.1), restricted to the columns used.
ALLOWED = {
    "payments": [
        "payment_id", "vendor_id", "payment_date", "payment_method",
        "payment_currency", "payment_amount", "bank_account_id",
        "payment_status", "settlement_status", "reference_number",
    ],
    "payment_allocations": [
        "payment_id", "invoice_id", "allocated_amount", "allocation_date",
    ],
    "invoices": [
        "invoice_id", "due_date", "currency", "invoice_total", "status",
    ],
    "bank_transactions": [
        "bank_transaction_id", "transaction_date", "posted_date",
        "transaction_type", "amount", "currency", "direction", "status",
        "counterparty_token", "payment_reference",
    ],
    "gl_entries": [
        "journal_id", "journal_line_id", "transaction_type",
        "source_transaction_id", "posting_date", "debit", "credit", "currency",
    ],
}  # fmt: skip

# Payment-related label keys (data-audit section 2) and where they are routed.
FAMILIES = {
    "F06_INVOICE_PAID_TWICE": "f06",
    "F12_ERP_PAYMENT_MISSING_FROM_BANK": "f12",
    "F14_BANK_ERP_AMOUNT_MISMATCH": "f14",
}
VARIANTS = {
    "legitimate_pending_settlement": "pending",
    "legitimate_weekend_clearing": "clearing",
    "legitimate_check_clearing": "clearing",
    "legitimate_due_date_payment": "due_date",
    "legitimate_split_payment": "split",
    "legitimate_batch_payment": "batch",
    "legitimate_payment_reversal": "reversal",
    "legitimate_net_fee_accounting": "net_fee",
    "legitimate_accounted_bank_fee": "bank_fee",
    "legitimate_fx_settlement": "fx",
}

G, NG, C = "gradable", "not_gradable", "contradiction"


def quantum(currency: str) -> Decimal:
    return Decimal(1) if currency == "JPY" else Decimal("0.01")


def bdays(start: date, end: date) -> int:
    """Weekdays in (start, end]; the inverse of upstream add_business_days."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def read_table(full: Path, name: str) -> list[dict[str, str]]:
    keep = ALLOWED[name]
    with (full / f"{name}.csv").open(newline="") as f:
        return [{c: row[c] for c in keep} for row in csv.DictReader(f)]


@dataclass
class Snapshot:
    payments: dict[str, dict[str, str]]
    invoices: dict[str, dict[str, str]]
    alloc_by_pay: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    alloc_by_inv: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    bank: dict[str, dict[str, str]] = field(default_factory=dict)
    bank_by_ref: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    gl_by_src: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    pay_by_ref: dict[str, list[str]] = field(default_factory=dict)
    late_bank: set[str] = field(default_factory=set)
    late_refs: set[str] = field(default_factory=set)
    late_gl_sources: set[str] = field(default_factory=set)
    orphans: list[dict[str, str]] = field(default_factory=list)


def load(root: Path) -> Snapshot:
    return load_dir(root / "data/benchmark/full")


def load_dir(full: Path) -> Snapshot:
    """Load one directory in the upstream full/ table layout (also the fixtures)."""
    root = full
    pays = read_table(root, "payments")
    snap = Snapshot(
        {p["payment_id"]: p for p in pays},
        {i["invoice_id"]: i for i in read_table(root, "invoices")},
    )
    ap, ai = defaultdict(list), defaultdict(list)
    for a in read_table(root, "payment_allocations"):
        ap[a["payment_id"]].append(a)
        ai[a["invoice_id"]].append(a)
    snap.alloc_by_pay, snap.alloc_by_inv = dict(ap), dict(ai)
    pr = defaultdict(list)
    for p in pays:
        pr[p["reference_number"]].append(p["payment_id"])
    snap.pay_by_ref = dict(pr)
    br = defaultdict(list)
    for b in read_table(root, "bank_transactions"):
        if date.fromisoformat(b["posted_date"]) > CUTOFF:  # cutoff rule
            snap.late_bank.add(b["bank_transaction_id"])
            snap.late_refs.add(b["payment_reference"])
            continue
        snap.bank[b["bank_transaction_id"]] = b
        br[b["payment_reference"]].append(b)
    snap.bank_by_ref = dict(br)
    gl = defaultdict(list)
    for g in read_table(root, "gl_entries"):
        if date.fromisoformat(g["posting_date"]) > CUTOFF:  # cutoff rule
            snap.late_gl_sources.add(g["source_transaction_id"])
            continue
        gl[g["source_transaction_id"]].append(g)
    snap.gl_by_src = dict(gl)
    for refs, rows in snap.bank_by_ref.items():
        if refs not in snap.pay_by_ref:
            snap.orphans.extend(b for b in rows if b["transaction_type"] != FEE_ROW)
    return snap


def money(value: str) -> Decimal:
    return Decimal(value)


def effective(p: dict[str, str]) -> bool:
    return p["payment_status"] == "completed" and p["settlement_status"] == "settled"


def principal_rows(s: Snapshot, p: dict[str, str]) -> list[dict[str, str]]:
    """Observable non-fee bank rows carrying the payment reference."""
    rows = s.bank_by_ref.get(p["reference_number"], [])
    return [b for b in rows if b["transaction_type"] != FEE_ROW]


def posted_debits(s: Snapshot, p: dict[str, str]) -> list[dict[str, str]]:
    return [
        b
        for b in principal_rows(s, p)
        if b["direction"] == "debit" and b["status"] == "posted"
    ]


def journals(rows: list[dict[str, str]], kind: str) -> list[tuple[str, Decimal, str]]:
    """Balanced journals of one transaction_type: (id, amount, currency).

    The currency is the lines' single currency, or "MIXED" when lines differ;
    callers must compare it with the amount they set it against.
    """
    by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for g in rows:
        if g["transaction_type"] == kind:
            by_id[g["journal_id"]].append(g)
    out = []
    for jid, lines in sorted(by_id.items()):
        d = sum((money(x["debit"]) for x in lines), Decimal(0))
        c = sum((money(x["credit"]) for x in lines), Decimal(0))
        if d == c and d > 0:
            currencies = {x["currency"] for x in lines}
            out.append((jid, d, currencies.pop() if len(currencies) == 1 else "MIXED"))
    return out


def invalid_journals(rows: list[dict[str, str]], kind: str) -> set[str]:
    """Ids of journals of this type that are not balanced and positive.

    An absence search counts raw GL rows, so such a journal is not "absent".
    """
    valid = {j[0] for j in journals(rows, kind)}
    return {g["journal_id"] for g in rows if g["transaction_type"] == kind} - valid


@dataclass
class Result:
    status: str
    reason: str
    ids: set[str] = field(default_factory=set)


def ok(reason: str, ids: set[str]) -> Result:
    return Result(G, reason, ids)


def ng(reason: str, ids: set[str] | None = None) -> Result:
    return Result(NG, reason, ids or set())


def bad(reason: str, ids: set[str] | None = None) -> Result:
    return Result(C, reason, ids or set())


def cross_currency(ids: set[str]) -> Result:
    """Amounts in different currencies are never compared (contract section 2)."""
    return ng("cross_currency_unsupported", ids)


def single_bank(s: Snapshot, p: dict[str, str]) -> Result | dict[str, str]:
    """The unique observable principal debit, or a not-gradable Result."""
    if len(s.pay_by_ref.get(p["reference_number"], [])) != 1:
        return ng("reference_shared_by_payments")
    rows = posted_debits(s, p)
    if len(rows) == 1:
        return rows[0]
    if not rows:
        return ng("no_observable_bank_debit")
    return ng("several_bank_debits_for_reference")


# --- contract checks -------------------------------------------------------
# Each check returns a Result: gradable (contract satisfied and its
# calculations agree with the label), not_gradable (a required record or
# search scope is missing or ambiguous) or contradiction (records complete,
# calculations disagree with the label).


def no_bank_reason(s: Snapshot, p: dict[str, str]) -> str:
    late = p["reference_number"] in s.late_refs
    return "bank_row_post_cutoff" if late else "no_observable_bank_debit"


def check_f06(s: Snapshot, inv_id: str) -> Result:
    """Committed overpayment: allocations of payments with a posted debit."""
    inv = s.invoices.get(inv_id)
    allocs = s.alloc_by_inv.get(inv_id, [])
    if inv is None or not allocs:
        return ng("invoice_or_allocations_missing")
    ids = {inv_id}
    allocated = committed = Decimal(0)
    bankless: list[dict[str, str]] = []
    for a in allocs:
        p = s.payments.get(a["payment_id"])
        if p is None:
            return ng("allocated_payment_missing", ids)
        ids.add(p["payment_id"])
        if not effective(p) or p["payment_currency"] != inv["currency"]:
            continue
        amount = money(a["allocated_amount"])
        allocated += amount
        rows = posted_debits(s, p)
        ids.update(b["bank_transaction_id"] for b in rows)
        if rows and len(s.pay_by_ref[p["reference_number"]]) != 1:
            # One debit cannot be attributed to several payments (section 3).
            return ng("reference_shared_by_payments", ids)
        if rows:
            committed += amount
        else:
            bankless.append(p)
    total, q = money(inv["invoice_total"]), quantum(inv["currency"])
    if committed - total > q:
        extra = "_with_bankless_payment" if bankless else ""
        return ok("committed_overpayment" + extra, ids)
    if allocated - total > q:
        late = any(p["reference_number"] in s.late_refs for p in bankless)
        why = "bank_row_post_cutoff" if late else "no_observable_bank_debit"
        return ng("overpayment_not_committed_" + why, ids)
    return bad("allocations_do_not_exceed_invoice", ids)


def erp_side(s: Snapshot, p: dict[str, str]) -> tuple[bool, set[str]]:
    """Allocation rows and a balanced payment journal exist (ERP committed)."""
    pid = p["payment_id"]
    pj = journals(s.gl_by_src.get(pid, []), "payment")
    ids = {pid} | {j[0] for j in pj}
    return bool(s.alloc_by_pay.get(pid)) and bool(pj), ids


def absence_scope(s: Snapshot, p: dict[str, str]) -> Result | None:
    """Bounded search; None means the scope is clean and empty."""
    if len(s.pay_by_ref.get(p["reference_number"], [])) != 1:
        return ng("reference_shared_by_payments")
    if principal_rows(s, p):
        return bad("bank_row_exists_for_reference")
    if p["reference_number"] in s.late_refs:
        return ng("bank_row_post_cutoff")
    amt = money(p["payment_amount"])
    near = [
        b
        for b in s.orphans
        if b["direction"] == "debit"
        and b["currency"] == p["payment_currency"]
        and money(b["amount"]) == amt
        and b["counterparty_token"] == p["bank_account_id"]
    ]
    if near:
        return ng("unreferenced_bank_candidate")
    return None


def check_absence(s: Snapshot, pid: str, *, want_old: bool) -> Result:
    p = s.payments.get(pid)
    if p is None:
        return ng("payment_missing")
    erp_ok, ids = erp_side(s, p)
    if not erp_ok:
        return ng("erp_side_incomplete", ids)
    scope = absence_scope(s, p)
    if scope is not None:
        scope.ids = ids
        return scope
    age = bdays(date.fromisoformat(p["payment_date"]), CUTOFF)
    old = age > CLEARING_BDAYS[p["payment_method"]]
    if want_old:  # F12
        if not effective(p):
            return bad("payment_not_completed_settled", ids)
        return ok("absent_beyond_window", ids) if old else bad("inside_window", ids)
    if old:  # pending
        return bad("absent_beyond_window", ids)
    return ok("absent_inside_window", ids)


def check_f14(s: Snapshot, pid: str, *, variant: str) -> Result:
    p = s.payments.get(pid)
    if p is None:
        return ng("payment_missing")
    b = single_bank(s, p)
    if isinstance(b, Result):
        if b.reason == "no_observable_bank_debit":
            return ng(no_bank_reason(s, p))
        return b
    ids = {pid, b["bank_transaction_id"]}
    fee_j = journals(s.gl_by_src.get(b["bank_transaction_id"], []), "bank_fee")
    ids.update(j[0] for j in fee_j)
    bad_fee = invalid_journals(
        s.gl_by_src.get(b["bank_transaction_id"], []), "bank_fee"
    )
    if bad_fee:
        return ng("invalid_fee_journal", ids | bad_fee)
    if b["currency"] != p["payment_currency"] or any(
        j[2] != p["payment_currency"] for j in fee_j
    ):
        return cross_currency(ids)
    q = quantum(p["payment_currency"])
    delta = money(p["payment_amount"]) - money(b["amount"])
    explained = sum((j[1] for j in fee_j), Decimal(0))
    if delta < -q:
        return ng("bank_exceeds_erp")
    unexplained = abs(delta - explained) > q
    if variant == "f14":
        if delta <= q:
            return bad("no_amount_difference", ids)
        if not unexplained:
            return bad("difference_explained_by_fee_journal", ids)
        # The F14 absence search needs zero fee journals; a partial one fails it.
        if fee_j:
            return ng("partial_fee_journal", ids)
        return ok("unexplained_shortfall", ids)
    if delta <= q:
        return bad("no_amount_difference", ids)
    if unexplained:
        return bad("fee_journal_does_not_explain_difference", ids)
    return ok("difference_explained_by_fee_journal", ids)


def check_bank_fee(s: Snapshot, bank_id: str) -> Result:
    row = s.bank.get(bank_id)
    if row is None:
        why = "bank_row_post_cutoff" if bank_id in s.late_bank else "bank_row_missing"
        return ng(why)
    if row["transaction_type"] != FEE_ROW:
        return bad("not_a_fee_row")
    refs = s.pay_by_ref.get(row["payment_reference"], [])
    if len(refs) != 1:
        return ng("fee_reference_not_unique_payment")
    ids = {bank_id, refs[0]}
    fee_j = journals(s.gl_by_src.get(bank_id, []), "bank_fee")
    ids.update(j[0] for j in fee_j)
    bad_fee = invalid_journals(s.gl_by_src.get(bank_id, []), "bank_fee")
    if bad_fee:
        return ng("invalid_fee_journal", ids | bad_fee)
    if any(j[2] != row["currency"] for j in fee_j):
        return cross_currency(ids)
    if not fee_j:
        why = (
            "journal_post_cutoff" if bank_id in s.late_gl_sources else "no_fee_journal"
        )
        return ng(why, ids) if why == "journal_post_cutoff" else bad(why, ids)
    total = sum((j[1] for j in fee_j), Decimal(0))
    if abs(total - money(row["amount"])) > quantum(row["currency"]):
        return bad("fee_journal_amount_differs", ids)
    return ok("fee_journal_equals_fee_row", ids)


def check_clearing(s: Snapshot, pid: str, method: str) -> Result:
    p = s.payments.get(pid)
    if p is None:
        return ng("payment_missing")
    b = single_bank(s, p)
    if isinstance(b, Result):
        if b.reason == "no_observable_bank_debit":
            return ng(no_bank_reason(s, p))
        return b
    ids = {pid, b["bank_transaction_id"]}
    if p["payment_method"] != method or not effective(p):
        return bad("method_or_status_mismatch", ids)
    if b["currency"] != p["payment_currency"]:
        return cross_currency(ids)
    if money(b["amount"]) != money(p["payment_amount"]):
        return bad("amount_differs", ids)
    lag = bdays(
        date.fromisoformat(p["payment_date"]), date.fromisoformat(b["posted_date"])
    )
    if lag <= CLEARING_BDAYS[method]:
        return ok("within_clearing_window", ids)
    return bad("beyond_clearing_window", ids)


def check_due_date(s: Snapshot, inv_id: str) -> Result:
    inv = s.invoices.get(inv_id)
    allocs = s.alloc_by_inv.get(inv_id, [])
    if inv is None or not allocs:
        return ng("invoice_or_allocations_missing")
    ids = {inv_id}
    hit = False
    net = Decimal(0)
    for a in allocs:
        p = s.payments.get(a["payment_id"])
        if p is None or not effective(p):
            continue
        b = single_bank(s, p)
        if isinstance(b, Result):
            return ng(b.reason, ids | {p["payment_id"]})
        ids.update({p["payment_id"], b["bank_transaction_id"]})
        if {p["payment_currency"], b["currency"]} != {inv["currency"]}:
            return cross_currency(ids)
        if money(b["amount"]) != money(p["payment_amount"]):
            return bad("amount_differs", ids)
        net += money(a["allocated_amount"])
        hit = hit or p["payment_date"] == inv["due_date"]
    if not hit:
        return bad("no_payment_on_due_date", ids)
    if abs(net - money(inv["invoice_total"])) > quantum(inv["currency"]):
        return bad("allocation_differs_from_total", ids)
    return ok("paid_on_due_date_fully_allocated", ids)


def check_split(s: Snapshot, inv_id: str) -> Result:
    inv = s.invoices.get(inv_id)
    allocs = s.alloc_by_inv.get(inv_id, [])
    if inv is None or len(allocs) < 2:
        return ng("fewer_than_two_allocations")
    ids = {inv_id}
    net = Decimal(0)
    for a in allocs:
        p = s.payments.get(a["payment_id"])
        if p is None:
            return ng("allocated_payment_missing", ids)
        ids.add(p["payment_id"])
        if not effective(p):
            return bad("leg_not_completed_settled", ids)
        b = single_bank(s, p)
        if isinstance(b, Result):
            return ng(b.reason, ids)
        ids.add(b["bank_transaction_id"])
        if {p["payment_currency"], b["currency"]} != {inv["currency"]}:
            return cross_currency(ids)
        if money(b["amount"]) != money(p["payment_amount"]):
            return bad("leg_bank_amount_differs", ids)
        net += money(a["allocated_amount"])
    if abs(net - money(inv["invoice_total"])) > quantum(inv["currency"]):
        return bad("allocations_not_equal_invoice", ids)
    return ok("allocations_equal_invoice", ids)


def check_batch(s: Snapshot, pid: str) -> Result:
    p = s.payments.get(pid)
    allocs = s.alloc_by_pay.get(pid, [])
    if p is None or len(allocs) < 2:
        return ng("fewer_than_two_allocations")
    b = single_bank(s, p)
    if isinstance(b, Result):
        return ng(b.reason, {pid})
    ids = {pid, b["bank_transaction_id"]} | {a["invoice_id"] for a in allocs}
    total = sum((money(a["allocated_amount"]) for a in allocs), Decimal(0))
    q = quantum(p["payment_currency"])
    currencies = {p["payment_currency"], b["currency"]}
    currencies.update(
        s.invoices[a["invoice_id"]]["currency"]
        for a in allocs
        if a["invoice_id"] in s.invoices
    )
    if len(currencies) != 1:
        return cross_currency(ids)
    if not effective(p):
        return bad("payment_not_completed_settled", ids)
    if abs(total - money(p["payment_amount"])) > q:
        return bad("allocations_not_equal_payment", ids)
    if money(b["amount"]) != money(p["payment_amount"]):
        return bad("bank_amount_differs", ids)
    for a in allocs:
        inv = s.invoices.get(a["invoice_id"])
        if inv is None:
            return ng("invoice_missing", ids)
        paid = sum(
            (money(x["allocated_amount"]) for x in s.alloc_by_inv[a["invoice_id"]]),
            Decimal(0),
        )
        if paid - money(inv["invoice_total"]) > quantum(inv["currency"]):
            return bad("invoice_overpaid", ids)
    return ok("allocations_equal_payment_and_bank", ids)


def check_reversal(s: Snapshot, pid: str) -> Result:
    p = s.payments.get(pid)
    if p is None:
        return ng("payment_missing")
    rows = principal_rows(s, p)
    debit = [b for b in rows if b["direction"] == "debit" and b["status"] == "posted"]
    credit = [
        b
        for b in rows
        if b["transaction_type"] == REVERSAL_ROW
        and b["direction"] == "credit"
        and b["status"] == "posted"
    ]
    if len(s.pay_by_ref.get(p["reference_number"], [])) != 1:
        return ng("reference_shared_by_payments")
    if len(debit) != 1 or len(credit) != 1:
        late = p["reference_number"] in s.late_refs
        return ng("bank_row_post_cutoff" if late else "debit_credit_pair_missing")
    ids = {pid, debit[0]["bank_transaction_id"], credit[0]["bank_transaction_id"]}
    rev_j = journals(s.gl_by_src.get(pid, []), "payment_reversal")
    ids.update(j[0] for j in rev_j)
    bad_rev = invalid_journals(s.gl_by_src.get(pid, []), "payment_reversal")
    if bad_rev:
        return ng("invalid_reversal_journal", ids | bad_rev)
    if not rev_j:
        late = pid in s.late_gl_sources
        return ng("journal_post_cutoff" if late else "reversal_journal_missing", ids)
    amt = money(p["payment_amount"])
    q = quantum(p["payment_currency"])
    if {debit[0]["currency"], credit[0]["currency"], *(j[2] for j in rev_j)} != {
        p["payment_currency"]
    }:
        return cross_currency(ids)
    if p["payment_status"] != "reversed" or p["settlement_status"] != "reversed":
        return bad("payment_not_marked_reversed", ids)
    if money(debit[0]["amount"]) != amt or money(credit[0]["amount"]) != amt:
        return bad("bank_amounts_differ", ids)
    if credit[0]["posted_date"] < debit[0]["posted_date"]:
        return bad("credit_before_debit", ids)
    if abs(sum((j[1] for j in rev_j), Decimal(0)) - amt) > q:
        return bad("journal_amount_differs", ids)
    for a in s.alloc_by_pay.get(pid, []):
        if s.invoices[a["invoice_id"]]["status"] == "paid":
            return bad("invoice_still_paid", ids)
    return ok("debit_credit_journal_equal", ids)


def check_fx(s: Snapshot, pid: str) -> Result:
    p = s.payments.get(pid)
    if p is None:
        return ng("payment_missing")
    b = single_bank(s, p)
    if isinstance(b, Result):
        return b
    ids = {pid, b["bank_transaction_id"]}
    if b["currency"] == p["payment_currency"]:
        return bad("currencies_equal", ids)
    return ok("currency_mismatch_abstain", ids)


# --- driver ----------------------------------------------------------------


def route(s: Snapshot, case: dict) -> Result | None:
    ent = case["primary_entity"]["id"]
    fam = FAMILIES.get(case["failure_type"])
    var = VARIANTS.get(case["scenario_variant"])
    if case["failure_type"] == "NO_FAILURE" and var is None:
        return None
    if fam == "f06":
        return check_f06(s, ent)
    if fam == "f12":
        return check_absence(s, ent, want_old=True)
    if fam == "f14":
        return check_f14(s, ent, variant="f14")
    if var == "pending":
        return check_absence(s, ent, want_old=False)
    if var == "clearing":
        method = "check" if "check" in case["scenario_variant"] else "ACH"
        return check_clearing(s, ent, method)
    if var == "due_date":
        return check_due_date(s, ent)
    if var == "split":
        return check_split(s, ent)
    if var == "batch":
        return check_batch(s, ent)
    if var == "reversal":
        return check_reversal(s, ent)
    if var == "net_fee":
        return check_f14(s, ent, variant="net_fee")
    if var == "bank_fee":
        return check_bank_fee(s, ent)
    if var == "fx":
        return check_fx(s, ent)
    return None


def fired(s: Snapshot, ids: set[str]) -> set[str]:
    """Failure predicates that fire on any invoice or payment in a record set."""
    out = set()
    for i in sorted(ids):
        if i in s.invoices and check_f06(s, i).status == G:
            out.add("f06")
        if i in s.payments:
            if check_absence(s, i, want_old=True).status == G:
                out.add("f12")
            if check_f14(s, i, variant="f14").status == G:
                out.add("f14")
    return out


def margin(s: Snapshot, case: dict) -> int | None:
    """Business days between the observed age/lag and the clearing limit."""
    p = s.payments.get(case["primary_entity"]["id"])
    if p is None:
        return None
    limit = CLEARING_BDAYS[p["payment_method"]]
    start = date.fromisoformat(p["payment_date"])
    key = key_of(case)
    if key in ("F12", "legitimate_pending_settlement"):
        return bdays(start, CUTOFF) - limit
    rows = posted_debits(s, p)
    if key.endswith("_clearing") and len(rows) == 1:
        return bdays(start, date.fromisoformat(rows[0]["posted_date"])) - limit
    return None


def key_of(case: dict) -> str:
    if case["failure_type"] in FAMILIES:
        return case["failure_type"].split("_")[0]
    return case["scenario_variant"]


def read_cases(root: Path) -> list[dict]:
    cases = []
    for split in SPLITS:
        path = root / "data/benchmark" / split / "rca_ground_truth.jsonl"
        with path.open() as f:
            for line in f:
                c = json.loads(line)
                c["split"] = split
                cases.append(c)
    return sorted(cases, key=lambda c: c["case_id"])


def table(title: str, rows: dict[str, Counter], cols: list[str]) -> None:
    print(f"\n{title}")
    print("| key | cases | " + " | ".join(cols) + " |")
    print("|---|---:|" + "---:|" * len(cols))
    tot: Counter = Counter()
    for k in sorted(rows):
        n = sum(rows[k][c] for c in cols)
        print(f"| {k} | {n} | " + " | ".join(str(rows[k][c]) for c in cols) + " |")
        tot.update(rows[k])
    n = sum(tot[c] for c in cols)
    print(f"| total | {n} | " + " | ".join(str(tot[c]) for c in cols) + " |")


def main() -> None:
    default = Path(__file__).resolve().parents[1] / "data/upstream/FinRCA-AI-Bench"
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--upstream", type=Path, default=default)
    args = ap.parse_args()
    snap = load(args.upstream)
    cases = read_cases(args.upstream)

    by_key: dict[str, Counter] = defaultdict(Counter)
    by_split: dict[str, Counter] = defaultdict(Counter)
    reasons: dict[str, Counter] = defaultdict(Counter)
    flagged: list[tuple[str, str, str, str]] = []
    uncovered: Counter = Counter()
    fires: dict[str, Counter] = defaultdict(Counter)
    margins: dict[str, list[int]] = defaultdict(list)
    for case in cases:
        res = route(snap, case)
        if res is None:
            continue
        key = key_of(case)
        by_key[key][res.status] += 1
        by_split[f"{key} / {case['split']}"][res.status] += 1
        reasons[key][f"{res.status}: {res.reason}"] += 1
        if res.status != G:
            flagged.append((case["case_id"], key, res.status, res.reason))
        if res.status == G and (m := margin(snap, case)) is not None:
            margins[key].append(m)
        for pred in fired(snap, res.ids):
            fires[key][pred] += 1
        upstream = {e for e in case["evidence_ids"] if not e.startswith("EVT_")}
        if res.status == G and not upstream <= res.ids:
            uncovered[key] += 1
    print(f"cutoff {CUTOFF}; clearing business days {CLEARING_BDAYS}")
    print(
        f"observable bank rows {len(snap.bank)}; dropped post-cutoff bank rows "
        f"{sorted(snap.late_bank)}; GL sources with post-cutoff lines "
        f"{len(snap.late_gl_sources)}"
    )
    cols = [G, NG, C]
    table("Per family / variant (train + validation)", by_key, cols)
    table("Per family / variant and split", by_split, cols)
    print("\nReason breakdown")
    for k in sorted(reasons):
        for r, n in sorted(reasons[k].items()):
            print(f"  {k}: {r} = {n}")
    print(
        "\nGradable cases whose non-event upstream evidence ids are not all "
        "in the contract record set"
    )
    for k in sorted(by_key):
        print(f"  {k}: {uncovered[k]} of {by_key[k][G]}")
    print("\nFailure predicates firing on a case's records (cases per predicate)")
    print("| key | f06 | f12 | f14 |")
    print("|---|---:|---:|---:|")
    for k in sorted(by_key):
        print(f"| {k} | {fires[k]['f06']} | {fires[k]['f12']} | {fires[k]['f14']} |")
    print("\nMargin to the clearing limit in business days (age/lag minus limit)")
    for k in sorted(margins):
        m = margins[k]
        print(f"  {k}: min {min(m)}, max {max(m)}, at limit exactly {m.count(0)}")
    print(f"\nunreferenced non-fee bank rows (orphans): {len(snap.orphans)}")
    print("\nNot gradable or contradicted cases")
    for row in flagged:
        print("  " + " | ".join(row))


if __name__ == "__main__":
    main()
