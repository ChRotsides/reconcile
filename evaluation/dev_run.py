"""Development run on the FinRCA train and validation splits (PLAN.md M2).

Evaluator side: it holds the token key, the links and the labels, so it must
never be imported by src/reconcile/. Standard library plus the preparation
adapters (which are evaluator-side code; the inference image deletes them).

    uv run python evaluation/dev_run.py run TAG [--upstream PATH]
    uv run python evaluation/dev_run.py evaluate TAG      # recompute metrics only

`run` refuses an existing TAG. Steps, in order:

1. key under data/private/TAG/ (outside every prepared directory);
2. snapshot into artifacts/dev-runs/TAG/prepared/snapshot;
3. requests for train and validation into separate inference and evaluator
   directories (the sealed splits are never requested; the preparation code
   refuses challenge_test, and SPLITS here lists only these two);
4. image build, then inference in the container for both splits;
5. only after both packets.jsonl files exist: labels are opened, packets are
   joined to cases, metrics are written (metrics.json, errors.json, report.txt).

Labels are read by `evaluate` only. The top-level combined label files and the
test and challenge_test directories are never opened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cohort as C  # noqa: E402  (shared cohort, groups and label conflicts)

from reconcile.adapters.finrca import create_key_file, prepare_finrca_snapshot  # noqa: E402
from reconcile.adapters.finrca_requests import prepare_finrca_requests  # noqa: E402
from reconcile.diagnosis.policy import POLICY_VERSION, SCOPE_LIMITATION, quantum  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEFAULT_UPSTREAM = Path(
    "/home/charalambos/Documents/fin-tech-projects/reconcile/data/upstream/FinRCA-AI-Bench"
)
SOURCE_REVISION = "76c93ec"
CUTOFF = date(2026, 6, 30)
SPLITS = ("train", "validation")  # the only splits this script ever names
IMAGE = "reconcile-inference:dev"
SCRIPT = REPO / "deploy/inference/run_inference.sh"
is_failure = C.is_failure
is_no_failure = C.is_no_failure

# ------------------------------------------------------------ orchestration


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_commit() -> dict[str, str]:
    """HEAD of this worktree read from the .git files (no git command)."""
    info: dict[str, str] = {}
    try:
        gitdir = Path((REPO / ".git").read_text().split("gitdir:")[1].strip())
        head = (gitdir / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref = head.split()[1]
            info["branch"] = ref
            common = (gitdir / "commondir").read_text().strip()
            refpath = (gitdir / common / ref).resolve()
            info["commit"] = refpath.read_text().strip()
        else:
            info["commit"] = head
    except (OSError, IndexError) as e:
        info["commit"] = f"unknown ({type(e).__name__})"
    h = hashlib.sha256()
    for p in sorted((REPO / "src/reconcile").rglob("*.py")):
        h.update(str(p.relative_to(REPO)).encode() + b"\0" + p.read_bytes())
    info["src_sha256"] = h.hexdigest()
    return info


def run_cmd(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


def run(tag: str, upstream: Path) -> None:
    root = REPO / "artifacts/dev-runs" / tag
    private = REPO / "data/private" / tag
    if root.exists() or private.exists():
        sys.exit(f"run tag {tag!r} already exists; choose a new tag")
    timing: dict[str, float] = {}
    t0 = time.monotonic()

    def lap(name: str, since: float) -> float:
        now = time.monotonic()
        timing[name] = round(now - since, 2)
        return now

    private.mkdir(parents=True)
    key = private / "key"
    create_key_file(key)
    t = lap("key", t0)

    prepared = root / "prepared"
    snap_dir = prepared / "snapshot"
    manifest = prepare_finrca_snapshot(
        upstream / "data/benchmark/full",
        snap_dir,
        key_path=key,
        source_revision=SOURCE_REVISION,
        cutoff=CUTOFF,
    )
    t = lap("snapshot", t)

    summaries = {}
    for split in SPLITS:
        summaries[split] = prepare_finrca_requests(
            upstream / "data/benchmark",
            split,
            snapshot_dir=snap_dir,
            key_path=key,
            inference_dir=prepared / "requests" / split,
            evaluator_dir=root / "evaluator" / split,
            policy_version=POLICY_VERSION,
        )
    t = lap("requests", t)

    run_cmd([str(SCRIPT), "--build"])
    image_id = run_cmd(
        ["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE]
    ).stdout.strip()
    t = lap("build", t)

    # In-container absence check with the real key, links and upstream paths.
    absent = ":".join(
        [
            str(key),
            str(root / "evaluator" / "train" / "links.jsonl"),
            str(root / "evaluator" / "validation" / "links.jsonl"),
            str(upstream),
            str(upstream / "data/benchmark/challenge_test"),
            str(REPO / "evaluation"),
        ]
    )
    check = subprocess.run(
        [
            str(SCRIPT),
            "--check",
            str(snap_dir),
            str(prepared / "requests/train"),
            str(root / "check-out"),
        ],
        text=True,
        capture_output=True,
        env={**os.environ, "ABSENT_PATHS": absent},
    )
    (root / "absence-check.txt").write_text(check.stdout + check.stderr)
    if check.returncode != 0:
        sys.exit(f"absence check failed:\n{check.stdout}{check.stderr}")
    t = lap("absence_check", t)

    for split in SPLITS:
        s0 = time.monotonic()
        res = run_cmd(
            [str(SCRIPT), str(snap_dir), str(prepared / "requests" / split),
             str(root / "inference" / split)]
        )  # fmt: skip
        timing[f"inference_{split}"] = round(time.monotonic() - s0, 2)
        (root / "inference" / f"{split}.stdout").write_text(res.stdout)
    t = lap("inference_total", t)

    run_manifest = {
        "tag": tag,
        "started_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "code": code_commit(),
        "snapshot_content_sha256": manifest["snapshot"]["content_sha256"],
        "snapshot_file_sha256": manifest["snapshot"]["file_sha256"],
        "source_revision": SOURCE_REVISION,
        "cutoff": CUTOFF.isoformat(),
        "policy_version": POLICY_VERSION,
        "image": IMAGE,
        "image_id": image_id,
        "requests": summaries,
        "packets_sha256": {
            s: sha256_file(root / "inference" / s / "packets.jsonl") for s in SPLITS
        },
        "timing_seconds": timing,
        "command": f"uv run python evaluation/dev_run.py run {tag}",
    }
    (root / "run-manifest.json").write_text(
        json.dumps(run_manifest, indent=2, sort_keys=True) + "\n"
    )
    # Packets are final from here on. Only now are labels opened.
    evaluate(tag, upstream)


# -------------------------------------------------------------- evaluation
# Evidence completeness is computed from the packet and the prepared snapshot
# against docs/evidence-contract.md (the required records and searches of the
# category the packet claims). Upstream evidence_ids are not used.


class Snap:
    """Read-only view of the prepared snapshot (tokens only)."""

    def __init__(self, path: Path) -> None:
        self.con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row

    def q(self, sql: str, *params: str) -> list[dict]:
        return [dict(r) for r in self.con.execute(sql, params)]

    def one(self, sql: str, *params: str) -> dict | None:
        rows = self.q(sql, *params)
        return rows[0] if len(rows) == 1 else None

    def payment(self, pid: str) -> dict | None:
        return self.one("SELECT * FROM payments WHERE payment_id = ?", pid)

    def invoice(self, iid: str) -> dict | None:
        return self.one("SELECT * FROM invoices WHERE invoice_id = ?", iid)

    def allocs_of_payment(self, pid: str) -> list[dict]:
        return self.q("SELECT * FROM payment_allocations WHERE payment_id = ?", pid)

    def allocs_of_invoice(self, iid: str) -> list[dict]:
        return self.q("SELECT * FROM payment_allocations WHERE invoice_id = ?", iid)

    def principal(self, ref: str) -> list[dict]:
        return self.q(
            "SELECT * FROM bank_transactions WHERE payment_reference = ?"
            " AND transaction_type <> 'bank fee'",
            ref,
        )

    def posted_debits(self, ref: str) -> list[dict]:
        return [
            r
            for r in self.principal(ref)
            if r["direction"] == "debit" and r["status"] == "posted"
        ]

    def balanced_journals(self, source: str, kind: str) -> list[str]:
        lines = self.q(
            "SELECT * FROM gl_entries WHERE source_transaction_id = ?"
            " AND transaction_type = ?",
            source,
            kind,
        )
        by: dict[str, list[dict]] = defaultdict(list)
        for ln in lines:
            by[ln["journal_id"]].append(ln)
        out = []
        for jid, ls in sorted(by.items()):
            if len({x["currency"] for x in ls}) != 1:
                continue
            d = sum(Decimal(x["debit"]) for x in ls)
            c = sum(Decimal(x["credit"]) for x in ls)
            if d == c and d > 0:
                out.append(jid)
        return out

    def twins(self, pay: dict) -> list[str]:
        return [
            r["payment_id"]
            for r in self.q(
                "SELECT payment_id FROM payments WHERE vendor_id = ?"
                " AND payment_amount = ? AND payment_date = ?"
                " AND payment_currency = ? AND payment_id <> ?",
                pay["vendor_id"],
                pay["payment_amount"],
                pay["payment_date"],
                pay["payment_currency"],
                pay["payment_id"],
            )
        ]


def effective(p: dict) -> bool:
    return p["payment_status"] == "completed" and p["settlement_status"] == "settled"


class View:
    """What a packet cites, keyed by (table, record_id)."""

    def __init__(self, packet: dict) -> None:
        self.by_id = {e["evidence_id"]: e for e in packet["evidence"]}
        self.records = {
            (e["record"]["table"], e["record"]["record_id"])
            for e in packet["evidence"]
            if e["record"]
        }
        self.searches = [
            e["search"] for e in packet["evidence"] if e["search"] is not None
        ]
        self.calcs = []
        for c in packet["calculations"]:
            cited = {self._key(o["evidence_id"]) for o in c["operands"]}
            self.calcs.append((c["calculation_id"], cited))
        self.findings = [
            (
                f["check_id"],
                f["state"],
                {self._key(i) for i in f["evidence_ids"]},
                set(f["calculation_ids"]),
            )
            for f in packet["diagnosis"]["findings"]
        ]
        self.limitations = list(packet["limitations"])
        self.calc_by_id = {c["calculation_id"]: c for c in packet["calculations"]}
        self.day_counts = [
            (
                d["calculation_id"],
                self._key(d["start_evidence"]),
                d["result"],
                d["limit"],
            )
            for d in packet["day_counts"]
        ]

    def _key(self, evidence_id: str) -> tuple[str, str] | None:
        e = self.by_id.get(evidence_id)
        return (
            (e["record"]["table"], e["record"]["record_id"])
            if e and e["record"]
            else None
        )

    def cites(self, table: str, ids: list[str]) -> bool:
        return bool(ids) and all((table, i) in self.records for i in ids)

    def search(self, table: str, count: int, *needles: str) -> bool:
        return any(
            s["table"] == table
            and s["observed_count"] == count
            and all(n in s["predicate"] for n in needles)
            for s in self.searches
        )

    def finding_over(
        self, check_id: str, *keys: tuple[str, str], calcs: tuple[str, ...] = ()
    ) -> bool:
        """A supported finding `check_id` that cites every record in `keys` and
        every calculation id in `calcs`."""
        return any(
            c == check_id
            and state == "supported"
            and set(keys) <= cited
            and set(calcs) <= cids
            for c, state, cited, cids in self.findings
        )

    def calculation(
        self, calc_id: str, operation: str
    ) -> (
        tuple[list[tuple[tuple[str, str] | None, str, Decimal]], Decimal, Decimal]
        | None
    ):
        """(operands as (record, field, amount), result, tolerance) of the
        calculation `calc_id` when its operation is `operation`, else None."""
        c = self.calc_by_id.get(calc_id)
        if c is None or c["operation"] != operation or not c["tolerance"]:
            return None
        ops = [
            (self._key(o["evidence_id"]), o["field"], Decimal(o["amount"].split()[0]))
            for o in c["operands"]
        ]
        return ops, Decimal(c["result"].split()[0]), Decimal(c["tolerance"].split()[0])

    def calc_over(self, *keys: tuple[str, str]) -> bool:
        return any(set(keys) <= cited for _, cited in self.calcs)

    def day_count_from(self, key: tuple[str, str]) -> tuple[int, int] | None:
        for _, start, result, limit in self.day_counts:
            if start == key:
                return result, limit
        return None


def alloc_ids(rows: list[dict]) -> list[str]:
    return [f"{r['payment_id']}|{r['invoice_id']}" for r in rows]


def bank_ids(rows: list[dict]) -> list[str]:
    return [r["bank_transaction_id"] for r in rows]


def _category_requirements(
    category: str, entity: tuple[str, str], s: Snap, v: View
) -> list[tuple[str, bool]]:
    """The per-category requirements of docs/evidence-contract.md sections 4
    and 5 on this primary entity (without the all-clear prerequisites)."""
    table, rid = entity
    out: list[tuple[str, bool]] = []

    def add(name: str, ok: bool) -> None:
        out.append((name, bool(ok)))

    if table == "bank_transactions":  # accounted bank fee (contract 5)
        row = s.one(
            "SELECT * FROM bank_transactions WHERE bank_transaction_id = ?", rid
        )
        pays = (
            s.q(
                "SELECT * FROM payments WHERE reference_number = ?",
                row["payment_reference"] or "",
            )
            if row
            else []
        )
        add("fee_row", v.cites("bank_transactions", [rid]))
        add(
            "fee_payment",
            len(pays) == 1 and v.cites("payments", [pays[0]["payment_id"]]),
        )
        add("fee_journal", v.cites("gl_entries", s.balanced_journals(rid, "bank_fee")))
        fee_allocs = (
            s.allocs_of_payment(pays[0]["payment_id"]) if len(pays) == 1 else []
        )
        add(
            "payment_allocations",
            bool(fee_allocs) and v.cites("payment_allocations", alloc_ids(fee_allocs)),
        )
        return out

    if table == "invoices":
        inv = s.invoice(rid)
        allocs = s.allocs_of_invoice(rid)
        pays = [s.payment(a["payment_id"]) for a in allocs]
        pays = [p for p in pays if p]
        add("invoice", v.cites("invoices", [rid]))
        add("allocations", v.cites("payment_allocations", alloc_ids(allocs)))
        if category == "F06":
            add(
                "allocating_payments",
                v.cites("payments", [p["payment_id"] for p in pays]),
            )
            committed = [
                p
                for p in pays
                if effective(p)
                and inv
                and p["payment_currency"] == inv["currency"]
                and s.posted_debits(p["reference_number"])
            ]
            debits = [
                b
                for p in committed
                for b in bank_ids(s.posted_debits(p["reference_number"]))
            ]
            add("committed_debits", v.cites("bank_transactions", debits))
            alloc_keys = [("payment_allocations", i) for i in alloc_ids(allocs)]
            add(
                "overpayment_calculation",
                v.calc_over(("invoices", rid), *alloc_keys[:1]),
            )
        elif category in ("legitimate_split_payment", "legitimate_due_date_payment"):
            eff = [p for p in pays if effective(p)]
            add(
                "effective_payments",
                v.cites("payments", [p["payment_id"] for p in eff]),
            )
            debits = [
                b for p in eff for b in bank_ids(s.posted_debits(p["reference_number"]))
            ]
            add("posted_debits", v.cites("bank_transactions", debits))
        else:
            return []
        return out

    pay = s.payment(rid)
    if pay is None:
        return []
    ref, pkey = pay["reference_number"], ("payments", rid)
    add("payment", v.cites("payments", [rid]))
    allocs = s.allocs_of_payment(rid)
    debits = s.posted_debits(ref)
    principal = s.principal(ref)
    one_debit = len(debits) == 1
    deb_key = (
        ("bank_transactions", debits[0]["bank_transaction_id"]) if one_debit else None
    )

    if category in ("F12", "legitimate_pending_settlement"):
        add("allocations", v.cites("payment_allocations", alloc_ids(allocs)))
        add(
            "payment_journal",
            v.cites("gl_entries", s.balanced_journals(rid, "payment")),
        )
        add(
            "search_a1_no_bank_row",
            v.search("bank_transactions", 0, f"payment_reference = '{ref}'"),
        )
        add(
            "search_a2_unique_reference",
            v.search("payments", 1, f"reference_number = '{ref}'"),
        )
        add(
            "search_a3_no_candidate",
            v.search(
                "bank_transactions",
                0,
                f"counterparty_token = '{pay['bank_account_id']}'",
                "matches no payment",
            ),
        )
        dc = v.day_count_from(pkey)
        want_over = category == "F12"
        add("business_day_age", dc is not None and ((dc[0] > dc[1]) == want_over))
    elif category == "F14":
        add(
            "posted_debit", one_debit and v.cites("bank_transactions", bank_ids(debits))
        )
        add("delta_calculation", deb_key is not None and v.calc_over(pkey, deb_key))
        add(
            "search_no_fee_journal",
            one_debit
            and v.search(
                "gl_entries",
                0,
                f"source_transaction_id = '{debits[0]['bank_transaction_id']}'",
                "bank_fee",
            ),
        )
    elif category in ("legitimate_weekend_clearing", "legitimate_check_clearing"):
        add(
            "posted_debit", one_debit and v.cites("bank_transactions", bank_ids(debits))
        )
        add(
            "amount_equality_calculation",
            deb_key is not None and v.calc_over(pkey, deb_key),
        )
        add("business_day_lag", v.day_count_from(pkey) is not None)
        add(
            "allocations",
            bool(allocs) and v.cites("payment_allocations", alloc_ids(allocs)),
        )
    elif category == "legitimate_batch_payment":
        add("allocations", v.cites("payment_allocations", alloc_ids(allocs)))
        add(
            "allocated_invoices", v.cites("invoices", [a["invoice_id"] for a in allocs])
        )
        add("posted_debit", v.cites("bank_transactions", bank_ids(debits)))
    elif category == "legitimate_payment_reversal":
        add("posted_debit", v.cites("bank_transactions", bank_ids(debits)))
        credits = [
            r
            for r in principal
            if r["direction"] == "credit" and r["status"] == "posted"
        ]
        add("posted_credit", v.cites("bank_transactions", bank_ids(credits)))
        add(
            "reversal_journal",
            v.cites("gl_entries", s.balanced_journals(rid, "payment_reversal")),
        )
        add(
            "allocated_invoices", v.cites("invoices", [a["invoice_id"] for a in allocs])
        )
    elif category == "legitimate_net_fee_accounting":
        add(
            "posted_debit", one_debit and v.cites("bank_transactions", bank_ids(debits))
        )
        fee = (
            s.balanced_journals(debits[0]["bank_transaction_id"], "bank_fee")
            if one_debit
            else []
        )
        add("fee_journals", v.cites("gl_entries", fee))
        add(
            "allocations",
            bool(allocs) and v.cites("payment_allocations", alloc_ids(allocs)),
        )
    elif category == "no_exception":
        add(
            "posted_debit", one_debit and v.cites("bank_transactions", bank_ids(debits))
        )
        add("allocations", v.cites("payment_allocations", alloc_ids(allocs)))
        add("delta_calculation", deb_key is not None and v.calc_over(pkey, deb_key))
        add("business_day_lag", v.day_count_from(pkey) is not None)
        invs = [a["invoice_id"] for a in allocs]
        eff_allocs = [
            a
            for i in invs
            for a in s.allocs_of_invoice(i)
            if (p := s.payment(a["payment_id"])) and effective(p)
        ]
        add("allocated_invoices", v.cites("invoices", invs))
        add(
            "effective_allocations_of_invoices",
            v.cites("payment_allocations", alloc_ids(eff_allocs)),
        )
        twins = s.twins(pay)
        if twins:
            add("equal_twins_cited", v.cites("payments", twins))
    else:
        return []
    return out


# Finding names the investigator gives the two all-clear prerequisites
# (docs/evidence-contract.md section 5, "All-clear prerequisites").
APPROVAL_FINDING = "final_approval_before_payment"
JOURNAL_FINDING = "payment_journal_matches_payment"
TOTALS_FINDING = "invoice_totals_not_exceeded"
PAYMENT_ALLOCATION_FINDING = "payment_not_over_allocated"
TERMINAL_ACTIONS = ("approved", "auto_approved")


def guarded_payments(
    category: str, entity: tuple[str, str], s: Snap
) -> list[dict] | None:
    """The payments whose prerequisites an all-clear `category` packet must
    show: the primary payment, the payment a fee row resolves to, or each
    effective payment allocated to the primary invoice. None when `category`
    is not an all-clear."""
    if not is_no_failure(category):
        return None
    table, rid = entity
    if table == "payments":
        pay = s.payment(rid)
        return [pay] if pay else []
    if table == "bank_transactions":
        row = s.one(
            "SELECT * FROM bank_transactions WHERE bank_transaction_id = ?", rid
        )
        pays = s.q(
            "SELECT * FROM payments WHERE reference_number = ?",
            (row["payment_reference"] or "") if row else "",
        )
        return pays if len(pays) == 1 else []
    pays = [s.payment(a["payment_id"]) for a in s.allocs_of_invoice(rid)]
    return [p for p in pays if p and effective(p)]


def effective_approval(s: Snap, invoice: str, paid: str) -> list[str] | None:
    """The approval events that make `invoice`'s approval effective on the
    payment date `paid`, from the snapshot alone: among the events dated on or
    before `paid`, the ones with the newest timestamp, when every one of them is
    a final approval (action approved or auto_approved, new_status approved) at
    the highest level reached by then. None when the approval is not effective
    (none, a later revocation or reopening, a lower level). Events after `paid`
    do not matter."""
    events = [
        e
        for e in s.q(
            "SELECT * FROM approval_events WHERE invoice_id = ?"
            " AND substr(event_timestamp, 1, 10) <= ?",
            invoice,
            paid,
        )
    ]
    if not events:
        return None
    top = max(int(e["approval_level"]) for e in events)
    newest = max(datetime.fromisoformat(e["event_timestamp"]) for e in events)
    latest = [
        e for e in events if datetime.fromisoformat(e["event_timestamp"]) == newest
    ]
    if all(
        e["action"] in TERMINAL_ACTIONS
        and e["new_status"] == "approved"
        and int(e["approval_level"]) >= top
        for e in latest
    ):
        return [e["approval_event_id"] for e in latest]
    return None


def _invoices_of(s: Snap, p: dict) -> list[str]:
    return sorted({a["invoice_id"] for a in s.allocs_of_payment(p["payment_id"])})


def _quantum(p: dict) -> Decimal:
    return quantum(p["payment_currency"]).amount


def _journal_keys(s: Snap, p: dict) -> list[tuple[str, str]]:
    return [("gl_entries", j) for j in s.balanced_journals(p["payment_id"], "payment")]


def chk_allocations(s: Snap, v: View, p: dict) -> bool:
    allocs = s.allocs_of_payment(p["payment_id"])
    return bool(allocs) and v.cites("payment_allocations", alloc_ids(allocs))


def chk_approval_event(s: Snap, v: View, p: dict) -> bool:
    """Every event that makes each allocated invoice's approval effective on the
    payment date is cited (a post-payment or revoked approval does not match)."""
    paid = p["payment_date"][:10]
    for inv in _invoices_of(s, p):
        ids = effective_approval(s, inv, paid)
        if not ids or not v.cites("approval_events", ids):
            return False
    return bool(_invoices_of(s, p))


def chk_approval_finding(s: Snap, v: View, p: dict) -> bool:
    paid, pkey = p["payment_date"][:10], ("payments", p["payment_id"])
    for inv in _invoices_of(s, p):
        ids = effective_approval(s, inv, paid)
        keys = [("approval_events", i) for i in ids or []]
        if not keys or not v.finding_over(APPROVAL_FINDING, pkey, *keys):
            return False
    return bool(_invoices_of(s, p))


def chk_payment_journal(s: Snap, v: View, p: dict) -> bool:
    keys = _journal_keys(s, p)
    return bool(keys) and v.cites("gl_entries", [k[1] for k in keys])


def _journal_calc_id(p: dict) -> str:
    return f"payment_minus_payment_journals:{p['payment_id']}"


def chk_journal_calculation(s: Snap, v: View, p: dict) -> bool:
    """The journal comparison is payment amount minus the payment journals'
    amounts (operation `difference`, first operand the payment amount, the others
    exactly the payment's journals), recomputed, and within one quantum."""
    keys = _journal_keys(s, p)
    got = v.calculation(_journal_calc_id(p), "difference")
    if not keys or got is None:
        return False
    ops, result, tol = got
    if not ops or ops[0][:2] != (("payments", p["payment_id"]), "payment_amount"):
        return False
    if ops[0][2] != Decimal(p["payment_amount"]):
        return False
    rest = ops[1:]
    if sorted(k for k, _, _ in rest if k) != sorted(keys) or any(
        f != "amount" for _, f, _ in rest
    ):
        return False
    return (
        result == ops[0][2] - sum((a for _, _, a in rest), Decimal(0))
        and tol == _quantum(p)
        and abs(result) <= tol
    )


def chk_journal_finding(s: Snap, v: View, p: dict) -> bool:
    keys = _journal_keys(s, p)
    return bool(keys) and v.finding_over(
        JOURNAL_FINDING,
        ("payments", p["payment_id"]),
        *keys,
        calcs=(_journal_calc_id(p),),
    )


def _balance_parts(s: Snap, inv: str) -> tuple[list[dict], list[dict]]:
    """(effective payments, their allocations) of an invoice."""
    pays, allocs = [], []
    for a in s.allocs_of_invoice(inv):
        other = s.payment(a["payment_id"])
        if other and effective(other):
            pays.append(other)
            allocs.append(a)
    return pays, allocs


def chk_totals_calculation(s: Snap, v: View, p: dict) -> bool:
    """For each allocated invoice that has effective allocations (a pending or
    reversed payment contributes none): the invoice, every effective allocating
    payment and every effective allocation are cited, and
    `invoice_open_balance:<invoice>` is invoice total minus those allocations
    (operation `difference`), not below minus one quantum."""
    invoices = _invoices_of(s, p)
    for inv in invoices:
        pays, allocs = _balance_parts(s, inv)
        if not allocs:
            continue
        got = v.calculation(f"invoice_open_balance:{inv}", "difference")
        row = s.invoice(inv)
        if got is None or row is None:
            return False
        ops, result, tol = got
        first = ops[0] if ops else None
        keys = [("payment_allocations", i) for i in alloc_ids(allocs)]
        rest = ops[1:]
        if not (
            first
            and first[:2] == (("invoices", inv), "invoice_total")
            and first[2] == Decimal(row["invoice_total"])
            and sorted(k for k, _, _ in rest if k) == sorted(keys)
            and all(f == "allocated_amount" for _, f, _ in rest)
            and result == first[2] - sum((a for _, _, a in rest), Decimal(0))
            and tol == _quantum(p)
            and result >= -tol
            and v.cites("payments", [x["payment_id"] for x in pays])
        ):
            return False
    return bool(invoices)


def chk_totals_finding(s: Snap, v: View, p: dict) -> bool:
    """The supported `invoice_totals_not_exceeded` finding cites the payment, its
    allocations, its invoices and the open-balance calculation of each invoice
    that has effective allocations."""
    pid = p["payment_id"]
    allocs = s.allocs_of_payment(pid)
    invoices = _invoices_of(s, p)
    keys = [
        ("payments", pid),
        *[("payment_allocations", i) for i in alloc_ids(allocs)],
        *[("invoices", i) for i in invoices],
    ]
    calcs = tuple(
        f"invoice_open_balance:{i}" for i in invoices if _balance_parts(s, i)[1]
    )
    return bool(allocs) and v.finding_over(TOTALS_FINDING, *keys, calcs=calcs)


def _over_alloc_calc_id(p: dict) -> str:
    return f"payment_minus_allocations:{p['payment_id']}"


def chk_payment_allocation_calculation(s: Snap, v: View, p: dict) -> bool:
    """`payment_minus_allocations:<payment>` is the payment amount minus all of
    the payment's allocations (operation `difference`), recomputed, and not
    below minus one quantum (the payment is not allocated beyond its amount)."""
    pid = p["payment_id"]
    keys = [("payment_allocations", i) for i in alloc_ids(s.allocs_of_payment(pid))]
    got = v.calculation(_over_alloc_calc_id(p), "difference")
    if not keys or got is None:
        return False
    ops, result, tol = got
    rest = ops[1:]
    return bool(
        ops
        and ops[0][:2] == (("payments", pid), "payment_amount")
        and ops[0][2] == Decimal(p["payment_amount"])
        and sorted(k for k, _, _ in rest if k) == sorted(keys)
        and all(f == "allocated_amount" for _, f, _ in rest)
        and result == ops[0][2] - sum((a for _, _, a in rest), Decimal(0))
        and tol == _quantum(p)
        and result >= -tol
    )


def chk_payment_allocation_finding(s: Snap, v: View, p: dict) -> bool:
    pid = p["payment_id"]
    keys = [("payments", pid)] + [
        ("payment_allocations", i) for i in alloc_ids(s.allocs_of_payment(pid))
    ]
    return len(keys) > 1 and v.finding_over(
        PAYMENT_ALLOCATION_FINDING, *keys, calcs=(_over_alloc_calc_id(p),)
    )


# The all-clear prerequisites (docs/evidence-contract.md section 5). Each check
# is evaluated for every payment the packet rests on. Adding a prerequisite is
# one line here (plus its check function above).
PAYMENT_CHECKS: list[tuple[str, Callable[[Snap, View, dict], bool]]] = [
    ("guard_allocations", chk_allocations),
    ("invoice_totals_calculation", chk_totals_calculation),
    ("invoice_totals_finding", chk_totals_finding),
    ("payment_allocation_calculation", chk_payment_allocation_calculation),
    ("payment_allocation_finding", chk_payment_allocation_finding),
    ("approval_event", chk_approval_event),
    ("approval_finding", chk_approval_finding),
    ("payment_journal", chk_payment_journal),
    ("payment_journal_calculation", chk_journal_calculation),
    ("payment_journal_finding", chk_journal_finding),
]
# Checks of the packet as a whole, on every all-clear.
PACKET_CHECKS: list[tuple[str, Callable[[View], bool]]] = [
    ("scope_statement", lambda v: SCOPE_LIMITATION in v.limitations),
]


def guard_requirements(pays: list[dict], s: Snap, v: View) -> list[tuple[str, bool]]:
    """Contract prerequisites of every all-clear: `PAYMENT_CHECKS` for each
    payment the packet rests on (all must hold) and `PACKET_CHECKS` once."""
    out = [(n, bool(pays) and all(f(s, v, p) for p in pays)) for n, f in PAYMENT_CHECKS]
    return [*out, *[(n, f(v)) for n, f in PACKET_CHECKS]]


def required(
    category: str, entity: tuple[str, str], s: Snap, v: View
) -> list[tuple[str, bool]]:
    """Contract requirements for `category` on this primary entity: a list of
    (name, satisfied). An empty list means the category has no contract here.
    Every all-clear category also needs the contract 0.3 prerequisites."""
    base = _category_requirements(category, entity, s, v)
    pays = guarded_payments(category, entity, s)
    if not base or pays is None:
        return base
    return [*base, *guard_requirements(pays, s, v)]


def completeness(packet: dict, entity: tuple[str, str], snap: Snap) -> dict:
    """Evidence completeness of an answered packet: every contract requirement
    of its category holds and every absence search it states reproduces on the
    snapshot (a false absence claim makes the packet incomplete)."""
    reqs = required(packet["diagnosis"]["category"], entity, snap, View(packet))
    mismatch = searches_reproduce(packet, snap)
    missing = [n for n, ok in reqs if not ok]
    if mismatch:
        missing.append("searches_reproduce")
    return {
        "contract_defined": bool(reqs),
        "complete": bool(reqs) and not missing,
        "missing": missing,
        "searches_mismatch": mismatch,
    }


# ------------------------------------------------------------------ joining


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def classify(row: dict) -> None:
    """Fill row['outcome'] (and 'kind' for wrong ones)."""
    disp, cat, grp = row["disposition"], row["category"], row["group"]
    label_fail = row["failure_type"] != "NO_FAILURE"
    expected = row["expected_category"]
    if grp in ("cohort", "label_conflict"):
        if disp != "answered":
            row["outcome"] = disp  # abstained | out_of_scope | failed
        elif cat == expected:
            row["outcome"] = "correct"
        else:
            row["outcome"] = "wrong"
            if not label_fail and is_failure(cat):
                row["kind"] = "false_alarm"
            elif label_fail and is_no_failure(cat):
                row["kind"] = "failure_called_no_failure"
            elif label_fail:
                row["kind"] = "wrong_failure_class"
            elif cat == "no_exception":
                row["kind"] = "legit_variant_called_no_exception"
            else:
                row["kind"] = "wrong_legit_variant"
    elif grp == "fx":
        if disp == "abstained" and row["reason"] == "unsupported_fx":
            row["outcome"] = "correct_abstention"
        elif disp == "answered":
            row["outcome"] = "wrong"
            row["kind"] = "false_alarm" if is_failure(cat) else "answered_fx_pair"
        else:
            row["outcome"] = disp
            row["kind"] = "abstained_other_reason"
    elif grp == "not_gradable":
        row["outcome"] = "answered_not_gradable" if disp == "answered" else disp
    elif grp == "contradicted":
        row["outcome"] = disp
        if disp == "answered":
            row["kind"] = (
                "answered_label_category" if cat == expected else "answered_other"
            )
    elif grp == "oos_failure":
        if disp == "answered":
            row["outcome"] = "answered_oos"
            row["kind"] = (
                "failure_class_on_other_family"
                if is_failure(cat)
                else "failure_called_no_failure"
            )
        else:
            row["outcome"] = disp
    else:  # oos_legit
        if disp == "answered":
            row["outcome"] = "answered_oos"
            if is_failure(cat):
                row["kind"] = "false_alarm"
            elif cat == "no_exception":
                row["kind"] = "no_exception_on_ap_side_legit"
            else:
                row["kind"] = "payment_variant_on_ap_side_legit"
        else:
            row["outcome"] = disp


def searches_reproduce(packet: dict, snap: Snap) -> list[str]:
    """Searches whose stated count differs when the predicate is re-run on the
    snapshot (docs/evidence-contract.md principle 3). Returns mismatch notes."""
    bad = []
    for e in packet["evidence"]:
        s = e["search"]
        if s is None:
            continue
        pred = s["predicate"]
        if "payment_reference matches no payment" in pred:
            pred = pred.replace(
                " AND payment_reference matches no payment",
                " AND (payment_reference IS NULL OR payment_reference NOT IN"
                " (SELECT reference_number FROM payments))",
            )
        n = snap.con.execute(
            f"SELECT COUNT(*) FROM {s['table']} WHERE {pred}"  # noqa: S608
        ).fetchone()[0]
        if n != s["observed_count"]:
            bad.append(f"{e['evidence_id']}:{s['observed_count']}!={n}")
    return bad


def fx_abstention_cites(packet: dict) -> bool:
    """Contract FX section: cites the payment and a bank row in another currency."""
    cur = {}
    for e in packet["evidence"]:
        if e["record"]:
            cur.setdefault(e["record"]["table"], set()).add(
                (e["values"] or {}).get("currency")
                or (e["values"] or {}).get("payment_currency")
            )
    return (
        "payments" in cur
        and "bank_transactions" in cur
        and len(cur["payments"] | cur["bank_transactions"]) > 1
    )


def load_split(
    root: Path,
    split: str,
    labels_by_case: dict,
    snap: Snap,
    conflicts: C.Conflicts,
) -> list[dict]:
    links = {
        r["request_id"]: r["case_id"]
        for r in read_jsonl(root / "evaluator" / split / "links.jsonl")
    }
    requests = {
        r["request_id"]: r
        for r in read_jsonl(root / "prepared/requests" / split / "requests.jsonl")
    }
    packets = read_jsonl(root / "inference" / split / "packets.jsonl")
    ids = [p["request"]["request_id"] for p in packets]
    if sorted(ids) != sorted(links) or set(ids) != set(requests):
        sys.exit(f"{split}: packets, requests and links do not cover the same cases")
    rows = []
    for p in packets:
        rid = p["request"]["request_id"]
        case_id = links[rid]
        lab = labels_by_case[case_id]
        d = p["diagnosis"]
        ent = requests[rid]["primary_entity"]
        group = C.group_of(
            lab["failure_type"], lab["scenario_variant"], case_id, conflicts.verified
        )
        row = {
            "split": split,
            "case_id": case_id,
            "failure_type": lab["failure_type"],
            "variant": lab["scenario_variant"],
            "group": group,
            "disposition": d["disposition"],
            "category": d["category"],
            "reason": d["reason"],
            "label_conflict_with": conflicts.verified.get(case_id, []),
            "conflict_candidate_with": conflicts.candidates.get(case_id, []),
            "review_candidate_with": conflicts.review.get(case_id, []),
            "entity_table": ent["table"],
            "expected_category": C.expected_category(
                lab["failure_type"], lab["scenario_variant"]
            ),
        }
        classify(row)
        row["searches_mismatch"] = searches_reproduce(p, snap)
        if group == "fx" and d["disposition"] == "abstained":
            row["fx_cites_ok"] = fx_abstention_cites(p)
        if d["disposition"] == "answered":
            row |= completeness(p, (ent["table"], ent["record_id"]), snap)
        rows.append(row)
    return rows


# ------------------------------------------------------------------ metrics


def frac(n: int, d: int) -> dict:
    return {"n": n, "d": d, "rate": (round(n / d, 4) if d else None)}


def cohort_metrics(
    rows: list[dict],
    groups: tuple[str, ...] = C.COHORT_VIEWS["all_cases"],
) -> dict:
    """Metrics over the gradable in-scope cohort. Default: the headline view,
    every such case. C.COHORT_VIEWS["sensitivity_without_verified_conflicts"]
    leaves out the hand-verified label conflicts; the baseline reports the same
    two views from the same module."""
    c = [r for r in rows if r["group"] in groups]
    answered = [r for r in c if r["disposition"] == "answered"]
    correct = [r for r in answered if r["outcome"] == "correct"]
    complete = [r for r in correct if r.get("complete")]
    legit = [r for r in c if r["failure_type"] == "NO_FAILURE"]
    coarse_ok = [
        r
        for r in answered
        if is_failure(r["category"]) == (r["failure_type"] != "NO_FAILURE")
    ]
    return {
        "cases": len(c),
        "dispositions": dict(Counter(r["disposition"] for r in c)),
        "answered": frac(len(answered), len(c)),
        "abstained": frac(sum(r["disposition"] == "abstained" for r in c), len(c)),
        "out_of_scope": frac(
            sum(r["disposition"] == "out_of_scope" for r in c), len(c)
        ),
        "failed": frac(sum(r["disposition"] == "failed" for r in c), len(c)),
        "answer_precision": frac(len(correct), len(answered)),
        "category_accuracy_answered": frac(len(correct), len(answered)),
        "failure_vs_no_failure_accuracy_answered": frac(len(coarse_ok), len(answered)),
        "correct_and_evidence_complete": frac(len(complete), len(c)),
        "evidence_complete_among_correct": frac(len(complete), len(correct)),
        "evidence_complete_among_answered": frac(
            sum(bool(r.get("complete")) for r in answered), len(answered)
        ),
        "false_alarms": frac(
            sum(r.get("kind") == "false_alarm" for r in legit), len(legit)
        ),
        "search_counts_reproduce_among_answered": frac(
            sum(not r["searches_mismatch"] for r in answered), len(answered)
        ),
        "wrong_by_kind": dict(Counter(r["kind"] for r in c if r["outcome"] == "wrong")),
        "abstain_or_nonanswer_reasons": dict(
            Counter(
                f"{r['disposition']}:{r['reason']}"
                for r in c
                if r["disposition"] != "answered"
            )
        ),
    }


def per_label(rows: list[dict], groups: tuple[str, ...]) -> dict:
    out: dict[str, dict] = {}

    def key(r: dict) -> str:
        return r["failure_type"] if r["failure_type"] != "NO_FAILURE" else r["variant"]

    for k in sorted({key(r) for r in rows if r["group"] in groups}):
        sub = [r for r in rows if r["group"] in groups and key(r) == k]
        ans = [r for r in sub if r["disposition"] == "answered"]
        cor = [r for r in ans if r["outcome"] == "correct"]
        out[k] = {
            "cases": len(sub),
            "answered": len(ans),
            "correct": len(cor),
            "complete_correct": sum(bool(r.get("complete")) for r in cor),
            "abstained": sum(r["disposition"] == "abstained" for r in sub),
            "out_of_scope": sum(r["disposition"] == "out_of_scope" for r in sub),
            "failed": sum(r["disposition"] == "failed" for r in sub),
            "answered_categories": dict(Counter(r["category"] for r in ans)),
        }
    return out


def label_key(r: dict) -> str:
    return r["failure_type"] if r["failure_type"] != "NO_FAILURE" else r["variant"]


def confusion(rows: list[dict], groups: tuple[str, ...]) -> dict:
    """label -> {answered category or non-answer disposition: count}."""
    out: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r["group"] in groups:
            out[label_key(r)][
                r["category"] if r["disposition"] == "answered" else r["disposition"]
            ] += 1
    return {k: dict(v) for k, v in sorted(out.items())}


def all_clear_risk(rows: list[dict]) -> dict:
    """Headline risk: failures outside the cohort that the tool answered
    legitimate_* or no_exception, i.e. a false all-clear. Per upstream family."""
    oos = [r for r in rows if r["group"] == "oos_failure"]

    def given(r: dict) -> bool:
        return r["disposition"] == "answered" and is_no_failure(r["category"])

    return {
        "overall": frac(sum(map(given, oos)), len(oos)),
        "per_family": {
            f: frac(
                sum(given(r) for r in oos if r["failure_type"] == f),
                sum(r["failure_type"] == f for r in oos),
            )
            for f in sorted({r["failure_type"] for r in oos})
        },
    }


def all_metrics(rows: list[dict]) -> dict:
    fx = [r for r in rows if r["group"] == "fx"]
    legit_all = [r for r in rows if r["failure_type"] == "NO_FAILURE"]
    return {
        "dispositions_all": dict(Counter(r["disposition"] for r in rows)),
        "cohort": cohort_metrics(rows),
        "cohort_sensitivity_without_verified_conflicts": cohort_metrics(
            rows, C.COHORT_VIEWS["sensitivity_without_verified_conflicts"]
        ),
        "review_candidates": [
            {k: r.get(k) for k in ("split", "case_id", "group", "variant", "outcome")}
            | {"review_candidate_with": r["review_candidate_with"]}
            for r in sorted(rows, key=lambda r: r["case_id"])
            if r["review_candidate_with"]
        ],
        "label_conflicts": [
            {
                k: r.get(k)
                for k in (
                    "split",
                    "case_id",
                    "variant",
                    "disposition",
                    "category",
                    "reason",
                    "outcome",
                    "label_conflict_with",
                )
            }
            for r in sorted(rows, key=lambda r: r["case_id"])
            if r["group"] == "label_conflict"
        ],
        "conflict_candidates_unverified": [
            {
                "split": r["split"],
                "case_id": r["case_id"],
                "group": r["group"],
                "variant": r["variant"],
                "conflict_candidate_with": r["conflict_candidate_with"],
            }
            for r in sorted(rows, key=lambda r: r["case_id"])
            if r["conflict_candidate_with"] and r["group"] != "label_conflict"
        ],
        "cohort_per_label": per_label(rows, (*C.COHORT_GROUPS, "fx")),
        "cohort_confusion": confusion(rows, (*C.COHORT_GROUPS, "fx")),
        "fx_slice": {
            "cases": len(fx),
            "correct_abstention_unsupported_fx": frac(
                sum(r["outcome"] == "correct_abstention" for r in fx), len(fx)
            ),
            "abstentions_citing_payment_and_other_currency_bank_row": frac(
                sum(bool(r.get("fx_cites_ok")) for r in fx), len(fx)
            ),
            "outcomes": dict(
                Counter(f"{r['outcome']}:{r.get('kind', '')}" for r in fx)
            ),
        },
        "special_cases": [
            {
                k: r.get(k)
                for k in (
                    "split",
                    "case_id",
                    "variant",
                    "failure_type",
                    "group",
                    "disposition",
                    "category",
                    "reason",
                    "outcome",
                    "kind",
                    "complete",
                    "missing",
                )
            }
            for r in sorted(rows, key=lambda r: r["case_id"])
            if r["group"] in ("contradicted", "not_gradable")
        ],
        "false_alarms_all_legitimate": {
            "any_legitimate": frac(
                sum(r.get("kind") == "false_alarm" for r in legit_all), len(legit_all)
            ),
            "in_cohort_variants_incl_fx": frac(
                sum(
                    r.get("kind") == "false_alarm"
                    for r in legit_all
                    if r["group"] in ("cohort", "label_conflict", "fx", "contradicted")
                ),
                sum(
                    r["group"] in ("cohort", "label_conflict", "fx", "contradicted")
                    for r in legit_all
                ),
            ),
            "ap_side_variants_out_of_scope": frac(
                sum(
                    r.get("kind") == "false_alarm"
                    for r in legit_all
                    if r["group"] == "oos_legit"
                ),
                sum(r["group"] == "oos_legit" for r in legit_all),
            ),
        },
        "all_clear_on_out_of_scope_failures": all_clear_risk(rows),
        "out_of_scope_families": {
            "failure_families": {
                "cases": sum(r["group"] == "oos_failure" for r in rows),
                "dispositions": dict(
                    Counter(
                        r["disposition"] for r in rows if r["group"] == "oos_failure"
                    )
                ),
                "answered_by_kind": dict(
                    Counter(
                        f"{r['category']}"
                        for r in rows
                        if r["group"] == "oos_failure"
                        and r["disposition"] == "answered"
                    )
                ),
                "reasons": dict(
                    Counter(
                        f"{r['disposition']}:{r['reason']}"
                        for r in rows
                        if r["group"] == "oos_failure"
                        and r["disposition"] != "answered"
                    )
                ),
            },
            "ap_side_legitimate": {
                "cases": sum(r["group"] == "oos_legit" for r in rows),
                "dispositions": dict(
                    Counter(r["disposition"] for r in rows if r["group"] == "oos_legit")
                ),
                "answered_by_kind": dict(
                    Counter(
                        f"{r['category']}"
                        for r in rows
                        if r["group"] == "oos_legit" and r["disposition"] == "answered"
                    )
                ),
                "reasons": dict(
                    Counter(
                        f"{r['disposition']}:{r['reason']}"
                        for r in rows
                        if r["group"] == "oos_legit" and r["disposition"] != "answered"
                    )
                ),
            },
            "per_label": per_label(rows, ("oos_failure", "oos_legit")),
        },
        "completeness_missing_items_among_answered_cohort": dict(
            Counter(
                m
                for r in rows
                if r["group"] in C.COHORT_GROUPS and r["disposition"] == "answered"
                for m in r.get("missing", [])
            )
        ),
    }


def pct(f: dict) -> str:
    return f"{f['n']}/{f['d']}" + (
        f" = {f['rate']:.4f}" if f["rate"] is not None else ""
    )


def render(m: dict) -> str:
    lines = [f"Run {m['tag']}"]

    def line(label: str, value: object) -> None:
        lines.append(f"  {label:34s} {value}")

    for name in ("all_splits", *SPLITS):
        a = m[name]
        c = a["cohort"]
        fx = a["fx_slice"]
        fa = a["false_alarms_all_legitimate"]
        lines += ["", f"== {name} ==", f"all requests: {a['dispositions_all']}"]
        lines.append(
            f"cohort, HEADLINE view: all gradable in-scope cases, no exclusions"
            f" (excl. FX and special): {c['cases']} cases"
        )
        line("dispositions", c["dispositions"])
        line("answered (coverage)", pct(c["answered"]))
        line("abstained", pct(c["abstained"]))
        line("out_of_scope", pct(c["out_of_scope"]))
        line("failed", pct(c["failed"]))
        line("answer precision (exact category)", pct(c["answer_precision"]))
        line(
            "failure vs no-failure accuracy",
            pct(c["failure_vs_no_failure_accuracy_answered"]),
        )
        line("evidence-complete | answered", pct(c["evidence_complete_among_answered"]))
        line("evidence-complete | correct", pct(c["evidence_complete_among_correct"]))
        line("correct and complete / cases", pct(c["correct_and_evidence_complete"]))
        line(
            "search counts reproduce", pct(c["search_counts_reproduce_among_answered"])
        )
        line("false alarms (cohort legit)", pct(c["false_alarms"]))
        line("wrong by kind", c["wrong_by_kind"])
        line("non-answer reasons", c["abstain_or_nonanswer_reasons"])
        w = a["cohort_sensitivity_without_verified_conflicts"]
        n_conf = len(a["label_conflicts"])
        lines.append(
            f"SENSITIVITY view without the {n_conf} verified label conflicts:"
            f" {w['cases']} cases"
        )
        line("answered (coverage)", pct(w["answered"]))
        line("answer precision (exact category)", pct(w["answer_precision"]))
        line("correct and complete / cases", pct(w["correct_and_evidence_complete"]))
        line("false alarms (cohort legit)", pct(w["false_alarms"]))
        line("non-answer reasons", w["abstain_or_nonanswer_reasons"])
        for r in a["label_conflicts"]:
            lines.append(
                f"  verified label conflict {r['case_id']} {r['variant']}:"
                f" {r['outcome']}"
                f" {r['reason'] or r['category']} <- "
                + ", ".join(
                    f"{x['failure_case']} {x['failure_type'][:3]} {x['shared']}"
                    for x in r["label_conflict_with"]
                )
            )
        for r in a["conflict_candidates_unverified"]:
            lines.append(
                f"  conflict candidate, unverified, not excluded {r['case_id']}"
                f" ({r['group']}, {r['variant']}) <- "
                + ", ".join(
                    f"{x['failure_case']} {x['failure_type'][:3]} {x['shared']}"
                    for x in r["conflict_candidate_with"]
                )
            )
        for r in a["review_candidates"]:
            lines.append(
                f"  review candidate, not excluded {r['case_id']} ({r['group']},"
                f" {r['variant']}, {r['outcome']}) <- "
                + ", ".join(
                    f"{x['failure_case']} {x['failure_type'][:3]} {x['shared']}"
                    for x in r["review_candidate_with"]
                )
            )
        line("FX correct abstentions", pct(fx["correct_abstention_unsupported_fx"]))
        line(
            "FX abstentions citing both",
            pct(fx["abstentions_citing_payment_and_other_currency_bank_row"]),
        )
        line("false alarms, all legit", pct(fa["any_legitimate"]))
        line("  cohort variants incl. FX", pct(fa["in_cohort_variants_incl_fx"]))
        line("  AP-side variants", pct(fa["ap_side_variants_out_of_scope"]))
        risk = a["all_clear_on_out_of_scope_failures"]
        line("RISK all-clear on out-of-scope failures", pct(risk["overall"]))
        for fam, frc in risk["per_family"].items():
            if frc["d"]:
                line(f"  {fam}", pct(frc))
        lines.append("per label: cases answered correct complete abstained oos failed")
        for k, v in a["cohort_per_label"].items():
            nums = [
                v[x]
                for x in (
                    "cases",
                    "answered",
                    "correct",
                    "complete_correct",
                    "abstained",
                    "out_of_scope",
                    "failed",
                )
            ]
            lines.append(f"  {k:36s} " + " ".join(f"{n:4d}" for n in nums))
        lines.append("confusion (label -> answered category or disposition):")
        for k, v in a["cohort_confusion"].items():
            lines.append(f"  {k}: {v}")
        o = a["out_of_scope_families"]
        for part in ("failure_families", "ap_side_legitimate"):
            p = o[part]
            lines.append(
                f"out of scope, {part}: {p['cases']} cases, {p['dispositions']}"
            )
            lines.append(f"    answered categories {p['answered_by_kind']}")
            lines.append(f"    non-answer reasons {p['reasons']}")
        for k, v in o["per_label"].items():
            lines.append(
                f"    {k:36s} n={v['cases']:4d} answered={v['answered']:4d} "
                f"{v['answered_categories']}"
            )
        lines.append(f"special cases: {a['special_cases']}")
    return "\n".join(lines) + "\n"


def evaluate(tag: str, upstream: Path = DEFAULT_UPSTREAM) -> None:
    root = REPO / "artifacts/dev-runs" / tag
    manifest = json.loads((root / "run-manifest.json").read_text())
    # Packets must be final: refuse if they changed since the run manifest.
    for split in SPLITS:
        if (
            sha256_file(root / "inference" / split / "packets.jsonl")
            != manifest["packets_sha256"][split]
        ):
            sys.exit(f"{split}: packets.jsonl differs from the run manifest")
    snap = Snap(root / "prepared/snapshot/snapshot.sqlite")
    rows: list[dict] = []
    labels_by_split = {  # labels opened only here, dev splits only
        split: {
            r["case_id"]: r
            for r in read_jsonl(
                upstream / "data/benchmark" / split / "rca_ground_truth.jsonl"
            )
        }
        for split in SPLITS
    }
    all_labels = {c: lab for ls in labels_by_split.values() for c, lab in ls.items()}
    conflicts = C.conflict_report(
        all_labels, snap.con, REPO / "data/private" / tag / "key"
    )
    for split in SPLITS:
        rows += load_split(root, split, labels_by_split[split], snap, conflicts)
    rows.sort(key=lambda r: (r["split"], r["case_id"]))
    metrics = {
        "tag": tag,
        "all_splits": all_metrics(rows),
        **{s: all_metrics([r for r in rows if r["split"] == s]) for s in SPLITS},
    }
    (root / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n"
    )
    (root / "cases.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    )
    errors = [
        r
        for r in rows
        if (
            r["group"] in ("cohort", "fx")
            and r["outcome"] not in ("correct", "correct_abstention")
        )
        or r["group"] in ("contradicted", "not_gradable", "label_conflict")
        or (
            r["group"] in ("oos_failure", "oos_legit")
            and r["disposition"] == "answered"
        )
    ]
    (root / "errors.json").write_text(
        json.dumps(errors, indent=2, sort_keys=True) + "\n"
    )
    (root / "report.txt").write_text(render(metrics))
    print(render(metrics))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("run", "evaluate"))
    ap.add_argument("tag")
    ap.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    args = ap.parse_args()
    if args.command == "run":
        run(args.tag, args.upstream)
    else:
        evaluate(args.tag, args.upstream)


if __name__ == "__main__":
    main()
