"""Feature extraction for the observable-input baseline (docs/baseline-observable.md).

Reads the prepared snapshot only (tokens, allowlisted columns, cutoff). Nothing
here opens a label, a key, a links file or the upstream checkout. For one
request it uses the primary entity's own row and rows reachable in at most the
investigator's hops: payment <-> allocations <-> invoices, payment -> bank rows
by shared reference, payment/bank row/invoice -> GL lines by source id,
invoice -> approval events, payment -> same-vendor look-alike payments.

Money is parsed and compared as Decimal. A feature that must be a number for
the model (a ratio, a log10 magnitude) is converted to float only at the end.
"""

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from reconcile.domain.calendar import business_days as shared_business_days

# Clearing limits in business days (docs/evidence-contract.md section 6, P1).
CLEARING_LIMITS = {"ACH": 3, "wire": 1, "check": 10, "virtual_card": 2}
NAN = float("nan")

# Feature groups used by the residual ablation (docs/leakage-test.md section 6).
RESIDUAL_GROUPS: dict[str, tuple[str, ...]] = {
    "R1_invoice_status_and_approvals": (
        "i_status_",
        "i_n_approval",
        "p_inv_min_approval",
    ),
    "R2_fx_codes": ("g_fx", "g_type_fx_settlement", "p_gl_has_fx"),
    "R3_entity_type": ("e_",),
    "R4_invoice_age": ("i_age_days",),
    "allocation_count": ("p_n_alloc", "i_n_alloc"),
}


def business_days(start: date, end: date) -> int:
    """Weekdays in (start, end] by the investigator's calendar
    (`reconcile.domain.calendar`); 0 when `end` precedes `start`, where the
    shared function raises (a debit dated before its payment is a feature, not
    an error, here)."""
    return shared_business_days(start, end) if end >= start else 0


def _d(text: str) -> date:
    return date.fromisoformat(text[:10])


def _log10(x: Decimal) -> float:
    return math.log10(float(x)) if x > 0 else NAN


def _ratio(a: Decimal, b: Decimal) -> float:
    return float(a / b) if b != 0 else NAN


def _bool(x: bool) -> float:
    return 1.0 if x else 0.0


class Snapshot:
    """The prepared snapshot loaded into memory, indexed by shared tokens."""

    def __init__(self, path: Path, cutoff: date) -> None:
        self.cutoff = cutoff
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row

        def rows(table: str) -> list[dict[str, str]]:
            return [dict(r) for r in con.execute(f"SELECT * FROM {table}")]  # noqa: S608

        self.payments = {r["payment_id"]: r for r in rows("payments")}
        self.invoices = {r["invoice_id"]: r for r in rows("invoices")}
        self.bank = {r["bank_transaction_id"]: r for r in rows("bank_transactions")}
        self.pay_by_ref: dict[str, list[dict[str, str]]] = defaultdict(list)
        for p in self.payments.values():
            self.pay_by_ref[p["reference_number"]].append(p)
        self.alloc_by_pay: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.alloc_by_inv: dict[str, list[dict[str, str]]] = defaultdict(list)
        for a in rows("payment_allocations"):
            self.alloc_by_pay[a["payment_id"]].append(a)
            self.alloc_by_inv[a["invoice_id"]].append(a)
        self.bank_by_ref: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.unref_by_cpty: dict[str, list[dict[str, str]]] = defaultdict(list)
        for b in self.bank.values():
            self.bank_by_ref[b["payment_reference"] or ""].append(b)
        for b in self.bank.values():
            ref = b["payment_reference"]
            if b["direction"] == "debit" and b["transaction_type"] != "bank fee":
                if not ref or ref not in self.pay_by_ref:
                    self.unref_by_cpty[b["counterparty_token"]].append(b)
        self.gl_by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.gl_by_journal: dict[str, list[dict[str, str]]] = defaultdict(list)
        for g in rows("gl_entries"):
            self.gl_by_source[g["source_transaction_id"]].append(g)
            self.gl_by_journal[g["journal_id"]].append(g)
        self.approvals: dict[str, list[dict[str, str]]] = defaultdict(list)
        for e in rows("approval_events"):
            self.approvals[e["invoice_id"]].append(e)
        self.twins_key: dict[tuple[str, str, str, str], int] = defaultdict(int)
        for p in self.payments.values():
            self.twins_key[_twin_key(p)] += 1
        con.close()

    # ---- retrieval helpers shared with evidence.py
    def principal(self, ref: str) -> list[dict[str, str]]:
        return [
            b
            for b in self.bank_by_ref.get(ref, [])
            if b["transaction_type"] != "bank fee"
        ]

    def posted_debits(self, ref: str) -> list[dict[str, str]]:
        return [
            b
            for b in self.principal(ref)
            if b["direction"] == "debit" and b["status"] == "posted"
        ]

    def balanced_journals(self, source: str, kind: str) -> list[str]:
        by: dict[str, list[dict[str, str]]] = defaultdict(list)
        for ln in self.gl_by_source.get(source, []):
            if ln["transaction_type"] == kind:
                by[ln["journal_id"]].append(ln)
        out = []
        for jid, ls in sorted(by.items()):
            if len({x["currency"] for x in ls}) != 1:
                continue
            debit = sum((Decimal(x["debit"]) for x in ls), Decimal(0))
            credit = sum((Decimal(x["credit"]) for x in ls), Decimal(0))
            if debit == credit and debit > 0:
                out.append(jid)
        return out

    def a3_candidates(self, pay: dict[str, str]) -> list[dict[str, str]]:
        """Contract A3: unreferenced non-fee debits, equal amount and currency,
        counterparty equal to the payment's bank account."""
        amount = Decimal(pay["payment_amount"])
        return [
            b
            for b in self.unref_by_cpty.get(pay["bank_account_id"], [])
            if Decimal(b["amount"]) == amount
            and b["currency"] == pay["payment_currency"]
        ]


