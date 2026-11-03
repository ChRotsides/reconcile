# Command line guide

Reconcile investigates one accounts-payable payment, invoice or bank row against the bank data you give it and writes a report that shows the evidence. It only reads files; it has no write, payment or database tools.

**Status.** No practitioner has used this tool yet. The demo runs on invented data. Nothing here is a claim that it helps an analyst, only a description of what the commands do.

## Install

Python 3.11 or later, Linux or macOS (the batch runner uses the `resource` module, which Windows lacks). There are no runtime dependencies.

Build the wheel from a checkout of this repository, then install it. Select Python 3.11 or later explicitly: outside the repository `uv venv` may pick an older Python and the install fails.

```bash
uv build --wheel
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python dist/reconcile-0.1.0-py3-none-any.whl
.venv/bin/reconcile --version
```

## Demo

```bash
reconcile demo --output demo-out
```

It prepares an invented company's CSV files (shipped inside the package), investigates six records, and writes an HTML report. It uses no network. It prints each case's title and the path of `demo-out/report/index.html`. The six cases are a possible duplicate payment, a payment possibly missing from the bank, a bank debit smaller than the payment with no fee journal, a debit reduced by a booked bank fee, a payment still within its clearing limit, and one with no conclusion because two payments share a reference.

## Prepare your own data

`reconcile data prepare --format F --source PATH --output DIR --cutoff YYYY-MM-DD`

- `csv-bundle`: `PATH` is a `mapping.toml` that says which CSV file holds each table and where each column comes from. See `examples/csv_bundle/` and the demo's `mapping.toml` (in the package, `reconcile/demo/bundle/`). Dates need declared formats, so mixed formats are handled explicitly.
- `xero-camt`: `PATH` is a small TOML config naming saved Xero JSON files and CAMT.053 XML files. See `fixtures/xero_camt/config.toml` and `docs/input-mapping.md`.
- `finrca`: for the public benchmark only; it needs `--key-file` and `--source-revision`.

The cutoff is the last day of data you trust. Rows after it are dropped. The command prints row counts, `bank_data_through`, `tables_not_supplied`, `fee_journals_recognised` and `reference_quality`. Read them: they decide what the tool can say. A missing table means that table cannot support a conclusion; no fee journals recognised means fee questions abstain.

### What is refused, and why

- A cutoff after `bank_data_through`: a payment made after the bank data ends would look missing from the bank.
- A date, number or status that matches no declared rule, or two rules that disagree: nothing is guessed. Every offending file, line and value is listed.
- An existing output folder: nothing is overwritten.
- A later `--cutoff` when investigating than the snapshot was prepared with: rows after it were dropped, so a search would wrongly report them as absent.

## Investigate one record

```bash
reconcile investigate --snapshot prepared --payment PAY-5004 --output case-1
```

Use `--invoice` or `--bank-transaction` for the other record types, or `--request FILE` for a request file. It prints a plain title and summary and writes `packet.json`, `report.html`, `request.json` and a run record into `case-1`.

## Batches

```bash
reconcile batch --snapshot prepared --records records.csv --output run-1
reconcile report --run run-1 --format html
```

`records.csv` has the header `table,record_id`, where `table` is `payments`, `invoices` or `bank_transactions`; duplicates are refused. Or give `--requests FILE.jsonl`. The run folder holds `packets.jsonl`, timings, the requests and `run-manifest.json`, which lists the hash of every file and is written last. `report` refuses a run with no manifest (an interrupted run) or with any file that differs from its recorded hash. Use `--format json` for one JSON file per case with the packet, title and explanation. There is no resume yet: rerun into a new folder.

## Reading a report

Open `report/index.html`, then a case. Each case page gives the conclusion in plain words, next steps, the findings (supported, contradicted or unknown), the exact calculations, the source rows, what is unknown, and the run details. Every conclusion cites rows you can find in your own export. An outcome is one of `answered`, `abstained` (not enough evidence, which is never an all-clear), `out_of_scope` or `failed`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Done. Includes cases that abstained or were out of scope. |
| 1 | A case failed (`investigate`), or at least one case in the batch failed. |
| 2 | Usage or data problem. One `error: ...` line on stderr, no traceback. |

## Library use

```python
from datetime import date
from pathlib import Path
from reconcile.diagnosis.pipeline import investigate
from reconcile.domain.contracts import InvestigationRequest, RecordRef

d = "<content_sha256 from prepared/manifest.json>"
request = InvestigationRequest(
    "case-1",
    d,
    RecordRef(d, "payments", "PAY-5004"),
    "Analyst asked to investigate this record.",
    date(2026, 6, 30),
    "evidence-contract-0.5",
)
packet = investigate(Path("prepared/snapshot.sqlite"), request)
```

## Where outputs live

Prepared snapshots, run folders and reports contain your real financial data. The commands create output folders readable by the owner only (mode 700; the public-benchmark `finrca` snapshot is the exception). Keep them out of git and off shared folders. Delete them when you are done.

## Containers

`deploy/inference/run_inference.sh SNAPSHOT_DIR REQUESTS_DIR OUT_DIR` runs the batch step in a network-less, read-only container that has no adapters, CLI or demo. Folders holding real data are mode 700, so run the script as the user who owns them. See `docs/isolation.md`.

## Reproducing benchmark results

Scoring is not a `reconcile` command, because the tool never imports evaluation code. Use `python -m evaluation.dev_run` (see `docs/evaluation-protocol.md`). `reconcile data fetch --dataset finrca --output DIR` clones the benchmark at the one pinned revision.

## Limits

- With real exports, many checks abstain. For Xero plus CAMT, every `legitimate_*` all-clear abstains unless approval history is supplied, and fee checks abstain without recognised fee journals. See `docs/input-mapping.md` section 10 for what was measured on synthetic fixtures and public samples.
- No real export has been tested, and no practitioner has confirmed any mapping.
- Foreign-currency payments, batch debits and truncated references are not modelled and abstain.
