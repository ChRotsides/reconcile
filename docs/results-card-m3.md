# M3 baseline results card

**Development data only (FinRCA `validation` split, synthetic benchmark). The sealed comparison has not been run.** Nothing here is evidence for the claim in `docs/evaluation-protocol.md`: the investigator's rules and evidence contract were partly tuned on these labels, and the baseline was selected among 15 candidates on this split. The numbers exercise the pipeline and the decision rule. Benchmark results, practitioner estimates and our own measurements are different kinds of evidence; only benchmark-style development figures appear here.

Sources (untracked artifacts): investigator `artifacts/dev-runs/m3-dev-7/` (`metrics.json` with top-level `hard_gates` and `provenance`, `report.txt`, `cases.jsonl`, `rerun-1.json`, `absence-check.txt`, `run-manifest.json`); baseline `artifacts/baselines/obs-m3-5/` (`metrics.json`, `run-manifest.json`, `predictions_validation.jsonl`, `cases.jsonl`, `cases_threshold_0.85.jsonl`); comparisons and decision `artifacts/rehearsal-record/` (`cmp_answer_all.json`, `cmp_threshold_0.85.json`, `decision.json`). The comparison files record their input digests, and `sha256sum` of the three case files matches them: `m3-dev-7/cases.jsonl` bdbab9a8... (also the `cases_sha256` in `m3-dev-7/metrics.json` `provenance`), `obs-m3-5/cases.jsonl` 22837545..., `obs-m3-5/cases_threshold_0.85.jsonl` df688ca4... (both also the digests in the baseline `run-manifest.json`). The comparison files do not themselves record a baseline-manifest digest; they record the baseline system, model and configuration, and the investigator's hard gates and provenance. That the compare step refuses inputs that do not match the scorer's recorded digest and the baseline manifest is a property of the code, which I did not test for this card. Per-family and per-difficulty rows for the baseline's `threshold_0.85` configuration, and the sensitivity figures of the two baseline configurations, were counted from the `cases` files with the same success definition as `evaluation/compare.py` (they are not stored in `metrics.json`).

## 1. Cases

Validation: 416 requests. Headline view `all_cases` (cohort plus 1 label conflict): 123 cases in 55 vendor clusters. Sensitivity view (without the 1 hand-verified label conflict, RCA_002212): 122 cases. Legitimate cases of the split (false-alarm denominator): 139 in 59 clusters. FX slice: 9. Out-of-scope failures: 225.

Investigator run (from `run-manifest.json`): tag `m3-dev-7`, policy `evidence-contract-0.5`, cutoff 2026-06-30, source revision `76c93ec`, run from a clean committed tree at code commit `5a6843f` (branch `m3-protocol`). The manifest and `provenance` record that commit plus code digests: `src_sha256` f33e3c8d... (unchanged from `m3-dev-5` and `m3-dev-6`), `evaluation_sha256` 615b9cbc... and `deploy_sha256` cf76c961.... The earlier `m3-dev-6` recorded a commit that predated the hardened scorer and had no evaluator or container digests, which is why the record runs (`m3-dev-7`, `obs-m3-5`, the rehearsal) were repeated at a clean commit with code digests. Snapshot content `da5d6ea9...` (`m3-dev-6` had `021491ad...`; the snapshot is rebuilt with a fresh key for each run, so the opaque tokens and digests differ between runs).

The scorer was hardened after review round 2 (commit `dd3cb40`): malformed packets are unsuccessful; batch, split and due-date settlement calculations are checked for meaning; every relevant journal is validated; the tolerance currency is checked; only recognised search forms are executed; and the run order is inference, then same-image rerun, then scoring from a verified snapshot copy. Run-level hard gates (in `metrics.json` and carried into the comparisons and the decision), all `true` in `m3-dev-7`: `absence_check_passed`, `all_answered_packets_well_formed`, `all_calculations_exact`, `all_citations_resolve`, `all_day_counts_exact`, `rerun_identical_same_image`.

