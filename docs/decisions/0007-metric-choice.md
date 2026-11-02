# 0007. Metric choice

**Status:** accepted, 2026-11-02; freezes with protocol-v1 (`evaluation/protocol.toml`, explained in `docs/evaluation-protocol.md`).

## Context
Category accuracy alone hides unsupported answers: the published classical-ML baseline already identifies all 49 three-family test cases on leaky inputs but returns no evidence (`PLAN.md` M0 gate outcome). `PLAN.md` section 7 therefore sets the primary outcome as evidence-complete correct-case coverage, and asks M3 to freeze the improvement threshold and false-alarm margin before the held-out test.

## Decision
- **Primary:** cases answered with the expected category whose packet meets every requirement of `docs/evidence-contract.md` (cited searches re-run on the snapshot), divided by all cases of the release cohort: F06, F12, F14 and nine payment-related legitimate variants. Abstentions, out-of-scope outcomes and failures are not successes. FX settlement is a separate abstention slice.
- **Views:** the headline scores every cohort case (no exclusion). A sensitivity view leaves out only hand-verified label conflicts (`evaluation/verified_label_conflicts.json`). On the sealed set the headline counts every in-scope case; the development-only exclusions (`CONTRADICTED`, `NOT_GRADABLE`) cannot apply.
- **Supporting metrics** (conventions in `evaluation/metrics.py`): answer precision, coverage and abstention; root cause accuracy per family and difficulty; evidence recall (requirement items met over required); citation resolution (D122: replaces the literal record-set precision of `PLAN.md`, because a cited record the contract does not require is not an error); claim support; monetary correctness with calculation coverage; execution reliability; efficiency with nearest-rank p50 and p95.
- **False-alarm scope:** legitimate cases diagnosed as failures over ALL legitimate cases of the split, in every group, whatever the view. Out-of-scope all-clear risk is reported beside it.
- **Decision rule:** against every baseline configuration (`answer_all`, `threshold_0.85`), on the sealed headline view: paired primary difference at least +5 points with the lower bound of a nominal 98% vendor-cluster percentile bootstrap interval above 0; and false-alarm difference upper bound at most 0. Fewer than 20 clusters gives `not_established`. User decision 2026-11-02 for the +5 bar, no false-alarm increase and the $25 budget; applying it to both configurations and alpha 0.02 (to reach about 95% actual coverage, measured by simulation) are stricter than or equal to it. Details and the coverage table: `docs/evaluation-protocol.md` sections 7 and 8.

## Alternatives considered
1. Category accuracy (the simplest option): rejected. It is satisfied by a classifier that gives no evidence.
2. Upstream `evidence_ids` as the evidence reference: rejected. For F12 and F14 each set includes an audit event the allowlist excludes, so it cannot be met from allowed inputs (`docs/evidence-contract.md` section 1).
3. Exclude suspected label conflicts automatically: rejected. Record overlap does not prove the label is wrong.
4. The `PLAN.md` candidate of +10 points: replaced by the user's +5 on 2026-11-02.
5. Nominal 95% interval: rejected. Simulated coverage was 0.908 to 0.956 at 20 to 80 clusters; nominal 98% gave 0.954 to 0.978 from 20 clusters.

## Evidence
Development validation run `m3-dev-1` against baseline `obs-m3-2` (`docs/results-card-m3.md`): investigator 119/123 correct and evidence-complete; baseline 109/123 (`answer_all`) and 94/123 (`threshold_0.85`); false alarms over all legitimate cases 0/139 against 25/139 and 3/139; dress-rehearsal verdict `holds`. Coverage measurement: `artifacts/bootstrap_coverage_exact.json` and `_a02.json`. Tests: `tests/unit/test_protocol.py`, `tests/unit/test_cohort.py`. Provenance: D028, D058, D105, D110, D114, D116, D118, D122.

## Consequences and limits
- All numbers are development data; the contract and investigator were written against train and validation labels, and the baseline was chosen among 15 candidates on validation (A055: selection bias unverified). The rehearsal verdict is not evidence for the claim. No sealed comparison exists.
- The coverage simulation uses chosen, not fitted, outcome standard deviations; its sensitivity check is pending.
- The baseline's evidence figure is a record-availability upper bound that favours it; the baseline has no packets or report templates (carried to M5).
- Practitioner input (usefulness, next-step agreement, self-reported effort) is separate evidence and none has been collected.
- The sealed run tooling (an unlock bound to the tagged protocol) does not exist yet and is an M7 task.

## Revisit if
The independent protocol review finds a flaw before the freeze; the sealed cohort has fewer than 20 vendor clusters (then the claim is `not_established` by design); or practitioners say the contract's evidence is not what they need. After the freeze, a change needs a new protocol version and an uncontaminated holdout.
