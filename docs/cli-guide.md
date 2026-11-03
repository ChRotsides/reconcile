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

The cutoff is the last day of data you trust. Rows after it are dropped. The command prints row counts, `bank_data_from`, `bank_data_through`, `tables_not_supplied`, `all_paying_accounts_covered`, `fee_journals_recognised` (your `fee_journals_typed` declaration), `reference_matching` and `reference_quality`. Read them: they decide what the tool can say. A missing table means that table cannot support a conclusion; fee journals not declared as typed means fee questions abstain.

### What you must declare

The tool cannot see whether your export is complete, so you say so, and the report repeats what you said as "declared by whoever prepared the data". Three declarations are not checked against anything: that every paying account is covered, how references match, and whether fee journals are typed. If one is wrong, a payment that is really missing from the bank can look settled, or a settled payment can look missing. The coverage dates are declarations too, but they are also checked against the statements where those state a period (and the CAMT reader refuses a date that claims more than the statements show).

- `bank_data_from` and `bank_data_through`: the bank data is complete between these dates. For `csv-bundle` set both in `mapping.toml` (`bank_data_from` is required). For `xero-camt` they come from the period each CAMT.053 statement states, not from the dates of its entries, and the common period is used. The config may also state `bank_data_from` and `bank_data_through`; that is required only when a statement states no period, and neither may claim more than the statements show.
- `all_paying_accounts_covered` (`csv-bundle`, must be `true`) or `statements_cover_all_paying_accounts` (`xero-camt`, must be `true`): you have supplied bank data for every account that payments are made from. If a paying account has no statements, its payments would look missing, so preparation refuses to run without this statement. It is a declaration; the tool does not check which account a statement belongs to.
- `reference_matching` (both formats): `exact` or `normalised`. There is no default. Choose `exact` unless you know better: references match only if they are equal after trimming surrounding whitespace. `normalised` also ignores case and collapses inner spaces, which can make two different references look the same. The setting is recorded in the manifest.
- `fee_journals_typed` (`csv-bundle`): `true` only if every bank-fee journal in the general ledger file is typed `bank_fee` and sourced to its bank row, so that no such journal means no fee was booked. Otherwise leave it `false`, and a bank debit smaller than its payment abstains instead of being reported as an unexplained shortfall. With `false`, the tool will not answer F14 (it cannot rule out an unrecorded fee journal); a fee journal it positively identifies as the fee for a debit can still support the net-fee all-clear. A Xero export types no journal as a bank fee, so it always declares `false`.

### What is refused, and why

- A cutoff after `bank_data_through`: a payment made after the bank data ends would look missing from the bank.
- A `bank_data_from` after `bank_data_through`, a coverage statement that is not `true`, or a `reference_matching` other than `exact` or `normalised`: the data cannot be used as a basis for "missing from the bank" without them.
- Not a refusal but an abstention: a payment dated before `bank_data_from` is not reported as missing from the bank or as pending (`payment_before_bank_coverage`), because a missing bank row says nothing about a period the data does not cover.
- A date, number or status that matches no declared rule, or two rules that disagree: nothing is guessed. Every offending file, line and value is listed.
- An existing output folder: nothing is overwritten.
- A request whose cutoff differs from the snapshot's, from a request file or a `--requests` line (which is named): rows after the cutoff were dropped when the data was prepared, so a search would wrongly report them as absent. `investigate` and `batch` take no `--cutoff`; they use the one the data was prepared with. To use a different cutoff, prepare the data again.
- A malformed value in the mapping or config file (for example a number where text belongs): one `error:` line, no traceback.

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

- With real exports, many checks abstain. For Xero plus CAMT, every `legitimate_*` all-clear abstains unless approval history is supplied, and an F14 answer becomes an abstention when fee journals are not declared as typed. See `docs/input-mapping.md` section 10 for what was measured on synthetic fixtures and public samples.
- No real export has been tested, and no practitioner has confirmed any mapping.
- There is no currency conversion. Where the tool compares the payment with a bank row found by reference, an invoice, another payment allocated to the same invoice, or a fee journal in another currency, it abstains (`unsupported_fx`). A payment journal in another currency also abstains (`payment_journal_currency_differs`). Comparisons this tool does not make are not guarded.
- A bank debit whose reference matches no payment is looked for only among debits from the same counterparty account in the same currency. If its amount equals the payment's, lies within the clearing window, or equals the payment plus some other unbanked payments to that account (a bounded search), the case abstains. A debit from another account, or without a counterparty account (which also abstains), is not covered by these guards.
- A payment with no bank row anywhere in the searches shown can be reported as possibly missing from the bank (F12). That depends on your coverage declarations above being true.