Baseline: tag `obs-m3-5`, selected `hgb lr=0.05 depth=3 l2=1.0`, seed 20261101, 77 features, threshold 0.85 chosen by the declared train cross-validation rule. Its `run-manifest.json` records the same code commit and digests as the investigator run. Its `cases.jsonl` and `cases_threshold_0.85.jsonl` are byte-identical to `obs-m3-4`'s (`cmp`) and have the same SHA-256 as `obs-m3-3`'s (`sha256sum`: 22837545... and df688ca4...), and its `metrics.json` differs from `obs-m3-4`'s only in `command`, `efficiency`, `investigator_validation`, `seconds` and `tag` (compared key by key in Python). The quality figures below are therefore unchanged from the previous card.

**Baseline isolation and selection (corrected wording).** The baseline script writes a hashed predictions file (`predictions_validation.jsonl`, 416 rows, SHA-256 4078a350... recorded as `predictions_sha256.validation` in `run-manifest.json`) from operational inputs only (requests and snapshot) before that split's links and labels are joined for scoring, and scoring verifies the hash. This does not make the validation predictions blind to validation labels: the model was selected among 15 candidates using validation labels, by design, because validation is the protocol's selection split. On the sealed split, selection must never touch sealed labels; running prediction as an isolated process with a fixed fitted model and no label or evaluator files reachable is an M7 requirement, not yet implemented. The script stops if the selected model or the cross-validation threshold differs from the protocol's frozen ones (`check_model`, `check_threshold` in `evaluation/baselines/observable/run.py`; I read this code and did not run the stop paths for this card).

## 2. Quality on validation (counts are numerator/denominator)

"Evidence-complete" for the investigator requires the packet audit and the semantic calculation checks to pass (section 8).

| Measure | Investigator | Baseline `answer_all` | Baseline `threshold_0.85` |
|---|---:|---:|---:|
| Answered, headline | 122/123 = 0.992 | 123/123 = 1.000 | 97/123 = 0.789 |
| Answer precision (exact category) | 119/122 = 0.975 | 109/123 = 0.886 | 94/97 = 0.969 |
| **Correct and evidence-complete, headline** | **119/123 = 0.967** | **109/123 = 0.886** | **94/123 = 0.764** |
| Correct and evidence-complete, sensitivity | 119/122 = 0.975 | 109/122 = 0.893 | 94/122 = 0.770 |
| Evidence-complete among correct | 119/119 | 109/109 | 94/94 |
| False alarms, ALL legitimate cases | 0/139 | 25/139 = 0.180 | 3/139 = 0.022 |
| False alarms, cohort legitimate (71) | 0/71 | 8/71 = 0.113 | 3/71 = 0.042 |
| False alarms, AP-side legitimate (59) | 0/59 | 17/59 = 0.288 | 0/59 |
| Out-of-scope all-clears | 0/225 | 6/225 = 0.027 | 0/225 |
| Abstained / out-of-scope / failed (headline) | 1 / 0 / 0 | 0 / 0 / 0 | 26 / 0 / 0 |
| FX slice | 9/9 correct abstentions | 0/9 abstained (all 9 answered `legitimate_fx_settlement`) | same as `answer_all` |

The investigator's 0/139 is an observed count, not a guarantee about rates.

**False-alarm definition.** The count is now group-independent (commit `498a3fa`): a case is a false alarm if its label is legitimate, the system answered, and the answer is a failure category, whatever the case's group. The counts did not change: investigator 0/526 over all legitimate train and validation cases (`m3-dev-7/metrics.json`, `all_splits`: 0/315 in-cohort variants including FX, 0/211 AP-side), validation 0/139; baseline validation 25/139 (`answer_all`) and 3/139 (`threshold_0.85`).

Baseline evidence completeness is a relaxed record-availability upper bound, not a packet check, so the baseline's "complete" column favours it. The baseline's `answer_all` also has 111/123 answered cases evidence-complete in the sense of `evidence_complete_among_answered` (which includes wrong answers); the figures above use correct-and-complete.

**By family (headline view; cases / answered / correct and complete).**

| Family | Investigator | `answer_all` | `threshold_0.85` |
|---|---:|---:|---:|
| F06 invoice paid twice | 18 / 18 / 18 | 18 / 18 / 18 | 18 / 18 / 18 |
| F12 ERP payment missing from bank | 16 / 16 / 16 | 16 / 16 / 16 | 16 / 16 / 16 |
| F14 bank/ERP amount mismatch | 18 / 18 / 18 | 18 / 18 / 17 | 18 / 15 / 15 |
| Legitimate variants (71) | 71 / 70 / 67 | 71 / 71 / 58 | 71 / 48 / 45 |