def _twin_key(p: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        p["vendor_id"],
        p["payment_amount"],
        p["payment_date"],
        p["payment_currency"],
    )


def effective(p: dict[str, str]) -> bool:
    return p["payment_status"] == "completed" and p["settlement_status"] == "settled"


@dataclass(frozen=True)
class Focus:
    """Rows a request resolves to (all may be absent)."""

    entity_table: str
    pay: dict[str, str] | None
    inv: dict[str, str] | None
    bank: dict[str, str] | None
    journal: list[dict[str, str]] | None


def resolve(s: Snapshot, table: str, record_id: str) -> Focus:
    pay = inv = bank = journal = None
    if table == "payments":
        pay = s.payments.get(record_id)
    elif table == "invoices":
        inv = s.invoices.get(record_id)
    elif table == "bank_transactions":
        bank = s.bank.get(record_id)
        if bank and bank["payment_reference"]:
            cands = s.pay_by_ref.get(bank["payment_reference"], [])
            pay = cands[0] if len(cands) == 1 else None
    elif table == "gl_entries":
        journal = s.gl_by_journal.get(record_id)
        src = journal[0]["source_transaction_id"] if journal else ""
        pay = s.payments.get(src)
        bank = s.bank.get(src)
        inv = s.invoices.get(src)
        if bank and not pay and bank["payment_reference"]:
            cands = s.pay_by_ref.get(bank["payment_reference"], [])
            pay = cands[0] if len(cands) == 1 else None
    return Focus(table, pay, inv, bank, journal)


def vendor_of(f: Focus) -> str:
    for r in (f.pay, f.inv):
        if r:
            return r["vendor_id"]
    return "none"


def extract(s: Snapshot, table: str, record_id: str) -> dict[str, float]:
    f = resolve(s, table, record_id)
    x: dict[str, float] = {}
    for t, name in (
        ("payments", "pay"),
        ("invoices", "inv"),
        ("bank_transactions", "bank"),
        ("gl_entries", "gl"),
    ):
        x[f"e_{name}"] = _bool(table == t)
    _payment_features(s, f, x)
    _invoice_features(s, f, x)
    _bank_features(s, f, x)
    _gl_features(s, f, x)
    return x


