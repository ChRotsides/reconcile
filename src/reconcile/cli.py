"""Command line: prepare data, investigate one record, run a batch, write reports.

Preparation adapters are imported only inside the commands that need them, so
`batch`, `investigate` and `report` run where the adapters are absent (the
inference image). `evaluate` is deliberately not a command: src/reconcile never
imports evaluation/ (docs/isolation.md).

Exit codes: 0 success; 1 a case failed; 2 a usage or prepared-data problem,
printed as one `error: ...` line without a traceback.
"""

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from reconcile.diagnosis.policy import POLICY_VERSION
from reconcile.domain.contracts import (
    Disposition,
    EvidencePacket,
    InvestigationRequest,
    RecordRef,
)
from reconcile.runs.record import (
    RunRecordError,
    app_version,
    make_private_dir,
    now_utc,
    read_snapshot_manifest,
    write_private_file,
    write_run_manifest,
)

FINRCA_URL = "https://github.com/PratikGhawate/FinRCA-AI-Bench"
FINRCA_REVISION = "76c93ec32536e29826e2ab9e68b58e0351410bc7"
FINRCA_DIR = "FinRCA-AI-Bench"
# The sealed split is not offered. Kept equal to finrca_requests.SPLITS by a test.
SPLITS = ("train", "validation", "test")
SYMPTOM = "Analyst asked to investigate this record."
RECORD_TABLES = ("payments", "invoices", "bank_transactions")


class CliError(Exception):
    """A usage or prepared-data problem: printed as `error: ...`, exit 2."""


# ------------------------------------------------------------------ requests


def _request_for(
    manifest: dict[str, Any], table: str, record_id: str, cutoff: date
) -> InvestigationRequest:
    digest = manifest["snapshot"]["content_sha256"]
    short = hashlib.sha256(record_id.encode()).hexdigest()[:12]
    return InvestigationRequest(
        request_id=f"case-{table}-{short}",
        snapshot=digest,
        primary_entity=RecordRef(digest, table, record_id),
        symptom=SYMPTOM,
        cutoff=cutoff,
        policy_version=POLICY_VERSION,
    )


def _cutoff(manifest: dict[str, Any], given: date | None) -> date:
    prepared = date.fromisoformat(manifest["cutoff"])
    if given is None:
        return prepared
    if given > prepared:
        raise CliError(
            f"cutoff {given} is later than the snapshot's cutoff {prepared}: rows "
            "after it were dropped when the data was prepared, so a search "
            "would wrongly report them as absent. Prepare the data again with "
            "a later cutoff."
        )
    return given


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a date (YYYY-MM-DD)"
        ) from None


def _records_requests(
    path: Path, manifest: dict[str, Any], cutoff: date
) -> list[InvestigationRequest]:
    try:
        f = path.open(newline="", encoding="utf-8-sig")
    except OSError as e:
        raise CliError(f"cannot read {path}: {e.strerror}") from e
    with f:
        reader = csv.reader(f)
        if next(reader, None) != ["table", "record_id"]:
            raise CliError(f"{path}: the first line must be exactly: table,record_id")
        requests: list[InvestigationRequest] = []
        seen: set[tuple[str, str]] = set()
        for line, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != 2 or not row[1].strip():
                raise CliError(f"{path} line {line}: expected table,record_id")
            table, record_id = row[0].strip(), row[1].strip()
            if table not in RECORD_TABLES:
                raise CliError(
                    f"{path} line {line}: table must be one of {list(RECORD_TABLES)}"
                )
            if (table, record_id) in seen:
                raise CliError(f"{path} line {line}: duplicate record")
            seen.add((table, record_id))
            requests.append(_request_for(manifest, table, record_id, cutoff))
    if not requests:
        raise CliError(f"{path} lists no records")
    return requests


def _lines(requests: Sequence[InvestigationRequest]) -> bytes:
    return "".join(r.to_json() + "\n" for r in requests).encode()


# ------------------------------------------------------------------ commands


