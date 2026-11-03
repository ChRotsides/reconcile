# Reconcile

A source-available, evidence-backed investigator for payment reconciliation exceptions. Free for research and other noncommercial use; commercial use needs a separate license ([License](#license)).

## What it is, and who it is for

Reconcile helps a finance operations analyst look into one accounts-payable-to-bank exception: a payment, an invoice or a bank row that does not reconcile. It reads exports of your accounting and bank data and writes a report showing a conclusion, the source records it rests on, the exact arithmetic, what it could not check and what to look at next. It is also a Python library and command line tool for developers.

It covers three kinds of exception (a possible duplicate payment, a payment missing from the bank, a bank amount smaller than the payment) and several ordinary cases that are not errors (for example a payment still within its clearing limit, or a debit reduced by a booked bank fee).

## Status today

- Milestones M0 to M4 have passed their gates, some conditionally: the practitioner reviews they asked for have not happened. M5 (a usable command line tool and HTML reports) is in progress.
- No practitioner has used it. No walkthrough session has been held ([kit](docs/practitioner-walkthrough.md)).
- The only results are development results on a synthetic public benchmark (FinRCA). The sealed comparison has not been run, and no real accounting or bank export has been read.

## What it does, and what it refuses to do

- It is read-only. It has no payment, write-back, database or shell tools.
- It makes no model calls and needs no network or credentials. A model-assisted drafting experiment was tested in M4 and removed ([results](docs/m4-results.md)).
- Money is exact (`Decimal` with an explicit currency); each calculation in a report is shown with its operands.
- When the evidence is missing, ambiguous or outside what it supports, it abstains or says the case is out of scope. It does not turn missing evidence into "nothing wrong".

## Try it

From a checkout, with Python 3.11 or later and [uv](https://docs.astral.sh/uv/) ([full commands](docs/cli-guide.md)):

```bash
uv build --wheel
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python dist/reconcile-0.1.0-py3-none-any.whl
.venv/bin/reconcile demo --output demo-out
```

Open `demo-out/report/index.html`. The demo uses invented data shipped in the package.

## Results of record (development data)

Source: [docs/results-card-m3.md](docs/results-card-m3.md), section 2 and 5. Synthetic benchmark, FinRCA `validation` split: 123 cases in the supported families. These are development results: the rules were partly tuned on this split and the baseline was chosen on it. The sealed comparison is still to come, and it could come out differently.

| Measure | Reconcile | Baseline `answer_all` | Baseline `threshold_0.85` |
|---|---:|---:|---:|
| Correct and evidence-complete | 119/123 = 0.967 | 109/123 = 0.886 | 94/123 = 0.764 |
| False alarms on legitimate cases | 0/139 | 25/139 = 0.180 | 3/139 = 0.022 |

The baseline is a gradient-boosted classifier on the same observable inputs. Its "evidence-complete" is a relaxed check that records exist, not a check of the evidence contract, so it favours the baseline. Paired differences in the first row are +0.0813 (98% interval +0.0349 to +0.1500) and +0.2033 (+0.1111 to +0.2866). Zero false alarms is an observed count, not a guarantee.

## What it cannot do yet

- Real exports are untested. Mappings for Xero (saved JSON) and CAMT.053 (XML) exist, but only fixtures and public samples were used ([input mapping](docs/input-mapping.md) section 10).
- Whether payment references match between accounting and bank data is unverified, and the checks depend on it.
- Xero has no approval history, so every all-clear on Xero data abstains. There is no live Xero or bank connection.
- Only payment-related exceptions are supported; other kinds are reported as out of scope. Multi-currency (FX) cases abstain.
- PDF or OCR input, a hosted interface and approvals are not planned for this release ([scope](SCOPE.md)).

## Documents

[CLI guide](docs/cli-guide.md) | [Plan](PLAN.md) | [Scope](SCOPE.md) | [Evaluation protocol](docs/evaluation-protocol.md) | [M3 results card](docs/results-card-m3.md) | [Decision records](docs/decisions/README.md) | [Practitioner findings](docs/practitioner-findings.md)

## License

Reconcile is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE), which permits research and other noncommercial use. It is not an OSI-approved open-source license. For commercial use, contact Charalambos Rotsides at lhampis@gmail.com.
