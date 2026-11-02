"""Development run on the FinRCA train and validation splits (PLAN.md M2).

Evaluator side: it holds the token key, the links and the labels, so it must
never be imported by src/reconcile/. Standard library plus the preparation
adapters (which are evaluator-side code; the inference image deletes them).

    uv run python evaluation/dev_run.py run TAG [--upstream PATH]
    uv run python evaluation/dev_run.py evaluate TAG      # recompute metrics only
    uv run python evaluation/dev_run.py rerun TAG         # determinism check

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
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from decimal import Context, Decimal, Inexact, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cohort as C  # noqa: E402  (shared cohort, groups and label conflicts)
import metrics as M  # noqa: E402  (shared metric definitions, PLAN.md section 7)
from metrics import (  # noqa: E402
    all_clear_risk,
    classify,
    cohort_metrics,
    confusion,
    frac,
    per_label,
)

from reconcile.adapters.finrca import create_key_file, prepare_finrca_snapshot  # noqa: E402
from reconcile.adapters.finrca_requests import prepare_finrca_requests  # noqa: E402
from reconcile.diagnosis.policy import (  # noqa: E402
    CLEARING_LIMITS,
    POLICY_VERSION,
    SCOPE_LIMITATION,
    clearing_policy,
    quantum,
)
from reconcile.domain.calendar import business_days  # noqa: E402
from reconcile.domain.money import MAX_DIGITS  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEFAULT_UPSTREAM = Path(
    "/home/charalambos/Documents/fin-tech-projects/reconcile/data/upstream/FinRCA-AI-Bench"
)
SOURCE_REVISION = "76c93ec"
CUTOFF = date(2026, 6, 30)
SPLITS = ("train", "validation")  # the only splits this script ever names
IMAGE = "reconcile-inference:dev"
SCRIPT = REPO / "deploy/inference/run_inference.sh"
TIMINGS_FILE = "timings.jsonl"  # names written by reconcile.runs.batch
STATS_FILE = "run-stats.json"
SNAPSHOT_FILE = "snapshot.sqlite"
CURRENCY = "USD"
is_failure = C.is_failure
is_no_failure = C.is_no_failure

# Exact evaluator arithmetic: the precision the money type needs for a sum or
# difference of two MAX_DIGITS-digit amounts (reconcile.domain.money), with
# rounding trapped. The default 28-digit context would silently round the
# 30-digit amounts the money type allows.
EXACT = Context(prec=2 * MAX_DIGITS + 5, traps=[Inexact, InvalidOperation])


def dsum(values: Iterable[Decimal]) -> Decimal:
    total = Decimal(0)
    for v in values:
        total = EXACT.add(total, v)
    return total


def dsub(a: Decimal, b: Decimal) -> Decimal:
    return EXACT.subtract(a, b)


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
        "snapshot_file_sha256": sha256_file(snap_dir / SNAPSHOT_FILE),
        "source_revision": SOURCE_REVISION,
        "cutoff": CUTOFF.isoformat(),
        "policy_version": POLICY_VERSION,
        "image": IMAGE,
        "image_id": image_id,
        "requests": summaries,
        "packets_sha256": {
            s: sha256_file(root / "inference" / s / "packets.jsonl") for s in SPLITS
        },
        "requests_file_sha256": {
            s: sha256_file(prepared / "requests" / s / "requests.jsonl") for s in SPLITS
        },
        "links_file_sha256": {
            s: sha256_file(root / "evaluator" / s / "links.jsonl") for s in SPLITS
        },
        "timings_sha256": {
            s: sha256_file(root / "inference" / s / TIMINGS_FILE) for s in SPLITS
        },
        "run_stats_sha256": {
            s: sha256_file(root / "inference" / s / STATS_FILE) for s in SPLITS
        },
        "timing_seconds": timing,
        "command": f"uv run python evaluation/dev_run.py run {tag}",
    }
    (root / "run-manifest.json").write_text(
        json.dumps(run_manifest, indent=2, sort_keys=True) + "\n"
    )
    # Packets are final from here on. Only now are labels opened.
    evaluate(tag, upstream)


def image_problem(manifest_id: str, found_id: str | None) -> str | None:
    """Why the rerun cannot be a valid check, or None. The rerun is only valid
    on the run's own image (protocol integrity.require_same_image_id)."""
    if found_id is None:
        return f"image {manifest_id} is no longer present locally"
    if found_id != manifest_id:
        return f"image id {found_id} differs from the manifest's {manifest_id}"
    return None