def _fetch(args: argparse.Namespace) -> int:
    target = args.output / FINRCA_DIR

    def git(*argv: str) -> str:
        try:
            done = subprocess.run(
                ["git", *argv], check=True, capture_output=True, text=True
            )
        except FileNotFoundError:
            raise CliError("git is not installed") from None
        except subprocess.CalledProcessError as e:
            raise CliError(f"git {argv[0]} failed: {e.stderr.strip()}") from None
        return done.stdout.strip()

    if target.exists():
        head = git("-C", str(target), "rev-parse", "HEAD")
        if head != FINRCA_REVISION:
            raise CliError(
                f"{target} is at {head}, not the pinned {FINRCA_REVISION}; "
                "remove it or choose another folder"
            )
        print(f"{target} already at the pinned revision {FINRCA_REVISION}")
        return 0
    args.output.mkdir(parents=True, exist_ok=True)
    git("clone", "--no-checkout", "--", FINRCA_URL, str(target))
    git("-C", str(target), "checkout", "--detach", FINRCA_REVISION)
    head = git("-C", str(target), "rev-parse", "HEAD")
    if head != FINRCA_REVISION:
        raise CliError(f"checkout ended at {head}, not {FINRCA_REVISION}")
    print(f"fetched {FINRCA_URL} at {FINRCA_REVISION} into {target}")
    return 0


def _print_manifest(manifest: dict[str, Any]) -> None:
    for name, info in manifest.get("tables", {}).items():
        if isinstance(info, dict) and "rows_after" in info:
            dropped = info.get("dropped_by_cutoff", 0)
            print(
                f"  {name}: {info['rows_after']} rows ({dropped} dropped after cutoff)"
            )
    for key in (
        "bank_data_through",
        "tables_not_supplied",
        "fee_journals_recognised",
        "reference_quality",
    ):
        if key in manifest:
            print(f"  {key}: {json.dumps(manifest[key], sort_keys=True)}")


def _prepare(args: argparse.Namespace) -> int:
    if args.output.exists():
        raise CliError(f"{args.output} already exists; choose a new folder")
    given = [
        args.benchmark_dir,
        args.split,
        args.requests_output,
        args.evaluator_output,
    ]
    if any(v is not None for v in given) and (
        args.format != "finrca" or any(v is None for v in given)
    ):
        raise CliError(
            "requests need --format finrca with --benchmark-dir, --split, "
            "--requests-output and --evaluator-output together"
        )
    # Adapters are imported here, never at module load.
    if args.format == "csv-bundle":
        from reconcile.adapters.csv_bundle import prepare_csv_bundle

        manifest = prepare_csv_bundle(args.source, args.output, cutoff=args.cutoff)
    elif args.format == "xero-camt":
        from reconcile.adapters.xero_camt import prepare_xero_camt

        manifest = prepare_xero_camt(args.source, args.output, cutoff=args.cutoff)
    else:
        from reconcile.adapters.finrca import prepare_finrca_snapshot

        if args.key_file is None or args.source_revision is None:
            raise CliError("--format finrca needs --key-file and --source-revision")
        manifest = prepare_finrca_snapshot(
            args.source,
            args.output,
            key_path=args.key_file,
            source_revision=args.source_revision,
            cutoff=args.cutoff,
        )
    print(f"prepared {args.output}")
    _print_manifest(manifest)
    if any(v is not None for v in given):
        from reconcile.adapters.finrca_requests import prepare_finrca_requests

        summary = prepare_finrca_requests(
            args.benchmark_dir,
            args.split,
            snapshot_dir=args.output,
            key_path=args.key_file,
            inference_dir=args.requests_output,
            evaluator_dir=args.evaluator_output,
            policy_version=POLICY_VERSION,
        )
        print(f"requests: {summary['requests']} ({summary['split']})")
    return 0


def _report_text(packet: EvidencePacket) -> None:
    from reconcile.reports.explain import summary, title

    print(title(packet))
    for paragraph in summary(packet):
        print(paragraph)


