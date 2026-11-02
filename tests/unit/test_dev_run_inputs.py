"""Binding of the scoring inputs and the scored case set in evaluation/dev_run.py
(evaluator side; toy data, no real labels): a run manifest must bind packets,
requests, evaluator links and the snapshot, each file is read once, and the
scored cases must be exactly the label roster of the split."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
D = importlib.import_module("dev_run")
from test_dev_run_efficiency import make_run, sha  # noqa: E402

CUTOFF = "2026-06-30"
DIGEST = "d" * 64


# ---------------------------------------------------------------- F9 manifest


@pytest.mark.parametrize(
    "key", ["packets_sha256", "requests_file_sha256", "links_file_sha256"]
)
def test_a_manifest_must_bind_every_input_for_every_split(
    tmp_path: Path, key: str
) -> None:
    m = make_run(tmp_path)
    D.verify_digests(tmp_path, m)
    for broken in ({}, {"train": m[key]["train"]}):  # empty or one split missing
        bad = copy.deepcopy(m)
        bad[key] = broken
        with pytest.raises(SystemExit, match=f"no {key}"):
            D.verify_digests(tmp_path, bad)
    bad = copy.deepcopy(m)
    del bad[key]  # an older manifest: refused, not silently skipped
    with pytest.raises(SystemExit, match="predates input binding"):
        D.verify_digests(tmp_path, bad)


def test_the_snapshot_file_is_bound(tmp_path: Path) -> None:
    m = make_run(tmp_path)
    (tmp_path / "prepared/snapshot" / D.SNAPSHOT_FILE).write_bytes(b"swapped")
    with pytest.raises(SystemExit, match="snapshot.sqlite differs"):
        D.verify_digests(tmp_path, m)
    del m["snapshot_file_sha256"]
    with pytest.raises(SystemExit, match="snapshot.sqlite differs"):
        D.verify_digests(tmp_path, m)


@pytest.mark.parametrize(
    "rel",
    [
        "inference/train/packets.jsonl",
        "prepared/requests/train/requests.jsonl",
        "evaluator/train/links.jsonl",
    ],
)
def test_a_changed_input_is_refused_when_read_for_scoring(
    tmp_path: Path, rel: str
) -> None:
    m = make_run(tmp_path)
    D.read_split_inputs(tmp_path, m, "train")
    (tmp_path / rel).write_text('{"changed": 1}\n')
    with pytest.raises(SystemExit, match="differs from the run manifest"):
        D.read_split_inputs(tmp_path, m, "train")
    (tmp_path / rel).unlink()
    with pytest.raises(SystemExit, match="differs from the run manifest"):
        D.read_split_inputs(tmp_path, m, "train")


def test_swapped_links_are_detected(tmp_path: Path) -> None:
    """Swapping two case ids in links.jsonl changes which case a packet is scored
    against while every packet hash stays the same."""
    m = make_run(tmp_path)
    links = tmp_path / "evaluator/train/links.jsonl"
    links.write_text(
        json.dumps({"request_id": "r1", "case_id": "c1"})
        + "\n"
        + json.dumps({"request_id": "r2", "case_id": "c2"})
        + "\n"
    )
    m["links_file_sha256"]["train"] = sha(links)
    D.read_split_inputs(tmp_path, m, "train")
    links.write_text(
        json.dumps({"request_id": "r1", "case_id": "c2"})
        + "\n"
        + json.dumps({"request_id": "r2", "case_id": "c1"})
        + "\n"
    )
    with pytest.raises(SystemExit, match="links.jsonl differs"):
        D.read_split_inputs(tmp_path, m, "train")


def test_each_input_file_is_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bytes that are hashed are the bytes that are parsed: a file that
    changes after its first read cannot change what is scored."""
    m = make_run(tmp_path)
    good = {"x": 1}
    for rel, key, split in (
        ("inference/train/packets.jsonl", "packets_sha256", "train"),
        ("prepared/requests/train/requests.jsonl", "requests_file_sha256", "train"),
        ("evaluator/train/links.jsonl", "links_file_sha256", "train"),
    ):
        (tmp_path / rel).write_text(json.dumps(good) + "\n")
        m[key][split] = sha(tmp_path / rel)
    real = Path.read_bytes
    reads: list[str] = []

    def once(self: Path) -> bytes:
        reads.append(self.name)
        if reads.count(self.name) > 1:  # a second read would see other bytes
            return b'{"x": 2}\n'
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", once)
    packets, requests, links = D.read_split_inputs(tmp_path, m, "train")
    assert packets == requests == links == [good]
    assert sorted(reads) == ["links.jsonl", "packets.jsonl", "requests.jsonl"]


# ------------------------------------------------- F10 roster, F8 request


Rows = list[dict[str, Any]]


