"""The drafting stage end to end with fake and replay clients (no network)."""

import builtins
import io
import json
import os
import pathlib
import re
from collections.abc import Callable
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_model_validate import faithful, load_packets

from reconcile.domain.contracts import (
    Diagnosis,
    Disposition,
    EvidencePacket,
    InvestigationRequest,
)
from reconcile.models.client import (
    ModelError,
    ModelRequest,
    ModelResponse,
    request_key,
)
from reconcile.models.draft import PROMPT_PATH, build_request, prompt_sha256
from reconcile.runs import draft as draft_run
from reconcile.runs.draft import main, run_drafts

MODEL = "claude-sonnet-5-5"
ROW_KEYS = {
    "request_id",
    "status",
    "reasons",
    "draft",
    "delivery",
    "model",
    "prompt_version",
    "prompt_sha256",
    "request_key",
    "usage",
    "cost_usd",
    "latency_s",
    "validation",
}


def response(text: str | None, *, tokens: int = 1000, latency: float = 1.0) -> Any:
    return ModelResponse(text, "end_turn", tokens, 200, 0, MODEL, "req_x", latency)


class FakeClient:
    """Answers by the request_id found in the user message."""

    def __init__(self, by_id: dict[str, Callable[[], ModelResponse]]) -> None:
        self.by_id = by_id
        self.calls: list[str] = []

    def complete(self, req: ModelRequest) -> ModelResponse:
        rid = re.search(r'"request_id":"([^"]+)"', req.user)
        assert rid is not None
        self.calls.append(rid[1])
        return self.by_id[rid[1]]()


def raising(err: ModelError) -> Callable[[], ModelResponse]:
    def go() -> ModelResponse:
        raise err

    return go


def write_packets(path: Path, packets: list[EvidencePacket]) -> None:
    path.write_text("".join(p.to_json() + "\n" for p in packets))


@pytest.fixture
def mixed(tmp_path: Path) -> dict[str, Any]:
    packets = load_packets()
    base = packets["f06_double_payment"]
    failed = EvidencePacket(
        replace_id(base, "req_zz_failed"),
        Diagnosis(Disposition.FAILED, None, reason="x"),
    )
    scope = EvidencePacket(
        replace_id(base, "req_zz_scope"),
        Diagnosis(Disposition.OUT_OF_SCOPE, None, reason="y"),
    )
    file = tmp_path / "packets.jsonl"
    write_packets(
        file,
        [
            scope,
            packets["real_approval_missing"],
            failed,
            packets["pending_settlement"],
            base,
        ],
    )
    return {"file": file, "packets": packets}


def replace_id(packet: EvidencePacket, rid: str) -> InvestigationRequest:
    return replace(packet.request, request_id=rid)


def go(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    file: Path,
    client: FakeClient,
    **kw: Any,
) -> dict[str, Any]:
    monkeypatch.setattr(draft_run, "make_client", lambda mode, replay: client)
    args: dict[str, Any] = {
        "model": MODEL,
        "effort": "low",
        "mode": "replay",
        "replay": None,
        "ledger_path": tmp_path / "ledger.json",
        "cap_usd": Decimal(25),
    }
    args.update(kw)
    return run_drafts(file, tmp_path / "out", **args)


def rows(tmp_path: Path) -> list[dict[str, Any]]:
    text = (tmp_path / "out" / "drafts.jsonl").read_text()
    return [json.loads(x) for x in text.splitlines()]