def _investigate(args: argparse.Namespace) -> int:
    from reconcile.diagnosis.pipeline import investigate
    from reconcile.reports.html import render_packet

    manifest = read_snapshot_manifest(args.snapshot)
    cutoff = _cutoff(manifest, args.cutoff)
    if args.request is not None:
        try:
            request = InvestigationRequest.from_json(args.request.read_text())
        except OSError as e:
            raise CliError(f"cannot read {args.request}: {e.strerror}") from e
    else:
        table, record_id = next(
            (t, v)
            for t, v in (
                ("payments", args.payment),
                ("invoices", args.invoice),
                ("bank_transactions", args.bank_transaction),
            )
            if v is not None
        )
        request = _request_for(manifest, table, record_id, cutoff)
    if request.cutoff > date.fromisoformat(manifest["cutoff"]):
        raise CliError(
            f"the request's cutoff {request.cutoff} is later than the snapshot's"
        )
    started = now_utc()
    out = make_private_dir(args.output)
    packet = investigate(args.snapshot / "snapshot.sqlite", request)
    request_text = request.to_json() + "\n"
    write_private_file(out / "request.json", request_text.encode())
    write_private_file(out / "packet.json", (packet.to_json() + "\n").encode())
    page = render_packet(packet, manifest=manifest)
    write_private_file(out / "report.html", page.encode())
    write_run_manifest(
        out,
        command="investigate",
        started=started,
        snapshot_dir=args.snapshot,
        request_source_sha256=hashlib.sha256(request_text.encode()).hexdigest(),
        outcomes={packet.diagnosis.disposition.value: 1},
    )
    _report_text(packet)
    print(f"report: {out / 'report.html'}")
    return 1 if packet.diagnosis.disposition is Disposition.FAILED else 0


def _run_batch(snapshot: Path, requests_bytes: bytes, out: Path) -> dict[str, int]:
    """Run the unchanged `run_requests` into the new folder `out` and add the
    run record last."""
    from reconcile.runs.batch import run_requests

    started = now_utc()
    make_private_dir(out)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "requests.jsonl"
            source.write_bytes(requests_bytes)
            counts = run_requests(snapshot, source, out)
    except BaseException:
        try:
            out.rmdir()  # only an empty folder: a failed start leaves nothing
        except OSError:
            pass
        raise
    write_private_file(out / "requests.jsonl", requests_bytes)
    write_run_manifest(
        out,
        command="batch",
        started=started,
        snapshot_dir=snapshot,
        request_source_sha256=hashlib.sha256(requests_bytes).hexdigest(),
        outcomes=counts,
    )
    return counts


