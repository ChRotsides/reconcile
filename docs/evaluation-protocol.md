# Evaluation protocol, version 1

**Status: draft until the commit is tagged `protocol-v1`.** This document explains `evaluation/protocol.toml` in words. If the two disagree, the TOML file and the code it drives (`evaluation/decide.py`, `evaluation/compare.py`, `evaluation/paired.py`, `evaluation/metrics.py`, `evaluation/cohort.py`) are what runs; report the disagreement as a defect. `tests/unit/test_protocol.py` checks that the TOML matches the code for the cohort, the data fields, the deterministic budgets, the statistics block, the status and splits, and the baseline configurations.

## 1. Purpose

`PLAN.md` section 7 requires the practical-improvement threshold and the false-alarm margin to be frozen before the held-out test. This protocol fixes, before any sealed label is opened: the cohort and the success definition, the two systems and their configurations, the decision rule, the statistics, the budgets, and the order of the sealed run. It supports one claim only: whether the deterministic investigator beats a classical-ML baseline on identical observable inputs on the sealed split, on the primary metric, without more false alarms. It says nothing about real exports, practitioner usefulness or speed-ups (`PLAN.md` section 7, "Usefulness evidence", is separate evidence and is never merged into this claim).

## 2. Splits and exposure

| Role | Split | Rule |
|---|---|---|
| development | `train` + `validation` | labels may be read; tuning allowed |
| selection | `validation` | baseline model, thresholds and M4 choices are picked here |
| reproduction | `test` | exposed in M0 (`docs/data-audit.md` section 11); used for reproduction only and never called unseen |
| sealed | `challenge_test` | opened once, in M7, after predictions are final |

The sealed split's exposure record is `docs/data-audit.md` section 11 (single exposure record; 11.3 is the current summary). Per that record its label, question and entity files were hashed and line-counted but not opened. Nothing under `challenge_test/` is opened, listed or counted before the M7 sealed run, and no per-family count is derived for it. Consequence: the size of the sealed cohort and its number of vendor clusters are unknown today.

Every number produced so far (`docs/dev-results.md`, `docs/baseline-observable.md`, `docs/results-card-m3.md`) is development data. The investigator's rules and the evidence contract were written with train and validation labels in view, and the baseline was chosen among 15 candidates on validation. None of it is evidence for the claim.

## 3. Release cohort, views and slices

**Release cohort.** Every sealed case whose label is one of the three families (F06 invoice paid twice, F12 ERP payment missing from bank, F14 bank/ERP amount mismatch) or one of nine legitimate variants (`legitimate_pending_settlement`, `_weekend_clearing`, `_check_clearing`, `_due_date_payment`, `_split_payment`, `_batch_payment`, `_payment_reversal`, `_net_fee_accounting`, `_accounted_bank_fee`). `legitimate_fx_settlement` is a separate slice (below). The cohort is defined from labels, on the evaluator side only (`evaluation/cohort.py` `FAMILY_KEYS`, `VARIANT_CATEGORY`); investigation code never sees it.

**Headline view (`all_cases`).** The cohort with no exclusion. In code the groups are `cohort` plus `label_conflict`. The headline primary figure is correct-and-evidence-complete over this view.

**Sensitivity view (`sensitivity_without_verified_conflicts`).** Leaves out label conflicts that were verified by hand after scoring, each recorded with its evidence in `evaluation/verified_label_conflicts.json`. It is reported beside the headline and never replaces it. A generic overlap rule only proposes candidates; it excludes nothing.

**What is not excluded on the sealed set.** `CONTRADICTED` and `NOT_GRADABLE` in `evaluation/cohort.py` are lists of development case ids (four and one). They cannot apply to sealed cases, so the sealed headline counts every in-scope sealed case. Verified label conflicts on the sealed set can only be identified after scoring, with recorded evidence, and appear only in the sensitivity view.

**FX slice.** `legitimate_fx_settlement` cases are their own slice. The correct outcome is abstention (the investigator does not support cross-currency settlement). They never count as successes in the primary metric and are reported as correct abstentions over the slice.

**Out-of-scope report.** Failures of the other twelve families are out of the investigator's scope. They are not in the primary denominator. The report gives the out-of-scope all-clear risk: failures answered `legitimate_*` or `no_exception`, over all out-of-scope failures (and per family). Out-of-scope legitimate cases count in the false-alarm denominator (section 5).

**Cluster.** The upstream `group_vendor_id`, read from the labels on the evaluator side. A row without it stops the comparison; there is no fallback (`evaluation/compare.py`). Question variants of one case are not independent cases (`docs/data-audit.md` section 5).

## 4. Primary metric