**By difficulty (headline view; cases / answered / correct and complete).**

| Difficulty | Investigator | `answer_all` | `threshold_0.85` |
|---|---:|---:|---:|
| easy | 28 / 27 / 27 | 28 / 28 / 17 | 28 / 8 / 5 |
| medium | 70 / 70 / 67 | 70 / 70 / 67 | 70 / 64 / 64 |
| hard | 25 / 25 / 25 | 25 / 25 / 25 | 25 / 25 / 25 |

The investigator's 4 misses are the abstention on RCA_002212 (the verified label conflict, reason `payment_journal_amount_differs`) and 3 legitimate cases it answered with the wrong legitimate variant (`wrong_legit_variant` 3): all three are `legitimate_weekend_clearing` answered `legitimate_batch_payment`, so 6 of 9 weekend-clearing cases are correct. The baseline's `answer_all` misses (14) are 8 false alarms, 1 wrong failure class and 5 wrong legitimate variants. All 8 of its cohort false alarms are called `F08_APPROVAL_WORKFLOW_FAILURE`: 7 on `legitimate_batch_payment` and 1 on `legitimate_weekend_clearing` (counted from `obs-m3-4/cases.jsonl`, which is byte-identical to `obs-m3-5/cases.jsonl` and has the same digest as `obs-m3-3`; see `docs/baseline-observable.md` section 3).

## 3. Investigator supporting metrics (validation, answered cohort cases)

| Metric | Value |
|---|---:|
| Evidence recall (requirement items met) | 1475/1475 = 1.000 |
| Citation resolution | 805/805 = 1.000 |
| Reference integrity: valid references / emitted findings | 485/485 = 1.000 (invalid 0/485) |
| Monetary correctness: exact / reported calculations | 491/491 = 1.000 |
| Calculation coverage (answered cases with a calculation) | 106/122 = 0.869 |
| Execution reliability (all 416 attempted): failed / time-limit abstentions / row-limit abstentions | 0/416, 0/416, 0/416 |

The evidence-recall denominator is 1475 in `m3-dev-7` (the same as `m3-dev-6`; 1385 in `m3-dev-5`): the hardened scorer checks more requirement items per answered packet, and all are met. Train: 4414/4414; all splits: 5889/5889. The other supporting counts on validation did not change. In `m3-dev-7` the answered packets are all well formed (`all_answered_packets_well_formed: true`; `well_formed` is true for 1608/1608 case rows).

**Reference integrity** (called claim support before the M3 review) checks that a finding's evidence ids are in the packet and resolve, and that its calculations and day counts exist and validate. It does not check whether the finding's assertion is true. Predicate-level claim checking is not implemented and is carried to M4 (`docs/evaluation-protocol.md`).

A rate of 1.000 on a development set the rules were written against is not a measure of real-world reliability. Zero observed errors does not establish zero risk.

## 4. Efficiency

| | Investigator (validation, 416 cases) | Baseline (`obs-m3-5`) |
|---|---:|---:|
| Latency p50 (nearest rank) | 0.00111 s | 0.0567 s |
| Latency p95 (nearest rank) | 0.00702 s | 0.0776 s |
| Throughput | 427.5 cases/s | 16.7 cases/s |
| Peak RSS | 28548 KiB (process; all splits 42732 KiB) | 330636 KiB (whole script process, including the grid search) |
| Cost | 0 USD (no paid calls) | 0 USD |

**These latencies are not comparable one-to-one.** The investigator figure is end-to-end per case inside the network-less container: retrieval and packet construction over the snapshot. The baseline figure is one single-row `predict_proba` call on the fitted model with the features already extracted, so it excludes feature extraction and training; its training fit took 2.511 s for the selected model and the whole script took 233.4 s including the 15-candidate grid. Peak RSS is process-wide and not a per-case figure; the two processes also do different work. Timings vary between runs: `obs-m3-3`, with identical quality figures, recorded a baseline p50 of 0.0529 s, `obs-m3-4` 0.0576 s and `obs-m3-5` 0.0567 s; the investigator's p50 was 0.00107 s in `m3-dev-5`, 0.00108 s in `m3-dev-6` and 0.00111 s in `m3-dev-7`.

