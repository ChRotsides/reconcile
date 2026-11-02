# Observable-input classical-ML baseline (M2)

Status: PLAN.md M2 task "the strongest applicable classical-ML baseline against the same observable inputs". **Current run `obs-5`** (2026-11-01; final code after the third review), compared with investigator run `m2-dev-7` (policy `evidence-contract-0.5`; `--investigator-run m2-dev-7`). These are the M2 results of record. `obs-4` (compared with `m2-dev-6`), `obs-3` (with `m2-dev-5`, policy 0.4) and `obs-1`, `obs-2` (old calendar and cohort) are history. Code: `evaluation/baselines/observable/` (`features.py`, `evidence.py`, `run.py`) and the shared `evaluation/cohort.py`. All figures in sections 2 to 4 come from `artifacts/baselines/obs-5/metrics.json` and `artifacts/dev-runs/m2-dev-7/cases.jsonl` (via `run.py` `investigator_rows`), unless a sentence names another run.

**What changed since `obs-2`.** `obs-3` counts business days in `(start, end]` with the investigator's function (`obs-2` used `[start, end)`), scores both systems on identical cohorts in two views (`obs-2` compared a 123-case baseline with a 122-case investigator), and describes the evidence check as a relaxed record-availability bound. The cohort-level result did not move (109/123 correct, the same 14 wrong cohort cases); the all-clear count on out-of-scope failures went from 5/225 to 6/225 (section 3). `obs-4` is `obs-3` re-run on the same code against the policy-0.5 investigator run: the baseline's cohort results are the same (109/123 correct, the same 14 wrong cohort cases, the same 6 out-of-scope all-clear cases), because the baseline does not depend on the investigator run except for the comparison column. `obs-5` is `obs-4` re-run with a new key against `m2-dev-7`: checked in the two `metrics.json` files, the unabstained results (`primary_no_abstention`, `per_label_no_abstention`), the same 14 wrong cohort cases and the same six out-of-scope all-clear cases are identical; the key-dependent parts differ (model grid ranges, permutation importances, and the abstention threshold, 0.85 in `obs-5` against 0.90 in `obs-4`, which changes the thresholded columns of section 3).

## 1. What this is and is not

- Development-set numbers on a synthetic benchmark (FinRCA, revision `76c93ec`), train and validation only. The investigator was written against the same labels (`docs/dev-results.md` section 1), and this baseline's model was selected on the validation split, so neither is unseen. `test`, `challenge_test` and the top-level combined label files were not opened.
- Same inputs as the investigator: the prepared snapshot (allowlist, opaque tokens, cutoff 2026-06-30) and neutral requests (primary entity table and token only). The baseline prepared its own key (`data/private/obs-5/key`) and snapshot with the same adapters. Feature code opens no label file, no key and no links file; labels are opened by `run.py` after all features exist.
- One model, one grid, one seed (`20261101`). No claim beyond the tables below.

## 2. Method

**Request to features** (`features.py`). Per request, from the snapshot only: the primary entity's own row, and rows reachable within the investigator's hops: payment to its allocations and invoices, payment to bank rows by shared `reference_number`, payment, bank row and invoice to GL lines by `source_transaction_id`, invoice to approval events, and same-vendor look-alike payments. 77 numeric features, deterministic, NaN where a block does not apply (HistGradientBoosting handles NaN; the other models use a median imputer with indicators):

| Block | Examples |
|---|---|
| entity type (4) | `e_pay`, `e_inv`, `e_bank`, `e_gl` |
| payment, from the primary payment, the payment of a bank row (unique reference) or the source of a GL journal (`p_*`) | amount magnitude, round flag, age in business days and days, weekday, method and status one-hots, clearing limit and age minus limit, allocation count and allocated/amount ratio, reference count, non-fee bank rows, posted debits, credits, returned rows, fee rows, debit/payment ratio, currency match, business-day lag and weekend span, unreferenced candidate rows (A3-style), balanced payment journals and journal/payment ratio, reversal, bank-fee and FX journal flags, equal twins, paid on due date, other effective payments on its invoices, allocated/total ratio of its invoices, fewest approval events among its invoices |
| invoice (`i_*`) | total, age at cutoff, status one-hots, approval events, has PO, allocation count, allocated/total ratio, payments, effective payments, committed payments (effective, same currency, posted debit), spread of payment dates, paid on due date |
| bank row (`b_*`) | fee flag, amount, credit flag, posted flag, payments sharing its reference, balanced fee journal |
| GL journal (`g_*`) | type one-hots, line count, imbalance, source kind |

