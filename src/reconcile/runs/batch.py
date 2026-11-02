"""Batch entry point: investigate every request in a file, write one packet each.

`python -m reconcile.runs.batch <snapshot_dir> <requests_file> <out_dir>`.
No resume or cache yet (M6).
"""

import json
import os
import platform
import resource
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from reconcile.diagnosis.pipeline import investigate
from reconcile.domain.contracts import (
    Diagnosis,
    Disposition,
    EvidencePacket,
    InvestigationRequest,
)

PACKETS_FILE = "packets.jsonl"
TIMINGS_FILE = "timings.jsonl"
STATS_FILE = "run-stats.json"
SNAPSHOT_FILE = "snapshot.sqlite"
RUNNER = "batch_v1"


def _failed(request: InvestigationRequest, exc: Exception) -> EvidencePacket:
    # The exception type only: a message could quote record contents.
    return EvidencePacket(
        request,
        Diagnosis(Disposition.FAILED, None, reason="unexpected_exception"),
        limitations=(f"Investigation failed: {type(exc).__name__}",),
        provenance=(("runner", RUNNER),),
    )


def _read_requests(path: Path) -> list[InvestigationRequest]:
    requests = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            requests.append(InvestigationRequest.from_json(line))
        except ValueError as e:  # a line with no request has nothing to fail as
            raise ValueError(f"{path}:{number}: invalid request: {e}") from e
    ids = [r.request_id for r in requests]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate request_id")
    return requests


def _write_atomic(out_dir: Path, name: str, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{name}-", dir=out_dir)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, out_dir / name)
    except BaseException:
        os.unlink(tmp)
        raise


def _peak_rss_kib() -> int:
    # Linux reports ru_maxrss in KiB (man getrusage); macOS reports bytes.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak // 1024 if sys.platform == "darwin" else peak


def run_requests(
    snapshot_dir: Path, requests_file: Path, out_dir: Path
) -> dict[str, int]:
    """Write `out_dir/packets.jsonl` (one packet per request, sorted by
    request_id) and return the count per disposition.

    Wall time per request goes to `timings.jsonl` and run totals and peak
    memory to `run-stats.json`, never into the packets, so the packets are
    byte-identical across reruns of the same inputs.

    `out_dir` must be absent or an empty directory (a container mount point
    always exists); anything else is refused. A malformed request file stops
    the run before anything is written.
    """
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise FileExistsError(f"{out_dir} already exists and is not empty")
    requests = sorted(_read_requests(requests_file), key=lambda r: r.request_id)
    snapshot = snapshot_dir / SNAPSHOT_FILE
    packets = []
    timings = []
    loop_start = time.perf_counter()
    for request in requests:
        start = time.perf_counter()
        try:
            packets.append(investigate(snapshot, request))
        except Exception as e:  # one bad case must not lose the others
            packets.append(_failed(request, e))
        timings.append((request.request_id, time.perf_counter() - start))
    total_seconds = time.perf_counter() - loop_start
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(out_dir, PACKETS_FILE, "".join(p.to_json() + "\n" for p in packets))
    _write_atomic(
        out_dir,
        TIMINGS_FILE,
        "".join(
            json.dumps({"request_id": rid, "seconds": sec}) + "\n"
            for rid, sec in timings
        ),
    )
    stats = {
        "requests": len(requests),
        "total_seconds": total_seconds,
        "peak_rss_kib": _peak_rss_kib(),
        "python_version": platform.python_version(),
        "runner": RUNNER,
    }
    _write_atomic(out_dir, STATS_FILE, json.dumps(stats, indent=2) + "\n")
    return dict(Counter(p.diagnosis.disposition.value for p in packets))


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: python -m reconcile.runs.batch SNAPSHOT_DIR REQUESTS_FILE OUT_DIR",
            file=sys.stderr,
        )
        return 2
    summary = run_requests(Path(argv[0]), Path(argv[1]), Path(argv[2]))
    print(f"packets: {sum(summary.values())} {sorted(summary.items())}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
