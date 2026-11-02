"""M4 drafting results table from saved artifacts (no model calls).

    uv run python evaluation/draft_eval.py --packets PACKETS --drafts DRAFTS_JSONL \
        --ledger LEDGER --out OUT.json

Reads the packets file (for each packet's disposition and its digest), the
`drafts.jsonl` written by `reconcile.runs.draft`, and the spend ledger. It reads
no labels. Rejection reasons are grouped into kinds; a draft with reasons of
several kinds counts once under each kind.
"""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import metrics

# First match wins; the patterns are the validator's reason texts.
REJECTION_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("action_word", re.compile(r"^action word ")),
    ("category", re.compile(r"^category |^calls the case legitimate")),
    ("amount", re.compile(r"^amount ")),
    ("date_or_count", re.compile(r"^date |^day count ")),
    ("coverage", re.compile(r" has no claim$")),
    (
        "finding_binding",
        re.compile(
            r"^claim \d+: (?:unknown finding|finding \S+ is not supported|"
            r"no evidence cited|evidence .* is not in finding|"
            r"calculation .* not in finding)"
        ),
    ),
    ("id", re.compile(r"^unknown (?:token|id) ")),
    ("next_step_action", re.compile(r"^next_step\.action ")),
    (
        "length_or_schema",
        re.compile(
            r"^draft |^summary must|^claims must|^claim \d+: |^next_step[. ]|"
            r"^malformed_json$"
        ),
    ),
)
FAILURE_KINDS = ("max_tokens", "refusal")


def rejection_kind(reason: str) -> str:
    for kind, pattern in REJECTION_KINDS:
        if pattern.search(reason):
            return kind
    return "other"


def failure_kind(reasons: list[str]) -> str:
    return next((r for r in reasons if r in FAILURE_KINDS), "other")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    c = Counter(r["status"] for r in rows)
    attempted = c["accepted"] + c["rejected"] + c["failed"]
    return {
        "rows": len(rows),
        "attempted": attempted,
        "accepted": c["accepted"],
        "rejected": c["rejected"],
        "failed": c["failed"],
        "skipped": c["skipped"],
    }


def evaluate(packets: Path, drafts: Path, ledger: Path) -> dict[str, Any]:
    disposition: dict[str, str] = {}
    for p in _read_jsonl(packets):
        disposition[p["request"]["request_id"]] = p["diagnosis"]["disposition"]
    rows = _read_jsonl(drafts)
    unknown = [r["request_id"] for r in rows if r["request_id"] not in disposition]
    if unknown:
        raise ValueError(f"draft rows without a packet: {unknown[:3]}")
    by_disposition = {
        d: _counts([r for r in rows if disposition[r["request_id"]] == d])
        for d in ("answered", "abstained")
    }
    kinds: Counter[str] = Counter()
    for r in rows:
        if r["status"] == "rejected":
            kinds.update({rejection_kind(x) for x in r["reasons"]})
        elif r["status"] == "failed":
            kinds[failure_kind(r["reasons"])] += 1
    accepted = [r for r in rows if r["status"] == "accepted"]
    bound = sum(r["validation"]["claims_bound_to_findings"] for r in accepted)
    row_cost = sum((Decimal(r["cost_usd"]) for r in rows), Decimal(0))
    ledger_cost = sum(
        (Decimal(e["cost_usd"]) for e in json.loads(ledger.read_text())["entries"]),
        Decimal(0),
    )
    attempted = _counts(rows)["attempted"]
    latencies = [r["latency_s"] for r in rows if r["latency_s"] is not None]
    return {
        "packets_sha256": hashlib.sha256(packets.read_bytes()).hexdigest(),
        "overall": _counts(rows),
        "by_disposition": by_disposition,
        "failure_and_rejection_kinds": dict(sorted(kinds.items())),
        "claims_bound_to_findings": {
            "total": bound,
            "accepted_drafts": len(accepted),
            "per_accepted_draft": bound / len(accepted) if accepted else None,
        },
        "cost_usd": {
            "drafts_rows_total": str(row_cost),
            "ledger_total": str(ledger_cost),
            "per_attempted": str(row_cost / attempted) if attempted else None,
            "per_accepted": str(row_cost / len(accepted)) if accepted else None,
            "input_tokens": sum(r["usage"]["input_tokens"] for r in rows),
            "output_tokens": sum(r["usage"]["output_tokens"] for r in rows),
        },
        "latency_s": {
            "calls": len(latencies),
            "p50": metrics.nearest_rank(latencies, 50),
            "p95": metrics.nearest_rank(latencies, 95),
        },
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packets", type=Path, required=True)
    ap.add_argument("--drafts", type=Path, required=True)
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    result = evaluate(a.packets, a.drafts, a.ledger)
    a.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["overall"], sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
