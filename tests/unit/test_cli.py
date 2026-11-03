"""Command line: exit codes, refusals, private folders, lazy imports."""

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reconcile import cli
from reconcile.demo import MAPPING
from reconcile.diagnosis.policy import POLICY_VERSION
from reconcile.domain.contracts import InvestigationRequest, RecordRef

DEMO_CUTOFF = "2026-06-30"


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("prepared") / "snap"
    argv = ["data", "prepare", "--format", "csv-bundle", "--source", str(MAPPING)]
    assert cli.main([*argv, "--output", str(out), "--cutoff", DEMO_CUTOFF]) == 0
    return out


def digest(snapshot: Path) -> str:
    manifest = json.loads((snapshot / "manifest.json").read_text())
    return str(manifest["snapshot"]["content_sha256"])


def run_cli(capsys: pytest.CaptureFixture[str], *argv: Any) -> tuple[int, str, str]:
    code = cli.main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def write_records(path: Path, *rows: str) -> Path:
    path.write_text("table,record_id\n" + "".join(r + "\n" for r in rows))
    return path


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys, "--version")
    assert code == 0 and out.startswith("reconcile ")


def test_prepare_prints_what_decides_trust(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run_cli(
        capsys, "data", "prepare", "--format", "csv-bundle", "--source", MAPPING,
        "--output", tmp_path / "s", "--cutoff", DEMO_CUTOFF,
    )  # fmt: skip
    assert code == 0
    for word in (
        "bank_data_through",
        "tables_not_supplied",
        "fee_journals_recognised",
        "reference_quality",
        "payments: 8 rows",
    ):
        assert word in out


def test_prepare_refuses_existing_output_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "s").mkdir()
    code, _, err = run_cli(
        capsys, "data", "prepare", "--format", "csv-bundle", "--source", MAPPING,
        "--output", tmp_path / "s", "--cutoff", DEMO_CUTOFF,
    )  # fmt: skip
    assert code == 2
    assert err.startswith("error: ") and "Traceback" not in err
    assert err.count("\n") == 1


def test_prepare_problem_in_the_data_is_one_error_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run_cli(
        capsys, "data", "prepare", "--format", "csv-bundle",
        "--source", tmp_path / "missing.toml", "--output", tmp_path / "s",
        "--cutoff", DEMO_CUTOFF,
    )  # fmt: skip
    assert code == 2 and err.startswith("error: ") and "Traceback" not in err


def test_finrca_prepare_needs_key_and_revision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run_cli(
        capsys, "data", "prepare", "--format", "finrca", "--source", tmp_path,
        "--output", tmp_path / "s", "--cutoff", DEMO_CUTOFF,
    )  # fmt: skip
    assert code == 2 and "--key-file" in err


def test_sealed_split_is_not_a_choice_and_matches_the_adapter() -> None:
    from reconcile.adapters.finrca_requests import SPLITS

    assert cli.SPLITS == SPLITS
    assert not any("challenge" in s for s in cli.SPLITS)