## 5. Paired comparisons (investigator minus baseline, headline view, validation)

Protocol statistics (`evaluation/protocol.toml`): method `cluster_percentile_bootstrap`, seed 20261102, 10000 replicates, alpha 0.02 (nominal 98% interval), vendor clusters, minimum 30 clusters (`too_few_clusters: false` in both files; 55 and 59 clusters).

| Comparison | Counts | Difference | 98% interval |
|---|---|---:|---|
| Primary vs `answer_all` | 123 cases, 55 clusters; both 108, only investigator 11, only baseline 1, neither 3 | +0.0813 (10/123) | [+0.0349, +0.1500] |
| Primary vs `threshold_0.85` | 123 cases, 55 clusters; both 94, only investigator 25, only baseline 0, neither 4 | +0.2033 (25/123) | [+0.1111, +0.2866] |
| False alarms vs `answer_all` | 139 legitimate cases, 59 clusters; only investigator 0, only baseline 25 | -0.1799 (-25/139) | [-0.2500, -0.0957] |
| False alarms vs `threshold_0.85` | 139 legitimate cases, 59 clusters; only investigator 0, only baseline 3 | -0.0216 (-3/139) | [-0.0526, 0.0000] |

The false-alarm criterion is "no observed increase": an observed-sample engineering rule, not a statistical non-inferiority claim, and its interval is descriptive.

## 6. Dress-rehearsal verdict

`evaluation/decide.py` on these four comparisons (`artifacts/rehearsal-record/decision.json`, `mode: rehearsal`, protocol version 1): all four checks `pass`, verdict `holds`. The decision records the six hard gates (`absence_check_passed`, `all_answered_packets_well_formed`, `all_calculations_exact`, `all_citations_resolve`, `all_day_counts_exact`, `rerun_identical_same_image`), all `true`; the decision rule treats a failed hard gate as a failure of a release verdict. The comparisons are bound to the scorer's `cases_sha256` and the baseline's case digests (see Sources). The false-alarm checks are labelled "no observed increase (descriptive interval)". Two facts matter for reading it:

- The false-alarm upper bound against `threshold_0.85` is exactly 0.0000, which the rule accepts (a bound above 0 fails). Three baseline false alarms in total is thin; one different vendor cluster in the bootstrap could move the bound.
- **This is a rehearsal of the pipeline, not a result.** Validation is development data: the investigator's rules were partly tuned on it, and the baseline was selected on it. The verdict on the sealed split could differ in either direction, and with fewer than 30 clusters a check would be `not_established` (`docs/evaluation-protocol.md`). The sealed cohort size and cluster count are unknown.

## 7. Reproducibility

- **Investigator rerun and absence check.** `artifacts/dev-runs/m3-dev-7/rerun-1.json`: `ok: true`, `identical: true`, `image_id_matches_manifest: true`; the manifest records the same rerun under `rerun`. The rerun image id `sha256:d4fbf0dd...bc4c4` equals the manifest's, and the train packets digest `73842d16...eb54fa` and validation packets digest `e2deaa53...41099f` equal the manifest's. The scorer ran only after this same-image rerun. The in-container absence check (`absence-check.txt`; manifest `absence_check.passed: true`, output digest 25159677...) lists the links files, evaluator, tests, fixtures, data and key paths, and the `challenge_test` directory as `absent`. The earlier `m3-dev-1` rerun ran on a rebuilt shared image tag and is not valid evidence.
- **Baseline key-invariance.** Before the fix in `docs/baseline-observable.md` section 7, a key change moved the model grid, the cross-validation table and the importances. After the fix, two runs with fresh keys (`obs-k1`, `obs-k2`) gave `metrics.json` files identical except `tag`, `command`, `seconds` and `efficiency` (a previous check, not repeated for this card).
- The `investigator_validation` block inside `obs-m3-5/metrics.json` names run `m3-dev-7`, the same run the investigator figures in this card are read from.
- The baseline's seed 20261101 is for the model and does not affect the bootstrap seed 20261102.

## 8. Evaluator checks tightened after the independent review

From the messages of commits `93ec9f3` and `be6179e`:

