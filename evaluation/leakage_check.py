"""Leakage test on the prepared FinRCA snapshot (PLAN.md M1; docs/leakage-test.md).

Evaluation-side only: it holds the token key and reads train/validation labels,
so it must never be imported by src/reconcile/. Standard library only.

It prepares a snapshot with a throwaway key kept outside the prepared directory,
then asks what an observer could recover from ALLOWED snapshot fields alone:
  1. inserted payments / invoices / bank rows / GL lines (data-audit L12, L2),
  2. raw ID or file order (row order, token order, column-vs-order correlation),
  3. the family of a case (F06 / F12 / F14 / payment-related NO_FAILURE),
  4. the declared residuals (FX codes in gl_entries; approval_events presence).
The key is used only to attach labels (raw ID -> token); every feature comes
from the snapshot.

Usage:
    uv run python evaluation/leakage_check.py --upstream PATH_TO_FinRCA-AI-Bench

Only train/ and validation/ rca_ground_truth.jsonl are opened.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from itertools import pairwise
from math import sqrt
from pathlib import Path
from typing import Any

from reconcile.adapters.finrca import _Tokens, prepare_finrca_snapshot

CUTOFF = date(2026, 6, 30)
REVISION = "76c93ec32536e29826e2ab9e68b58e0351410bc7"
SPLITS = ("train", "validation")  # the only label directories this script opens
PAYMENT_LEGIT = {
    "pending_settlement", "weekend_clearing", "check_clearing", "due_date_payment",
    "split_payment", "batch_payment", "payment_reversal", "net_fee_accounting",
    "accounted_bank_fee", "fx_settlement",
}  # fmt: skip
FAMILY = {"F06": "F06", "F12": "F12", "F14": "F14"}
INSERTED_PAY_REF = 6042  # data-audit L12: references above this are inserted
INSERTED_INV_NUM = 428000
FX_TYPE, FX_ACCOUNT = "fx_settlement", "799100-FX-SETTLEMENT"
NULL_B = 200  # label permutations for the null threshold
Row = dict[str, str | None]
Features = dict[str, str]

# Unique row key per table: (column, token namespace). Used to join raw rows to
# snapshot rows for the order test.
ORDER_KEYS = {
    "vendors": [("vendor_id", "vendor")],
    "purchase_orders": [("po_id", "po")],
    "po_lines": [("po_line_id", "po_line")],
    "invoices": [("invoice_id", "invoice")],
    "invoice_lines": [("invoice_line_id", "invoice_line")],
    "payments": [("payment_id", "payment")],
    "payment_allocations": [("payment_id", "payment"), ("invoice_id", "invoice")],
    "bank_transactions": [("bank_transaction_id", "bank_txn")],
    "bank_statements": [("bank_statement_id", "statement")],
    "vendor_change_log": [("change_id", "change")],
    "approval_events": [("approval_event_id", "approval")],
    "gl_entries": [("journal_line_id", "journal_line")],
}  # fmt: skip


# ------------------------------------------------------------------ models --
def bal_acc(y: Sequence[str], p: Sequence[str]) -> float:
    c, tot = Counter(zip(y, p, strict=True)), Counter(y)
    return sum(c[(k, k)] / n for k, n in tot.items()) / len(tot)


def fit_field(xs: Sequence[str], y: Sequence[str]) -> Callable[[str], str]:
    """Per-value class with the highest class-normalised share (balanced)."""
    tot = Counter(y)
    cnt: dict[str, Counter[str]] = defaultdict(Counter)
    for x, c in zip(xs, y, strict=True):
        cnt[x][c] += 1
    best = {x: max(cc, key=lambda k: cc[k] / tot[k]) for x, cc in cnt.items()}
    default = tot.most_common(1)[0][0]
    return lambda x: best.get(x, default)


def fit_tree(X: list[Features], y: list[str], depth: int = 3) -> Any:
    """Greedy class-balanced Gini tree on `feature == value` splits."""
    tot = Counter(y)
    w = {c: 1 / n for c, n in tot.items()}

    def imp(cnt: Counter[str]) -> float:
        s = sum(cnt[c] * w[c] for c in cnt)
        return 0.0 if s == 0 else s * (1 - sum((cnt[c] * w[c] / s) ** 2 for c in cnt))

    def build(idx: list[int], d: int) -> Any:
        cnt = Counter(y[i] for i in idx)
        leaf = max(cnt, key=lambda c: cnt[c] * w[c])
        if d == 0 or len(cnt) == 1 or len(idx) < 10:
            return leaf
        best, base = None, imp(cnt)
        for f in X[0]:
            by: dict[str, Counter[str]] = defaultdict(Counter)
            for i in idx:
                by[X[i][f]][y[i]] += 1
            for v, left in by.items():
                nl = sum(left.values())
                if nl < 5 or len(idx) - nl < 5:
                    continue
                score = imp(left) + imp(cnt - left)
                if score < base - 1e-9 and (best is None or score < best[0]):
                    best = (score, f, v)
        if best is None:
            return leaf
        _, f, v = best
        yes = [i for i in idx if X[i][f] == v]
        no = [i for i in idx if X[i][f] != v]
        return (f, v, build(yes, d - 1), build(no, d - 1))

    return build(list(range(len(y))), depth)


def tree_predict(t: Any, x: Features) -> str:
    while isinstance(t, tuple):
        t = t[2] if x[t[0]] == t[1] else t[3]
    return str(t)


def tree_text(t: Any, ind: str = "") -> str:
    if not isinstance(t, tuple):
        return f"{ind}-> {t}\n"
    f, v, a, b = t
    sub = ind + "  "
    return f"{ind}{f} == {v}\n{tree_text(a, sub)}{ind}else\n{tree_text(b, sub)}"


def field_preds(
    Xtr: list[Features], ytr: list[str], Xte: list[Features]
) -> dict[str, list[str]]:
    out = {}
    for f in Xtr[0]:
        predict = fit_field([x[f] for x in Xtr], ytr)
        out[f] = [predict(x[f]) for x in Xte]
    return out


def null_threshold(preds: dict[str, list[str]], y: list[str], seed: int = 0) -> float:
    """95th percentile of the best single-field balanced accuracy under shuffled
    labels: a family-wise threshold, so choosing the best of many fields is paid for."""
    rng, ys, best = random.Random(seed), list(y), []
    for _ in range(NULL_B):
        rng.shuffle(ys)
        best.append(max(bal_acc(ys, p) for p in preds.values()))
    return sorted(best)[int(0.95 * NULL_B)]


def cv_eval(
    X: list[Features], y: list[str], k: int = 5
) -> tuple[dict[str, list[str]], list[str]]:
    """k-fold CV predictions for every single field and for a depth-3 tree."""
    order = list(range(len(y)))
    random.Random(0).shuffle(order)
    fold = {i: n % k for n, i in enumerate(order)}
    sp: dict[str, list[str]] = {f: [""] * len(y) for f in X[0]}
    tp = [""] * len(y)
    for j in range(k):
        tr = [i for i in range(len(y)) if fold[i] != j]
        te = [i for i in range(len(y)) if fold[i] == j]
        got = field_preds([X[i] for i in tr], [y[i] for i in tr], [X[i] for i in te])
        t = fit_tree([X[i] for i in tr], [y[i] for i in tr])
        for f, ps in got.items():
            for i, p in zip(te, ps, strict=True):
                sp[f][i] = p
        for i in te:
            tp[i] = tree_predict(t, X[i])
    return sp, tp


# ---------------------------------------------------------------- features --
def mag(s: str | None) -> str:
    return "NA" if s is None else f"e{Decimal(s).adjusted()}"


def rnd(s: str | None) -> str:
    return "NA" if s is None else str(Decimal(s) == Decimal(s).to_integral_value())


def wd(s: str | None) -> str:
    return "NA" if not s else f"wd{date.fromisoformat(s[:10]).weekday()}"


def age(s: str | None) -> str:
    if not s:
        return "NA"
    d = (CUTOFF - date.fromisoformat(s[:10])).days
    return next(f"age<={b}" for b in (0, 1, 2, 5, 10, 30, 90, 365, 10**6) if d <= b)


def gap(a: str | None, b: str | None) -> str:
    if not a or not b:
        return "NA"
    d = (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    return next(f"d<={x}" for x in (-1, 0, 1, 2, 3, 5, 7, 14, 30, 60, 10**6) if d <= x)


def total(rows: list[Row], col: str) -> Decimal:
    return sum((Decimal(r[col] or 0) for r in rows), Decimal(0))


def cmp(a: Decimal, b: Decimal) -> str:
    return "eq" if a == b else ("lt" if a < b else "gt")


class Snap:
    """The prepared snapshot, indexed for feature extraction. Reads only the db."""

    def __init__(self, path: Path) -> None:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.t: dict[str, list[Row]] = {}
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            cur = con.execute(f'SELECT * FROM "{name}" ORDER BY rowid')
            cols = [d[0] for d in cur.description]
            self.t[name] = [dict(zip(cols, r, strict=True)) for r in cur]
        self.pay = {r["payment_id"]: r for r in self.t["payments"]}
        self.inv = {r["invoice_id"]: r for r in self.t["invoices"]}
        self.vend = {r["vendor_id"]: r for r in self.t["vendors"]}
        self.bank = {r["bank_transaction_id"]: r for r in self.t["bank_transactions"]}
        self.by_ref = self._group("bank_transactions", "payment_reference")
        self.alloc_pay = self._group("payment_allocations", "payment_id")
        self.alloc_inv = self._group("payment_allocations", "invoice_id")
        self.gl = self._group("gl_entries", "source_transaction_id")
        self.appr = self._group("approval_events", "invoice_id")

    def _group(self, table: str, col: str) -> dict[str | None, list[Row]]:
        g: dict[str | None, list[Row]] = defaultdict(list)
        for r in self.t[table]:
            g[r[col]].append(r)
        return g

    def vendor(self, vid: str | None) -> Features:
        v = self.vend.get(vid, {})
        out = {
            f"own.v_{c}": str(v.get(c, "NA"))
            for c in ("vendor_type", "country", "currency", "payment_terms",
                      "default_payment_method", "vendor_status")
        }  # fmt: skip
        out["own.v_created"] = str(v.get("created_at") or "NA")[:4]
        return out

    def payment_own(self, p: Row) -> Features:
        ca = p["created_at"] or ""
        f = {
            "own.pay_method": str(p["payment_method"]),
            "own.pay_currency": str(p["payment_currency"]),
            "own.pay_status": str(p["payment_status"]),
            "own.pay_settlement": str(p["settlement_status"]),
            "own.pay_weekday": wd(p["payment_date"]),
            "own.pay_month": str(p["payment_date"])[:7],
            "own.pay_age": age(p["payment_date"]),
            "own.pay_created_hour": ca[11:13],
            "own.pay_created_is_date": str(ca[:10] == p["payment_date"]),
            "own.pay_amount_mag": mag(p["payment_amount"]),
            "own.pay_amount_round": rnd(p["payment_amount"]),
            "own.pay_has_bank_acct": str(p["bank_account_id"] is not None),
        }
        return f | self.vendor(p["vendor_id"])

    def invoice_own(self, i: Row) -> Features:
        f = {
            "own.inv_status": str(i["status"]),
            "own.inv_terms": str(i["payment_terms"]),
            "own.inv_currency": str(i["currency"]),
            "own.inv_weekday": wd(i["invoice_date"]),
            "own.inv_month": str(i["invoice_date"])[:7],
            "own.inv_age": age(i["invoice_date"]),
            "own.inv_due_gap": gap(i["invoice_date"], i["due_date"]),
            "own.inv_received_gap": gap(i["invoice_date"], i["received_date"]),
            "own.inv_total_mag": mag(i["invoice_total"]),
            "own.inv_total_round": rnd(i["invoice_total"]),
            "own.inv_has_po": str(i["po_id"] is not None),
            "own.inv_tax_zero": str(Decimal(i["tax"] or 0) == 0),
            "own.inv_shipping_zero": str(Decimal(i["shipping"] or 0) == 0),
        }
        return f | self.vendor(i["vendor_id"])

    def bank_own(self, b: Row) -> Features:
        return {
            "own.bank_type": str(b["transaction_type"]),
            "own.bank_status": str(b["status"]),
            "own.bank_direction": str(b["direction"]),
            "own.bank_currency": str(b["currency"]),
            "own.bank_account": str(b["bank_account_id"]),
            "own.bank_lag": gap(b["transaction_date"], b["posted_date"]),
            "own.bank_weekday": wd(b["transaction_date"]),
            "own.bank_amount_mag": mag(b["amount"]),
            "own.bank_amount_round": rnd(b["amount"]),
        }

    def gl_codes(self, ids: Sequence[str | None], prefix: str) -> Features:
        lines = [g for i in ids for g in self.gl.get(i, [])]

        def joined(col: str) -> str:
            return "+".join(sorted({str(g[col]) for g in lines})) or "none"

        return {
            f"rel.{prefix}_gl_types": joined("transaction_type"),
            f"rel.{prefix}_gl_accounts": joined("gl_account"),
            f"rel.{prefix}_gl_n": str(len(lines)),
        }

    def payment_rel(self, p: Row) -> Features:
        """Observable evidence about one payment: allocations, bank rows, GL."""
        al = self.alloc_pay.get(p["payment_id"], [])
        banks = self.by_ref.get(p["reference_number"], [])
        amt = Decimal(p["payment_amount"] or 0)
        main = max(banks, key=lambda b: Decimal(b["amount"] or 0), default=None)
        f = {
            "rel.pay_n_alloc": str(len(al)),
            "rel.pay_alloc_vs_amount": cmp(total(al, "allocated_amount"), amt)
            if al
            else "NA",
            "rel.pay_n_bank": str(len(banks)),
        }
        if main:
            f |= {
                "rel.pay_bank_vs_amount": cmp(Decimal(main["amount"] or 0), amt),
                "rel.pay_bank_status": str(main["status"]),
                "rel.pay_bank_type": str(main["transaction_type"]),
                "rel.pay_bank_lag": gap(p["payment_date"], main["posted_date"]),
                "rel.pay_bank_cur_eq": str(main["currency"] == p["payment_currency"]),
            }
        else:
            for k in ("vs_amount", "status", "type", "lag", "cur_eq"):
                f[f"rel.pay_bank_{k}"] = "NA"
        ids = [p["payment_id"], *(b["bank_transaction_id"] for b in banks)]
        return f | self.gl_codes(ids, "pay")

    def invoice_rel(self, i: Row) -> Features:
        al = self.alloc_inv.get(i["invoice_id"], [])
        pays = [self.pay[a["payment_id"]] for a in al if a["payment_id"] in self.pay]
        tot = Decimal(i["invoice_total"] or 0)
        banked = sum(bool(self.by_ref.get(p["reference_number"])) for p in pays)
        statuses = {str(p["payment_status"]) for p in pays}
        pay_gap = "NA"
        if len(pays) > 1:
            pay_gap = gap(pays[0]["payment_date"], pays[1]["payment_date"])
        f = {
            "rel.inv_n_alloc": str(len(al)),
            "rel.inv_n_payments": str(len({p["payment_id"] for p in pays})),
            "rel.inv_alloc_vs_total": cmp(total(al, "allocated_amount"), tot)
            if al
            else "NA",
            "rel.inv_pay_gap": pay_gap,
            "rel.inv_pay_statuses": "+".join(sorted(statuses)) or "NA",
            "rel.inv_pays_with_bank": str(banked),
            "rel.inv_n_approval": str(len(self.appr.get(i["invoice_id"], []))),
        }
        ids = [i["invoice_id"]] + [p["payment_id"] for p in pays]
        ids += [
            b["bank_transaction_id"]
            for p in pays
            for b in self.by_ref.get(p["reference_number"], [])
        ]
        return f | self.gl_codes(ids, "inv")

    def entity(self, typ: str, eid: str) -> Features | None:
        """Own-row ('own.') and evidence ('rel.') features of a primary entity."""
        if typ == "payment" and eid in self.pay:
            p = self.pay[eid]
            f = self.payment_own(p) | self.payment_rel(p)
        elif typ == "invoice" and eid in self.inv:
            f = self.invoice_own(self.inv[eid]) | self.invoice_rel(self.inv[eid])
        elif typ == "bank_transaction" and eid in self.bank:
            b = self.bank[eid]
            f = self.bank_own(b)
            pay = next(
                (
                    p
                    for p in self.pay.values()
                    if p["reference_number"] == b["payment_reference"]
                ),
                None,
            )
            f |= self.payment_rel(pay) if pay else {}
        else:
            return None
        return f


def fill(rows: list[Features]) -> list[Features]:
    keys = sorted({k for r in rows for k in r})
    return [{k: r.get(k, "NA") for k in keys} for r in rows]


# ------------------------------------------------------------------ report --
def table(rows: list[tuple[Any, ...]], head: tuple[str, ...]) -> None:
    print("| " + " | ".join(head) + " |")
    print("|" + "---|" * len(head))
    for r in rows:
        print("| " + " | ".join(str(c) for c in r) + " |")
    print()


def binary_task(name: str, X: list[Features], pos: list[bool]) -> None:
    y = ["inserted" if p else "organic" for p in pos]
    n_pos = sum(pos)
    print(
        f"### {name}: {n_pos} inserted of {len(y)} "
        f"(base rate {n_pos / len(y):.4f}; chance balanced accuracy 0.500)\n"
    )
    sp, tp = cv_eval(X, y)
    thr = null_threshold(sp, y)
    rows = []
    for f, p in sorted(sp.items(), key=lambda kv: -bal_acc(y, kv[1])):
        tpos = sum(1 for a, b in zip(y, p, strict=True) if a == b == "inserted")
        fpos = sum(
            1 for a, b in zip(y, p, strict=True) if a == "organic" and b == "inserted"
        )
        ba = bal_acc(y, p)
        prec = tpos / (tpos + fpos) if tpos + fpos else 0.0
        rows.append(
            (
                f,
                f"{ba:.3f}",
                f"{tpos}/{n_pos}",
                f"{fpos}/{len(y) - n_pos}",
                f"{prec:.3f}",
                "FLAG" if ba > thr else "",
            )
        )
    table(
        rows,
        (
            "field",
            "CV balanced acc",
            "inserted found",
            "organic flagged",
            "precision",
            f"> null95 {thr:.3f}",
        ),
    )
    print(f"Shallow tree (depth 3, all fields), 5-fold CV: {bal_acc(y, tp):.3f}\n")


def spearman(a: Sequence[Any], b: Sequence[Any]) -> float:
    def ranks(v: Sequence[Any]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r, i = [0.0] * len(v), 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r

    ra, rb = ranks(a), ranks(b)
    n, ma, mb = len(ra), sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return cov / sqrt(va * vb) if va and vb and n > 2 else 0.0


def concordant(a: Sequence[Any], b: Sequence[Any], pairs: int = 100_000) -> float:
    rng, ok, tot = random.Random(1), 0, 0
    for _ in range(pairs):
        i, j = rng.sample(range(len(a)), 2)
        d = (a[i] > a[j]) - (a[i] < a[j])
        e = (b[i] > b[j]) - (b[i] < b[j])
        if d and e:
            tot += 1
            ok += d == e
    return ok / tot


def order_test(snap: Snap, tokens: _Tokens, full: Path) -> None:
    """Row order / token order / attribute order against raw file order."""
    date_re = re.compile(r"\d{4}-\d\d-\d\d")
    rows, attr_rows = [], []
    for name, key in ORDER_KEYS.items():
        with (full / f"{name}.csv").open(newline="", encoding="utf-8") as f:
            raw = list(csv.DictReader(f))
        ids = [r[key[0][0]] for r in raw]
        sorted_in_file = (
            all(a <= b for a, b in pairwise(ids)) if len(key) == 1 else None
        )
        pos = {
            tuple(tokens.token(ns, r[c]) for c, ns in key): n for n, r in enumerate(raw)
        }
        snap_rows = [
            (rid, r)
            for rid, r in enumerate(snap.t[name])
            if tuple(str(r[c]) for c, _ in key) in pos
        ]
        rawpos = [pos[tuple(str(r[c]) for c, _ in key)] for _, r in snap_rows]
        rowid = [rid for rid, _ in snap_rows]
        n = len(rowid)
        thr = 4 / sqrt(n)
        rho_row = spearman(rowid, rawpos)
        tok = [r[key[0][0]] for _, r in snap_rows] if len(key) == 1 else None
        rho_tok = spearman(tok, rawpos) if tok else None
        conc = concordant(rowid, rawpos)
        flag = abs(rho_row) > thr or (rho_tok is not None and abs(rho_tok) > thr)
        rows.append(
            (
                name,
                n,
                sorted_in_file,
                f"{rho_row:+.3f}",
                "-" if rho_tok is None else f"{rho_tok:+.3f}",
                f"{conc:.3f}",
                f"{thr:.3f}",
                "FLAG" if flag else "",
            )
        )
        for c in snap.t[name][0]:
            vals = [r[c] for _, r in snap_rows]
            if None in vals or c in {k for k, _ in key}:
                continue
            if all(date_re.match(str(v)) for v in vals):
                keyed: list[Any] = [str(v) for v in vals]
            else:
                try:
                    keyed = [Decimal(str(v)) for v in vals]
                except ArithmeticError:
                    continue
            if len(set(keyed)) < 20:
                continue
            rho = spearman(keyed, rawpos)
            attr_rows.append(
                (
                    name,
                    c,
                    n,
                    f"{rho:+.3f}",
                    f"{thr:.3f}",
                    "FLAG" if abs(rho) > thr else "",
                )
            )
    print("### Row order and token order against raw file/ID order\n")
    table(
        rows,
        (
            "table",
            "rows",
            "raw file ascending by ID",
            "Spearman rowid",
            "Spearman token",
            "concordant pairs (rowid)",
            "4/sqrt(n)",
            "",
        ),
    )
    print("### Attribute columns against raw order (20+ distinct values)\n")
    table(
        sorted(attr_rows, key=lambda r: -abs(float(r[3]))),
        ("table", "column", "rows", "Spearman", "4/sqrt(n)", ""),
    )


def inserted_tests(snap: Snap, tokens: _Tokens, full: Path) -> None:
    def read(name: str) -> list[dict[str, str]]:
        with (full / f"{name}.csv").open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    pay_ins = {
        tokens.token("payment", r["payment_id"]): int(
            r["reference_number"].rsplit("-", 1)[1]
        )
        > INSERTED_PAY_REF
        for r in read("payments")
    }
    ref_ins = {
        tokens.token("payment_ref", r["reference_number"]): pay_ins[
            tokens.token("payment", r["payment_id"])
        ]
        for r in read("payments")
    }
    inv_ins = {
        tokens.token("invoice", r["invoice_id"]): int(r["invoice_id"].split("_")[1])
        > INSERTED_INV_NUM
        for r in read("invoices")
    }

    ps = [p for p in snap.t["payments"] if p["payment_id"] in pay_ins]
    X = fill([snap.payment_own(p) | snap.payment_rel(p) for p in ps])
    binary_task(
        "Payments (inserted = raw reference above 6042)",
        X,
        [pay_ins[str(p["payment_id"])] for p in ps],
    )

    own_p = [{k: v for k, v in x.items() if k.startswith("own.")} for x in X]
    binary_task(
        "Payments, own row only", own_p, [pay_ins[str(p["payment_id"])] for p in ps]
    )

    # Subgroup: the inserted payments that have an allocation (the F06 duplicates)
    # against organic payments; inserted payments with no allocation are dropped.
    keep = [
        n
        for n, p in enumerate(ps)
        if not pay_ins[str(p["payment_id"])] or X[n]["rel.pay_n_alloc"] != "0"
    ]
    sub = [pay_ins[str(ps[n]["payment_id"])] for n in keep]
    binary_task(
        "Payments, inserted with allocation, all fields", [X[n] for n in keep], sub
    )
    binary_task(
        "Payments, inserted with allocation, own row only",
        [own_p[n] for n in keep],
        sub,
    )

    iv = [i for i in snap.t["invoices"] if i["invoice_id"] in inv_ins]
    X = fill([snap.invoice_own(i) | snap.invoice_rel(i) for i in iv])
    binary_task(
        "Invoices (inserted = raw ID above INV_0428000)",
        X,
        [inv_ins[str(i["invoice_id"])] for i in iv],
    )
    own = [{k: v for k, v in x.items() if k.startswith("own.")} for x in X]
    binary_task(
        "Invoices, own row only", own, [inv_ins[str(i["invoice_id"])] for i in iv]
    )

    bs = [b for b in snap.t["bank_transactions"] if b["payment_reference"] in ref_ins]
    X = fill([snap.bank_own(b) for b in bs])
    binary_task(
        "Bank rows with a payment reference (inserted = inserted payment's)",
        X,
        [ref_ins[str(b["payment_reference"])] for b in bs],
    )

    gs = [g for g in snap.t["gl_entries"] if g["source_transaction_id"] in pay_ins]
    X = [
        {
            "gl_type": str(g["transaction_type"]),
            "gl_account": str(g["gl_account"]),
            "gl_period_is_posting_month": str(
                str(g["posting_date"])[:7] == g["accounting_period"]
            ),
            "gl_currency": str(g["currency"]),
            "gl_side": str(Decimal(g["debit"] or 0) > 0),
            "gl_weekday": wd(g["posting_date"]),
            "gl_age": age(g["posting_date"]),
        }
        for g in gs
    ]
    binary_task(
        "GL lines sourced to payments (inserted = payment inserted)",
        X,
        [pay_ins[str(g["source_transaction_id"])] for g in gs],
    )


def load_cases(upstream: Path, tokens: _Tokens) -> list[tuple[str, ...]]:
    """(split, class, variant, entity type, raw id, token) of in-scope dev cases."""
    out: list[tuple[str, ...]] = []
    for split in SPLITS:
        with (
            upstream / "data/benchmark" / split / "rca_ground_truth.jsonl"
        ).open() as f:
            for line in f:
                d = json.loads(line)
                ft, pe = d["failure_type"], d["primary_entity"]
                if ft[:3] in FAMILY:
                    cls = ft[:3]
                elif (
                    ft == "NO_FAILURE"
                    and d["scenario_variant"].removeprefix("legitimate_")
                    in PAYMENT_LEGIT
                ):
                    cls = "LEGIT"
                else:
                    continue
                out.append(
                    (
                        split,
                        cls,
                        d["scenario_variant"] if cls == "LEGIT" else "",
                        pe["type"],
                        pe["id"],
                        tokens.token(
                            pe["type"].replace("bank_transaction", "bank_txn"), pe["id"]
                        ),
                    )
                )
    return out


def family_tests(snap: Snap, cases: list[Any]) -> list[Any]:
    data: list[Any] = []
    for split, cls, variant, typ, raw, tok in cases:
        f = snap.entity(typ, tok)
        data.append((split, cls, variant, typ, f, raw))
    miss = [d for d in data if d[4] is None]
    data = [d for d in data if d[4] is not None]
    print(f"Cases: {len(data)} in scope ({len(miss)} primary entities not found).\n")
    X_all = fill([{**d[4], "entity_type": d[3]} for d in data])
    tr = [i for i, d in enumerate(data) if d[0] == "train"]
    va = [i for i, d in enumerate(data) if d[0] == "validation"]
    ytr = [data[i][1] for i in tr]
    yva = [data[i][1] for i in va]
    print(
        "Class counts (train / validation): "
        + ", ".join(
            f"{c} {Counter(ytr)[c]}/{Counter(yva)[c]}" for c in sorted(set(ytr))
        )
    )
    major = Counter(yva).most_common(1)[0][0]
    hit = yva.count(major)
    print(
        f"Base rate on validation (best constant, always '{major}'): accuracy "
        f"{hit}/{len(yva)} = {hit / len(yva):.3f}; "
        f"balanced accuracy {1 / len(set(ytr)):.3f}\n"
    )

    def run(label: str, keep: Callable[[str], bool]) -> None:
        feats = [k for k in X_all[0] if keep(k)]
        Xs = [{k: x[k] for k in feats} for x in X_all]
        Xtr, Xva = [Xs[i] for i in tr], [Xs[i] for i in va]
        sp = field_preds(Xtr, ytr, Xva)
        thr = null_threshold(sp, yva)
        t = fit_tree(Xtr, ytr)
        tp = [tree_predict(t, x) for x in Xva]
        print(f"#### {label}\n")
        ok = sum(a == b for a, b in zip(yva, tp, strict=True))
        print(
            f"Tree (depth 3): validation accuracy {ok}/{len(yva)}, balanced "
            f"accuracy {bal_acc(yva, tp):.3f}; null95 of best single field {thr:.3f}\n"
        )
        print("```\n" + tree_text(t) + "```\n")
        rows = [
            (
                f,
                f"{sum(a == b for a, b in zip(yva, p, strict=True))}/{len(yva)}",
                f"{bal_acc(yva, p):.3f}",
                "FLAG" if bal_acc(yva, p) > thr else "",
            )
            for f, p in sorted(sp.items(), key=lambda kv: -bal_acc(yva, kv[1]))
        ]
        table(
            rows[:12],
            ("single field", "val correct", "val balanced acc", f"> null95 {thr:.3f}"),
        )

    run("Set A: entity type alone", lambda k: k == "entity_type")
    run(
        "Set B: own-row and vendor fields of the primary entity (leakage set)",
        lambda k: k.startswith("own.") or k == "entity_type",
    )
    run("Set C: all observable fields, including joined evidence", lambda k: True)
    for typ in ("payment", "invoice"):
        idx = [i for i, d in enumerate(data) if d[3] == typ]
        sub_tr = [i for i in idx if i in set(tr)]
        sub_va = [i for i in idx if i in set(va)]
        y1, y2 = [data[i][1] for i in sub_tr], [data[i][1] for i in sub_va]
        print(
            f"#### Within {typ}-primary cases (train {dict(Counter(y1))}, "
            f"validation {dict(Counter(y2))})\n"
        )
        views: list[tuple[str, Callable[[str], bool]]] = [
            ("own-row only", lambda k: k.startswith("own.")),
            ("all observable", lambda k: k != "entity_type"),
        ]
        for lab, keep in views:
            feats = [k for k in X_all[0] if keep(k)]
            Xt = [{k: X_all[i][k] for k in feats} for i in sub_tr]
            Xv = [{k: X_all[i][k] for k in feats} for i in sub_va]
            sp = field_preds(Xt, y1, Xv)
            thr = null_threshold(sp, y2)
            t = fit_tree(Xt, y1)
            tp = [tree_predict(t, x) for x in Xv]
            top = sorted(sp.items(), key=lambda kv: -bal_acc(y2, kv[1]))[:8]
            major = Counter(y2).most_common(1)[0][0]
            ok = sum(a == b for a, b in zip(y2, tp, strict=True))
            print(
                f"- {lab}: base rate {y2.count(major)}/{len(y2)} ({major}), chance "
                f"balanced {1 / len(set(y1)):.3f}; tree {ok}/{len(y2)} (balanced "
                f"{bal_acc(y2, tp):.3f}); null95 {thr:.3f}; top fields: "
                + "; ".join(
                    f"{f} {bal_acc(y2, p):.3f}{' FLAG' if bal_acc(y2, p) > thr else ''}"
                    for f, p in top
                )
            )
        print()
    for feat, typ in (
        ("own.inv_age", "invoice"),
        ("own.pay_status", "payment"),
        ("own.pay_age", "payment"),
    ):
        crosstab(data, feat, typ)
    return data


def crosstab(data: list[Any], feat: str, typ: str) -> None:
    c: Counter[tuple[str, str]] = Counter()
    for _s, cls, variant, t, f, _r in data:
        if t == typ:
            c[
                (
                    f[feat],
                    cls if cls != "LEGIT" else variant.removeprefix("legitimate_"),
                )
            ] += 1
    print(
        f"`{feat}` by class or variant ({typ}-primary dev cases, train+validation):\n"
    )
    table(
        [(v, k, n) for (v, k), n in sorted(c.items())],
        ("value", "class or variant", "cases"),
    )


def residuals(snap: Snap, data: list[Any], tokens: _Tokens, full: Path) -> None:
    print("### Residual 1: FX-settlement codes in gl_entries\n")
    gl = snap.t["gl_entries"]
    fx = [
        g
        for g in gl
        if g["transaction_type"] == FX_TYPE or g["gl_account"] == FX_ACCOUNT
    ]
    n_type = sum(g["transaction_type"] == FX_TYPE for g in gl)
    n_acct = sum(g["gl_account"] == FX_ACCOUNT for g in gl)
    n_src = len({g["source_transaction_id"] for g in fx})
    print(
        f"Snapshot-wide: {len(fx)} GL lines with transaction_type {FX_TYPE!r} or "
        f"gl_account {FX_ACCOUNT!r}, {n_src} distinct source rows; "
        f"{n_type} by type, {n_acct} by account.\n"
    )
    rows = []
    by: dict[tuple[str, str, str], list[bool]] = defaultdict(list)
    for _split, cls, variant, typ, f, raw in data:
        has = any(
            FX_TYPE in f.get(k, "") or FX_ACCOUNT in f.get(a, "")
            for k, a in (
                ("rel.pay_gl_types", "rel.pay_gl_accounts"),
                ("rel.inv_gl_types", "rel.inv_gl_accounts"),
            )
        )
        by[(cls, typ, variant if variant == "legitimate_fx_settlement" else "")].append(
            has
        )
        if has and variant != "legitimate_fx_settlement":
            print(f"FX code outside the FX variant: class {cls}, {typ} {raw}\n")
    for (cls, typ, variant), v in sorted(by.items()):
        rows.append((cls, typ, variant or "(other variants)", f"{sum(v)}/{len(v)}"))
    table(
        rows,
        (
            "class",
            "primary entity",
            "variant",
            "cases with an FX code on the entity, its payments or their bank rows",
        ),
    )
    crosstab(data, "rel.pay_gl_types", "payment")
    crosstab(data, "rel.pay_gl_accounts", "payment")
    crosstab(data, "rel.inv_gl_types", "invoice")

    print("### Residual 2: approval_events presence for inserted invoices\n")
    inv_raw = {}
    with (full / "invoices.csv").open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            inv_raw[tokens.token("invoice", r["invoice_id"])] = (
                int(r["invoice_id"].split("_")[1]) > INSERTED_INV_NUM
            )
    cnt: Counter[tuple[bool, bool]] = Counter()
    for i in snap.t["invoices"]:
        cnt[(inv_raw[str(i["invoice_id"])], bool(snap.appr.get(i["invoice_id"])))] += 1
    table(
        [
            (
                ("inserted" if k[0] else "organic"),
                ("has approval events" if k[1] else "none"),
                v,
            )
            for k, v in sorted(cnt.items())
        ],
        ("invoices", "approval_events", "count"),
    )
    print("Dev cases whose primary entity is an invoice, by approval presence:\n")
    cls_cnt: Counter[tuple[str, bool]] = Counter()
    for split in SPLITS:
        with (full.parent / split / "rca_ground_truth.jsonl").open() as f:
            for line in f:
                d = json.loads(line)
                if d["primary_entity"]["type"] == "invoice":
                    tok = tokens.token("invoice", d["primary_entity"]["id"])
                    v = (
                        d["failure_type"]
                        if d["failure_type"] != "NO_FAILURE"
                        else d["scenario_variant"]
                    )
                    cls_cnt[(v, bool(snap.appr.get(tok)))] += 1
    names = sorted({k[0] for k in cls_cnt})
    table(
        [
            (n, f"{cls_cnt[(n, False)]}/{cls_cnt[(n, False)] + cls_cnt[(n, True)]}")
            for n in names
            if cls_cnt[(n, False)]
        ],
        ("invoice-primary class or variant", "cases with NO approval events"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", type=Path, required=True)
    ap.add_argument("--seed", default="leakage-test-key-0")
    args = ap.parse_args()
    full = args.upstream / "data/benchmark/full"
    with tempfile.TemporaryDirectory() as tmp:
        key = Path(tmp) / "private" / "key"
        key.parent.mkdir()
        key.write_text(
            hashlib.sha256(args.seed.encode()).hexdigest() + "\n"
        )  # test key, not a secret
        out = Path(tmp) / "prepared"
        prepare_finrca_snapshot(
            full, out, key_path=key, source_revision=REVISION, cutoff=CUTOFF
        )
        tokens = _Tokens(bytes.fromhex(key.read_text().strip()))
        snap = Snap(out / "snapshot.sqlite")
    print(f"# Leakage test output (key seed {args.seed!r}; revision {REVISION[:7]})\n")
    print("## 1. Inserted rows\n")
    inserted_tests(snap, tokens, full)
    print("## 2. Order\n")
    order_test(snap, tokens, full)
    print("## 3. Family\n")
    data = family_tests(snap, load_cases(args.upstream, tokens))
    print("## 4. Residuals\n")
    residuals(snap, data, tokens, full)
    return 0


if __name__ == "__main__":
    sys.exit(main())
