# 0007. Metric choice

**Status:** accepted for M2, 2026-11-02. **Provisional:** M3 is about to freeze the evaluation protocol (`evaluation/protocol.yaml`) and will update this record with the frozen definitions, thresholds and denominators.

## Context
Category accuracy alone hides unsupported answers: the published classical-ML baseline already identifies all 49 three-family test cases on leaky inputs but returns no evidence (`PLAN.md` M0 gate outcome). `PLAN.md` section 7 therefore sets the primary outcome as evidence-complete correct-case coverage.

## Decision (state at the end of M2)
- **Primary:** cases with the correct supported category and complete evidence under our own evidence contract, divided by all cases in the cohort. Abstentions and failures count as not successful. Cohort: F06, F12, F14 and the ten payment-related legitimate variants (503 gradable train and validation cases). Evidence completeness is checked against `docs/evidence-contract.md`, not upstream `evidence_ids`, with every cited search re-run on the snapshot.
- **Headline view scores all cases.** A **sensitivity view** excludes only the 5 label conflicts verified by hand and listed in `evaluation/verified_label_conflicts.json`. A generic overlap rule only proposes candidates; it never excludes a case.
- **Safety metrics beside it:** false-alarm rate on legitimate cases, and the share of out-of-scope failures given an all-clear (the "out-of-scope all-clear risk"). FX cases are a separate abstention slice and never count as successes.
- **Comparison:** the investigator against an observable-input classical-ML baseline on identical cohorts. The baseline's evidence figure is a record-availability upper bound, not a packet.
- Statistics group cases by vendor group; question variants of one case are not independent cases (`docs/data-audit.md` section 5).
- Evidence precision is not the literal `PLAN.md` formula for now: recall is the share of contract requirement items met, and precision is replaced by the citation resolution rate (D122; to be restated in the M3 protocol).

## Alternatives considered
1. Category accuracy (the simplest option): rejected. It is satisfied by a classifier that gives no evidence.
2. Upstream `evidence_ids` as the evidence reference: rejected. For F12 and F14 each set includes an audit event the allowlist excludes, so it cannot be met from allowed inputs (`docs/evidence-contract.md` section 1).
3. Exclude suspected label conflicts automatically: rejected. Record overlap does not prove the label is wrong.

## Evidence
Run `m2-dev-7` (`docs/dev-results.md` section 14): headline 481/503 = 0.956 correct and complete (validation 119/123); sensitivity view 481/498 = 0.966; evidence-complete among answered 497/497; false alarms 0/526; out-of-scope all-clears 0/859. Baseline `obs-5` on validation, all cases: investigator 119/123, baseline 109/123; false alarms on legitimate cases 0/139 against 25/139; all-clears 0/225 against 6/225 (`docs/baseline-observable.md` section 3). Tests: `tests/unit/test_cohort.py`, `tests/unit/test_dev_run_completeness.py`, `tests/unit/test_observable_baseline.py`. Provenance: D028, D058, D105, D110, D114, D116, D118, D122.

## Consequences and limits
- These are development numbers. The contract and investigator were written against the same train and validation labels, and the baseline was chosen among 15 candidates on validation; neither is unseen (A055: selection bias unverified). No sealed comparison exists.
- No uncertainty intervals are reported yet, and the baseline lacks packets and report templates (carried to M5).
- Practitioner input (usefulness, next-step agreement, self-reported effort) is a separate kind of evidence and none has been collected.
- The contract rules were partly tuned on development results; one such change is disclosed in `docs/evidence-contract.md` section 4.1.

## Revisit if
M3 freezes the protocol (primary metric, practical-improvement threshold, false-alarm margin, paired vendor-cluster bootstrap), the independent protocol review finds a flaw, or practitioners say the contract's evidence is not what they need.
