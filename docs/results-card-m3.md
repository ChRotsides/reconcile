# M3 baseline results card

**Development data only (FinRCA `validation` split, synthetic benchmark). The sealed comparison has not been run.** Nothing here is evidence for the claim in `docs/evaluation-protocol.md`: the investigator's rules and evidence contract were partly tuned on these labels, and the baseline was selected among 15 candidates on this split. The numbers exercise the pipeline and the decision rule. Benchmark results, practitioner estimates and our own measurements are different kinds of evidence; only benchmark-style development figures appear here.

Sources (untracked artifacts): investigator `artifacts/dev-runs/m3-dev-1/` (`metrics.json`, `report.txt`, `cases.jsonl`, `rerun-1.json`); baseline `artifacts/baselines/obs-m3-2/` (`metrics.json`, `cases.jsonl`, `cases_threshold_0.85.jsonl`); comparisons `artifacts/compare/m3-val-protocol-answer_all.json`, `m3-val-protocol-threshold_0.85.json`; decision `artifacts/compare/m3-val-decision.json`. Per-family and per-difficulty rows for the baseline's `threshold_0.85` configuration, and the sensitivity figures of the two baseline configurations, were counted from the `cases` files with the same success definition as `evaluation/compare.py` (they are not stored in `metrics.json`).

## 1. Cases

Validation: 416 requests. Headline view `all_cases` (cohort plus 1 label conflict): 123 cases in 55 vendor clusters. Sensitivity view (without the 1 hand-verified label conflict, RCA_002212): 122 cases. Legitimate cases of the split (false-alarm denominator): 139 in 59 clusters. FX slice: 9. Out-of-scope failures: 225. Investigator run: tag `m3-dev-1`, policy `evidence-contract-0.5`, cutoff 2026-06-30, source revision `76c93ec`, snapshot content `2a751d28...c8d9`. Baseline: tag `obs-m3-2`, selected `hgb lr=0.05 depth=3 l2=1.0`, seed 20261101, 77 features, threshold 0.85 chosen by the declared train cross-validation rule.

## 2. Quality on validation (counts are numerator/denominator)

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

The investigator's 4 misses are the abstention on RCA_002212 (the verified label conflict) and 3 legitimate cases it answered with the wrong legitimate variant (`wrong_legit_variant` 3; of which `legitimate_weekend_clearing` 3 of 9 are not correct). The baseline's `answer_all` misses (14) are 8 false alarms, 1 wrong failure class and 5 wrong legitimate variants; 7 of its 8 cohort false alarms are legitimate batch payments called an F08 approval failure (`docs/baseline-observable.md` section 3).

## 3. Investigator supporting metrics (validation, answered cohort cases)

| Metric | Value |
|---|---:|
| Evidence recall (requirement items met) | 1361/1361 = 1.000 |
| Citation resolution | 805/805 = 1.000 |
| Claim support: unsupported / emitted | 0/485 = 0.000 |
| Monetary correctness: exact / reported calculations | 491/491 = 1.000 |
| Calculation coverage (answered cases with a calculation) | 106/122 = 0.869 |
| Execution reliability (all 416 attempted): failed / time-limit abstentions / row-limit abstentions | 0/416, 0/416, 0/416 |

A rate of 1.000 on a development set the rules were written against is not a measure of real-world reliability. Zero observed errors does not establish zero risk.

## 4. Efficiency

| | Investigator (validation, 416 cases) | Baseline (`obs-m3-2`) |
|---|---:|---:|
| Latency p50 (nearest rank) | 0.00113 s | 0.0729 s |
| Latency p95 (nearest rank) | 0.00730 s | 0.0745 s |
| Throughput | 409.2 cases/s | 15.3 cases/s |
| Peak RSS | 28680 KiB (process; all splits 44452 KiB) | 330112 KiB (whole script process, including the grid search) |
| Cost | 0 USD (no paid calls) | 0 USD |

**These latencies are not comparable one-to-one.** The investigator figure is end-to-end per case inside the network-less container: retrieval and packet construction over the snapshot. The baseline figure is one single-row `predict_proba` call on the fitted model with the features already extracted, so it excludes feature extraction and training; its training fit took 2.404 s for the selected model and the whole script took 239.2 s including the 15-candidate grid. Peak RSS is process-wide and not a per-case figure; the two processes also do different work.

## 5. Paired comparisons (investigator minus baseline, headline view, validation)

Method `cluster_percentile_bootstrap`, seed 20261102, 10000 replicates, alpha 0.02 (nominal 98% interval), vendor clusters.

| Comparison | Counts | Difference | 98% interval |
|---|---|---:|---|
| Primary vs `answer_all` | 123 cases, 55 clusters; both 108, only investigator 11, only baseline 1, neither 3 | +0.0813 (10/123) | [+0.0349, +0.1500] |
| Primary vs `threshold_0.85` | 123 cases, 55 clusters; both 94, only investigator 25, only baseline 0, neither 4 | +0.2033 (25/123) | [+0.1111, +0.2866] |
| False alarms vs `answer_all` | 139 legitimate cases, 59 clusters; only investigator 0, only baseline 25 | -0.1799 (-25/139) | [-0.2500, -0.0957] |
| False alarms vs `threshold_0.85` | 139 legitimate cases, 59 clusters; only investigator 0, only baseline 3 | -0.0216 (-3/139) | [-0.0526, 0.0000] |

## 6. Dress-rehearsal verdict

`evaluation/decide.py` on these four comparisons (`artifacts/compare/m3-val-decision.json`): all four checks `pass`, verdict `holds`. Two facts matter for reading it:

- The false-alarm upper bound against `threshold_0.85` is exactly 0.0000, which the rule accepts (`> 0` fails). Three baseline false alarms in total is thin; one different vendor cluster in the bootstrap could move the bound.
- **This is a rehearsal of the pipeline, not a result.** Validation is development data: the investigator's rules were partly tuned on it, and the baseline was selected on it. The verdict on the sealed split could differ in either direction, and with few clusters it may be `not_established`. The sealed cohort size and cluster count are unknown.

## 7. Reproducibility

- **Investigator rerun.** `artifacts/dev-runs/m3-dev-1/rerun-1.json`: `identical: true`; train packets digest `1739e5a4...89af2d4` and validation packets digest `9ee11726...467b62` both equal the manifest's digests. The same file reports `image_id_matches_manifest: false`: the rerun image id is `sha256:b31d75e3...2cdb79` and the manifest's is `sha256:dcbef6ec...dfc`. The mismatch is attributed to a concurrent rebuild of the shared image tag `reconcile-inference:dev` between the run and the rerun; that attribution is unverified. The packet digests were identical despite it, but the rerun did not run on the same image as the original run.
- **Baseline key-invariance.** Before the fix in `docs/baseline-observable.md` section 7, a key change moved the model grid, the cross-validation table and the importances. After the fix, two runs with fresh keys (`obs-k1`, `obs-k2`) give `metrics.json` files identical except `tag`, `command`, `seconds` and `efficiency` (checked again for this card). `obs-m3-2` differs from `obs-k1` only in those keys and in `investigator_validation`, which compares against `m3-dev-1` instead of `m2-dev-7-wp1`.
- The baseline in this card is the `obs-m3-2` run; its seed 20261101 is for the model and does not affect the bootstrap seed 20261102.
