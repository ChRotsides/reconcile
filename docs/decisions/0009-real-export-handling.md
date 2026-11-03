# 0009. Handling real exports

**Status:** accepted for M5, 2026-11-03. Tested on invented fixtures and public samples only.

## Context
M5 lets people run the investigator on their own exports. FinRCA is uniform, so it cannot show what goes wrong on real files: a bank export that stops early, a missing counterparty, a journal that cannot be typed as a fee. Each of these can turn missing evidence into a false alarm, and `AGENTS.md` section 2 forbids that.

## Decision
- **Source IDs stay unchanged** on real exports, so an analyst can find the record. Only FinRCA is tokenised. Snapshots and reports therefore hold real data and are private to their owner (mode 700).
- **Nothing is guessed.** A date must match a declared format, and a date that two formats read differently stops preparation. Values must be in a declared closed map. Rows are never dropped.
- **What cannot be measured is declared, and shown as declared:** the dates the bank data covers, that every paying account is covered, whether references match exactly or after normalising, and whether fee journals are typed. Each is recorded in the manifest and in the report.
- **Where a value or a search is unavailable, the investigator abstains.** That covers an absent required value, an unmatched-debit search that cannot run, a shortfall with no declared fee typing, a payment before the bank data starts, and a payment whose bank rows include a reversal, credit or return.
- **The request cutoff must equal the prepared cutoff.** An earlier or later one means preparing again.
- **Supplied controls are enforced.** CAMT entry counts, per-currency balances and statement continuity must agree, or the statement is refused.
- **Benchmark packets must not change:** each rule fires only on values FinRCA does not have.

## Where
`src/reconcile/adapters/operational.py`, `csv_bundle.py`, `xero.py`, `camt053.py`, `xero_camt.py`, `inputs.py`; `src/reconcile/diagnosis/pipeline.py`, `settled.py`, `invoice.py`, `payment.py`; `tests/unit/test_missing_data.py` and the adapter tests. Provenance: D157 to D172.

## Alternatives rejected
1. Refuse any export missing a field: safe, but unusable on common bank files, even though duplicate cases do not need those fields.
2. Fill gaps with defaults or placeholders: a placeholder hides that a search did not run.
3. Infer coverage or fee typing from the rows present: a quiet account and a truncated export look the same, and one typed journal says nothing about the others.
4. Leave these failures to M6: M5 is what exposes them.

## Evidence
Three review rounds found reproducible false F06 and F12 paths on this input, and each now has a regression test. Train and validation packets stay byte-identical to run `m3-dev-8`. Measured outcomes per check are in `docs/input-mapping.md` section 10.3.

## Consequences and limits
- On Xero plus CAMT.053 alone only F06 is answered; the rest abstain for stated reasons (`docs/m5-results.md` section 3).
- Declarations are claims by whoever prepares the data, and nothing can check them.
- No real export has been read. Whether references match across real files (A029) is the largest open question.

## Revisit if
A real export or a practitioner contradicts a declaration's premise, a format's mapping, or the shortlist itself.
