"""The offline demo: exact outcomes, working report links, no network."""

import re
import socket
import stat
from pathlib import Path
from typing import Any

import pytest

from reconcile.cli import main
from reconcile.domain.contracts import EvidencePacket

# (table, record id) -> (disposition, category or reason)
EXPECTED = {
    ("invoices", "INV-1001"): ("answered", "F06"),
    ("payments", "PAY-5003"): ("answered", "F12"),
    ("payments", "PAY-5004"): ("answered", "F14"),
    ("payments", "PAY-5005"): ("answered", "legitimate_net_fee_accounting"),
    ("payments", "PAY-5006"): ("answered", "legitimate_pending_settlement"),
    ("payments", "PAY-5007"): ("abstained", "ambiguous_reference"),
}


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("demo") / "out"

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the demo opened a socket")

    patch = pytest.MonkeyPatch()
    patch.setattr(socket, "socket", no_network)
    try:
        assert main(["demo", "--output", str(out)]) == 0
    finally:
        patch.undo()
    return out


def test_outcomes_are_exactly_the_six_cases(demo: Path) -> None:
    lines = (demo / "run" / "packets.jsonl").read_text().splitlines()
    got = {}
    for line in lines:
        p = EvidencePacket.from_json(line)
        e = p.request.primary_entity
        d = p.diagnosis
        got[(e.table, e.record_id)] = (d.disposition.value, d.category or d.reason)
    assert got == EXPECTED


def test_every_case_file_exists_and_index_links_resolve(demo: Path) -> None:
    index = demo / "report" / "index.html"
    hrefs = re.findall(r'href="([^"]+)"', index.read_text())
    case_links = [h for h in hrefs if h.startswith("cases/")]
    assert len(case_links) == len(EXPECTED)
    for h in case_links:
        assert (index.parent / h).is_file()
    assert len(list((demo / "report" / "cases").iterdir())) == len(EXPECTED)


def test_demo_folders_are_owner_only(demo: Path) -> None:
    for d in (demo, demo / "run", demo / "report", demo / "report" / "cases"):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_demo_refuses_an_existing_folder(
    demo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["demo", "--output", str(demo)]) == 2
    assert capsys.readouterr().err.startswith("error: ")


def test_demo_data_is_in_the_package_folder() -> None:
    from reconcile.demo import BUNDLE

    names = {p.name for p in BUNDLE.iterdir()}
    assert {"mapping.toml", "records.csv", "gl_entries.csv"} <= names
    # A bank_fee journal must exist so the fee checks can run.
    assert "bank_fee" in (BUNDLE / "gl_entries.csv").read_text()


def test_demo_prints_cases_sorted_by_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["demo", "--output", str(tmp_path / "d")]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ": " in ln]
    keys = [
        ln.split(":")[0] for ln in lines[: -0 or None] if not ln.startswith("index")
    ]
    assert keys == sorted(keys) and len(keys) == len(EXPECTED)