def _payment_features(s: Snapshot, f: Focus, x: dict[str, float]) -> None:
    pay = f.pay
    names = [
        "p_amount_log10", "p_round", "p_age_bd", "p_age_days", "p_weekday", "p_settled",
        "p_limit", "p_age_over_limit", "p_n_alloc", "p_alloc_ratio", "p_ref_count",
        "p_n_bank", "p_n_posted_debit", "p_n_credit", "p_n_fee_rows", "p_debit_ratio",
        "p_debit_cur_match", "p_lag_bd", "p_lag_spans_weekend", "p_n_a3",
        "p_n_pay_journal",
        "p_pay_journal_ratio", "p_gl_has_reversal", "p_gl_has_bankfee", "p_gl_has_fx",
        "p_n_twins", "p_on_due_date", "p_inv_other_eff_pays", "p_inv_alloc_ratio_total",
        "p_inv_min_approval", "p_n_returned",
    ]  # fmt: skip
    names += [f"p_method_{m}" for m in CLEARING_LIMITS]
    names += [f"p_status_{k}" for k in ("completed", "submitted", "reversed")]
    for n in names:
        x[n] = NAN
    if pay is None:
        return
    amount = Decimal(pay["payment_amount"])
    paid = _d(pay["payment_date"])
    method = pay["payment_method"]
    ref = pay["reference_number"]
    age = business_days(paid, s.cutoff)
    limit = CLEARING_LIMITS.get(method)
    x["p_amount_log10"] = _log10(amount)
    x["p_round"] = _bool(amount % 100 == 0)
    x["p_age_bd"] = float(age)
    x["p_age_days"] = float((s.cutoff - paid).days)
    x["p_weekday"] = float(paid.weekday())
    x["p_settled"] = _bool(pay["settlement_status"] == "settled")
    x["p_limit"] = float(limit) if limit is not None else NAN
    x["p_age_over_limit"] = float(age - limit) if limit is not None else NAN
    for m in CLEARING_LIMITS:
        x[f"p_method_{m}"] = _bool(method == m)
    for k in ("completed", "submitted", "reversed"):
        x[f"p_status_{k}"] = _bool(pay["payment_status"] == k)

    allocs = s.alloc_by_pay.get(pay["payment_id"], [])
    x["p_n_alloc"] = float(len(allocs))
    x["p_alloc_ratio"] = _ratio(
        sum((Decimal(a["allocated_amount"]) for a in allocs), Decimal(0)), amount
    )
    x["p_ref_count"] = float(len(s.pay_by_ref.get(ref, [])))
    principal = s.principal(ref)
    debits = s.posted_debits(ref)
    x["p_n_bank"] = float(len(principal))
    x["p_n_posted_debit"] = float(len(debits))
    x["p_n_credit"] = float(sum(b["direction"] == "credit" for b in principal))
    x["p_n_returned"] = float(sum(b["status"] == "returned" for b in principal))
    x["p_n_fee_rows"] = float(len(s.bank_by_ref.get(ref, [])) - len(principal))
    x["p_n_a3"] = float(len(s.a3_candidates(pay)))
    if len(debits) == 1:
        d = debits[0]
        x["p_debit_cur_match"] = _bool(d["currency"] == pay["payment_currency"])
        if d["currency"] == pay["payment_currency"]:
            x["p_debit_ratio"] = _ratio(Decimal(d["amount"]), amount)
        lag = business_days(paid, _d(d["posted_date"]))
        x["p_lag_bd"] = float(lag)
        x["p_lag_spans_weekend"] = _bool((_d(d["posted_date"]) - paid).days > lag)
    pj = s.balanced_journals(pay["payment_id"], "payment")
    x["p_n_pay_journal"] = float(len(pj))
    lines = [
        g
        for g in s.gl_by_source.get(pay["payment_id"], [])
        if g["transaction_type"] == "payment"
    ]
    if lines:
        x["p_pay_journal_ratio"] = _ratio(
            sum((Decimal(g["debit"]) for g in lines), Decimal(0)), amount
        )
    src_ids = [pay["payment_id"], *[d["bank_transaction_id"] for d in debits]]
    types = {g["transaction_type"] for i in src_ids for g in s.gl_by_source.get(i, [])}
    x["p_gl_has_reversal"] = _bool("payment_reversal" in types)
    x["p_gl_has_bankfee"] = _bool("bank_fee" in types)
    x["p_gl_has_fx"] = _bool("fx_settlement" in types)
    x["p_n_twins"] = float(s.twins_key[_twin_key(pay)] - 1)

    invs = [
        s.invoices[a["invoice_id"]] for a in allocs if a["invoice_id"] in s.invoices
    ]
    if invs:
        x["p_on_due_date"] = _bool(
            any(i["due_date"][:10] == pay["payment_date"][:10] for i in invs)
        )
        other = 0
        for i in invs:
            for a in s.alloc_by_inv.get(i["invoice_id"], []):
                q = s.payments.get(a["payment_id"])
                if q and q["payment_id"] != pay["payment_id"] and effective(q):
                    other += 1
        x["p_inv_other_eff_pays"] = float(other)
        total = sum((Decimal(i["invoice_total"]) for i in invs), Decimal(0))
        invalloc = sum(
            (
                Decimal(a["allocated_amount"])
                for i in invs
                for a in s.alloc_by_inv[i["invoice_id"]]
            ),
            Decimal(0),
        )
        x["p_inv_alloc_ratio_total"] = _ratio(invalloc, total)
        x["p_inv_min_approval"] = float(
            min(len(s.approvals.get(i["invoice_id"], [])) for i in invs)
        )