def test_statuses_rows_and_stats(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mixed: dict[str, Any]
) -> None:
    p = mixed["packets"]
    bad = faithful(p["pending_settlement"])
    bad["summary"] += " A fee of 77.77 EUR was charged."
    client = FakeClient(
        {
            "req_f06_double_payment": lambda: response(
                json.dumps(faithful(p["f06_double_payment"])), latency=1.0
            ),
            "req_pending_settlement": lambda: response(json.dumps(bad), latency=3.0),
            "req_real_approval_missing": raising(
                ModelError("refusal", model=MODEL, input_tokens=500, output_tokens=0)
            ),
        }
    )
    stats = go(monkeypatch, tmp_path, mixed["file"], client)
    out = rows(tmp_path)

    assert [r["request_id"] for r in out] == sorted(r["request_id"] for r in out)
    assert all(set(r) == ROW_KEYS for r in out)
    by = {r["request_id"]: r for r in out}
    ok = by["req_f06_double_payment"]
    assert ok["status"] == "accepted" and ok["reasons"] == []
    assert ok["draft"] == faithful(p["f06_double_payment"])
    assert ok["delivery"] == "packet_with_unvalidated_prose"
    assert ok["validation"] == {
        "claims_bound_to_findings": 1,
        "unvalidated_prose": True,
    }
    assert ok["prompt_sha256"] == prompt_sha256() and ok["prompt_version"] == "draft_v1"
    assert ok["request_key"] == request_key(
        build_request(p["f06_double_payment"], MODEL, "low", 1500)
    )
    assert ok["usage"] == {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_tokens": 0,
    }
    assert Decimal(ok["cost_usd"]) == Decimal("0.004")

    rej = by["req_pending_settlement"]
    assert rej["status"] == "rejected" and rej["draft"] is None
    assert any("77.77" in x for x in rej["reasons"])
    assert rej["delivery"] == "packet_only"
    assert rej["validation"]["claims_bound_to_findings"] == 0

    fail = by["req_real_approval_missing"]
    assert fail["status"] == "failed" and fail["reasons"] == ["refusal"]
    assert fail["delivery"] == "packet_only" and fail["usage"]["input_tokens"] == 500
    assert Decimal(fail["cost_usd"]) > 0  # a refusal is billed

    for rid in ("req_zz_failed", "req_zz_scope"):
        s = by[rid]
        assert s["status"] == "skipped" and s["draft"] is None
        assert s["request_key"] is None and s["cost_usd"] == "0"
        assert s["delivery"] == "packet_only"
    assert sorted(client.calls) == [
        "req_f06_double_payment",
        "req_pending_settlement",
        "req_real_approval_missing",
    ]

    assert json.loads((tmp_path / "out" / "draft-stats.json").read_text()) == stats
    assert stats["status_counts"] == {
        "accepted": 1,
        "rejected": 1,
        "failed": 1,
        "skipped": 2,
    }
    assert Decimal(stats["total_cost_usd"]) == sum(Decimal(r["cost_usd"]) for r in out)
    assert stats["latency_p50_s"] == 3.0 and stats["latency_p95_s"] == 3.0
    assert stats["stopped"] is None and stats["not_attempted"] == 0


def test_malformed_json_and_empty_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mixed: dict[str, Any]
) -> None:
    client = FakeClient(
        {
            "req_f06_double_payment": lambda: response("{not json"),
            "req_pending_settlement": lambda: response(None),
            "req_real_approval_missing": lambda: response('["a list"]'),
        }
    )
    go(monkeypatch, tmp_path, mixed["file"], client)
    by = {r["request_id"]: r for r in rows(tmp_path)}
    assert by["req_f06_double_payment"]["reasons"] == ["malformed_json"]
    assert by["req_f06_double_payment"]["status"] == "rejected"
    assert by["req_pending_settlement"]["status"] == "failed"
    assert by["req_real_approval_missing"]["status"] == "rejected"


def test_budget_stops_cleanly_with_partial_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mixed: dict[str, Any]
) -> None:
    p = mixed["packets"]

    def big(pk: EvidencePacket) -> Callable[[], ModelResponse]:
        return lambda: response(json.dumps(faithful(pk)), tokens=12_500_000)

    client = FakeClient(
        {
            "req_f06_double_payment": big(p["f06_double_payment"]),
            "req_pending_settlement": big(p["pending_settlement"]),
            "req_real_approval_missing": big(p["real_approval_missing"]),
        }
    )
    stats = go(monkeypatch, tmp_path, mixed["file"], client)
    assert stats["stopped"] == "budget" and stats["not_attempted"] > 0
    out = rows(tmp_path)
    assert [r["status"] for r in out].count("accepted") == 1
    assert len(client.calls) == 1  # no call is made once the cap would be crossed
    assert (tmp_path / "out" / "draft-stats.json").exists()


