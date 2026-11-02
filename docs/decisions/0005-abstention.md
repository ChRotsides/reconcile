# 0005. Abstention

**Status:** accepted, 2026-11-02 (decided in M1 and M2).

## Context
`PLAN.md` section 4 requires every case to end in `answered`, `abstained`, `out_of_scope` or `failed`, and says `legitimate` is a supported financial conclusion, never a fallback for a failed investigation.

## Decision
- Dispositions and their meaning are fixed in `src/reconcile/domain/contracts.py`. A `Diagnosis` with category `legitimate` or `legitimate_*` is rejected unless it is answered, has supported findings, and lists no missing information.
- Abstain, with a specific reason code, when evidence is missing or ambiguous: for example `primary_entity_not_in_snapshot`, `ambiguous_reference`, `commitment_not_proven_at_cutoff`, a retrieval limit reached, and the known-gap patterns (`bank_batch_grouping_unmodelled`, `reversal_followed_by_repayment`, `reference_unmatched_nearby_debit`).
- Cross-currency pairs abstain with `unsupported_fx`; there is no conversion path in the allowed inputs.
- A request outside the three families gets `out_of_scope`. An unexpected error gives `failed` with the exception type only.
- No all-clear without an allocation to an invoice, a valid final approval at the payment date, and a balanced payment journal in the payment's currency. Every all-clear states what was not checked (`docs/evidence-contract.md` section 5).
- An answered "no exception" with evidence exists for shapes such as distinct identical payments and matched partial payments (user decision, 2026-11-01). It is not a general clean bill.

## Alternatives considered
1. Always answer with the best-scoring category (the simplest option): rejected. The first development run (`m2-dev-1`) called 184 of 859 out-of-scope failures legitimate or no-exception (`docs/dev-results.md` sections 5 and 9).
2. Extend scope to diagnose the remaining families (F08, F09): rejected for now by the user; it is a scope change.
3. Specific abstentions plus the all-clear guard: chosen. It cost some answers (five cohort cases became abstentions).

## Evidence
- `tests/unit/test_domain_contracts.py::test_missing_evidence_cannot_produce_legitimate`, `test_legitimate_subcategories_follow_the_same_rule`.
- `tests/unit/test_investigator.py`: `test_missing_primary_entity_abstains`, `test_old_pending_payment_abstains`, `test_batch_search_over_limit_abstains`, `test_payment_before_final_approval_abstains`, `test_partial_payment_on_due_date_is_not_legitimate`; `tests/unit/test_runs.py::test_exception_becomes_failed_packet`.
- Run `m2-dev-7` (`docs/dev-results.md` section 14): over 1,608 requests, answered 587, abstained 301, out_of_scope 720, failed 0. All-clear on out-of-scope failures 0/859; false alarms 0/526 legitimate cases; 30/30 FX cases abstained. Run 1 had 184/859 all-clears (`m2-dev-1` and `m2-dev-3` in the same file).
- Provenance: D059, D092, D097, D098, D103, D106, D107.

## Consequences and limits
- Zero observed false alarms and all-clears on development data does not establish zero risk (`PLAN.md` section 7). The contract was written from the same generator, so it fits these cases by construction (`docs/dev-results.md` section 1).
- The known-gap abstentions rest on guesses about real data until practitioners describe those shapes (no practitioner has been interviewed).
- An abstention costs an analyst time; the abstention rate is reported, not hidden.

## Revisit if
Practitioners disagree with an abstention reason or a no-exception answer, scope is widened, or a held-out run shows an all-clear on a failure.
