"""Baseline report pages (evaluation/baselines/observable/report.py)."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

DIR = Path(__file__).resolve().parents[2] / "evaluation/baselines/observable"


def _load() -> Any:
    sys.path.insert(0, str(DIR))
    sys.path.insert(0, str(DIR.parents[1]))
    try:
        spec = importlib.util.spec_from_file_location(
            "baseline_report", DIR / "report.py"
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["baseline_report"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(DIR))
        sys.path.remove(str(DIR.parents[1]))


R = _load()
CONFIGS = [("answer_all", None), ("threshold_0.85", 0.85)]
CASE = {
    "request_id": "req_a",
    "table": "payments",
    "record_id": "rec1",
    "predicted": "F12",
    "confidence": 0.8123456789012345,
}


def test_case_page_shows_what_the_baseline_has() -> None:
    page = R.render_case(CASE, [("payment", True), ("allocations", False)], CONFIGS)
    assert "Payment possibly missing from the bank" in page
    assert "payments rec1" in page
    assert "<td>payment</td><td>met</td>" in page
    assert "<td>allocations</td><td>not met</td>" in page
    # 0.812 is below 0.85: the threshold configuration abstains, answer_all answers
    assert "<td>answer_all</td><td>none</td><td>answers</td>" in page
    assert "<td>threshold_0.85</td><td>0.85</td><td>abstains</td>" in page


def test_probability_is_shown_exactly_as_given() -> None:
    assert "0.8123456789012345" in R.render_case(CASE, [], CONFIGS)


def test_threshold_boundary_answers() -> None:
    assert R.decision(0.85, 0.85) == "answers"
    assert R.decision(0.8499999999999999, 0.85) == "abstains"
    assert R.decision(0.0, None) == "answers"


def test_notice_is_on_every_page() -> None:
    assert R.NOTICE in R.render_case(CASE, [], CONFIGS)
    assert R.NOTICE in R.render_index([CASE], CONFIGS)
    assert "does not mean the evidence contract is satisfied" in R.NOTICE
    for phrase in ("no exact calculations", "no citations", "no next steps"):
        assert phrase in R.NOTICE


def test_unknown_category_is_named_not_invented() -> None:
    page = R.render_case({**CASE, "predicted": "F05_X"}, [], CONFIGS)
    assert "F05_X (no plain title" in page
    assert "no requirements for this category" in page


def test_hostile_text_is_escaped_and_no_external_assets() -> None:
    evil = "<script>alert(1)</script>"
    case = {**CASE, "request_id": evil, "record_id": evil, "predicted": evil}
    for page in (
        R.render_case(case, [(evil, True)], CONFIGS),
        R.render_index([case], CONFIGS),
    ):
        assert "<script>" not in page
        assert "&lt;script&gt;" in page
        assert "http://" not in page and "https://" not in page
        assert "default-src 'none'" in page


def test_write_report_reads_no_label_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preds = tmp_path / "p.jsonl"
    reqs = tmp_path / "r.jsonl"
    preds.write_text(
        json.dumps(
            {
                "request_id": "req_a",
                "predicted": "F12",
                "confidence": 0.9,
                "abstained": False,
                "failure_type": "SECRET_LABEL_VALUE",
                "case_id": "RCA_SECRET",
            }
        )
        + "\n"
    )
    reqs.write_text(
        json.dumps(
            {
                "request_id": "req_a",
                "cutoff": "2026-06-30",
                "primary_entity": {"table": "payments", "record_id": "rec1"},
                "expected_category": "SECRET_LABEL_VALUE",
            }
        )
        + "\n"
    )
    monkeypatch.setattr(R, "Snapshot", lambda path, cutoff: object())
    monkeypatch.setattr(R.evidence, "retrieve", lambda *a: [("payment", True)])
    out = tmp_path / "out"
    assert R.write_report(preds, reqs, tmp_path / "s.sqlite", out, CONFIGS) == 1
    text = "".join(p.read_text() for p in out.iterdir())
    assert "req_a.html" in text and "<td>payment</td><td>met</td>" in text
    for secret in ("SECRET_LABEL_VALUE", "RCA_SECRET"):
        assert secret not in text


def test_configurations_come_from_the_frozen_protocol() -> None:
    assert R.configurations() == CONFIGS