`correct_and_evidence_complete`: cases answered with the expected category whose packet meets every evidence-contract requirement (`docs/evidence-contract.md`) and passes the packet audit, divided by all cases in the view. A requirement is met only by the right evidence: each required calculation must have the required operation, operands and conclusion (an arithmetically correct but irrelevant calculation does not count), each required search must have exactly the predicate the evaluator builds and must reproduce its count on the snapshot, and journals must balance. The audit requires every cited record to resolve in the run's snapshot with the recorded values and every calculation and day count to recompute exactly. Every labelled case of the split must be scored; a missing case stops the evaluation instead of shrinking the denominator. Abstentions, out-of-scope outcomes and failures are not successes. A missing or empty evidence set never counts as support.

## 5. Supporting metrics

Definitions and empty-denominator conventions are in the `evaluation/metrics.py` module and function docstrings; a rate is `None` when the denominator is zero, never 0 or 1. In summary:

- **Answer precision, coverage, abstention:** correct answered over answered; answered, abstained, out-of-scope and failed over all view cases. Root-cause accuracy per family and per difficulty (`root_cause_accuracy`), with non-answers counted as misses in `correct_over_cases`.
- **Evidence recall:** evidence-contract requirement items met over required items, micro-averaged over answered cohort cases.
- **Citation resolution** (replaces record-set evidence precision, D122): cited evidence items whose table and record id exist in the snapshot with the recorded values, over cited items. A cited record the contract does not require is not an error; an unresolvable or altered one is. Search-only items cite no record and are outside the count.
- **Reference and calculation integrity** (called claim support before the M3 review): findings of answered packets whose evidence ids are in the packet and resolve and whose calculations and day counts exist and validate, over emitted findings. It checks references and arithmetic, not whether the finding's assertion is true. Predicate-level claim checking is not implemented; M4 must build it before any model-written claim is scored, because the model-assistance rule requires zero unsupported claims.
- **Monetary correctness:** calculations that recompute exactly from snapshot-bound operands over calculations reported, plus calculation coverage (answered cases with at least one calculation over answered cases). Money is `Decimal` with explicit currency.
- **Execution reliability:** failed, abstained for the retrieval time limit, and abstained for the row limit, each over all attempted cases.
- **Efficiency:** per attempted case latency p50 and p95 by nearest rank (value at 1-based rank `ceil(p/100*n)` of the sorted values), throughput, peak memory, cost per attempted case and cost per correct evidence-complete packet.
- **False-alarm rate:** legitimate cases diagnosed as failures over ALL legitimate cases of the split, in every group (cohort variants, FX, label-conflict and out-of-scope legitimate variants), whatever the view. Every false alarm is listed.

## 6. Systems compared

**Investigator:** `deterministic_v2` at the frozen code commit. Read-only rules and retrieval, no model.

**Baseline:** a HistGradientBoosting classifier with learning rate 0.05, depth 3, L2 1.0, fit on train, on the same observable inputs as the investigator (allowlisted fields, keyed opaque tokens). It has two configurations, both compared:

- `answer_all`: it always answers.
- `threshold_0.85`: it abstains below a probability of 0.85. The threshold rule was declared before use: the smallest value on the 0.50 to 0.95 grid whose train grouped-cross-validated cohort precision is at least 0.99, else the largest (`docs/baseline-observable.md` section 2).

**Baseline evidence is a relaxed record-availability upper bound** (`evaluation/baselines/observable/evidence.py`): it checks that the needed records exist, not that a packet cites them, and omits the investigator's all-clear guard. This favours the baseline; the baseline's primary figure is an upper bound under the investigator's own check. The baseline has no packets or report; usefulness comparisons of packets are outside this protocol.

## 7. Decision rule

The rule is implemented by `evaluation/decide.py` on the output of `evaluation/compare.py` (investigator minus baseline, paired on the same cases, headline view, sealed split). The claim holds only if both checks pass against EVERY baseline configuration (`answer_all` and `threshold_0.85`):

1. **Primary:** the paired point difference in correct-and-evidence-complete is at least 0.05 (5 percentage points), and the lower bound of the nominal 98% vendor-cluster bootstrap interval is greater than 0.
2. **False alarms ("no observed increase"):** the upper bound of the same interval for the false-alarm difference (investigator minus baseline, over all legitimate cases) is at most 0. A bound of exactly 0.0 passes; a bound above 0 fails. This is an aggregate, observed-sample engineering criterion, not a statistical non-inferiority claim and not a casewise one: false alarms only the investigator raises can be offset by false alarms only the baseline raises in the same vendors, and the bound is a percentile, not a maximum over resamples. That is why every investigator false alarm is listed and explained in the report regardless of the verdict. When neither system raises a false alarm the interval is [0, 0] and says nothing about the underlying rates; the report must then say "neither system raised a false alarm on N legitimate cases" and nothing stronger. A false alarm is a legitimate-labelled case answered with a failure category, in any group.