def test_main_returns_nonzero_when_stopped_by_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mixed: dict[str, Any]
) -> None:
    client = FakeClient({})
    monkeypatch.setattr(draft_run, "make_client", lambda mode, replay: client)
    argv = [
        str(mixed["file"]),
        str(tmp_path / "out"),
        "--model", MODEL,
        "--effort", "low",
        "--mode", "replay",
        "--cap-usd", "0",
        "--ledger", str(tmp_path / "l.json"),
    ]  # fmt: skip
    assert main(argv) == 3
    assert client.calls == []


def test_limit_and_select(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mixed: dict[str, Any]
) -> None:
    sel = tmp_path / "ids.txt"
    sel.write_text("req_pending_settlement\nreq_f06_double_payment\nreq_nope\n")
    p = mixed["packets"]
    client = FakeClient(
        {
            "req_f06_double_payment": lambda: response(
                json.dumps(faithful(p["f06_double_payment"]))
            ),
        }
    )
    go(monkeypatch, tmp_path, mixed["file"], client, select=sel, limit=1)
    assert [r["request_id"] for r in rows(tmp_path)] == ["req_f06_double_payment"]


def test_output_is_atomic_and_refuses_existing_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mixed: dict[str, Any]
) -> None:
    go(monkeypatch, tmp_path, mixed["file"], FakeClient({}), limit=0)
    names = sorted(x.name for x in (tmp_path / "out").iterdir())
    assert names == ["draft-stats.json", "drafts.jsonl"]  # no temp files left
    with pytest.raises(FileExistsError):
        go(monkeypatch, tmp_path, mixed["file"], FakeClient({}))


def test_failed_write_leaves_no_partial_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mixed: dict[str, Any]
) -> None:
    def boom(src: Any, dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        go(monkeypatch, tmp_path, mixed["file"], FakeClient({}), limit=0)
    assert list((tmp_path / "out").iterdir()) == []


def test_replay_mode_end_to_end(tmp_path: Path, mixed: dict[str, Any]) -> None:
    p = mixed["packets"]["f06_double_payment"]
    req = build_request(p, MODEL, "low", 1500)
    replay = tmp_path / "replay.jsonl"
    resp = response(json.dumps(faithful(p)))
    replay.write_text(
        json.dumps({"key": request_key(req), "response": asdict(resp)}) + "\n"
    )
    run_drafts(
        mixed["file"],
        tmp_path / "out",
        model=MODEL,
        effort="low",
        mode="replay",
        replay=replay,
        ledger_path=tmp_path / "ledger.json",
        cap_usd=Decimal(25),
    )
    by = {r["request_id"]: r for r in rows(tmp_path)}
    assert by["req_f06_double_payment"]["status"] == "accepted"
    miss = by["req_pending_settlement"]  # nothing recorded for it
    assert miss["status"] == "failed" and miss["reasons"] == ["replay_miss"]


def test_only_declared_files_are_opened(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mixed: dict[str, Any]
) -> None:
    opened: list[str] = []

    def watch(real: Callable[..., Any]) -> Callable[..., Any]:
        def inner(file: Any, *a: Any, **k: Any) -> Any:
            if isinstance(file, str | os.PathLike):
                opened.append(os.path.realpath(file))
            return real(file, *a, **k)

        return inner

    monkeypatch.setattr(builtins, "open", watch(builtins.open))
    monkeypatch.setattr(io, "open", watch(io.open))
    real_bytes = pathlib.Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        opened.append(os.path.realpath(self))
        return real_bytes(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", read_bytes)

    p = mixed["packets"]
    client = FakeClient(
        {
            "req_f06_double_payment": lambda: response(
                json.dumps(faithful(p["f06_double_payment"]))
            ),
            "req_pending_settlement": lambda: response("{"),
            "req_real_approval_missing": lambda: response("{"),
        }
    )
    go(monkeypatch, tmp_path, mixed["file"], client)
    tmp = os.path.realpath(tmp_path)
    allowed_outside = {os.path.realpath(PROMPT_PATH)}
    stray = [
        x for x in opened if not x.startswith(tmp + os.sep) and x not in allowed_outside
    ]
    assert stray == []
    assert os.path.realpath(mixed["file"]) in opened
