"""Batch runner: one packet per request, sorted, atomic, never crashes on a case."""

import json
import platform
from datetime import date
from pathlib import Path

import pytest

from reconcile.diagnosis.pipeline import investigate
from reconcile.domain.contracts import (
    Disposition,
    EvidencePacket,
    InvestigationRequest,
    RecordRef,
)
from reconcile.runs import batch

DIGEST = "a" * 64


def request(request_id: str, record: str = "invoice_x") -> InvestigationRequest:
    return InvestigationRequest(
        request_id,
        DIGEST,
        RecordRef(DIGEST, "invoices", record),
        "Check whether there is a reconciliation issue.",
        date(2026, 6, 30),
        "policy_test",
    )


def write_requests(path: Path, *requests: InvestigationRequest) -> Path:
    path.write_text("".join(r.to_json() + "\n" for r in requests))
    return path


def read_packets(out: Path) -> list[EvidencePacket]:
    lines = (out / batch.PACKETS_FILE).read_text().splitlines()
    return [EvidencePacket.from_json(line) for line in lines]


def test_one_sorted_packet_per_request(tmp_path: Path) -> None:
    snap = tmp_path / "snap"  # no snapshot: every case ends as a failed packet
    snap.mkdir()
    reqs = write_requests(
        tmp_path / "r.jsonl", request("req_b"), request("req_c"), request("req_a")
    )
    summary = batch.run_requests(snap, reqs, tmp_path / "out")
    packets = read_packets(tmp_path / "out")
    assert [p.request.request_id for p in packets] == ["req_a", "req_b", "req_c"]
    assert summary == {"failed": 3}
    assert {f.name for f in (tmp_path / "out").iterdir()} == {
        batch.PACKETS_FILE,
        batch.TIMINGS_FILE,
        batch.STATS_FILE,
    }


def test_packets_match_investigate(tmp_path: Path) -> None:
    snap = tmp_path / "snap"
    snap.mkdir()
    r = request("req_a")
    reqs = write_requests(tmp_path / "r.jsonl", r)
    batch.run_requests(snap, reqs, tmp_path / "out")
    expected = investigate(snap / batch.SNAPSHOT_FILE, r)
    assert read_packets(tmp_path / "out") == [expected]


def test_refuses_existing_nonempty_out_dir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "old").write_text("keep")
    reqs = write_requests(tmp_path / "r.jsonl", request("req_a"))
    with pytest.raises(FileExistsError):
        batch.run_requests(tmp_path, reqs, out)
    assert [f.name for f in out.iterdir()] == ["old"]


def test_accepts_empty_existing_out_dir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()  # a container mount point
    reqs = write_requests(tmp_path / "r.jsonl", request("req_a"))
    batch.run_requests(tmp_path, reqs, out)
    assert len(read_packets(out)) == 1


def test_exception_becomes_failed_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def flaky(path: Path, r: InvestigationRequest) -> EvidencePacket:
        if r.request_id == "req_b":
            raise RuntimeError("secret record contents")
        return investigate(path, r)

    monkeypatch.setattr("reconcile.runs.batch.investigate", flaky)
    reqs = write_requests(
        tmp_path / "r.jsonl", request("req_a"), request("req_b"), request("req_c")
    )
    batch.run_requests(tmp_path, reqs, tmp_path / "out")
    packets = read_packets(tmp_path / "out")
    assert [p.request.request_id for p in packets] == ["req_a", "req_b", "req_c"]
    bad = packets[1].diagnosis
    assert bad.disposition is Disposition.FAILED
    assert bad.reason == "unexpected_exception"
    assert "RuntimeError" in packets[1].limitations[0]
    assert "secret" not in json.dumps(packets[1].to_obj())  # type name only


def test_no_partial_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: EvidencePacket) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(EvidencePacket, "to_json", boom)
    reqs = write_requests(tmp_path / "r.jsonl", request("req_a"))
    with pytest.raises(OSError):
        batch.run_requests(tmp_path, reqs, tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []  # no packets file, no temp file


def test_malformed_or_duplicate_requests_write_nothing(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(request("req_a").to_json() + "\n{not json\n")
    with pytest.raises(ValueError, match="bad.jsonl:2"):
        batch.run_requests(tmp_path, bad, tmp_path / "out1")
    dup = write_requests(tmp_path / "dup.jsonl", request("req_a"), request("req_a"))
    with pytest.raises(ValueError, match="duplicate"):
        batch.run_requests(tmp_path, dup, tmp_path / "out2")
    assert not (tmp_path / "out1").exists() and not (tmp_path / "out2").exists()


def read_timings(out: Path) -> list[dict[str, object]]:
    lines = (out / batch.TIMINGS_FILE).read_text().splitlines()
    return [json.loads(line) for line in lines]


def test_packets_byte_identical_across_runs(tmp_path: Path) -> None:
    reqs = write_requests(
        tmp_path / "r.jsonl", request("req_b"), request("req_a"), request("req_c")
    )
    batch.run_requests(tmp_path, reqs, tmp_path / "out1")
    batch.run_requests(tmp_path, reqs, tmp_path / "out2")
    first = (tmp_path / "out1" / batch.PACKETS_FILE).read_bytes()
    assert first == (tmp_path / "out2" / batch.PACKETS_FILE).read_bytes()


def test_timings_cover_exactly_the_requests(tmp_path: Path) -> None:
    reqs = write_requests(tmp_path / "r.jsonl", request("req_b"), request("req_a"))
    batch.run_requests(tmp_path, reqs, tmp_path / "out")
    rows = read_timings(tmp_path / "out")
    assert [r["request_id"] for r in rows] == ["req_a", "req_b"]
    for row in rows:
        assert set(row) == {"request_id", "seconds"}
        assert isinstance(row["seconds"], float) and row["seconds"] >= 0


def test_failing_request_still_timed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(path: Path, r: InvestigationRequest) -> EvidencePacket:
        raise RuntimeError("x")

    monkeypatch.setattr("reconcile.runs.batch.investigate", boom)
    reqs = write_requests(tmp_path / "r.jsonl", request("req_a"), request("req_b"))
    batch.run_requests(tmp_path, reqs, tmp_path / "out")
    assert [r["request_id"] for r in read_timings(tmp_path / "out")] == [
        "req_a",
        "req_b",
    ]


def test_run_stats_fields(tmp_path: Path) -> None:
    reqs = write_requests(tmp_path / "r.jsonl", request("req_a"), request("req_b"))
    batch.run_requests(tmp_path, reqs, tmp_path / "out")
    stats = json.loads((tmp_path / "out" / batch.STATS_FILE).read_text())
    assert set(stats) == {
        "requests",
        "total_seconds",
        "peak_rss_kib",
        "python_version",
        "runner",
    }
    assert stats["requests"] == 2
    assert isinstance(stats["total_seconds"], float) and stats["total_seconds"] >= 0
    assert isinstance(stats["peak_rss_kib"], int) and stats["peak_rss_kib"] > 0
    assert stats["python_version"] == platform.python_version()
    assert stats["runner"] == batch.RUNNER
    row_total = sum(r["seconds"] for r in read_timings(tmp_path / "out"))  # type: ignore[misc]
    assert stats["total_seconds"] >= row_total