- A correct answer counts as evidence-complete only if the packet audit passes (citations resolve in the run's snapshot; calculations and day counts recompute exactly) and each required calculation has the right operation, operands and conclusion.
- Searches must match the evaluator's exact predicate; journals must balance in full; evaluator arithmetic uses an exact Decimal context; scoring inputs are digest-bound, read once and reconciled with the label roster.
- Net-fee `unexplained`, accounted-fee `fee_row_minus_journals` and the three reversal equalities must be differences of exactly the contract's records, recomputed exactly within one quantum; the reversal credit must not predate the debit. An arithmetically correct but irrelevant calculation no longer counts.
- Claim support was renamed reference integrity (section 3).

**No development case changed outcome.** How this was checked: `artifacts/dev-runs/m3-dev-2/cases.jsonl` and `artifacts/dev-runs/m3-dev-5/cases.jsonl` have the same 1608 case ids (train and validation); `disposition`, `category`, `outcome` and `complete` are identical for all of them (0 differences). The evidence-complete totals in `metrics.json` are also identical for train, validation and all splits (validation: 122/122 among answered, 119/119 among correct, 119/123 correct and complete). What differs between the two runs is evidence-detail fields in the case rows, the metric rename, and the validation evidence-recall denominator, which rose from 1361 (`m3-dev-2`) to 1385 (`m3-dev-5`) as more requirement items are checked, all met. Only `m3-dev-2` against `m3-dev-5` was compared here; the commit messages state "no case changes" for `m3-dev-3` and `m3-dev-5`, which was not re-verified for `m3-dev-3`.

**Scorer hardening after review round 2 (`dd3cb40`): no development case changed.** `artifacts/dev-runs/m3-dev-5/cases.jsonl` and `artifacts/dev-runs/m3-dev-6/cases.jsonl` were loaded by `case_id` and compared field by field. Both have the same 1608 case ids; `disposition`, `category`, `outcome` and `reason` are identical for all 1608 (0 differences each). Every answered case row carries `complete` (587 of 587 in `m3-dev-7`; non-answers have none), and `outcome == "correct"` means only that the category matches the label: a primary success needs both, which is what `evaluation/compare.py` counts. Completeness is also visible in the metrics totals, which are identical for validation (122/122 among answered, 119/119 among correct, 119/123 correct and complete), and in `m3-dev-6` all 587 answered rows that carry `req_total` have `req_met == req_total`. Fields that differ between the runs are only: `well_formed` (new, in all 1608 rows), `req_met`/`req_total` (163 rows, new), and `review_candidate_with` in 6 rows (different opaque shared tokens from the fresh key). Metrics that moved: the evidence-recall denominators (validation 1385 to 1475, train 4198 to 4414, all splits 5583 to 5889, all met). Citation resolution, reference integrity and monetary correctness have the same counts in both runs (validation 805/805, 485/485, 491/491). Only `m3-dev-5` against `m3-dev-6` was compared for this change; the earlier comparison in this section (`m3-dev-2` against `m3-dev-5`) was not repeated, and no-change for `m3-dev-3` was not re-verified.

**Record runs repeated at a clean commit: no development case changed.** `artifacts/dev-runs/m3-dev-6/cases.jsonl` and `artifacts/dev-runs/m3-dev-7/cases.jsonl` were loaded by `case_id` in Python and compared field by field. Both have the same 1608 case ids; `disposition`, `category`, `outcome`, `reason`, `well_formed`, `req_met` and `req_total` are identical for all 1608 (0 differences each). The only field that differs between the two files is `review_candidate_with` in 6 rows (different opaque shared tokens from the fresh key); all other fields of all rows are equal. `metrics.json` of the two runs was compared key by key: the only differences are `tag`, the `efficiency` timings and peak RSS, `provenance` (`snapshot_file_sha256`, `cases_sha256`, code commit, the new `evaluation_sha256` and `deploy_sha256`) and the opaque tokens in `review_candidates`; every quality figure (validation, train, all splits) is identical. For the baseline, `obs-m3-5/cases.jsonl` and `cases_threshold_0.85.jsonl` are byte-identical to `obs-m3-4`'s (`cmp`), and the three comparison files and the decision give the same counts, differences and intervals as the previous card's (read from `artifacts/rehearsal-record/`; they differ only in recording the new case digest and provenance).