def rerun(tag: str) -> None:
    """Re-run the container inference on the run's own snapshot and requests
    into fresh directories and compare packet digests with the run manifest."""
    root = REPO / "artifacts/dev-runs" / tag
    manifest = json.loads((root / "run-manifest.json").read_text())
    verify_digests(root, manifest)  # the inputs being re-run are the run's own
    # Pin the run's own image by id (run_inference.sh honours $IMAGE), so a
    # rebuilt shared tag cannot change what is checked.
    have = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", manifest["image_id"]],
        text=True,
        capture_output=True,
    )
    problem = image_problem(
        manifest["image_id"], have.stdout.strip() if have.returncode == 0 else None
    )
    n = 1
    while (root / f"rerun-{n}").exists() or (root / f"rerun-{n}.json").exists():
        n += 1
    if problem:
        result = {"tag": tag, "rerun": n, "ok": False, "reason": problem}
        (root / f"rerun-{n}.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        sys.exit(f"rerun-{n}: {problem}")
    result: dict = {
        "tag": tag,
        "rerun": n,
        "ok": True,
        "image_id": have.stdout.strip(),
        "image_id_matches_manifest": True,
        "splits": {},
    }
    for split in SPLITS:
        out = root / f"rerun-{n}" / split
        run_cmd(
            [str(SCRIPT), str(root / "prepared/snapshot"),
             str(root / "prepared/requests" / split), str(out)],
            env={**os.environ, "IMAGE": manifest["image_id"]},
        )  # fmt: skip
        new = sha256_file(out / "packets.jsonl")
        result["splits"][split] = {
            "expected_sha256": manifest["packets_sha256"][split],
            "rerun_sha256": new,
            "identical": new == manifest["packets_sha256"][split],
        }
    result["identical"] = all(v["identical"] for v in result["splits"].values())
    result["ok"] = result["identical"]
    (root / f"rerun-{n}.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["identical"]:
        sys.exit(f"rerun-{n}: packets differ from the run manifest")


def verify_digests(root: Path, manifest: dict) -> None:
    """The run manifest must bind every scoring input, for every split: packets,
    requests and evaluator links (checked by `read_split_inputs`, on the bytes
    that are then scored) and the snapshot file. A manifest without them (an
    older run) is refused: re-run rather than score unbound inputs. Timings and
    run stats are checked when recorded."""
    for key in ("packets_sha256", "requests_file_sha256", "links_file_sha256"):
        if set(manifest.get(key) or {}) != set(SPLITS):
            sys.exit(
                f"run manifest has no {key} for every split; "
                "it predates input binding, so it cannot be evaluated: re-run"
            )
    want = manifest.get("snapshot_file_sha256")
    path = root / "prepared/snapshot" / SNAPSHOT_FILE
    if not want or not path.exists() or sha256_file(path) != want:
        sys.exit(f"{SNAPSHOT_FILE} differs from the run manifest")
    for key, name in (
        ("timings_sha256", TIMINGS_FILE),
        ("run_stats_sha256", STATS_FILE),
    ):
        for split, want in manifest.get(key, {}).items():
            path = root / "inference" / split / name
            if not path.exists() or sha256_file(path) != want:
                sys.exit(f"{split}: {name} differs from the run manifest")


def read_split_inputs(
    root: Path, manifest: dict, split: str
) -> tuple[list[dict], list[dict], list[dict]]:
    """(packets, requests, links) of a split. Each file is read once: the bytes
    hashed against the run manifest are the bytes parsed and scored."""
    out = []
    for key, path in (
        ("packets_sha256", root / "inference" / split / "packets.jsonl"),
        ("requests_file_sha256", root / "prepared/requests" / split / "requests.jsonl"),
        ("links_file_sha256", root / "evaluator" / split / "links.jsonl"),
    ):
        data = path.read_bytes() if path.exists() else b""
        if hashlib.sha256(data).hexdigest() != manifest[key][split]:
            sys.exit(f"{split}: {path.name} differs from the run manifest")
        out.append(parse_jsonl(data))
    return out[0], out[1], out[2]


def efficiency_block(
    root: Path, manifest: dict, correct_complete: dict[str, int]
) -> dict:
    """Per-split and all-split efficiency from the batch runner's timings and
    run stats. `correct_complete` is the headline-view correct and
    evidence-complete count per split. Runs recorded before these files existed
    give None with a note. The investigator makes no paid calls, so the cost
    per case is zero."""
    if "timings_sha256" not in manifest or "run_stats_sha256" not in manifest:
        return {"efficiency": None, "note": "run predates timings.jsonl/run-stats.json"}
    seconds: dict[str, list[float]] = {}
    rss: dict[str, int] = {}
    for split in SPLITS:
        d = root / "inference" / split
        seconds[split] = [r["seconds"] for r in read_jsonl(d / TIMINGS_FILE)]
        rss[split] = json.loads((d / STATS_FILE).read_text())["peak_rss_kib"]

    def one(secs: list[float], peak: int, ok: int) -> dict:
        e = M.efficiency(secs, peak, Decimal("0"), ok)
        return {k: str(v) if isinstance(v, Decimal) else v for k, v in e.items()} | {
            "currency": CURRENCY
        }

    out = {s: one(seconds[s], rss[s], correct_complete[s]) for s in SPLITS}
    out["all_splits"] = one(
        [x for s in SPLITS for x in seconds[s]],
        max(rss.values()),
        sum(correct_complete.values()),
    )
    return {"efficiency": out, "note": None}


# -------------------------------------------------------------- evaluation
# Evidence completeness is computed from the packet and the prepared snapshot
# against docs/evidence-contract.md (the required records and searches of the
# category the packet claims). Upstream evidence_ids are not used.


def journal_totals(
    lines: list[dict], source: str, kind: str
) -> tuple[str, Decimal, Decimal] | None:
    """(currency, attributable debit, whole-journal debit) of a journal given ALL
    its observable lines, or None. Both contract conditions are required: (a)
    one currency, no negative line amount, and the whole journal's debit equals
    its credit and is positive; (b) the lines of type `kind` sourced to `source`
    are themselves balanced and positive (docs/evidence-contract.md section 3)."""
    if len({x["currency"] for x in lines}) != 1:
        return None
    own = [
        x
        for x in lines
        if x["source_transaction_id"] == source and x["transaction_type"] == kind
    ]
    try:
        if any(Decimal(x[side]) < 0 for x in lines for side in ("debit", "credit")):
            return None
        total = (
            dsum(Decimal(x["debit"]) for x in lines),
            dsum(Decimal(x["credit"]) for x in lines),
        )
        mine = (
            dsum(Decimal(x["debit"]) for x in own),
            dsum(Decimal(x["credit"]) for x in own),
        )
    except ArithmeticError:
        return None
    if total[0] != total[1] or total[0] <= 0 or mine[0] != mine[1] or mine[0] <= 0:
        return None
    return lines[0]["currency"], mine[0], total[0]


class Snap:
    """Read-only view of the prepared snapshot (tokens only)."""

    def __init__(self, path: Path, digest: str) -> None:
        # `digest` is the snapshot content digest every cited record must carry.
        self.digest = digest
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

    def journal_lines(self, journal: str) -> list[dict]:
        """Every observable line of the journal, whatever its source or type."""
        return self.q(
            "SELECT * FROM gl_entries WHERE journal_id = ? AND posting_date <= ?",
            journal,
            CUTOFF.isoformat(),
        )

    def balanced_journals(self, source: str, kind: str) -> list[str]:
        """Journals with a line of type `kind` sourced to `source` that pass
        `journal_totals` (docs/evidence-contract.md section 3, conditions a, b)."""
        ids = self.q(
            "SELECT DISTINCT journal_id FROM gl_entries WHERE source_transaction_id = ?"
            " AND transaction_type = ? AND posting_date <= ? ORDER BY journal_id",
            source,
            kind,
            CUTOFF.isoformat(),
        )
        return [
            r["journal_id"]
            for r in ids
            if journal_totals(self.journal_lines(r["journal_id"]), source, kind)
        ]

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
        self.day_counts = {d["calculation_id"]: d for d in packet["day_counts"]}

    def _key(self, evidence_id: str) -> tuple[str, str] | None:
        e = self.by_id.get(evidence_id)
        return (
            (e["record"]["table"], e["record"]["record_id"])
            if e and e["record"]
            else None
        )

    def cites(self, table: str, ids: list[str]) -> bool:
        return bool(ids) and all((table, i) in self.records for i in ids)

    def search(self, table: str, count: int, predicate: str) -> bool:
        """A search of `table` with exactly the predicate the evaluator built for
        this requirement (whitespace aside) and the stated count. A predicate that
        contains the right condition plus a restriction is a different search."""
        want = " ".join(predicate.split())
        return any(
            s["table"] == table
            and s["observed_count"] == count
            and " ".join(s["predicate"].split()) == want
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
        try:
            ops = [
                (
                    self._key(o["evidence_id"]),
                    o["field"],
                    Decimal(o["amount"].split()[0]),
                )
                for o in c["operands"]
            ]
            return (
                ops,
                Decimal(c["result"].split()[0]),
                Decimal(c["tolerance"].split()[0]),
            )
        except (ArithmeticError, IndexError):  # malformed amount text
            return None

    def day_count(self, calc_id: str) -> dict | None:
        return self.day_counts.get(calc_id)


def alloc_ids(rows: list[dict]) -> list[str]:
    return [f"{r['payment_id']}|{r['invoice_id']}" for r in rows]


def bank_ids(rows: list[dict]) -> list[str]:
    return [r["bank_transaction_id"] for r in rows]


# The absence searches a category requires, with the exact predicate text the
# evaluator expects (docs/evidence-contract.md section 4.2 and 4.3). Built here,
# not copied from the packet: a search that also carries an extra restriction
# is a different, narrower search.


def a1_predicate(ref: str) -> str:
    return (
        f"payment_reference = '{ref}' AND transaction_type <> 'bank fee'"
        f" AND posted_date <= '{CUTOFF.isoformat()}'"
    )


def a2_predicate(ref: str) -> str:
    return f"reference_number = '{ref}'"


def a3_predicate(pay: dict) -> str:
    return (
        "direction = 'debit' AND transaction_type <> 'bank fee'"
        f" AND currency = '{pay['payment_currency']}'"
        f" AND amount = '{pay['payment_amount']}'"
        f" AND counterparty_token = '{pay['bank_account_id']}'"
        f" AND posted_date <= '{CUTOFF.isoformat()}'"
        " AND payment_reference matches no payment"
    )


def fee_search_predicate(bank_id: str) -> str:
    return (
        f"transaction_type = 'bank_fee' AND source_transaction_id = '{bank_id}'"
        f" AND posting_date <= '{CUTOFF.isoformat()}'"
    )


def lag_ok(v: View, pay: dict, deb_key: tuple[str, str] | None) -> bool:
    """`business_day_lag`: payment date to the debit's posted date, within the
    method's clearing limit."""
    return deb_key is not None and day_count_ok(
        v, pay, "business_day_lag", (deb_key, "posted_date"), over_limit=False
    )


def committed_overpayment_ok(
    s: Snap, v: View, inv: dict | None, committed_allocs: list[dict]
) -> bool:
    """F06 (contract 4.1): `invoice_total_minus_committed` is the invoice total
    minus exactly the committed allocations (operation `difference`), every
    operand equal to the snapshot, the result recomputed, and below minus one
    quantum of the invoice currency (committed exceeds the invoice total)."""
    got = v.calculation("invoice_total_minus_committed", "difference")
    if got is None or inv is None or not committed_allocs:
        return False
    ops, result, tol = got
    q = quantum(inv["currency"]).amount
    first = ops[0]
    return (
        first[:2] == (("invoices", inv["invoice_id"]), "invoice_total")
        and first[2] == Decimal(inv["invoice_total"])
        and operands_match(
            ops[1:], "allocated_amount", allocation_amounts(s, committed_allocs)
        )
        and result == dsub(first[2], dsum(a for _, _, a in ops[1:]))
        and tol == q
        and result < q.copy_negate()
    )


def delta_ok(v: View, pay: dict, debit: dict | None, shortfall: bool) -> bool:
    """`delta` is payment amount minus bank amount, exactly those two operands
    (a sum of the same two figures does not do), operands equal to the snapshot.
    `shortfall`: delta above one quantum (F14); else |delta| within one quantum
    (the amounts match)."""
    got = v.calculation("delta", "difference")
    if got is None or debit is None:
        return False
    ops, result, tol = got
    q = _quantum(pay)
    debit_key = ("bank_transactions", debit["bank_transaction_id"])
    if not (
        len(ops) == 2
        and ops[0][:2] == (("payments", pay["payment_id"]), "payment_amount")
        and ops[0][2] == Decimal(pay["payment_amount"])
        and operands_match(ops[1:], "amount", {debit_key: Decimal(debit["amount"])})
        and result == dsub(ops[0][2], ops[1][2])
        and tol == q
    ):
        return False
    return result > q if shortfall else result.copy_abs() <= q


def journal_amounts(s: Snap, source: str, kind: str) -> dict[tuple[str, str], Decimal]:
    """Journal id -> total debit of the journal's lines of type `kind` sourced to
    `source`: the figure a calculation compares (a journal can also carry lines
    sourced elsewhere)."""
    out: dict[tuple[str, str], Decimal] = {}
    for j in s.balanced_journals(source, kind):
        totals = journal_totals(s.journal_lines(j), source, kind)
        if totals:
            out[("gl_entries", j)] = totals[1]
    return out


def difference_within(
    v: View,
    calc_id: str,
    head: list[tuple[tuple[str, str], str, Decimal]],
    rest: dict[tuple[str, str], Decimal],
    rest_field: str,
    q: Decimal,
) -> bool:
    """`calc_id` is a `difference` whose operands are exactly `head` in order
    (record, field, snapshot amount) followed by the `rest` records (any order,
    field `rest_field`, snapshot amounts), whose result is recomputed as the
    first operand minus the others, with tolerance one quantum, and that lies
    within one quantum of zero."""
    got = v.calculation(calc_id, "difference")
    if got is None:
        return False
    ops, result, tol = got
    n = len(head)
    return (
        len(ops) >= n
        and ops[:n] == head
        and operands_match(ops[n:], rest_field, rest)
        and result == dsub(ops[0][2], dsum(a for _, _, a in ops[1:]))
        and tol == q
        and result.copy_abs() <= q
    )


def net_fee_ok(s: Snap, v: View, pay: dict, debit: dict) -> bool:
    """Net-fee accounting (contract 4.3, 5): `delta` is a shortfall and
    `unexplained` = payment - debit - the bank_fee journals sourced to the
    debit, within one quantum."""
    bid = debit["bank_transaction_id"]
    head = [
        (
            ("payments", pay["payment_id"]),
            "payment_amount",
            Decimal(pay["payment_amount"]),
        ),
        (("bank_transactions", bid), "amount", Decimal(debit["amount"])),
    ]
    return delta_ok(v, pay, debit, shortfall=True) and difference_within(
        v,
        "unexplained",
        head,
        journal_amounts(s, bid, "bank_fee"),
        "amount",
        _quantum(pay),
    )


def accounted_fee_ok(s: Snap, v: View, row: dict) -> bool:
    """Accounted bank fee (contract 5): `fee_row_minus_journals` = fee row amount
    minus the bank_fee journals sourced to the fee row, within one quantum."""
    bid = row["bank_transaction_id"]
    head = [(("bank_transactions", bid), "amount", Decimal(row["amount"]))]
    return difference_within(
        v,
        "fee_row_minus_journals",
        head,
        journal_amounts(s, bid, "bank_fee"),
        "amount",
        quantum(row["currency"]).amount,
    )


def reversal_ok(s: Snap, v: View, pay: dict, debit: dict, credit: dict) -> bool:
    """Payment reversal (contract 5): debit = credit = the reversal journal
    amount = the payment amount, each as a difference of exactly those two
    records, within one quantum. The caller supplies the one posted debit and
    the one posted reversal credit."""
    pid = pay["payment_id"]
    journals = journal_amounts(s, pid, "payment_reversal")
    # ISO dates: the order is checked here, not left to the investigator
    if len(journals) != 1 or credit["posted_date"] < debit["posted_date"]:
        return False
    ((jkey, jamt),) = journals.items()
    d = (
        ("bank_transactions", debit["bank_transaction_id"]),
        "amount",
        Decimal(debit["amount"]),
    )
    c = (
        ("bank_transactions", credit["bank_transaction_id"]),
        "amount",
        Decimal(credit["amount"]),
    )
    j = (jkey, "amount", jamt)
    p = (("payments", pid), "payment_amount", Decimal(pay["payment_amount"]))
    q = _quantum(pay)
    steps = [
        ("debit_minus_credit", [d, c]),
        ("credit_minus_journal", [c, j]),
        ("journal_minus_payment", [j, p]),
    ]
    return all(difference_within(v, cid, h, {}, "amount", q) for cid, h in steps)


def day_count_ok(
    v: View,
    pay: dict,
    calc_id: str,
    end: tuple[tuple[str, str], str] | None,
    over_limit: bool,
) -> bool:
    """The day count `calc_id` runs from the payment's `payment_date` to `end`
    (a cited record and field; None = the cutoff), carries the method-specific
    limit and policy name declared in reconcile.diagnosis.policy (not whatever
    limit the packet states), and is above that limit (`over_limit`) or not.
    The count itself is checked against the snapshot dates by the packet audit."""
    d = v.day_count(calc_id)
    method = pay["payment_method"]
    limit = CLEARING_LIMITS.get(method)
    if d is None or limit is None:
        return False
    same_end = (
        d["end_evidence"] is None
        if end is None
        else (v._key(d["end_evidence"] or "") == end[0] and d["end_field"] == end[1])
    )
    return (
        v._key(d["start_evidence"]) == ("payments", pay["payment_id"])
        and d["start_field"] == "payment_date"
        and same_end
        and d["limit"] == limit
        and d["limit_policy"] == clearing_policy(method)
        and (d["result"] > limit) == over_limit
    )


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
        add("fee_calculation", row is not None and accounted_fee_ok(s, v, row))
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
            committed_ids = {p["payment_id"] for p in committed}
            add(
                "overpayment_calculation",
                committed_overpayment_ok(
                    s, v, inv, [a for a in allocs if a["payment_id"] in committed_ids]
                ),
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
    ref = pay["reference_number"]
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
            "search_a1_no_bank_row", v.search("bank_transactions", 0, a1_predicate(ref))
        )
        add("search_a2_unique_reference", v.search("payments", 1, a2_predicate(ref)))
        add(
            "search_a3_no_candidate",
            v.search("bank_transactions", 0, a3_predicate(pay)),
        )
        add(
            "business_day_age",
            day_count_ok(v, pay, "business_day_age", None, category == "F12"),
        )
    elif category == "F14":
        add(
            "posted_debit", one_debit and v.cites("bank_transactions", bank_ids(debits))
        )
        add(
            "delta_calculation",
            one_debit and delta_ok(v, pay, debits[0], shortfall=True),
        )
        add(
            "search_no_fee_journal",
            one_debit
            and v.search(
                "gl_entries",
                0,
                fee_search_predicate(debits[0]["bank_transaction_id"]),
            ),
        )
    elif category in ("legitimate_weekend_clearing", "legitimate_check_clearing"):
        add(
            "posted_debit", one_debit and v.cites("bank_transactions", bank_ids(debits))
        )
        add(
            "amount_equality_calculation",
            one_debit and delta_ok(v, pay, debits[0], shortfall=False),
        )
        add("business_day_lag", lag_ok(v, pay, deb_key))
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
        rev = [r for r in credits if r["transaction_type"] == "payment reversal"]
        add(
            "reversal_calculations",
            one_debit and len(rev) == 1 and reversal_ok(s, v, pay, debits[0], rev[0]),
        )
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
            "unexplained_calculation",
            one_debit and net_fee_ok(s, v, pay, debits[0]),
        )
        add(
            "allocations",
            bool(allocs) and v.cites("payment_allocations", alloc_ids(allocs)),
        )
    elif category == "no_exception":
        add(
            "posted_debit", one_debit and v.cites("bank_transactions", bank_ids(debits))
        )
        add("allocations", v.cites("payment_allocations", alloc_ids(allocs)))
        add(
            "delta_calculation",
            one_debit and delta_ok(v, pay, debits[0], shortfall=False),
        )
        add("business_day_lag", lag_ok(v, pay, deb_key))
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


def operands_match(
    rest: list[tuple[tuple[str, str] | None, str, Decimal]],
    field: str,
    expected: dict[tuple[str, str], Decimal],
) -> bool:
    """The operands after the first are exactly the expected records, each with
    the field `field` and the amount the snapshot gives (not the packet's word)."""
    keys = [k for k, _, _ in rest]
    return (
        None not in keys
        and sorted(k for k in keys if k) == sorted(expected)
        and all(f == field for _, f, _ in rest)
        and all(expected[k] == a for k, _, a in rest if k)
    )


def allocation_amounts(s: Snap, allocs: list[dict]) -> dict[tuple[str, str], Decimal]:
    return {
        ("payment_allocations", f"{a['payment_id']}|{a['invoice_id']}"): Decimal(
            a["allocated_amount"]
        )
        for a in allocs
    }


def attributable_journal_amounts(s: Snap, p: dict) -> dict[tuple[str, str], Decimal]:
    """The payment journals' amounts: the figure to compare with the payment."""
    return journal_amounts(s, p["payment_id"], "payment")


def journal_attribution_ok(v: View, p: dict) -> bool:
    """Each journal operand of this payment's journal calculation records, where
    it records them at all, its source and GL type, and they must be this payment
    and `payment`. Other journals a packet cites (the other payment of a split,
    bank-fee and reversal journals) are checked by their own requirements."""
    calc = v.calc_by_id.get(_journal_calc_id(p))
    for o in (calc or {"operands": []})["operands"][1:]:
        e = v.by_id.get(o["evidence_id"])
        vals = (e["values"] if e else None) or {}
        src = vals.get("source") or vals.get("source_transaction_id")
        kind = vals.get("gl_type") or vals.get("transaction_type")
        if (src is not None and src != p["payment_id"]) or (
            kind is not None and kind != "payment"
        ):
            return False
    return True


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
    exactly the payment's journals), every operand equal to the snapshot (the
    payment amount, and for each journal the total of its lines of type payment
    sourced to the payment), the result recomputed, and within one quantum."""
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
    if not operands_match(rest, "amount", attributable_journal_amounts(s, p)):
        return False
    return (
        journal_attribution_ok(v, p)
        and result == dsub(ops[0][2], dsum(a for _, _, a in rest))
        and tol == _quantum(p)
        and result.copy_abs() <= tol
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
        rest = ops[1:]
        if not (
            first
            and first[:2] == (("invoices", inv), "invoice_total")
            and first[2] == Decimal(row["invoice_total"])
            and operands_match(rest, "allocated_amount", allocation_amounts(s, allocs))
            and result == dsub(first[2], dsum(a for _, _, a in rest))
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
    allocs = s.allocs_of_payment(pid)
    got = v.calculation(_over_alloc_calc_id(p), "difference")
    if not allocs or got is None:
        return False
    ops, result, tol = got
    rest = ops[1:]
    return bool(
        ops
        and ops[0][:2] == (("payments", pid), "payment_amount")
        and ops[0][2] == Decimal(p["payment_amount"])
        and operands_match(rest, "allocated_amount", allocation_amounts(s, allocs))
        and result == dsub(ops[0][2], dsum(a for _, _, a in rest))
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
    of its category holds, every absence search it states reproduces on the
    snapshot (a false absence claim makes the packet incomplete), AND the packet
    audit passes (every cited record resolves, every calculation and day count
    is exact, every finding's references are valid). A packet that fails the
    audit is not an evidence-complete success, whatever it cites."""
    reqs = required(packet["diagnosis"]["category"], entity, snap, View(packet))
    audit = packet_audit(packet, snap)
    missing = [n for n, ok in reqs if not ok]
    if audit["searches_mismatch"]:
        missing.append("searches_reproduce")
    contract_met = bool(reqs) and not missing
    if not audit["audit_ok"]:
        missing.append("packet_audit")
    return {
        "contract_defined": bool(reqs),
        "req_total": len(reqs),
        "req_met": sum(ok for _, ok in reqs),
        "contract_complete": contract_met,
        "complete": contract_met and audit["audit_ok"],
        "missing": missing,
        **audit,
    }


# ------------------------------------------------------------ packet audit
# Per-packet facts for the PLAN.md section 7 metrics (citation resolution,
# reference integrity, monetary correctness). Aggregation lives in metrics.py.

KEY_COLUMN = {
    "payments": "payment_id",
    "invoices": "invoice_id",
    "bank_transactions": "bank_transaction_id",
    "approval_events": "approval_event_id",
}
MONEY = re.compile(r"-?\d+(\.\d+)? [A-Z]{3}")
JOURNAL_VALUES = {"amount", "journal_total", "currency", "source", "gl_type"}
JOURNAL_TIMESTAMPS = {"posting_date"}


def _value_matches(packet_val: str, snap_val: str, currency: str | None) -> bool:
    """Money strings ("12.50 USD") compare as Decimal amount plus currency, any
    other value as text."""
    if MONEY.fullmatch(packet_val):
        amount, cur = packet_val.split()
        try:
            return currency in (None, cur) and Decimal(amount) == Decimal(snap_val)
        except ArithmeticError:
            return False
    return packet_val == snap_val


def _journal_matches(values: dict, timestamps: dict, journal: str, snap: Snap) -> bool:
    """A journal item: `source`/`gl_type` name lines of the journal, `amount` is
    the total debit of those lines, `journal_total` the whole journal's debit,
    `posting_date` its latest posting date. Only these fields exist; any other
    field, or a journal that is not balanced as a whole and on the named lines,
    does not resolve."""
    lines = snap.journal_lines(journal)
    totals = journal_totals(lines, values.get("source", ""), values.get("gl_type", ""))
    if (
        totals is None
        or not set(values) <= JOURNAL_VALUES
        or not set(timestamps) <= JOURNAL_TIMESTAMPS
        or values.get("currency") != totals[0]
    ):
        return False
    if "posting_date" in timestamps and timestamps["posting_date"] != max(
        x["posting_date"] for x in lines
    ):
        return False
    currency, amount, total = totals
    expected = {"amount": amount, "journal_total": total}
    return all(
        k not in values
        or (
            MONEY.fullmatch(values[k]) is not None
            and values[k].split()[1] == currency
            and Decimal(values[k].split()[0]) == v
        )
        for k, v in expected.items()
    )


def evidence_resolves(e: dict, snap: Snap) -> bool:
    """The cited record is in this run's snapshot (digest), exists there, and
    every recorded value and timestamp is a field of it with the snapshot's
    value. Search-only items have no record and are not handled here."""
    table, rid = e["record"]["table"], e["record"]["record_id"]
    if e["record"].get("snapshot") != snap.digest:
        return False
    values = e["values"] or {}
    timestamps = e.get("timestamps") or {}
    if table == "gl_entries":
        return _journal_matches(values, timestamps, rid, snap)
    if table == "payment_allocations":
        pid, _, iid = rid.partition("|")
        row = snap.one(
            "SELECT * FROM payment_allocations WHERE payment_id = ? AND invoice_id = ?",
            pid,
            iid,
        )
        pay = snap.payment(pid)
        currency = pay["payment_currency"] if pay else None
    elif table in KEY_COLUMN:
        row = snap.one(f"SELECT * FROM {table} WHERE {KEY_COLUMN[table]} = ?", rid)  # noqa: S608
        currency = (row or {}).get("currency") or (row or {}).get("payment_currency")
    else:
        return False
    return (
        row is not None
        and all(
            k in row and _value_matches(v, str(row[k]), currency)
            for k, v in values.items()
        )
        and all(k in row and v == str(row[k]) for k, v in timestamps.items())
    )


def calculation_exact(c: dict, by_id: dict, resolved: dict[str, bool]) -> bool:
    """Every operand is a resolved evidence item whose recorded `field` equals
    the operand's amount, all in one currency, and the stated result equals the
    sum (first minus the rest for a difference) of the operands exactly, in the
    exact Decimal context (no rounding)."""
    amounts: list[Decimal] = []
    cur: set[str] = set()
    for o in c["operands"]:
        e = by_id.get(o["evidence_id"])
        if (
            e is None
            or not e["record"]
            or not resolved[o["evidence_id"]]
            or (e["values"] or {}).get(o["field"]) != o["amount"]
            or not MONEY.fullmatch(o["amount"])
        ):
            return False
        a, k = o["amount"].split()
        amounts.append(Decimal(a))
        cur.add(k)
    if not amounts or not MONEY.fullmatch(c["result"]):
        return False
    r, k = c["result"].split()
    if cur != {k}:
        return False
    try:
        if c["operation"] == "sum":
            want = dsum(amounts)
        elif c["operation"] == "difference":
            want = dsub(amounts[0], dsum(amounts[1:]))
        else:
            return False
    except ArithmeticError:  # more digits than the money type allows
        return False
    return Decimal(r) == want


def _snapshot_day(e: dict, field: str, snap: Snap) -> date | None:
    """The date the snapshot holds in `field` of the cited record, None if the
    record has no such field or it is not a date. A journal has one date field,
    `posting_date` (its latest line)."""
    table, rid = e["record"]["table"], e["record"]["record_id"]
    if table == "gl_entries":
        if field != "posting_date":
            return None
        value = max((x["posting_date"] for x in snap.journal_lines(rid)), default=None)
    elif table == "payment_allocations":
        pid, _, iid = rid.partition("|")
        row = snap.one(
            "SELECT * FROM payment_allocations WHERE payment_id = ? AND invoice_id = ?",
            pid,
            iid,
        )
        value = (row or {}).get(field)
    elif table in KEY_COLUMN:
        row = snap.one(f"SELECT * FROM {table} WHERE {KEY_COLUMN[table]} = ?", rid)  # noqa: S608
        value = (row or {}).get(field)
    else:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def day_count_exact(
    d: dict, by_id: dict, resolved: dict[str, bool], snap: Snap, cutoff: date
) -> bool:
    """Both cited dates are the snapshot's (cited items resolve) and `result` is
    the business-day count between them (weekdays in (start, end]); an absent end
    runs to the request cutoff."""
    ends = [(d["start_evidence"], d["start_field"])]
    if d["end_evidence"] is not None:
        ends.append((d["end_evidence"], d["end_field"]))
    days: list[date] = []
    for ev, field in ends:
        e = by_id.get(ev)
        day = (
            _snapshot_day(e, field, snap)
            if e is not None and e["record"] and resolved[ev]
            else None
        )
        if day is None:
            return False
        days.append(day)
    if d["end_evidence"] is None:
        days.append(cutoff)
    return days[1] >= days[0] and business_days(days[0], days[1]) == d["result"]


def packet_audit(packet: dict, snap: Snap) -> dict:
    """Counts for an answered packet: cited record items and how many resolve;
    calculations and day counts and how many are exact; findings and how many
    have valid references (see metrics.reference_integrity: this is NOT a check
    that the finding's assertion is true). `audit_ok` is the hard gate for a
    primary success; `completeness` also requires every stated search to
    reproduce (reported apart, as `searches_mismatch`)."""
    by_id = {e["evidence_id"]: e for e in packet["evidence"]}
    mismatch = searches_reproduce(packet, snap)
    bad_search = {n.split(":")[0] for n in mismatch}
    resolved = {
        i: (evidence_resolves(e, snap) if e["record"] else i not in bad_search)
        for i, e in by_id.items()
    }
    exact = {
        c["calculation_id"]: calculation_exact(c, by_id, resolved)
        for c in packet["calculations"]
    }
    cutoff = date.fromisoformat(packet["request"]["cutoff"])
    counts = {
        d["calculation_id"]: day_count_exact(d, by_id, resolved, snap, cutoff)
        for d in packet["day_counts"]
    }
    valid = exact | counts
    findings = packet["diagnosis"]["findings"]
    # A finding that cites nothing (no evidence, no calculation) establishes
    # nothing: it is counted as not valid, not passed by an empty `all`.
    referenced_ok = sum(
        bool(f["evidence_ids"] or f["calculation_ids"])
        and all(resolved.get(i, False) for i in f["evidence_ids"])
        and all(valid.get(i, False) for i in f["calculation_ids"])
        for f in findings
    )
    cited_total = sum(bool(e["record"]) for e in by_id.values())
    cited_resolved = sum(bool(e["record"]) and resolved[i] for i, e in by_id.items())
    return {
        "cited_total": cited_total,
        "cited_resolved": cited_resolved,
        "findings": len(findings),
        "findings_reference_ok": referenced_ok,
        "calcs": len(packet["calculations"]),
        "calcs_exact": sum(exact.values()),
        "day_counts": len(counts),
        "day_counts_exact": sum(counts.values()),
        "searches_mismatch": mismatch,
        "audit_ok": (
            cited_resolved == cited_total
            and sum(exact.values()) == len(exact)
            and sum(counts.values()) == len(counts)
            and referenced_ok == len(findings)
        ),
    }


# ------------------------------------------------------------------ joining


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def parse_jsonl(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode().splitlines() if line.strip()]


TABLE_NAME = re.compile(r"[a-z_]+")


def searches_reproduce(packet: dict, snap: Snap) -> list[str]:
    """Searches whose stated count differs when the predicate is re-run on the
    snapshot (docs/evidence-contract.md principle 3). Returns mismatch notes. A
    search that cannot be run (malformed SQL, unknown table) is invalid
    evidence, reported as a mismatch, never an exception."""
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
        try:
            if not TABLE_NAME.fullmatch(s["table"]):
                raise sqlite3.Error("bad table name")
            n = snap.con.execute(
                f"SELECT COUNT(*) FROM {s['table']} WHERE {pred}"  # noqa: S608
            ).fetchone()[0]
        except sqlite3.Error:
            bad.append(f"{e['evidence_id']}:malformed")
            continue
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


def case_row(
    split: str,
    case_id: str,
    lab: dict,
    packet: dict,
    entity: dict,
    snap: Snap,
    conflicts: C.Conflicts,
) -> dict:
    """The scored row of one case: label facts, the packet's disposition and,
    for an answered packet, evidence completeness including the packet audit."""
    d = packet["diagnosis"]
    group = C.group_of(
        lab["failure_type"], lab["scenario_variant"], case_id, conflicts.verified
    )
    row = {
        "split": split,
        "case_id": case_id,
        "failure_type": lab["failure_type"],
        "variant": lab["scenario_variant"],
        "difficulty": lab.get("difficulty"),
        "group_vendor_id": lab.get("group_vendor_id"),
        "group": group,
        "disposition": d["disposition"],
        "category": d["category"],
        "reason": d["reason"],
        "label_conflict_with": conflicts.verified.get(case_id, []),
        "conflict_candidate_with": conflicts.candidates.get(case_id, []),
        "review_candidate_with": conflicts.review.get(case_id, []),
        "entity_table": entity["table"],
        "expected_category": C.expected_category(
            lab["failure_type"], lab["scenario_variant"]
        ),
    }
    classify(row)
    row["searches_mismatch"] = searches_reproduce(packet, snap)
    if group == "fx" and d["disposition"] == "abstained":
        row["fx_cites_ok"] = fx_abstention_cites(packet)
    if d["disposition"] == "answered":
        row |= completeness(packet, (entity["table"], entity["record_id"]), snap)
    return row


def load_split(
    split: str,
    labels_by_case: dict,
    snap: Snap,
    conflicts: C.Conflicts,
    packets: list[dict],
    requests: list[dict],
    links: list[dict],
) -> list[dict]:
    """Score one split from its already-verified packets, requests and links.
    The scored case set must be exactly the label roster of the split (every
    label case has one request, one link and one packet), and every packet must
    answer the request it was prepared for: same request, snapshot and cutoff.
    Anything else is refused, never scored on a reduced set."""
    link_of = {r["request_id"]: r["case_id"] for r in links}
    request_of = {r["request_id"]: r for r in requests}
    ids = [p["request"]["request_id"] for p in packets]
    if len(link_of) != len(links) or len(request_of) != len(requests):
        sys.exit(f"{split}: duplicate request ids in requests or links")
    if sorted(ids) != sorted(link_of) or set(ids) != set(request_of):
        sys.exit(f"{split}: packets, requests and links do not cover the same cases")
    cases = sorted(link_of.values())
    if cases != sorted(labels_by_case):
        missing = sorted(set(labels_by_case) - set(cases))[:5]
        extra = sorted(set(cases) - set(labels_by_case))[:5]
        sys.exit(
            f"{split}: scored cases differ from the label roster"
            f" (missing {missing}, extra {extra})"
        )
    rows = []
    for p in packets:
        rid = p["request"]["request_id"]
        req = request_of[rid]
        if (
            p["request"] != req
            or req["snapshot"] != snap.digest
            or req["cutoff"] != CUTOFF.isoformat()
        ):
            sys.exit(f"{split}: packet {rid} does not answer its prepared request")
        case_id = link_of[rid]
        rows.append(
            case_row(
                split,
                case_id,
                labels_by_case[case_id],
                p,
                req["primary_entity"],
                snap,
                conflicts,
            )
        )
    return rows


# ------------------------------------------------------------------ metrics


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
        "root_cause_accuracy_by_family": M.root_cause_accuracy_by(rows, "failure_type"),
        "root_cause_accuracy_by_difficulty": M.root_cause_accuracy_by(
            rows, "difficulty"
        ),
        "evidence_recall": M.evidence_recall(rows),
        "citation_resolution": M.citation_resolution(rows),
        "reference_integrity": M.reference_integrity(rows),
        "monetary_correctness": M.monetary_correctness(rows),
        "execution_reliability": M.execution_reliability(rows),
        "false_alarms_by": M.false_alarms_by(legit_all),
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
    eff = m.get("efficiency")
    lines.append("")
    if eff is None:
        lines.append(f"efficiency: none ({m.get('note')})")
    else:
        lines.append(
            "== efficiency (attempted cases, investigator makes no paid calls) =="
        )
        for name, e in eff.items():
            lines.append(
                f"  {name:11s} n={e['attempted']} p50={e['latency_p50_s']}s"
                f" p95={e['latency_p95_s']}s"
                f" throughput={e['throughput_cases_per_s']} cases/s"
            )
            lines.append(
                f"    peak_rss_kib={e['peak_rss_kib']}"
                f" cost/case={e['cost_per_attempted_case']} {e['currency']}"
                f" cost/correct-complete={e['cost_per_correct_complete_packet']}"
            )
    return "\n".join(lines) + "\n"


def evaluate(tag: str, upstream: Path = DEFAULT_UPSTREAM) -> None:
    root = REPO / "artifacts/dev-runs" / tag
    manifest = json.loads((root / "run-manifest.json").read_text())
    # Inputs must be final: refuse if any changed since the run manifest, and
    # read them before any label is opened.
    verify_digests(root, manifest)
    inputs = {s: read_split_inputs(root, manifest, s) for s in SPLITS}
    snap = Snap(
        root / "prepared/snapshot" / SNAPSHOT_FILE, manifest["snapshot_content_sha256"]
    )
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
        rows += load_split(
            split, labels_by_split[split], snap, conflicts, *inputs[split]
        )
    rows.sort(key=lambda r: (r["split"], r["case_id"]))
    metrics = {
        "tag": tag,
        "all_splits": all_metrics(rows),
        **{s: all_metrics([r for r in rows if r["split"] == s]) for s in SPLITS},
    }
    ok = {s: metrics[s]["cohort"]["correct_and_evidence_complete"]["n"] for s in SPLITS}
    metrics |= efficiency_block(root, manifest, ok)
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
    ap.add_argument("command", choices=("run", "evaluate", "rerun"))
    ap.add_argument("tag")
    ap.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    args = ap.parse_args()
    if args.command == "run":
        run(args.tag, args.upstream)
    elif args.command == "rerun":
        rerun(args.tag)
    else:
        evaluate(args.tag, args.upstream)


if __name__ == "__main__":
    main()