def _check_request_cutoffs(path: Path, data: bytes, manifest: dict[str, Any]) -> None:
    prepared = date.fromisoformat(manifest["cutoff"])
    for number, line in enumerate(data.decode(errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            request = InvestigationRequest.from_json(line)
        except (ValueError, TypeError) as e:
            raise CliError(f"{path} line {number}: invalid request: {e}") from e
        if request.cutoff > prepared:
            raise CliError(
                f"{path} line {number}: cutoff {request.cutoff} is later than "
                f"the snapshot's cutoff {prepared}: rows after it were dropped, "
                "so a search would wrongly report them as absent"
            )


def _batch(args: argparse.Namespace) -> int:
    manifest = read_snapshot_manifest(args.snapshot)
    if args.records is not None:
        requests = _records_requests(
            args.records, manifest, _cutoff(manifest, args.cutoff)
        )
        data = _lines(requests)
    else:
        if args.cutoff is not None:
            raise CliError(
                "--cutoff applies to --records only; each request in --requests "
                "carries its own cutoff"
            )
        try:
            data = args.requests.read_bytes()
        except OSError as e:
            raise CliError(f"cannot read {args.requests}: {e.strerror}") from e
        _check_request_cutoffs(args.requests, data, manifest)
    counts = _run_batch(args.snapshot, data, args.output)
    print(f"run: {args.output}")
    for outcome, n in sorted(counts.items()):
        print(f"  {outcome}: {n}")
    failed = counts.get(Disposition.FAILED.value, 0)
    if failed:
        print(f"{failed} case(s) failed; see packets.jsonl", file=sys.stderr)
    return 1 if failed else 0


def _report(args: argparse.Namespace) -> int:
    from reconcile.reports.write import write_report

    out = args.output if args.output is not None else args.run / "report"
    opened = write_report(args.run, out, args.format)
    print(f"report: {opened}")
    return 0


def _demo(args: argparse.Namespace) -> int:
    from reconcile.adapters.csv_bundle import prepare_csv_bundle
    from reconcile.demo import CUTOFF, MAPPING, RECORDS
    from reconcile.reports.explain import title
    from reconcile.reports.write import PACKETS_FILE, write_report

    root = make_private_dir(args.output)
    cutoff = date.fromisoformat(CUTOFF)
    snapshot = root / "snapshot"
    manifest = prepare_csv_bundle(MAPPING, snapshot, cutoff=cutoff)
    requests = _records_requests(RECORDS, manifest, cutoff)
    _run_batch(snapshot, _lines(requests), root / "run")
    index = write_report(root / "run", root / "report", "html")
    packets = [
        EvidencePacket.from_json(line)
        for line in (root / "run" / PACKETS_FILE).read_text().splitlines()
    ]
    for p in sorted(
        packets,
        key=lambda p: (
            p.request.primary_entity.table,
            p.request.primary_entity.record_id,
        ),
    ):
        e = p.request.primary_entity
        print(f"{e.table} {e.record_id}: {title(p)}")
    print(f"index: {index}")
    return 0


# -------------------------------------------------------------------- parser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reconcile",
        description="Read-only investigator for accounts-payable-to-bank exceptions.",
    )
    parser.add_argument(
        "--version", action="version", version=f"reconcile {app_version()}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    data = sub.add_parser("data", help="fetch or prepare data").add_subparsers(
        dest="data_command", required=True
    )
    fetch = data.add_parser("fetch", help="clone the pinned FinRCA benchmark")
    fetch.add_argument("--dataset", choices=["finrca"], required=True)
    fetch.add_argument("--output", type=Path, required=True)
    fetch.set_defaults(handler=_fetch)

    prep = data.add_parser("prepare", help="turn exports into a prepared snapshot")
    prep.add_argument(
        "--format", choices=["csv-bundle", "xero-camt", "finrca"], required=True
    )
    prep.add_argument(
        "--source",
        type=Path,
        required=True,
        help="mapping/config file, or the FinRCA 'full' folder",
    )
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--cutoff", type=_parse_date, required=True)
    prep.add_argument("--key-file", type=Path)
    prep.add_argument("--source-revision")
    prep.add_argument("--benchmark-dir", type=Path)
    prep.add_argument("--split", choices=SPLITS)
    prep.add_argument("--requests-output", type=Path)
    prep.add_argument("--evaluator-output", type=Path)
    prep.set_defaults(handler=_prepare)

    inv = sub.add_parser("investigate", help="investigate one record")
    inv.add_argument("--snapshot", type=Path, required=True)
    which = inv.add_mutually_exclusive_group(required=True)
    which.add_argument("--request", type=Path)
    which.add_argument("--payment")
    which.add_argument("--invoice")
    which.add_argument("--bank-transaction")
    inv.add_argument("--cutoff", type=_parse_date)
    inv.add_argument("--output", type=Path, required=True)
    inv.set_defaults(handler=_investigate)

    batch = sub.add_parser("batch", help="investigate many records")
    batch.add_argument("--snapshot", type=Path, required=True)
    source = batch.add_mutually_exclusive_group(required=True)
    source.add_argument("--requests", type=Path)
    source.add_argument("--records", type=Path)
    batch.add_argument("--cutoff", type=_parse_date)
    batch.add_argument("--output", type=Path, required=True)
    batch.set_defaults(handler=_batch)

    report = sub.add_parser("report", help="write a report from a finished run")
    report.add_argument("--run", type=Path, required=True)
    report.add_argument("--format", choices=["html", "json"], required=True)
    report.add_argument("--output", type=Path)
    report.set_defaults(handler=_report)

    demo = sub.add_parser("demo", help="run the offline demo on invented data")
    demo.add_argument("--output", type=Path, required=True)
    demo.set_defaults(handler=_demo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:  # argparse: 0 for --help/--version, 2 for usage
        return e.code if isinstance(e.code, int) else 2
    try:
        code: int = args.handler(args)
        return code
    except (CliError, RunRecordError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
