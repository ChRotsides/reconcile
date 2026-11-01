"""Neutral request preparation for FinRCA (docs/data-audit.md L1, L9, section 3.1).

From a split's `benchmark_questions.jsonl` only `case_id` and
`primary_entity` (type, id) are kept; every other field (expected answer,
difficulty, wording, question style) is dropped as soon as the line is parsed.
The five question styles of a case collapse into one request.

Two outputs go to two separate directories:

* `inference_dir/requests.jsonl`: `InvestigationRequest` records, sorted by
  `request_id`. The request ID is an HMAC of the case ID under the secret key,
  so it carries no case-ID order (case-ID blocks encode the family, L1). The
  primary entity is the snapshot token of the raw ID, made by the same token
  code and key as the snapshot.
* `evaluator_dir/links.jsonl`: `request_id` to `case_id`. Evaluator-side only.

The evaluator directory must not equal, lie inside or contain the inference or
snapshot directory (the snapshot directory is what inference code can read).
The key file must lie outside both directories and the snapshot directory.
The same inputs and key give byte-identical files.
Only train, validation and test can be requested; the
sealed `challenge_test` split is refused before any file is touched.
"""

import hashlib
import hmac
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from reconcile.adapters.finrca import MANIFEST_FILE, _load_key, _Tokens
from reconcile.domain.contracts import InvestigationRequest, RecordRef

SPLITS = ("train", "validation", "test")
REQUESTS_FILE = "requests.jsonl"
LINKS_FILE = "links.jsonl"
QUESTIONS_FILE = "benchmark_questions.jsonl"
REQUEST_NAMESPACE = "request"  # no snapshot token namespace has this name

# upstream primary_entity.type -> (snapshot table, token namespace, symptom noun)
ENTITY_TYPES: dict[str, tuple[str, str, str]] = {
    "payment": ("payments", "payment", "payment"),
    "invoice": ("invoices", "invoice", "invoice"),
    "bank_transaction": ("bank_transactions", "bank_txn", "bank transaction"),
    "gl_journal": ("gl_entries", "journal", "general-ledger journal"),
}
# Depends on the entity type only (a real exception queue also knows the type)
# and names no failure class.
SYMPTOM = "Check whether there is a reconciliation issue associated with this {noun}."


def _read_cases(path: Path) -> dict[str, tuple[str, str]]:
    """case_id -> (entity type, raw entity id); nothing else leaves this function."""
    cases: dict[str, tuple[str, str]] = {}
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            rec = json.loads(line)
            case_id = rec["case_id"]
            entity = rec["primary_entity"]
            found = (entity["type"], entity["id"])
            del rec
            if not all(isinstance(x, str) and x for x in (case_id, *found)):
                raise ValueError(f"{path.name} line {n}: malformed case or entity")
            if found[0] not in ENTITY_TYPES:
                raise ValueError(f"{path.name} line {n}: unknown entity type")
            if cases.setdefault(case_id, found) != found:
                raise ValueError(f"{path.name} line {n}: entity differs within a case")
    return cases


def _inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _write_new(path: Path, lines: list[str]) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(line + "\n" for line in lines)
        os.link(tmp, path)  # fails if path exists: never overwrite
    finally:
        os.unlink(tmp)


def prepare_finrca_requests(
    benchmark_dir: Path,
    split: str,
    *,
    snapshot_dir: Path,
    key_path: Path,
    inference_dir: Path,
    evaluator_dir: Path,
    policy_version: str,
) -> dict[str, Any]:
    """Write the requests and links files for one split and return a summary
    (counts and the requests file digest; no case IDs).

    `benchmark_dir` is FinRCA's `data/benchmark`. `snapshot_dir` is the prepared
    snapshot built with the same key; its manifest supplies the snapshot digest
    and the cutoff.
    """
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}, not {split!r}")
    inference_dir, evaluator_dir = inference_dir.resolve(), evaluator_dir.resolve()
    key_path = key_path.resolve()
    if _inside(inference_dir, evaluator_dir) or _inside(evaluator_dir, inference_dir):
        raise ValueError("inference and evaluator directories must be separate")
    # The snapshot directory is mounted for inference: links must never land in it.
    snap_dir = snapshot_dir.resolve()
    if _inside(evaluator_dir, snap_dir) or _inside(snap_dir, evaluator_dir):
        raise ValueError("evaluator directory must not overlap the snapshot directory")
    if any(_inside(key_path, d) for d in (inference_dir, evaluator_dir, snap_dir)):
        raise ValueError("key file must be outside the output and snapshot directories")
    for d in (inference_dir, evaluator_dir):
        if (d / REQUESTS_FILE).exists() or (d / LINKS_FILE).exists():
            raise FileExistsError(f"{d} already holds prepared requests or links")

    key = _load_key(key_path)
    manifest = json.loads((snapshot_dir / MANIFEST_FILE).read_text())
    if manifest["key_id"] != hmac.new(key, b"key-id", hashlib.sha256).hexdigest()[:8]:
        raise ValueError("key does not belong to this snapshot")
    snapshot = manifest["snapshot"]["content_sha256"]
    cutoff = date.fromisoformat(manifest["cutoff"])

    cases = _read_cases(benchmark_dir / split / QUESTIONS_FILE)
    tokens = _Tokens(key)
    requests: list[tuple[str, str, str]] = []  # (request_id, case_id, json line)
    for case_id, (etype, raw_id) in cases.items():
        table, namespace, noun = ENTITY_TYPES[etype]
        request = InvestigationRequest(
            request_id="req_" + tokens.token(REQUEST_NAMESPACE, case_id),
            snapshot=snapshot,
            primary_entity=RecordRef(snapshot, table, tokens.token(namespace, raw_id)),
            symptom=SYMPTOM.format(noun=noun),
            cutoff=cutoff,
            policy_version=policy_version,
        )
        requests.append((request.request_id, case_id, request.to_json()))
    requests.sort()

    inference_dir.mkdir(parents=True, exist_ok=True)
    evaluator_dir.mkdir(parents=True, exist_ok=True)
    request_lines = [r[2] for r in requests]
    link_lines = [
        json.dumps(
            {"request_id": rid, "case_id": cid, "split": split},
            sort_keys=True,
            separators=(",", ":"),
        )
        for rid, cid, _ in requests
    ]
    _write_new(evaluator_dir / LINKS_FILE, link_lines)
    _write_new(inference_dir / REQUESTS_FILE, request_lines)
    digest = hashlib.sha256("".join(line + "\n" for line in request_lines).encode())
    return {
        "split": split,
        "requests": len(requests),
        "requests_sha256": digest.hexdigest(),
        "snapshot": snapshot,
        "cutoff": cutoff.isoformat(),
        "policy_version": policy_version,
    }
