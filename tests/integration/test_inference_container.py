"""Inference in a container with only the snapshot, requests and output mounted.

Skips when Docker is unavailable. Builds the image if it is absent. The
snapshot and requests are prepared on the host with the adapters, which are
not in the image.
"""

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from reconcile.adapters.finrca import (
    _load_key,
    _Tokens,
    create_key_file,
    prepare_finrca_snapshot,
)
from reconcile.domain.contracts import InvestigationRequest, RecordRef
from reconcile.runs.batch import run_requests

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "inference" / "run_inference.sh"
FIXTURE = ROOT / "fixtures" / "public" / "split_payment"
IMAGE = os.environ.get("IMAGE", "reconcile-inference:dev")
CUTOFF = date(2026, 6, 30)


def _docker_works() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _docker_works(), reason="Docker is unavailable")


def _run(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "IMAGE": IMAGE, **(env or {})},
    )


@pytest.fixture(scope="module")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    build = subprocess.run(
        [str(SCRIPT), "--build"],
        capture_output=True,
        text=True,
        env={**os.environ, "IMAGE": IMAGE},
    )
    assert build.returncode == 0, build.stderr[-2000:]
    root = tmp_path_factory.mktemp("container")
    key = root / "private" / "key"
    key.parent.mkdir()
    create_key_file(key)
    manifest = prepare_finrca_snapshot(
        FIXTURE,
        root / "snapshot",
        key_path=key,
        source_revision="fixture:split_payment",
        cutoff=CUTOFF,
    )
    digest = manifest["snapshot"]["content_sha256"]
    tokens = _Tokens(_load_key(key))
    (root / "requests").mkdir()
    lines = []
    for request_id, table, raw in [
        ("req_b", "invoices", "INV-NOT-THERE"),  # abstains
        ("req_a", "invoices", ""),  # replaced below with a real invoice
    ]:
        if not raw:
            raw = _first_invoice(FIXTURE)
        namespace = "invoice"
        lines.append(
            InvestigationRequest(
                request_id,
                digest,
                RecordRef(digest, table, tokens.token(namespace, raw)),
                "Check whether there is a reconciliation issue.",
                CUTOFF,
                "policy_test",
            ).to_json()
        )
    (root / "requests" / "requests.jsonl").write_text("\n".join(lines) + "\n")
    return {
        "root": root,
        "key": key,
        "snapshot": root / "snapshot",
        "requests": root / "requests",
    }


def _first_invoice(fixture: Path) -> str:
    import csv

    with (fixture / "invoices.csv").open() as f:
        return next(csv.DictReader(f))["invoice_id"]


def test_absence_check(inputs: dict[str, Path], tmp_path: Path) -> None:
    host_paths = [
        inputs["key"],
        inputs["root"] / "links" / "links.jsonl",
        ROOT / "data" / "upstream" / "FinRCA-AI-Bench",
        ROOT
        / "data"
        / "upstream"
        / "FinRCA-AI-Bench"
        / "data"
        / "benchmark"
        / "challenge_test",
        ROOT / "evaluation",
        ROOT / "tests",
        ROOT / "fixtures",
        ROOT / "src",
    ]
    result = _run(
        "--check",
        str(inputs["snapshot"]),
        str(inputs["requests"]),
        str(tmp_path / "out"),
        env={"ABSENT_PATHS": ":".join(map(str, host_paths))},
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PRESENT" not in result.stdout and "MISSING" not in result.stdout
    for p in host_paths:
        assert f"absent  {p}" in result.stdout
    assert "present /in/snapshot/snapshot.sqlite (expected)" in result.stdout
    assert "present /in/requests/requests.jsonl (expected)" in result.stdout


def test_adapters_not_importable_in_image() -> None:
    r = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            IMAGE,
            "-c",
            "import reconcile.adapters",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0 and "ModuleNotFoundError" in r.stderr


def test_container_packets_equal_in_process(
    inputs: dict[str, Path], tmp_path: Path
) -> None:
    out = tmp_path / "container_out"
    result = _run(str(inputs["snapshot"]), str(inputs["requests"]), str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    local = tmp_path / "local_out"
    run_requests(inputs["snapshot"], inputs["requests"] / "requests.jsonl", local)
    container_bytes = (out / "packets.jsonl").read_bytes()
    assert container_bytes == (local / "packets.jsonl").read_bytes()
    assert len(container_bytes.splitlines()) == 2
    print(result.stdout)


def test_existing_output_dir_refused(inputs: dict[str, Path], tmp_path: Path) -> None:
    out = tmp_path / "taken"
    out.mkdir()
    result = _run(str(inputs["snapshot"]), str(inputs["requests"]), str(out))
    assert result.returncode != 0
