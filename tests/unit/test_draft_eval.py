"""The M4 results table from synthetic saved artifacts (evaluation/draft_eval.py)."""

import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
draft_eval = importlib.import_module("draft_eval")


def row(
    rid: str,
    status: str,
    reasons: list[str] | None = None,
    cost: str = "0.01",
    latency: float | None = 1.0,
    bound: int = 0,
) -> dict[str, Any]:
    return {
        "request_id": rid,
        "status": status,
        "reasons": reasons or [],
        "usage": {"input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 0},
        "cost_usd": cost,
        "latency_s": latency,
        "validation": {"claims_bound_to_findings": bound},
    }


def write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path, Path]:
    disp = {"a": "answered", "b": "answered", "c": "abstained", "d": "abstained"}
    disp |= {"e": "answered", "f": "out_of_scope"}
    packets = tmp_path / "packets.jsonl"
    write(
        packets,
        [
            {"request": {"request_id": k}, "diagnosis": {"disposition": v}}
            for k, v in disp.items()
        ],
    )
    drafts = tmp_path / "drafts.jsonl"
    write(
        drafts,
        [
            row("a", "accepted", bound=3, latency=1.0),
            row(
                "b",
                "rejected",
                [
                    "action word 'pay' does not appear in the packet",
                    "amount 5 is not in the packet",
                ],
                latency=2.0,
            ),
            row("c", "accepted", bound=1, latency=3.0),
            row("d", "failed", ["max_tokens"], latency=None),
            row("e", "rejected", ["claim 0: wrong keys"], latency=4.0),
            row("f", "skipped", ["disposition_out_of_scope"], cost="0", latency=None),
        ],
    )
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({"entries": [{"cost_usd": "0.02"}, {"cost_usd": "0.05"}]})
    )
    return packets, drafts, ledger


def test_counts_overall_and_by_disposition(files: tuple[Path, Path, Path]) -> None:
    r = draft_eval.evaluate(*files)
    assert r["overall"] == {
        "rows": 6,
        "attempted": 5,
        "accepted": 2,
        "rejected": 2,
        "failed": 1,
        "skipped": 1,
    }
    assert r["by_disposition"]["answered"]["accepted"] == 1
    assert r["by_disposition"]["answered"]["rejected"] == 2
    assert r["by_disposition"]["abstained"] == {
        "rows": 2,
        "attempted": 2,
        "accepted": 1,
        "rejected": 0,
        "failed": 1,
        "skipped": 0,
    }


def test_kinds_claims_cost_latency_and_digest(files: tuple[Path, Path, Path]) -> None:
    r = draft_eval.evaluate(*files)
    assert r["failure_and_rejection_kinds"] == {
        "action_word": 1,
        "amount": 1,
        "length_or_schema": 1,
        "max_tokens": 1,
    }
    assert r["claims_bound_to_findings"] == {
        "total": 4,
        "accepted_drafts": 2,
        "per_accepted_draft": 2.0,
    }
    assert r["cost_usd"]["drafts_rows_total"] == "0.05"
    assert r["cost_usd"]["ledger_total"] == "0.07"
    assert r["cost_usd"]["per_attempted"] == "0.01"
    assert r["cost_usd"]["per_accepted"] == "0.025"
    assert r["latency_s"] == {"calls": 4, "p50": 2.0, "p95": 4.0}
    assert r["packets_sha256"] == hashlib.sha256(files[0].read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        ("category 'F06' is not this packet's diagnosis", "category"),
        ("calls the case legitimate without a legitimate diagnosis", "category"),
        ("unknown id 'pay:x'", "id"),
        ("unknown token abc...", "id"),
        ("claim 1: evidence 'x' is not in finding f", "finding_binding"),
        ("claim 1: unknown finding 'x'", "finding_binding"),
        ("finding within_clearing_window has no claim", "coverage"),
        ("summary must be a non-empty string of at most 600", "length_or_schema"),
        ("malformed_json", "length_or_schema"),
        ("next_step.text must be non-empty, at most 300", "length_or_schema"),
        ("next_step must have exactly action and text", "length_or_schema"),
        ("day count 9 is not in the packet", "date_or_count"),
        ("something new", "other"),
    ],
)
def test_rejection_kind(reason: str, kind: str) -> None:
    assert draft_eval.rejection_kind(reason) == kind


def test_draft_row_without_packet_is_an_error(files: tuple[Path, Path, Path]) -> None:
    packets, drafts, ledger = files
    write(drafts, [row("zzz", "accepted")])
    with pytest.raises(ValueError, match="without a packet"):
        draft_eval.evaluate(packets, drafts, ledger)
