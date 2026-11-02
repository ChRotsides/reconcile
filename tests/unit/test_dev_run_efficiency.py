"""Efficiency extraction, digest checks and group_vendor_id on scored rows in
evaluation/dev_run.py (evaluator side; no labels from the real benchmark)."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
D = importlib.import_module("dev_run")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_run(root: Path, with_stats: bool = True) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "packets_sha256": {},
        "requests_file_sha256": {},
        "links_file_sha256": {},
        "timings_sha256": {},
        "run_stats_sha256": {},
    }
    (root / "prepared/snapshot").mkdir(parents=True)
    (root / "prepared/snapshot" / D.SNAPSHOT_FILE).write_bytes(b"snapshot")
    manifest["snapshot_file_sha256"] = sha(root / "prepared/snapshot" / D.SNAPSHOT_FILE)
    for split, secs in (("train", [0.1, 0.2, 0.4]), ("validation", [0.3, 0.5])):
        d = root / "inference" / split
        d.mkdir(parents=True)
        (d / "packets.jsonl").write_text("{}\n")
        manifest["packets_sha256"][split] = sha(d / "packets.jsonl")
        for key, sub, name in (
            ("requests_file_sha256", f"prepared/requests/{split}", "requests.jsonl"),
            ("links_file_sha256", f"evaluator/{split}", "links.jsonl"),
        ):
            (root / sub).mkdir(parents=True)
            (root / sub / name).write_text("{}\n")
            manifest[key][split] = sha(root / sub / name)
        if with_stats:
            (d / D.TIMINGS_FILE).write_text(
                "".join(
                    json.dumps({"request_id": f"r{i}", "seconds": s}) + "\n"
                    for i, s in enumerate(secs)
                )
            )
            (d / D.STATS_FILE).write_text(
                json.dumps({"requests": len(secs), "peak_rss_kib": 1000 + len(secs)})
            )
            manifest["timings_sha256"][split] = sha(d / D.TIMINGS_FILE)
            manifest["run_stats_sha256"][split] = sha(d / D.STATS_FILE)
    if not with_stats:
        del manifest["timings_sha256"], manifest["run_stats_sha256"]
    return manifest


def test_efficiency_per_split_and_overall(tmp_path: Path) -> None:
    m = make_run(tmp_path)
    out = D.efficiency_block(tmp_path, m, {"train": 2, "validation": 1})
    e = out["efficiency"]
    assert e["train"]["attempted"] == 3 and e["validation"]["attempted"] == 2
    assert e["all_splits"]["attempted"] == 5
    assert e["train"]["latency_p50_s"] == 0.2
    assert e["all_splits"]["peak_rss_kib"] == 1003  # max over splits
    assert e["all_splits"]["currency"] == "USD"
    assert e["all_splits"]["cost_per_attempted_case"] == "0"
    assert e["all_splits"]["cost_per_correct_complete_packet"] == str(Decimal(0) / 3)
    json.dumps(out)  # serialisable (no Decimal left)


def test_old_run_without_timing_files_gives_none(tmp_path: Path) -> None:
    m = make_run(tmp_path, with_stats=False)
    out = D.efficiency_block(tmp_path, m, {"train": 0, "validation": 0})
    assert out["efficiency"] is None and "predates" in out["note"]
    D.verify_digests(tmp_path, m)  # old manifests still verify


def test_verify_digests_detects_changed_timings(tmp_path: Path) -> None:
    m = make_run(tmp_path)
    D.verify_digests(tmp_path, m)
    (tmp_path / "inference/train" / D.TIMINGS_FILE).write_text("tampered\n")
    with pytest.raises(SystemExit, match="timings.jsonl differs"):
        D.verify_digests(tmp_path, m)


def test_verify_digests_detects_missing_stats(tmp_path: Path) -> None:
    m = make_run(tmp_path)
    (tmp_path / "inference/validation" / D.STATS_FILE).unlink()
    with pytest.raises(SystemExit, match="run-stats.json differs"):
        D.verify_digests(tmp_path, m)