def _invoice_features(s: Snapshot, f: Focus, x: dict[str, float]) -> None:
    inv = f.inv
    names = [
        "i_total_log10", "i_age_days", "i_n_alloc", "i_alloc_ratio", "i_n_pay",
        "i_n_eff_pay",
        "i_n_committed", "i_pay_gap_days", "i_on_due_date", "i_n_approval", "i_has_po",
    ]  # fmt: skip
    names += [
        f"i_status_{k}"
        for k in ("paid", "approved", "canceled", "credit_applied", "received")
    ]
    for n in names:
        x[n] = NAN
    if inv is None:
        return
    total = Decimal(inv["invoice_total"])
    x["i_total_log10"] = _log10(total)
    x["i_age_days"] = float((s.cutoff - _d(inv["invoice_date"])).days)
    for k in ("paid", "approved", "canceled", "credit_applied", "received"):
        x[f"i_status_{k}"] = _bool(inv["status"] == k)
    x["i_n_approval"] = float(len(s.approvals.get(inv["invoice_id"], [])))
    x["i_has_po"] = _bool(bool(inv["po_id"]))
    allocs = s.alloc_by_inv.get(inv["invoice_id"], [])
    pays = [
        s.payments[a["payment_id"]] for a in allocs if a["payment_id"] in s.payments
    ]
    x["i_n_alloc"] = float(len(allocs))
    x["i_alloc_ratio"] = _ratio(
        sum((Decimal(a["allocated_amount"]) for a in allocs), Decimal(0)), total
    )
    x["i_n_pay"] = float(len({p["payment_id"] for p in pays}))
    eff = [p for p in pays if effective(p)]
    x["i_n_eff_pay"] = float(len(eff))
    x["i_n_committed"] = float(
        sum(
            1
            for p in eff
            if p["payment_currency"] == inv["currency"]
            and s.posted_debits(p["reference_number"])
        )
    )
    if len(pays) > 1:
        dates = sorted(_d(p["payment_date"]) for p in pays)
        x["i_pay_gap_days"] = float((dates[-1] - dates[0]).days)
    if pays:
        x["i_on_due_date"] = _bool(
            any(p["payment_date"][:10] == inv["due_date"][:10] for p in pays)
        )


def _bank_features(s: Snapshot, f: Focus, x: dict[str, float]) -> None:
    for n in (
        "b_is_fee",
        "b_amount_log10",
        "b_is_credit",
        "b_posted",
        "b_ref_pay_count",
        "b_fee_journal",
    ):
        x[n] = NAN
    b = f.bank
    if b is None:
        return
    x["b_is_fee"] = _bool(b["transaction_type"] == "bank fee")
    x["b_amount_log10"] = _log10(Decimal(b["amount"]))
    x["b_is_credit"] = _bool(b["direction"] == "credit")
    x["b_posted"] = _bool(b["status"] == "posted")
    x["b_ref_pay_count"] = float(
        len(s.pay_by_ref.get(b["payment_reference"] or "", []))
    )
    x["b_fee_journal"] = float(
        len(s.balanced_journals(b["bank_transaction_id"], "bank_fee"))
    )


def _gl_features(s: Snapshot, f: Focus, x: dict[str, float]) -> None:
    kinds = (
        "payment",
        "payment_reversal",
        "bank_fee",
        "fx_settlement",
        "invoice",
        "credit_memo",
        "invoice_cancellation",
    )
    for k in kinds:
        x[f"g_type_{k}"] = NAN
    x["g_n_lines"] = NAN
    x["g_imbalance"] = NAN
    x["g_fx"] = NAN
    x["g_src_payment"] = x["g_src_bank"] = x["g_src_invoice"] = NAN
    if not f.journal:
        return
    t = f.journal[0]["transaction_type"]
    for k in kinds:
        x[f"g_type_{k}"] = _bool(t == k)
    x["g_fx"] = _bool(t == "fx_settlement")
    x["g_n_lines"] = float(len(f.journal))
    debit = sum((Decimal(g["debit"]) for g in f.journal), Decimal(0))
    credit = sum((Decimal(g["credit"]) for g in f.journal), Decimal(0))
    x["g_imbalance"] = float(abs(debit - credit))
    x["g_src_payment"] = _bool(f.pay is not None and f.bank is None)
    x["g_src_bank"] = _bool(f.bank is not None)
    x["g_src_invoice"] = _bool(f.inv is not None)


def feature_names(sample: dict[str, float]) -> list[str]:
    return sorted(sample)


def group_columns(names: list[str], prefixes: tuple[str, ...]) -> list[str]:
    return [n for n in names if any(n.startswith(p) for p in prefixes)]