Each check is `pass`, `fail` or `not_established`. A point difference below 0.05, an interval lower bound not above 0, or a false-alarm upper bound above 0 is `fail`. Fewer than 30 clusters gives no interval, and the check is then `not_established`, never `pass`, with one precedence rule: a primary point difference below 0.05 is `fail` even when there is no interval, because a point estimate under the practical bar fails the claim whatever an interval would show. The verdict is `fails` if any check fails, else `not_established` if any check is not established, else `holds`. In a release verdict (sealed split) every hard gate recorded by the scorer must also hold, or the verdict is `fails`: the in-container absence check passed, the rerun on the same image gave identical packets, and every answered packet is well formed, cites only resolving records and has exact calculations and day counts (`PLAN.md` section 7, hard gates). `decide.py` also refuses comparisons whose method, seed, repetitions, alpha, minimum clusters or view differ from the protocol, requires exactly the protocol's configurations, requires the same investigator run across configurations (by file digest) and different baseline files, and records the split and digests. A verdict on any split other than the sealed one is labelled a rehearsal, not a release verdict.

**Who decided what.** The user decided on 2026-11-02: the +5 point minimum paired difference, the rule that false alarms must not increase, and the $25 model budget. The parent made two choices that are stricter than or equal to the user's rule, not looser: it applies both checks to both baseline configurations (the user's rule did not say how to treat two configurations), and it set alpha at 0.02 so that the interval reaches about 95% actual coverage (section 8). The `PLAN.md` +10 point figure was only a candidate; the user's +5 replaces it.

**Fixed in advance:** the cohort, the metric, the rule and its numbers, the seed and repetitions, and both baseline configurations. Not fixed and not part of the claim: anything about real exports or users.

## 8. Statistics

- **Method:** `cluster_percentile_bootstrap` (`evaluation/paired.py`). Vendor clusters are resampled with replacement; each replicate recomputes the ratio-of-totals difference. Percentile bounds are `r[floor(alpha/2*(reps-1))]` and `r[ceil((1-alpha/2)*(reps-1))]` of the sorted replicates, with no interpolation.
- **Seed 20261102, 10000 replicates, alpha 0.02 (nominal 98%), at least 30 clusters.**
- **Why alpha 0.02 and 30 clusters.** The percentile cluster bootstrap undercovers with few clusters, so coverage was measured by simulation (`evaluation/bootstrap_coverage.py`; results `artifacts/bootstrap_coverage_exact.json` for alpha 0.05 and `artifacts/bootstrap_coverage_exact_a02.json` for alpha 0.02; untracked). True paired difference 0.07; success rates 0.95 and 0.88; cluster sizes drawn from the exact validation (55 clusters, largest 35) and train cluster-size lists; 2000 bootstrap replicates per simulated dataset.

| Clusters | Nominal 95%, validation sizes | Nominal 95%, train sizes | Nominal 98%, validation sizes | Nominal 98%, train sizes |
|---:|---:|---:|---:|---:|
| 10 | | | 0.804 | 0.839 |
| 15 | | | 0.917 | 0.938 |
| 20 | 0.910 | 0.926 | 0.954 | 0.958 |
| 30 | 0.908 | 0.914 | 0.955 | 0.956 |
| 35 | 0.924 | 0.918 | | |
| 40 | 0.930 | 0.926 | | |
| 45 | 0.922 | 0.934 | | |
| 55 | 0.934 | 0.956 | 0.961 | 0.978 |
| 80 | 0.944 | 0.916 | 0.970 | 0.967 |

The 95% columns used 500 simulated datasets per cell; the 98% columns used 1000. Reading: a nominal 95% interval covered about 0.91 to 0.96; a nominal 98% interval covered 0.954 to 0.978 from 20 clusters, which is the intended 95%. Below 20 clusters coverage falls (0.917 and 0.938 at 15; 0.804 and 0.839 at 10). The sensitivity check below moved the minimum to 30.
- **Assumptions of that measurement (not verified against real data):** the outcome model's standard deviations (cluster 1.0, case 1.0, system-by-cluster 0.5, logit scale) and the two success rates were chosen, not fitted to data; with the real correlation structure coverage could be better or worse. Sensitivity check (alpha 0.02, 1000 datasets per cell; `artifacts/bootstrap_coverage_sens_*.json`), shared cluster SD / system-by-cluster SD:

| SDs | 20 clusters (validation, train sizes) | 30 clusters | 55 clusters |
|---|---|---|---|
| 0.5 / 0.5 | 0.952, 0.960 | 0.955, 0.965 | 0.964, 0.971 |
| 1.0 / 0.5 (main) | 0.954, 0.958 | 0.955, 0.956 | 0.961, 0.978 |
| 1.0 / 1.0 | 0.946, 0.956 | 0.956, 0.956 | 0.972, 0.975 |
| 2.0 / 0.5 | 0.934, 0.946 | 0.944, 0.954 | 0.964, 0.969 |

With a strong vendor effect, 20 clusters fell about two standard errors short of 0.95 (0.934, SE 0.008); from 30 clusters every scenario is within about one standard error of 0.95 or above it. So the protocol requires 30 clusters. This remains a measurement under a chosen model, not a guarantee for the real correlation structure.
- Zero observed errors does not establish zero risk; intervals describe the sealed sample only, and dependency inside a vendor is the only dependency modelled.

## 9. Budgets

- **Deterministic investigator:** at most 200 rows per query and 10 seconds per case. Both are enforced and produce an abstention with a reason when hit. The 3-hop limit is declared but not enforced (carried to M6).
- **Model assistance (M4 onward):** a total of $25 across M4 and M7 together; a run stops at the cap. At most 1 model call and 1 bounded transient-error retry per case. Per-case token and wall-time caps are set in the M4 configuration before any paid call, so that the projected sealed-run cost fits the remaining budget. Retries, failures and uncached calls count in cost; cache hits are reported separately. The investigator without a model costs 0 USD.

## 10. Model-assistance rule

The headline claim (section 7) is about the frozen deterministic investigator only. A model-assisted system is never the headline. A model path is kept only if, on validation against the deterministic investigator, the paired primary point difference is greater than 0, false alarms do not increase, no emitted claim is unsupported (which needs the predicate-level claim checker M4 must build), and the budget holds. A drafting-only path that cannot change the primary metric is kept only if a recorded practitioner review finds a usefulness benefit. Otherwise it is removed. Before the sealed run, its variability is measured by running a fixed subset three times: the 30 validation headline-cohort cases with the smallest sha256 of the case id. If a model path survives, it is a separate, secondary claim against the deterministic investigator on the sealed split, under the same rule and statistics, run once within the budget; its result never changes the headline verdict. The deterministic rerun requirement (section 12) applies to the deterministic investigator only.

## 11. Ablations

Reported, not part of the decision rule: baseline `threshold_0.85` against `answer_all`; model assistance on against off (M4); and an oracle-evidence diagnostic (M4), which separates retrieval from reasoning and is never a headline.

## 12. Integrity and sealed-run order

The investigator run manifest records the code commit, image id, absence-check result, and the SHA-256 of the snapshot file, each split's requests, links, packets, timings and run stats, and the same-image rerun result. The scorer's `metrics.json` records the protocol SHA-256, label and conflict-file digests, the `cases.jsonl` digest and the hard gates. The baseline's `run-manifest.json` records its model, each configuration's cases file and digest, and the digest of its predictions file, written before that split's links and labels are read. `evaluation/dev_run.py run` already follows this order for development splits:

1. Tag and digest the protocol.
2. Prepare the inputs (a new key; no labels) and run the in-container absence check.
3. Run inference once; record every input and output digest.
4. Verify the recorded bytes, rerun inference on the run's own image (`require_same_image_id`) and require identical packet digests (`require_deterministic_rerun`). A rerun on a different image is a failure, not a note.
5. Only then open the labels and score, from a private copy of the verified snapshot; `evaluate` refuses a manifest without a passing same-image rerun.
6. Compare each baseline configuration with `compare.py` (bound to the scorer's `cases.jsonl` digest and the baseline manifest) and apply `decide.py`, which fails a release verdict if any hard gate fails.

**What M7 still has to implement.** The tooling does not do this today and refuses the sealed split on purpose: `src/reconcile/adapters/finrca_requests.py` refuses `challenge_test` before touching any file, and `evaluation/dev_run.py` lists only `train` and `validation` and never opens the other directories. M7 must add an explicit unlock bound to the tagged protocol (for example, a check that the protocol SHA-256 and the packet digests are recorded before the label files are readable). That unlock does not exist yet and must be built, reviewed and tested before the sealed run.

## 13. Changing the protocol after the freeze

After the tag `protocol-v1`, no number in `protocol.toml` changes. A different cohort, threshold, margin, alpha, metric or configuration needs a new protocol version and an uncontaminated holdout where feasible; the sealed split cannot be reused for a new cycle once it has been scored (`PLAN.md` section 7, go/no-go).