Business days are Monday to Friday in `(start, end]`, from `reconcile.domain.calendar.business_days` (the investigator's function; `features.py` no longer has its own, only a wrapper that returns 0 where the shared function raises because a bank row is dated before its payment). `obs-2` used `[start, end)`: a Saturday-to-Monday interval counted 0 days, now 1, and a wire payment dated Saturday 27 June at the Tuesday 30 June cutoff is 2 business days old, not 1, so the pending/F12 boundary moves for some payments. Clearing limits are the contract's P1 (ACH 3, wire 1, check 10, virtual card 2). Money is parsed and compared as `Decimal`; ratios and log10 magnitudes become floats only as model inputs.

**Target.** The fine label: the failure family (15 families) or, for legitimate cases, the scenario variant (16 variants, including the six AP-side ones). The classifier therefore sees the same label space the full benchmark has, answers every request, and an answer counts as "no failure" when it is a `legitimate_*` label. Trained on the 1,192 train requests only; validation 416 requests; the final model is not refit on train plus validation.

**Models and selection.** 15 candidates: HistGradientBoosting (learning rate 0.05 or 0.1, depth 3 or none, L2 0 or 1; 150 iterations, no early stopping), multinomial logistic regression (C 0.1, 1, 10; imputer plus scaling), random forest (300 trees, depth none or 12, min leaf 1 or 3). Selection criterion: exact-category correct count on the validation gradable cohort (123 cases); the first best wins. Selected: **HistGradientBoosting, learning rate 0.05, max depth 3, L2 1.0** (109/123).

| Model family | Validation cohort correct (range over its grid) |
|---|---:|
| HistGradientBoosting (8) | 103 to 109 |
| Random forest (4) | 98 to 102 |
| Logistic regression (3) | 91 to 94 |

HistGradientBoosting is also the upstream published winner (`docs/baseline-reproduction.md`); here it is also best on the observable inputs. Selection bias: the best of 15 on 123 cases is optimistic, and the neighbours of the winner (106 to 109) show the spread (the HistGradientBoosting grid ranges 103 to 109, `obs-5`), so the 109 is an upper-end estimate for this baseline.

**Scoring.** Cohort, groups, label conflicts and category mapping are one module, `evaluation/cohort.py` (`group_of`, `label_candidates`, `conflict_report`, `FAMILY_KEYS`, `VARIANT_CATEGORY`, `CONTRADICTED`, `NOT_GRADABLE`, `is_no_failure`, `is_failure`), imported by both `dev_run.py` and the baseline (`tests/unit/test_cohort.py` fails if either script defines them again). Both systems are reported in the same two views. The **headline view scores every gradable in-scope case** (123 validation cases, no exclusion). A **sensitivity view** leaves out only the label conflicts verified by hand and recorded with their evidence in the tracked file `evaluation/verified_label_conflicts.json` (5 pairs in train and validation, 1 in validation: RCA_002212, 122 cases). The generic rule over the labels never excludes anything: it proposes *conflict candidates* (a failure case's primary entity or evidence-named record lies in the legitimate case's own records; every candidate still needs a hand check) and *review candidates* (any other record that both cases name or reach through allocations, on either or both sides); both are listed in `metrics.json` and `conflict_report` refuses a verified pair that is not a candidate. `run.py` recomputes the investigator's groups from the shared function and stops if the baseline and investigator cohorts differ in either view, or if the investigator's `cases.jsonl` was written with a different verified set (re-run `dev_run.py evaluate`). Model selection and the abstention threshold use all in-scope cases (123 on validation), the headline definition. The metric definitions are replicated from `dev_run.py` (`classify`, `cohort_metrics`, `all_clear_risk`; these are not shared code). A classifier always answers, so coverage is 100% in the primary view. A "false alarm" follows `dev_run`: a legitimate case answered with any category that is not `legitimate_*` (so it includes a predicted F08 or F01); the narrower "predicted F06, F12 or F14" count is given beside it.

**Optional abstention.** Declared rule: take the cross-validated (5-fold, grouped by vendor token) class probabilities of the selected configuration on TRAIN; the threshold is the smallest value on the grid 0.50 to 0.95 (step 0.05) whose train cohort answer precision is at least 0.99; the baseline abstains below it. Chosen: 0.85 in `obs-5` (train cross-validated precision 0.9965 at 0.85, 0.9866 at 0.80; 0.90 in `obs-4`, where the 0.85 value was 0.9894, and in `obs-3` and `obs-1`; 0.85 in `obs-2`).

**Evidence check: a relaxed record-availability upper bound** (`evidence.py`). For the predicted category and the primary entity, test whether the snapshot holds the record kinds and absence counts the category's main contract branch needs. It is not a check of the contract and not a packet check: nothing is assembled, cited or rendered. What it does not check:
- the contract 0.3 all-clear prerequisites: completed final approval of each allocated invoice on or before the payment date, the payment journal in the payment's currency, payment minus journals within one quantum;
- full allocation of an invoice, or its total against its allocations (split, due date), a split leg's debit equal to its payment, "paid on the due date";
- weekend and check clearing: the payment method, the lag against the limit, the debit amount equal to the payment, a debit not before the payment;
- F14: the size of the shortfall, an unbalanced or mixed-currency fee journal (counted as "no balanced journal"), a debit in another currency;
- F06: that the committed debits, not only the allocations, exceed the invoice total;
- batch and reversal: allocation sums, over-allocation, the credit not before the debit, the reversed payment's invoices not being paid;
- `no_exception` and every category the contract does not define here (other failure families, the FX variant, AP-side variants, GL entities): always incomplete;
- citation ids, calculations, day-count items, claim text, and whether stated search counts reproduce (they are computed from the snapshot).

**Decision (finding 10): relabel, do not strengthen.** Strengthening to the contract and the guard would be a second implementation of the investigator's rules, which is the duplication that produced findings 8 and 11, and the baseline would still emit no packet to check. The honest description is a bound: the baseline's "evidence-complete" figures say "the records are available", an upper bound on what a packet-producing baseline could show under the investigator's stricter check. `metrics.json` carries `evidence_complete_meaning` saying this. The metric keys keep their names because the investigator rows go through the same function; read them accordingly. A wrong category usually fails its own check (a bank row exists, so "F12" cannot have a count-0 reference search); `obs-5`: 111 of 123 answers, and 109 of 109 correct ones, passed (110 of 122 without the label conflict).

## 3. Validation results against the investigator

Gradable validation cohort in both views: **all cases** (123, the headline) and the **sensitivity view without the verified label conflict** (122; the conflict is RCA_002212). The two systems are scored on identical cohorts in both (`run.py` stops otherwise; `identical_cohorts_checked` is true in `obs-5/metrics.json`). Investigator: `m2-dev-7`, nothing failed. Baseline: `obs-5`, selected HistGradientBoosting (lr 0.05, depth 3, L2 1.0), abstention threshold 0.85 (chosen on TRAIN). The investigator's completeness figures are those of the round-2 evaluator, which also requires the policy-0.5 guard items (`dev-results.md` sections 12 and 13).

All cases (123, headline):

| Metric | Investigator `m2-dev-7` | Baseline, no abstention | Baseline, threshold 0.85 |
|---|---:|---:|---:|
| Coverage (answered / cohort) | 122/123 = 0.992 | 123/123 = 1.000 | 97/123 = 0.789 |
| Exact-category precision (correct / answered) | 119/122 = 0.975 | 109/123 = 0.886 | 94/97 = 0.969 |
| Failure vs no-failure accuracy, answered | 122/122 = 1.000 | 115/123 = 0.935 | 94/97 = 0.969 |
| **Correct and evidence-complete / cohort (primary)** | **119/123 = 0.967** | 109/123 = 0.886 (record availability only, section 2) | 94/123 = 0.764 |
| False alarms, cohort legitimate (dev_run definition) | 0/71 | 8/71 = 0.113 | 3/71 = 0.042 |
| of which predicted F06, F12 or F14 | 0/71 | 0/71 | 0/71 |
| False alarms, AP-side legitimate (out of scope) | 0/59 | 17/59 = 0.288 | 0/59 |
| False alarms, all legitimate | 0/139 | 25/139 = 0.180 | 3/139 = 0.022 |
| All-clear on out-of-scope failures | 0/225 | 6/225 = 0.027 | 0/225 |
| FX slice (9 cases; investigator abstains `unsupported_fx` = correct) | 9/9 abstained | 9/9 called `legitimate_fx_settlement` | not applicable |

Sensitivity view, without the verified label conflict (122 cases):

| Metric | Investigator `m2-dev-7` | Baseline, no abstention | Baseline, threshold 0.85 |
|---|---:|---:|---:|
| Coverage | 122/122 = 1.000 | 122/122 = 1.000 | 97/122 = 0.795 |
| Exact-category precision | 119/122 = 0.975 | 109/122 = 0.893 | 94/97 = 0.969 |
| Failure vs no-failure accuracy, answered | 122/122 = 1.000 | 114/122 = 0.934 | 94/97 = 0.969 |
| **Correct and evidence-complete / cohort (primary)** | **119/122 = 0.975** | 109/122 = 0.893 | 94/122 = 0.770 |
| False alarms, cohort legitimate | 0/70 | 8/70 = 0.114 | 3/70 = 0.043 |
| False alarms, all legitimate | 0/139 | 25/139 = 0.180 | 3/139 = 0.022 |
| All-clear on out-of-scope failures | 0/225 | 6/225 = 0.027 | 0/225 |

Reading (headline view). On the same observable inputs the strong classifier is clearly worse on the primary outcome (0.886 against 0.967 without abstention) and never reaches the investigator by abstaining (0.764 at 0.85). With abstention at 0.85 its exact-category precision on the answered cases (94/97 = 0.969) is about the investigator's (119/122 = 0.975) and slightly below it; in `obs-4`, with the threshold at 0.90, it was higher (92/93 = 0.989), so on this comparison the precision of the thresholded classifier depends on the key-chosen threshold and is not a stable advantage in either direction. What it gives up is coverage (97/123 = 0.789 against 0.992) and with it the primary outcome (0.764 against 0.967); it also makes 3 false alarms on legitimate cases (3/139) where the investigator makes none. Without abstention it is less precise (109/123 = 0.886) and has the false-alarm and all-clear weaknesses in the table (25/139 and 6/225), which the investigator, a rule set with an explicit contract, holds at 0 on this development set. The classifier's evidence completeness is not an independent measurement (section 2); its correct answers are as "complete" as the snapshot allows.

**All-clear on out-of-scope failures: 5 to 6.** `obs-2` gave 5/225 (F02 1, F03 2, F04 1, F08 1); `obs-3`, `obs-4` and `obs-5` give 6/225 (F02 1, F03 2, F04 2, F08 1), the same six cases in both. The difference from `obs-2` is one case, RCA_000367 (F04), which `obs-3` and `obs-4` label `legitimate_po_tolerance` and `obs-2` did not. The cohort predictions are identical between the two runs (the same 14 wrong cohort cases, 109/123). The change is attributed to the calendar change (the baseline now counts `(start, end]`, which changes the age and lag features for some payments, including payments of that case's entity); that attribution is not isolated: `obs-3` also used a new key, and the earlier key comparison (below) shows that a key change alone moves the grids and the threshold by a few cases. Out-of-scope all-clear counts of 5 and 6 are within that noise; the investigator is 0/225 in both.

The classifier is not weak where the evidence is simple: F06 18/18, F12 16/16, F14 17/18, pending 6/6, reversal 9/9, net fee 11/11, accounted fee 4/4, check 4/4, split 4/4. Its losses:

| Label | Cases | Correct | Wrong answers |
|---|---:|---:|---|
| legitimate batch payment | 15 | 8 | 7 called F08 (approval workflow failure) |
| legitimate due-date payment | 9 | 5 | exact PO match 2, split cost centre 1, split payment 1 |
| legitimate weekend clearing | 9 | 7 | F08 1, batch 1 |
| F14 | 18 | 17 | F08 1 |

Seven of the eight cohort false alarms are legitimate batch payments called an F08 approval failure (the eighth is a weekend clearing payment). Hypothesis, not tested: batch payments touch several invoices, so the minimum-approval-count feature is more often low or odd for them, which the classifier associates with F08. The investigator checks approvals with a fixed rule (completed final approval before the payment) and made none of these errors on validation.

Out-of-scope failures answered "no failure" (6, `obs-5`): F02 1, F03 2, F04 2, F08 1, of 225. Not a measure of its safety: F08 and F09 are in the label space and the classifier can name them.

Stability. `obs-1` to `obs-5` used different keys. Tokens, and therefore the request order, differ, and the cohort figures are the same (109/123, false alarms 8/71 and 0 narrow) except the all-clear count (5/225 in `obs-1` and `obs-2`, 6/225 in `obs-3` to `obs-5`, above); the random-forest grid moved by one to three cases (`obs-2` 97 to 103, `obs-3` 99 to 102, `obs-4` 99 to 104, `obs-5` 98 to 102) and the CV-chosen abstention threshold was 0.90 in `obs-1` (94 of 123 answered, 93 correct), 0.85 in `obs-2` (98 answered, 94 correct) 0.90 in `obs-3` (93 answered, 92 correct), 0.90 in `obs-4` (93 answered, 92 correct) and 0.85 in `obs-5` (97 answered, 94 correct). The abstention view is therefore the less stable one.

## 4. What the model relies on

Permutation importance on validation (selected model, 10 repeats, drop in exact-label accuracy over all 416 requests; `obs-5`):

| Feature | Drop | Meaning |
|---|---:|---|
| `i_alloc_ratio` | 0.099 | allocated amount over invoice total (F06 evidence) |
| `i_status_received` | 0.092 | invoice status (residual R1) |
| `p_pay_journal_ratio` | 0.084 | payment journal amount over payment (F09 evidence) |
| `p_debit_ratio` | 0.072 | bank debit over payment (F14, net fee) |
| `b_amount_log10` | 0.071 | bank row amount (bank-row cases) |
| `p_alloc_ratio` | 0.068 | allocated over payment |
| `e_gl` | 0.057 | entity is a GL journal (residual R3) |
| `p_n_bank` | 0.050 | bank rows on the reference (F12, pending) |
| `p_n_returned`, `i_n_committed`, `p_gl_has_bankfee`, `i_n_approval`, `p_gl_has_reversal`, `i_status_canceled` | 0.042 to 0.032 | returned rows, committed payments, fee journal, approval events (R1), reversal journal, status |

By block, permuting all the columns of the block together (relative drop of cohort correct): payment block 0.74, invoice block 0.39, bank-row block 0.05, GL block 0.0. The reliance is mostly on the evidence a practitioner would use.

**Residuals.** Retraining the selected configuration without a residual group (validation cohort correct out of the 123 in-scope cases, the pre-conflict selection definition; baseline 109):

| Group removed | Columns | Cohort correct | Joint permutation drop |
|---|---|---:|---:|
| R1 invoice status and approvals | `i_status_*`, `i_n_approval`, `p_inv_min_approval` | 103 | 0.063 |
| R2 FX codes | `g_fx`, `g_type_fx_settlement`, `p_gl_has_fx` | 109 | 0.000 |
| R3 entity type | `e_*` | 109 | 0.032 |
| R4 invoice age | `i_age_days` | 106 | 0.001 |
| allocation count | `p_n_alloc`, `i_n_alloc` | 105 | 0.088 |
| all five | | 94 | |

Without all five groups: 94/123 = 0.764 correct (headline view; 94/122 = 0.770 in the sensitivity view), cohort false alarms 18/71 (18/70; still 0 for F06, F12, F14), all-clear 6/225, all legitimate 47/139.

Findings:
- R2 is not used: removing it changes nothing. The full model labels the FX slice `legitimate_fx_settlement` 9/9; whether that comes from the FX journal or from the currency mismatch (`p_debit_cur_match`) was not isolated.
- R1, R3 (no cost in `obs-5`) and the allocation count carry weight (up to 6 cases each, 15 together with R4). They are not clean leaks: invoice approvals and the allocation count are evidence the investigator also reads, and the entity type is a request field (R3). The group definitions mix the declared residual and genuine evidence, so the 15 cases are an upper bound for "won through a residual", not a measurement of it. R1's `i_status_received` is the second most important single feature; it separates inserted F01 and credit-memo invoices (outside the cohort), so it explains out-of-scope behaviour more than cohort correctness.
- R4: removing `i_age_days` costs 3 cases and its permutation importance is about 0 (0.001), so the retrain difference is within what the 15-candidate selection spread shows; not a firm reliance. A date-only model for invoice-primary cohort cases (31 validation cases, F06 against legitimate) gets balanced accuracy 0.712 (leakage test: 0.756), and the full model gets 1.000 on the same cases through allocation amounts. So R4 exists as a weak signal but the baseline does not need it.
- Entity type only (HistGradientBoosting depth 3 on `e_*`, full label space) gets 0 of 123 on the cohort: it names the most common out-of-scope family for each type. Beside every headline number this is the R3 control; here it is far below 109.
- The baseline does not win through a residual: with the five groups removed it still scores 94 of 123 in-scope cases, and the investigator, which does not use R1 to R4 as diagnostic evidence (its approvals check is a prerequisite, `dev-results.md` section 10), scores 119 of 123 (headline view).

## 5. Limitations

- Development data on a synthetic benchmark; selection on validation (best of 15). Both systems were built with these labels in view. Results do not transfer to real exports.
- One snapshot and key per run; three keys gave the same cohort figures. Small cohort (122 or 123), small strata (batch 15, due date 9), so a one-case change moves a rate by 0.8 points.
- The label space for the baseline includes the out-of-scope families, which the investigator cannot name. That helps the baseline on some safety metrics (it can say F08) and it is not a like-for-like removal of that advantage. A baseline trained only on the 13 cohort labels would have no out-of-scope label to give and would be forced to answer everything as a cohort category; that variant was not run.
- The evidence check is a relaxed record-availability upper bound and omits the all-clear guard (section 2). The baseline's primary outcome is therefore an upper bound on what a packet-producing baseline would achieve under the investigator's check, not a like-for-like number.
- Features were hand-chosen from the allowlist. A better feature set, early stopping, longer training or other models might do better; the grid is small. The abstention threshold uses a declared target precision (0.99) that is itself a policy choice.
- Permutation importance is conditional on correlated features; groups were tested by retraining as well as by permutation.
- Nothing was run on `test` or `challenge_test`; the sealed comparison (M7) must prepare its own inputs and apply the validated choices (model, grid winner, threshold rule) without refitting choices on that data.

## 6. Reproduce

```bash
uv run --group eval python evaluation/baselines/observable/run.py TAG --investigator-run m2-dev-7   # TAG must be new; about 3 min
```

Needs the `eval` dependency group (scikit-learn 1.9.1, numpy 2.5.4, scipy 1.18.1 in `uv.lock`; not part of the runtime or dev groups) and the upstream checkout (`--upstream`, default `/home/charalambos/Documents/fin-tech-projects/reconcile/data/upstream/FinRCA-AI-Bench`). Compares with the investigator run named by `--investigator-run`, which is required (there is no default, so a stale run cannot be picked up silently; its `cases.jsonl` must exist and must have been written by `dev_run.py evaluate` of the current code). Outputs under `artifacts/baselines/TAG/` (untracked): `metrics.json` (grid, selected model, every table above), `cases.jsonl` (one row per validation request: prediction, confidence, retrieval result), `prepared/`, `evaluator/`. Key at `data/private/TAG/key`. Snapshot content digest of `obs-5`: `0440902f...9b49`.

## 6. Key-invariance fix and efficiency fields (M3, B7)

Cause (A062): training samples were ordered by `request_id`, an HMAC token, and `GroupKFold` groups were named by vendor tokens (it breaks size ties by group name). Both change with the key, which changed CV folds, tree and logistic fits and so the grid, CV table and importances. Measured: the validation `cases.jsonl` of `obs-orig` and `obs-wp1` hold the same 416 cases in different order. Fix (`run.py` `featurize`): samples are ordered by `case_id` (read from the evaluator-side `links.jsonl`, which holds no label) and each vendor group is named by its first case. Features are unchanged. Check: runs `obs-k1` and `obs-k2` (fresh keys, investigator run `m2-dev-7-wp1`) give `metrics.json` files identical except `tag`, `command`, `seconds` and `efficiency`; test `test_featurize_order_and_groups_do_not_depend_on_tokens`.

`metrics.json` now has `efficiency`: per-case latency (one single-row `predict_proba` call per validation case, features already extracted), p50/p95 by nearest rank, training fit seconds of the selected model, batch predict seconds, peak RSS of the whole script in KiB, cost 0. Numbers from `obs-k1` are in the B7 report, not repeated here.
