# 0003. Observable retrieval relationships

**Status:** accepted, 2026-11-02 (decided in M0 to M2).

## Context
`PLAN.md` section 3 says to follow observable financial relationships, add conservative candidate search only where references are missing, and avoid a vector database. Identical IDs across systems do not prove a relationship (section 4).

## Decision
- Inputs are an enumerated allowlist of columns (`docs/data-audit.md` section 3.1), tokenised with a shared namespace so genuine payment-to-bank references still join, and cut off at 2026-06-30 by availability date.
- The investigator follows foreign keys and shared references over a SQLite snapshot with declared limits: 3 hops, 200 rows per query, 10 seconds per investigation. Hitting a row or time limit gives `abstained` with "incomplete evidence", never silent truncation (`src/reconcile/retrieval/snapshot.py`).
- Missing counterparts are searched with bounded, cited absence searches (no bank row with this reference; reference unique; no unreferenced debit with the same currency, amount and counterparty). `docs/evidence-contract.md` sections 2 and 4.
- Equal-looking payments are never merged by amount or date. Ingestion deduplicates only identical rows with the same table key and rejects the same key with different values (`src/reconcile/adapters/finrca.py`).
- Clearing limits (ACH 3, wire 1, check 10, virtual card 2 business days) and the one-minor-unit tolerance are a versioned policy (`src/reconcile/diagnosis/policy.py`, `evidence-contract-0.5`), copied from the benchmark generator's declared values, not fitted to labels.

## Alternatives considered
1. Treat any reference or amount match as a link (the simplest option): rejected. It turns coincidence into a diagnosis; the batch, truncated-reference and identical-payment fixtures show the wrong answers it would give.
2. Semantic or vector retrieval: rejected for now. `PLAN.md` requires a measured justification, and none exists.

## Evidence
- `tests/unit/test_finrca_adapter.py::test_distinct_ids_with_identical_values_both_survive`, `test_exact_duplicate_row_is_a_repeated_import`.
- `tests/unit/test_investigator.py`: `test_row_limit_is_incomplete_evidence`, `test_time_limit_is_incomplete_evidence`, `test_f12_age_versus_limit_boundary`, `test_policy_is_declared_in_every_packet`.
- `tests/unit/test_fixtures.py` runs the public fixtures in `fixtures/public/` (including `real_identical_distinct_payments`, `real_bank_batch_debit`, `real_truncated_bank_reference`).
- Provenance: D037, D045, D054, D055, D056, D064, D084, D101.

## Consequences and limits
- The hop limit is recorded in each packet's provenance but is not enforced in code; the row and time limits are (`PLAN.md` M2 gate outcome). Hop enforcement is carried to M6.
- Completeness of the snapshot is assumed, not proven (A025). Absence findings are only as good as that assumption.
- Business days are weekdays only; there is no holiday calendar (A026).
- The relationships fit the FinRCA tables. The Xero/CAMT.053 mapping in `docs/input-mapping.md` shows several gaps, so fit to real exports is untested.

## Revisit if
Practitioner input shows different clearing limits or reference habits (new policy version), or an export lacks a field the relationships rely on.
