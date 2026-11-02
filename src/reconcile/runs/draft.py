"""Drafting stage: model-written explanations of finished evidence packets.

`python -m reconcile.runs.draft PACKETS_FILE OUT_DIR --model M --effort E
--mode replay|live|record [--replay PATH] [--ledger PATH] [--cap-usd 25]
[--limit N] [--select FILE_OF_REQUEST_IDS]`

Reads only the packets file (plus the optional selection, replay and ledger
files): never labels, links, keys or the snapshot. A draft that is rejected,
failed or skipped leaves the analyst with the deterministic packet alone.
Accepted drafts bind each claim to a deterministic finding; the remaining
prose is unvalidated and the row says so.
"""

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from reconcile.domain.contracts import EvidencePacket
from reconcile.models.budget import SpendLedger, cost_usd
from reconcile.models.client import (
    ModelError,
    ModelResponse,
    complete_with_retry,
    make_client,
    request_key,
)
from reconcile.models.draft import (
    PROMPT_VERSION,
    build_request,
    prompt_sha256,
    should_draft,
)
from reconcile.models.validate import claims_bound_to_findings, validate

DRAFTS_FILE = "drafts.jsonl"
STATS_FILE = "draft-stats.json"
BILLED_FAILURES = ("refusal", "max_tokens")
PACKET_ONLY = "packet_only"
WITH_DRAFT = "packet_with_unvalidated_prose"


def _read_packets(path: Path) -> list[EvidencePacket]:
    packets = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            packets.append(EvidencePacket.from_json(line))
        except ValueError as e:
            raise ValueError(f"{path}:{number}: invalid packet: {e}") from e
    ids = [p.request.request_id for p in packets]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate request_id")
    return sorted(packets, key=lambda p: p.request.request_id)


def _write_atomic(out_dir: Path, name: str, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{name}-", dir=out_dir)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, out_dir / name)
    except BaseException:
        os.unlink(tmp)
        raise


def _row(
    request_id: str,
    status: str,
    reasons: list[str],
    *,
    model: str,
    sha: str,
    draft: dict[str, Any] | None = None,
    key: str | None = None,
    usage: tuple[int, int, int] = (0, 0, 0),
    cost: Decimal = Decimal(0),
    latency: float | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "status": status,
        "reasons": reasons,
        "draft": draft,
        "delivery": WITH_DRAFT if status == "accepted" else PACKET_ONLY,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": sha,
        "request_key": key,
        "usage": {
            "input_tokens": usage[0],
            "output_tokens": usage[1],
            "cache_read_tokens": usage[2],
        },
        "cost_usd": str(cost),
        "latency_s": latency,
        "validation": {
            "claims_bound_to_findings": (
                claims_bound_to_findings(draft) if draft is not None else 0
            ),
            "unvalidated_prose": True,
        },
    }


def _judge(
    packet: EvidencePacket, response: ModelResponse
) -> tuple[str, list[str], dict[str, Any] | None]:
    if response.text is None:
        return "failed", ["empty_response"], None
    try:
        draft = json.loads(response.text)
    except ValueError:
        return "rejected", ["malformed_json"], None
    problems = validate(draft, packet)
    if problems:
        return "rejected", problems, None
    return "accepted", [], draft


def _percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def run_drafts(
    packets_file: Path,
    out_dir: Path,
    *,
    model: str,
    effort: str,
    mode: str,
    replay: Path | None,
    ledger_path: Path | None,
    cap_usd: Decimal,
    limit: int | None = None,
    select: Path | None = None,
    max_output_tokens: int = 1500,
) -> dict[str, Any]:
    """Write `drafts.jsonl` and `draft-stats.json`; return the stats."""
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise FileExistsError(f"{out_dir} already exists and is not empty")
    packets = _read_packets(packets_file)
    if select is not None:
        wanted = {x.strip() for x in select.read_text().splitlines() if x.strip()}
        packets = [p for p in packets if p.request.request_id in wanted]
    if limit is not None:
        packets = packets[:limit]
    client = make_client(mode, replay)
    ledger = SpendLedger(ledger_path or out_dir / "spend-ledger.json", cap_usd)
    sha = prompt_sha256()
    rows: list[dict[str, Any]] = []
    stopped: str | None = None
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        for packet in packets:
            rid = packet.request.request_id
            kw: dict[str, Any] = {"model": model, "sha": sha}
            if not should_draft(packet):
                skip = f"disposition_{packet.diagnosis.disposition.value}"
                rows.append(_row(rid, "skipped", [skip], **kw))
                continue
            req = build_request(packet, model, effort, max_output_tokens)
            key = request_key(req)
            try:
                response = complete_with_retry(client, req, ledger)
            except ModelError as err:
                if err.reason == "budget":
                    stopped = "budget"
                    break
                billed = err.reason in BILLED_FAILURES
                usage = (
                    (err.input_tokens, err.output_tokens, 0) if billed else (0, 0, 0)
                )
                cost = cost_usd(model, usage[0], usage[1])
                rows.append(
                    _row(
                        rid,
                        "failed",
                        [err.reason],
                        key=key,
                        usage=usage,
                        cost=cost,
                        **kw,
                    )
                )
                continue
            status, reasons, draft = _judge(packet, response)
            rows.append(
                _row(
                    rid,
                    status,
                    reasons,
                    draft=draft,
                    key=key,
                    usage=(
                        response.input_tokens,
                        response.output_tokens,
                        response.cache_read_tokens,
                    ),
                    cost=cost_usd(model, response.input_tokens, response.output_tokens),
                    latency=response.latency_s,
                    **kw,
                )
            )
    finally:
        _write_atomic(
            out_dir,
            DRAFTS_FILE,
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
        )
    latencies = sorted(r["latency_s"] for r in rows if r["latency_s"] is not None)
    stats = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": sha,
        "packets_selected": len(packets),
        "rows": len(rows),
        "not_attempted": len(packets) - len(rows),
        "stopped": stopped,
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "total_cost_usd": str(sum((Decimal(r["cost_usd"]) for r in rows), Decimal(0))),
        "latency_p50_s": _percentile(latencies, 0.5),
        "latency_p95_s": _percentile(latencies, 0.95),
    }
    _write_atomic(out_dir, STATS_FILE, json.dumps(stats, indent=2) + "\n")
    return stats


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m reconcile.runs.draft")
    ap.add_argument("packets_file", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--model", required=True)
    ap.add_argument("--effort", required=True, choices=["low", "medium", "high"])
    ap.add_argument("--mode", required=True, choices=["replay", "live", "record"])
    ap.add_argument("--replay", type=Path)
    ap.add_argument("--ledger", type=Path)
    ap.add_argument("--cap-usd", type=Decimal, default=Decimal(25))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--select", type=Path)
    a = ap.parse_args(argv)
    stats = run_drafts(
        a.packets_file,
        a.out_dir,
        model=a.model,
        effort=a.effort,
        mode=a.mode,
        replay=a.replay,
        ledger_path=a.ledger,
        cap_usd=a.cap_usd,
        limit=a.limit,
        select=a.select,
    )
    print(f"drafts: {stats['rows']} {sorted(stats['status_counts'].items())}")
    if stats["stopped"]:
        print(
            f"stopped: {stats['stopped']}; {stats['not_attempted']} not attempted",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