def test_investigate_writes_packet_report_and_record(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "inv"
    code, text, _ = run_cli(
        capsys, "investigate", "--snapshot", snapshot, "--payment", "PAY-5004",
        "--output", out,
    )  # fmt: skip
    assert code == 0
    assert "Bank debit smaller than the payment" in text
    assert str(out / "report.html") in text
    assert {p.name for p in out.iterdir()} == {
        "request.json",
        "packet.json",
        "report.html",
        "snapshot-manifest.json",
        "run-manifest.json",
    }
    assert mode(out) == 0o700
    request = InvestigationRequest.from_json((out / "request.json").read_text())
    assert re.fullmatch(r"case-payments-[0-9a-f]{12}", request.request_id)
    assert request.symptom == cli.SYMPTOM and request.policy_version == POLICY_VERSION


def test_investigate_abstains_on_unknown_record_with_exit_0(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, text, _ = run_cli(
        capsys, "investigate", "--snapshot", snapshot, "--invoice", "NOPE",
        "--output", tmp_path / "o",
    )  # fmt: skip
    assert code == 0 and "No conclusion reached" in text


def test_later_cutoff_is_refused_earlier_is_accepted(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run_cli(
        capsys, "investigate", "--snapshot", snapshot, "--payment", "PAY-5004",
        "--cutoff", "2026-07-01", "--output", tmp_path / "late",
    )  # fmt: skip
    assert code == 2 and "snapshot's cutoff" in err
    assert not (tmp_path / "late").exists()
    records = write_records(tmp_path / "r.csv", "payments,PAY-5003")
    code, _, err = run_cli(
        capsys, "batch", "--snapshot", snapshot, "--records", records,
        "--cutoff", "2026-07-01", "--output", tmp_path / "late-batch",
    )  # fmt: skip
    assert code == 2 and "snapshot's cutoff" in err
    assert not (tmp_path / "late-batch").exists()
    code, _, _ = run_cli(
        capsys, "investigate", "--snapshot", snapshot, "--payment", "PAY-5004",
        "--cutoff", "2026-06-15", "--output", tmp_path / "early",
    )  # fmt: skip
    assert code == 0


def test_investigate_refuses_existing_output(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "o").mkdir()
    code, _, err = run_cli(
        capsys, "investigate", "--snapshot", snapshot, "--payment", "PAY-5004",
        "--output", tmp_path / "o",
    )  # fmt: skip
    assert code == 2 and err.startswith("error: ")


def foreign_request(snapshot: Path, tmp_path: Path) -> Path:
    # Names a snapshot other than the prepared one: the investigator fails the case.
    other = "b" * 64
    req = InvestigationRequest(
        "case-x", other, RecordRef(other, "payments", "PAY-5004"), "s",
        date(2026, 6, 30), POLICY_VERSION,
    )  # fmt: skip
    path = tmp_path / "foreign.jsonl"
    path.write_text(req.to_json() + "\n")
    return path


def test_failed_case_gives_exit_1_in_investigate_and_batch(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = foreign_request(snapshot, tmp_path)
    code, _, _ = run_cli(
        capsys, "investigate", "--snapshot", snapshot, "--request", path,
        "--output", tmp_path / "i",
    )  # fmt: skip
    assert code == 1
    code, out, err = run_cli(
        capsys, "batch", "--snapshot", snapshot, "--requests", path,
        "--output", tmp_path / "b",
    )  # fmt: skip
    assert code == 1 and "failed: 1" in out and "failed" in err


def test_batch_from_records(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = write_records(
        tmp_path / "r.csv",
        "payments,PAY-5003",
        "invoices,INV-1001",
        "payments,PAY-5007",
    )
    out = tmp_path / "run"
    code, text, _ = run_cli(
        capsys, "batch", "--snapshot", snapshot, "--records", records, "--output", out
    )
    assert code == 0 and "answered: 2" in text and "abstained: 1" in text
    manifest = json.loads((out / "run-manifest.json").read_text())
    assert manifest["complete"] and manifest["outcomes"] == {
        "answered": 2,
        "abstained": 1,
    }
    assert mode(out) == 0o700
    assert "PAY-5003" not in (out / "run-manifest.json").read_text()


@pytest.mark.parametrize(
    "rows",
    [
        ["payments,PAY-5003", "payments,PAY-5003"],  # duplicate
        ["gl_entries,JE-1"],  # unsupported table
        ["payments,"],  # no id
    ],
)
def test_bad_records_are_refused(
    snapshot: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    rows: list[str],
) -> None:
    records = write_records(tmp_path / "r.csv", *rows)
    code, _, err = run_cli(
        capsys, "batch", "--snapshot", snapshot, "--records", records,
        "--output", tmp_path / "run",
    )  # fmt: skip
    assert code == 2 and err.startswith("error: ")
    assert not (tmp_path / "run").exists()


def test_records_header_is_checked(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "r.csv"
    path.write_text("id\nPAY-5003\n")
    code, _, err = run_cli(
        capsys, "batch", "--snapshot", snapshot, "--records", path,
        "--output", tmp_path / "run",
    )  # fmt: skip
    assert code == 2 and "table,record_id" in err


def finished_run(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], *rows: str
) -> Path:
    records = write_records(tmp_path / "r.csv", *rows)
    out = tmp_path / "run"
    assert (
        run_cli(
            capsys,
            "batch",
            "--snapshot",
            snapshot,
            "--records",
            records,
            "--output",
            out,
        )[0]
        == 0
    )
    return out


def test_report_html_and_json(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = finished_run(
        snapshot, tmp_path, capsys, "payments,PAY-5003", "payments,PAY-5007"
    )
    code, _, _ = run_cli(capsys, "report", "--run", run, "--format", "html")
    assert code == 0
    assert (run / "report" / "index.html").is_file()
    assert len(list((run / "report" / "cases").iterdir())) == 2
    assert mode(run / "report") == 0o700
    code, _, _ = run_cli(
        capsys, "report", "--run", run, "--format", "json", "--output", tmp_path / "j"
    )
    assert code == 0
    docs = [json.loads(p.read_text()) for p in (tmp_path / "j").iterdir()]
    assert len(docs) == 2
    assert all({"packet", "title", "explanation"} == set(d) for d in docs)
    assert {d["title"] for d in docs} == {
        "Payment possibly missing from the bank",
        "No conclusion reached",
    }


def test_report_refuses_existing_output(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = finished_run(snapshot, tmp_path, capsys, "payments,PAY-5003")
    (tmp_path / "there").mkdir()
    code, _, err = run_cli(
        capsys,
        "report",
        "--run",
        run,
        "--format",
        "html",
        "--output",
        tmp_path / "there",
    )
    assert code == 2 and err.startswith("error: ")


def test_report_refuses_a_tampered_packets_file(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = finished_run(snapshot, tmp_path, capsys, "payments,PAY-5003")
    packets = run / "packets.jsonl"
    text = packets.read_text()
    packets.write_text(text.replace("F12", "F06"))
    code, _, err = run_cli(capsys, "report", "--run", run, "--format", "html")
    assert code == 2 and "differs" in err and not (run / "report").exists()


def test_report_refuses_an_unfinished_run(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = finished_run(snapshot, tmp_path, capsys, "payments,PAY-5003")
    (run / "run-manifest.json").unlink()
    code, _, err = run_cli(capsys, "report", "--run", run, "--format", "html")
    assert code == 2 and "did not finish" in err


def test_report_of_a_single_investigation_is_pointed_to_its_html(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_cli(
        capsys, "investigate", "--snapshot", snapshot, "--payment", "PAY-5004",
        "--output", tmp_path / "i",
    )  # fmt: skip
    code, _, err = run_cli(
        capsys, "report", "--run", tmp_path / "i", "--format", "html"
    )
    assert code == 2 and "report.html" in err


HOSTILE = [
    "../../escape",
    "<script>alert(1)</script>",
    "a/b\\c",
    "x‮y",
    "con:..",
    "..",
]


def test_hostile_record_ids_cannot_escape_the_report_folder(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = finished_run(
        snapshot, tmp_path, capsys, *[f'payments,"{h}"' for h in HOSTILE]
    )
    out = tmp_path / "rep"
    assert (
        run_cli(capsys, "report", "--run", run, "--format", "html", "--output", out)[0]
        == 0
    )
    root = out.resolve()
    produced = list(out.rglob("*"))
    assert all(root in p.resolve().parents or p.resolve() == root for p in produced)
    assert not (tmp_path / "escape").exists()
    pages = (out / "index.html").read_text() + "".join(
        p.read_text() for p in (out / "cases").iterdir()
    )
    assert "<script>" not in pages
    assert len(list((out / "cases").iterdir())) == len(HOSTILE)


def test_colliding_file_names_are_refused(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = digest(snapshot)
    reqs = [
        InvestigationRequest(
            rid,
            d,
            RecordRef(d, "payments", "PAY-5003"),
            "s",
            date(2026, 6, 30),
            POLICY_VERSION,
        )  # fmt: skip
        for rid in ("a:b", "a_b")
    ]
    path = tmp_path / "r.jsonl"
    path.write_text("".join(r.to_json() + "\n" for r in reqs))
    run = tmp_path / "run"
    assert (
        run_cli(
            capsys, "batch", "--snapshot", snapshot, "--requests", path, "--output", run
        )[0]
        == 0
    )
    code, _, err = run_cli(capsys, "report", "--run", run, "--format", "html")
    assert code == 2 and "same report file name" in err
    assert not (run / "report").exists()


def test_batch_output_must_be_new(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = write_records(tmp_path / "r.csv", "payments,PAY-5003")
    (tmp_path / "run").mkdir()
    code, _, err = run_cli(
        capsys, "batch", "--snapshot", snapshot, "--records", records,
        "--output", tmp_path / "run",
    )  # fmt: skip
    assert code == 2 and err.startswith("error: ")


def test_fetch_uses_a_fixed_git_argument_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        out = cli.FINRCA_REVISION + "\n" if "rev-parse" in argv else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    target = tmp_path / "up"
    code, _, _ = run_cli(
        capsys, "data", "fetch", "--dataset", "finrca", "--output", target
    )
    assert code == 0
    assert calls[0][0] == [
        "git",
        "clone",
        "--no-checkout",
        "--",
        cli.FINRCA_URL,
        str(target / "FinRCA-AI-Bench"),
    ]
    assert ["checkout", "--detach", cli.FINRCA_REVISION] == calls[1][0][-3:]
    assert all(isinstance(a, list) and not k.get("shell") for a, k in calls)


def test_fetch_checks_an_existing_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "FinRCA-AI-Bench").mkdir()
    head = {"value": cli.FINRCA_REVISION}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "clone" not in argv
        return subprocess.CompletedProcess(argv, 0, head["value"] + "\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    argv = ("data", "fetch", "--dataset", "finrca", "--output", tmp_path)
    code, out, _ = run_cli(capsys, *argv)
    assert code == 0 and "pinned revision" in out
    head["value"] = "0" * 40
    code, _, err = run_cli(capsys, *argv)
    assert code == 2 and "not the pinned" in err


def test_there_is_no_evaluate_command(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run_cli(capsys, "evaluate")
    assert code == 2 and "invalid choice" in err


def test_importing_the_cli_loads_no_adapter() -> None:
    code = (
        "import sys, reconcile.cli, reconcile.reports.write, reconcile.runs.batch\n"
        "bad = [m for m in sys.modules if m.startswith('reconcile.adapters')]\n"
        "assert not bad, bad\n"
        "assert not [m for m in sys.modules if m.startswith('evaluation')]\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr


def test_batch_and_report_never_load_an_adapter(snapshot: Path, tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "from reconcile.cli import main\n"
        "rc = main(['batch', '--snapshot', sys.argv[1], '--records', sys.argv[2],"
        " '--output', sys.argv[3]])\n"
        "rc2 = main(['report', '--run', sys.argv[3], '--format', 'html'])\n"
        "bad = [m for m in sys.modules if m.startswith('reconcile.adapters')]\n"
        "assert rc == 0 and rc2 == 0 and not bad, (rc, rc2, bad)\n"
    )
    records = write_records(tmp_path / "r.csv", "payments,PAY-5003")
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            code,
            str(snapshot),
            str(records),
            str(tmp_path / "o"),
        ],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr


def test_inference_image_drops_the_cli_and_demo() -> None:
    dockerfile = (
        Path(cli.__file__).parents[2] / "deploy/inference/Dockerfile"
    ).read_text()
    for name in ("adapters", "cli.py", "demo"):
        assert f"/app/reconcile/{name}" in dockerfile


def test_batch_requests_with_a_later_cutoff_are_refused(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = digest(snapshot)

    def line(rid: str, cutoff: date) -> str:
        r = InvestigationRequest(
            rid, d, RecordRef(d, "payments", "PAY-5003"), "s", cutoff, POLICY_VERSION
        )
        return r.to_json() + "\n"

    path = tmp_path / "r.jsonl"
    path.write_text(line("ok", date(2026, 6, 30)) + line("late", date(2026, 7, 1)))
    code, _, err = run_cli(
        capsys, "batch", "--snapshot", snapshot, "--requests", path,
        "--output", tmp_path / "run",
    )  # fmt: skip
    assert code == 2 and "line 2" in err and "snapshot's cutoff" in err
    assert not (tmp_path / "run").exists()
    path.write_text(line("ok", date(2026, 6, 30)))
    code, _, err = run_cli(
        capsys, "batch", "--snapshot", snapshot, "--requests", path,
        "--cutoff", "2026-06-01", "--output", tmp_path / "run2",
    )  # fmt: skip
    assert code == 2 and "--records only" in err
    path.write_text(line("ok", date(2026, 6, 30)) + "not json\n")
    code, _, err = run_cli(
        capsys, "batch", "--snapshot", snapshot, "--requests", path,
        "--output", tmp_path / "run3",
    )  # fmt: skip
    assert code == 2 and "line 2" in err and "Traceback" not in err
    path.write_text(line("ok", date(2026, 6, 30)))
    assert run_cli(
        capsys, "batch", "--snapshot", snapshot, "--requests", path,
        "--output", tmp_path / "run4",
    )[0] == 0  # fmt: skip
