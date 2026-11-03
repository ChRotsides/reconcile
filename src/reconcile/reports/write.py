"""Write a report folder from a verified run folder: HTML or one JSON per case.

File names come from request IDs with every character outside [A-Za-z0-9._-]
replaced, so an ID cannot name a path outside the report folder. Two IDs that
give the same name are refused.
"""

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from reconcile.domain.contracts import EvidencePacket
from reconcile.reports.explain import explain, title
from reconcile.reports.html import render_index, render_packet
from reconcile.runs.record import (
    SNAPSHOT_MANIFEST_COPY,
    make_private_dir,
    verify_run,
    write_private_file,
)

PACKETS_FILE = "packets.jsonl"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _order(p: EvidencePacket) -> tuple[str, str]:
    e = p.request.primary_entity
    return (e.table, e.record_id)


def file_stems(request_ids: Iterable[str]) -> dict[str, str]:
    """request_id -> file stem, refusing collisions (case-insensitively, since
    some filesystems fold case)."""
    names: dict[str, str] = {}
    seen: dict[str, str] = {}
    for rid in request_ids:
        stem = _UNSAFE.sub("_", rid)
        if stem.lower() in seen:
            raise ValueError(
                f"request IDs {seen[stem.lower()]!r} and {rid!r} give the same "
                f"report file name {stem!r}"
            )
        seen[stem.lower()] = rid
        names[rid] = stem
    return names


def case_file_names(packets: list[EvidencePacket]) -> dict[str, str]:
    return file_stems(p.request.request_id for p in packets)


def _load(run_dir: Path) -> tuple[list[EvidencePacket], dict[str, Any]]:
    listed = verify_run(run_dir)["files"]
    packets_path = run_dir / PACKETS_FILE
    if PACKETS_FILE not in listed:
        raise ValueError(
            f"{run_dir} has no {PACKETS_FILE}: it is a single investigation; "
            "open its report.html"
        )
    packets = [
        EvidencePacket.from_json(line)
        for line in packets_path.read_text().splitlines()
        if line.strip()
    ]
    manifest = json.loads((run_dir / SNAPSHOT_MANIFEST_COPY).read_text())
    return packets, manifest


def write_report(run_dir: Path, out_dir: Path, fmt: str) -> Path:
    """Write the report for `run_dir` into the new folder `out_dir` and return
    the file to open (index.html, or the folder for json)."""
    if fmt not in ("html", "json"):
        raise ValueError(f"unknown report format {fmt!r}")
    packets, manifest = _load(run_dir)
    names = case_file_names(packets)  # refuse before creating anything
    make_private_dir(out_dir)
    if fmt == "json":
        for p in packets:
            doc = {
                "packet": json.loads(p.to_json()),
                "title": title(p),
                "explanation": list(explain(p)),
            }
            text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
            write_private_file(
                out_dir / f"{names[p.request.request_id]}.json", text.encode()
            )
        return out_dir
    cases = out_dir / "cases"
    cases.mkdir(mode=0o700)
    links = {rid: f"cases/{stem}.html" for rid, stem in names.items()}
    for p in packets:
        page = render_packet(p, manifest=manifest)
        write_private_file(cases / f"{names[p.request.request_id]}.html", page.encode())
    index = out_dir / "index.html"
    write_private_file(index, render_index(sorted(packets, key=_order), links).encode())
    return index
