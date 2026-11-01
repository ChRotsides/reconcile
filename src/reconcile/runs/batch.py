"""Batch entry point: investigate every request in a file, write one packet each.

`python -m reconcile.runs.batch <snapshot_dir> <requests_file> <out_dir>`.
No resume or cache yet (M6).
"""

import os
import sys
import tempfile
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


def run_requests(
    snapshot_dir: Path, requests_file: Path, out_dir: Path
) -> dict[str, int]:
    """Write `out_dir/packets.jsonl` (one packet per request, sorted by
    request_id) and return the count per disposition.

    `out_dir` must be absent or an empty directory (a container mount point
    always exists); anything else is refused. A malformed request file stops
    the run before anything is written.
    """
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise FileExistsError(f"{out_dir} already exists and is not empty")
    requests = sorted(_read_requests(requests_file), key=lambda r: r.request_id)
    snapshot = snapshot_dir / SNAPSHOT_FILE
    packets = []
    for request in requests:
        try:
            packets.append(investigate(snapshot, request))
        except Exception as e:  # one bad case must not lose the others
            packets.append(_failed(request, e))
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".packets-", dir=out_dir)
    try:
        with os.fdopen(fd, "w") as f:
            for p in packets:
                f.write(p.to_json() + "\n")
        os.replace(tmp, out_dir / PACKETS_FILE)
    except BaseException:
        os.unlink(tmp)
        raise
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