def toy(
    cases: list[str], labelled: list[str] | None = None
) -> tuple[dict[str, Any], Rows, Rows, Rows]:
    labels = {
        c: {"failure_type": "F12", "scenario_variant": "v", "difficulty": "easy"}
        for c in (cases if labelled is None else labelled)
    }
    requests = [
        {
            "request_id": f"q_{c}",
            "snapshot": DIGEST,
            "cutoff": CUTOFF,
            "primary_entity": {"table": "payments", "record_id": f"p_{c}"},
        }
        for c in cases
    ]
    links = [{"request_id": f"q_{c}", "case_id": c} for c in cases]
    packets = [
        {
            "request": copy.deepcopy(r),
            "diagnosis": {
                "disposition": "abstained",
                "category": None,
                "reason": "x",
            },
            "evidence": [],
        }
        for r in requests
    ]
    return labels, packets, requests, links


def score(labels: dict[str, Any], packets: Rows, requests: Rows, links: Rows) -> Rows:
    snap = SimpleNamespace(con=sqlite3.connect(":memory:"), digest=DIGEST)
    conflicts = SimpleNamespace(verified={}, candidates={}, review={})
    rows: Rows = D.load_split(
        "train", labels, snap, conflicts, packets, requests, links
    )
    return rows


def test_rows_carry_group_vendor_id() -> None:
    labels, packets, requests, links = toy(["c1"])
    labels["c1"]["group_vendor_id"] = "V-123"
    (row,) = score(labels, packets, requests, links)
    assert row["group_vendor_id"] == "V-123"


def test_the_full_roster_is_scored() -> None:
    labels, packets, requests, links = toy(["c1", "c2", "c3"])
    assert [r["case_id"] for r in score(labels, packets, requests, links)] == [
        "c1",
        "c2",
        "c3",
    ]


def test_a_case_omitted_from_requests_links_and_packets_is_refused() -> None:
    """The same hard case dropped from requests, links and packets leaves every
    pairwise coverage check satisfied and the denominator silently smaller."""
    labels, packets, requests, links = toy(["c1", "c2", "c3"])
    for part in (packets, requests, links):
        del part[2]
    with pytest.raises(SystemExit, match=r"label roster \(missing \['c3'\]"):
        score(labels, packets, requests, links)


def test_a_scored_case_without_a_label_is_refused() -> None:
    labels, packets, requests, links = toy(["c1", "c2"], labelled=["c1"])
    with pytest.raises(SystemExit, match=r"extra \['c2'\]"):
        score(labels, packets, requests, links)


def test_two_requests_for_one_case_are_refused() -> None:
    labels, packets, requests, links = toy(["c1", "c2"])
    links[1]["case_id"] = "c1"  # c1 twice, c2 never
    with pytest.raises(SystemExit, match="label roster"):
        score(labels, packets, requests, links)


def test_duplicate_request_ids_are_refused() -> None:
    labels, packets, requests, links = toy(["c1", "c2"])
    requests.append(copy.deepcopy(requests[0]))
    with pytest.raises(SystemExit, match="duplicate request ids"):
        score(labels, packets, requests, links)


def test_packets_must_cover_the_requests() -> None:
    labels, packets, requests, links = toy(["c1", "c2"])
    del packets[1]
    with pytest.raises(SystemExit, match="do not cover the same cases"):
        score(labels, packets, requests, links)


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(cutoff="2026-07-31"),
        lambda r: r.update(snapshot="e" * 64),
        lambda r: r["primary_entity"].update(record_id="another_entity"),
        lambda r: r.update(policy_version="other"),
    ],
    ids=["cutoff", "snapshot", "entity", "policy"],
)
def test_a_packet_for_another_request_is_refused(change: Any) -> None:
    labels, packets, requests, links = toy(["c1"])
    change(packets[0]["request"])
    with pytest.raises(SystemExit, match="does not answer its prepared request"):
        score(labels, packets, requests, links)


def test_requests_for_another_snapshot_or_cutoff_are_refused() -> None:
    """Packet and request agree with each other but not with the run."""
    for field, value in (("cutoff", "2026-07-31"), ("snapshot", "e" * 64)):
        labels, packets, requests, links = toy(["c1"])
        requests[0][field] = value
        packets[0]["request"][field] = value
        with pytest.raises(SystemExit, match="does not answer its prepared request"):
            score(labels, packets, requests, links)


def test_sha256_helper_agrees_with_hashlib(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_bytes(b"abc")
    assert D.sha256_file(f) == hashlib.sha256(b"abc").hexdigest()


@pytest.mark.parametrize("bad", ["missing", "null", "number", "no_request"])
def test_a_packet_without_a_string_request_id_is_refused_with_its_line(
    bad: str,
) -> None:
    labels, packets, requests, links = toy(["c1", "c2"])
    if bad == "missing":
        del packets[1]["request"]["request_id"]
    elif bad == "null":
        packets[1]["request"]["request_id"] = None
    elif bad == "number":
        packets[1]["request"]["request_id"] = 7
    else:
        del packets[1]["request"]
    with pytest.raises(SystemExit, match="line 2"):
        score(labels, packets, requests, links)
