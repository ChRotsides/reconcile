# Observable-input classical-ML baseline (M2)

Status: PLAN.md M2 task "the strongest applicable classical-ML baseline against the same observable inputs". Run tag `obs-2` (final code, 2026-11-01), key-order check `obs-1`. Code: `evaluation/baselines/observable/` (`features.py`, `evidence.py`, `run.py`). Investigator compared: run `m2-dev-4` (policy `evidence-contract-0.3`), validation rows recomputed from `artifacts/dev-runs/m2-dev-4/cases.jsonl` with the same definitions (`run.py` `investigator_validation`).

## 1. What this is and is not

- Development-set numbers on a synthetic benchmark (FinRCA, revision `76c93ec`), train and validation only. The investigator was written against the same labels (`docs/dev-results.md` section 1), and this baseline's model was selected on the validation split, so neither is unseen. `test`, `challenge_test` and the top-level combined label files were not opened.
- Same inputs as the investigator: the prepared snapshot (allowlist, opaque tokens, cutoff 2026-06-30) and neutral requests (primary entity table and token only). The baseline prepared its own key (`data/private/obs-2/key`) and snapshot with the same adapters. Feature code opens no label file, no key and no links file; labels are opened by `run.py` after all features exist.
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

Business days are Monday to Friday in `[start, end)`, clearing limits are the contract's P1 (ACH 3, wire 1, check 10, virtual card 2). Money is parsed and compared as `Decimal`; ratios and log10 magnitudes become floats only as model inputs.

**Target.** The fine label: the failure family (15 families) or, for legitimate cases, the scenario variant (16 variants, including the six AP-side ones). The classifier therefore sees the same label space the full benchmark has, answers every request, and an answer counts as "no failure" when it is a `legitimate_*` label. Trained on the 1,192 train requests only; validation 416 requests; the final model is not refit on train plus validation.

**Models and selection.** 15 candidates: HistGradientBoosting (learning rate 0.05 or 0.1, depth 3 or none, L2 0 or 1; 150 iterations, no early stopping), multinomial logistic regression (C 0.1, 1, 10; imputer plus scaling), random forest (300 trees, depth none or 12, min leaf 1 or 3). Selection criterion: exact-category correct count on the validation gradable cohort (123 cases); the first best wins. Selected: **HistGradientBoosting, learning rate 0.05, max depth 3, L2 1.0** (109/123).

| Model family | Validation cohort correct (range over its grid) |
|---|---:|
| HistGradientBoosting (8) | 103 to 109 |
| Random forest (4) | 97 to 103 |
| Logistic regression (3) | 90 to 92 |

HistGradientBoosting is also the upstream published winner (`docs/baseline-reproduction.md`); here it is also best on the observable inputs. Selection bias: the best of 15 on 123 cases is optimistic, and the neighbours of the winner (106 to 109) show the spread, so the 109 is an upper-end estimate for this baseline.

**Scoring** replicates `evaluation/dev_run.py` (not imported): cohort and groups `group_of` (lines 607-618: 123 gradable validation cases, 9 FX, plus the contradicted and not-gradable sets), category mapping `FAMILY_KEYS` and `VARIANT_CATEGORY` (56-77), `is_no_failure` and `is_failure` (86-91), outcome kinds `classify` (621-680), false-alarm and precision definitions (`cohort_metrics`, 778-822), all-clear risk (`all_clear_risk`, 862-879). A classifier always answers, so coverage is 100% in the primary view. A "false alarm" follows `dev_run`: a legitimate case answered with any category that is not `legitimate_*` (so it includes a predicted F08 or F01); the narrower "predicted F06, F12 or F14" count is given beside it.

**Optional abstention.** Declared rule: take the cross-validated (5-fold, grouped by vendor token) class probabilities of the selected configuration on TRAIN; the threshold is the smallest value on the grid 0.50 to 0.95 (step 0.05) whose train cohort answer precision is at least 0.99; the baseline abstains below it. Chosen: 0.85 (`obs-2`; 0.90 in `obs-1`).

**Deterministic evidence retrieval** (`evidence.py`, implemented). For the predicted category and the primary entity, retrieve the records and absence counts that `docs/evidence-contract.md` requires and test each requirement against the snapshot: F12 and pending (allocations, balanced payment journal, reference search A1 count 0, reference uniqueness A2 count 1, unreferenced candidate search A3 count 0, business-day age above or at most the limit), F14 (a unique posted debit, no balanced `bank_fee` journal on it), weekend and check clearing (unique posted debit, allocations), batch, reversal, net fee, accounted fee, F06, split and due date (the lists in `dev-results.md` section 3). The requirement list is the evaluator's `required` (`dev_run.py` 389-599) re-implemented against snapshot state. Categories the contract does not define (any failure family other than F06, F12, F14, the FX variant, AP-side variants, GL entities) count as incomplete.
This is weaker than the check applied to the investigator, and it is partly circular: the baseline emits no packet, so nothing is checked for citation, calculation or day-count items or claim text, and a requirement is met whenever the snapshot supports the category. It measures "could the contract's evidence be assembled for this answer", not "was it assembled and cited". Read "evidence-complete" below with that in mind. A wrong category usually fails it (111 of 123 answers complete, 109 of 109 correct answers complete).

## 3. Validation results against the investigator

Gradable validation cohort, 123 cases. Investigator: `m2-dev-4`, no cases failed. Baseline: `obs-2`.

| Metric | Investigator `m2-dev-4` | Baseline, no abstention | Baseline, threshold 0.85 (chosen on TRAIN) |
|---|---:|---:|---:|
| Coverage (answered / cohort) | 122/123 = 0.992 | 123/123 = 1.000 | 98/123 = 0.797 |
| Exact-category precision (correct / answered) | 119/122 = 0.975 | 109/123 = 0.886 | 94/98 = 0.959 |
| Failure vs no-failure accuracy, answered | 122/122 = 1.000 | 115/123 = 0.935 | 94/98 = 0.959 |
| **Correct and evidence-complete / cohort (primary)** | **119/123 = 0.967** | 109/123 = 0.886 (retrieval check, see above) | 94/123 = 0.764 |
| False alarms, cohort legitimate (dev_run definition) | 0/71 | 8/71 = 0.113 | 4/71 = 0.056 |
| of which predicted F06, F12 or F14 | 0/71 | 0/71 | 0/71 |
| False alarms, AP-side legitimate (out of scope) | 0/59 | 17/59 = 0.288 | 0/59 |
| False alarms, all legitimate | 0/139 | 25/139 = 0.180 | 4/139 = 0.029 |
| All-clear on out-of-scope failures | 0/225 | 5/225 = 0.022 | 0/225 |
| FX slice (9 cases; investigator abstains `unsupported_fx` = correct) | 9/9 abstained | 9/9 called `legitimate_fx_settlement` | not applicable |

Reading. On the same observable inputs the strong classifier is clearly worse on the primary outcome (0.886 against 0.967 without abstention), and it never reaches the investigator by abstaining (0.764 at 0.85). It gives up precision and false-alarm and all-clear safety, which the investigator, a rule set with an explicit contract, holds at 0 on this development set. The classifier's evidence completeness is not an independent measurement (above); its correct answers are as "complete" as the snapshot allows.

The classifier is not weak where the evidence is simple: F06 18/18, F12 16/16, F14 17/18, pending 6/6, reversal 9/9, net fee 11/11, accounted fee 4/4, check 4/4, split 4/4. Its losses:

| Label | Cases | Correct | Wrong answers |
|---|---:|---:|---|
| legitimate batch payment | 15 | 8 | 7 called F08 (approval workflow failure) |
| legitimate due-date payment | 9 | 5 | exact PO match 2, split cost centre 1, split payment 1 |
| legitimate weekend clearing | 9 | 7 | F08 1, batch 1 |
| F14 | 18 | 17 | F08 1 |

Seven of the eight cohort false alarms are legitimate batch payments called an F08 approval failure (the eighth is a weekend clearing payment). Hypothesis, not tested: batch payments touch several invoices, so the minimum-approval-count feature is more often low or odd for them, which the classifier associates with F08. The investigator checks approvals with a fixed rule (completed final approval before the payment) and made none of these errors on validation.

Out-of-scope failures answered "no failure" (5): F02 1, F03 2, F04 1, F08 1, of 225. Not a measure of its safety: F08 and F09 are in the label space and the classifier can name them.

Stability. `obs-1` used a different key. Tokens, and therefore the request order, differ, and the cohort figures are the same (109/123, false alarms 8/71 and 0 narrow, all-clear 5/225); the random-forest grid moved by one to two cases (97 to 103 instead of 98 to 103) and the CV-chosen abstention threshold was 0.90 (94 of 123 answered, 93 correct) instead of 0.85. The abstention view is therefore the less stable one.

## 4. What the model relies on

Permutation importance on validation (selected model, 10 repeats, drop in exact-label accuracy over all 416 requests; `obs-2`):

| Feature | Drop | Meaning |
|---|---:|---|
| `i_alloc_ratio` | 0.103 | allocated amount over invoice total (F06 evidence) |
| `i_status_received` | 0.093 | invoice status (residual R1) |
| `p_pay_journal_ratio` | 0.088 | payment journal amount over payment (F09 evidence) |
| `p_debit_ratio` | 0.070 | bank debit over payment (F14, net fee) |
| `b_amount_log10` | 0.069 | bank row amount (bank-row cases) |
| `p_alloc_ratio` | 0.067 | allocated over payment |
| `e_gl` | 0.058 | entity is a GL journal (residual R3) |
| `p_n_bank` | 0.045 | bank rows on the reference (F12, pending) |
| `i_n_approval` | 0.043 | approval events of the invoice (residual R1) |
| `p_gl_has_bankfee`, `p_n_returned`, `i_n_committed`, `p_gl_has_reversal`, `i_status_canceled` | 0.041 to 0.033 | fee journal, returned rows, committed payments, reversal journal, status |

By block, permuting all the columns of the block together (relative drop of cohort correct): payment block 0.75, invoice block 0.38, bank-row block 0.05, GL block 0.0. The reliance is mostly on the evidence a practitioner would use.

**Residuals.** Retraining the selected configuration without a residual group (validation cohort correct; baseline 109):

| Group removed | Columns | Cohort correct | Joint permutation drop |
|---|---|---:|---:|
| R1 invoice status and approvals | `i_status_*`, `i_n_approval`, `p_inv_min_approval` | 105 | 0.083 |
| R2 FX codes | `g_fx`, `g_type_fx_settlement`, `p_gl_has_fx` | 109 | 0.000 |
| R3 entity type | `e_*` | 108 | 0.032 |
| R4 invoice age | `i_age_days` | 104 | 0.004 |
| allocation count | `p_n_alloc`, `i_n_alloc` | 106 | 0.091 |
| all five | | 94 | |

Without all five groups: 94/123 = 0.764 correct, cohort false alarms 18/71 (still 0 for F06, F12, F14), all-clear 6/225.

Findings:
- R2 is not used: removing it changes nothing. The full model labels the FX slice `legitimate_fx_settlement` 9/9; whether that comes from the FX journal or from the currency mismatch (`p_debit_cur_match`) was not isolated.
- R1, R3 and the allocation count carry weight (1 to 4 cases each, 15 together). They are not clean leaks: invoice approvals and the allocation count are evidence the investigator also reads, and the entity type is a request field (R3). The group definitions mix the declared residual and genuine evidence, so the 15 cases are an upper bound for "won through a residual", not a measurement of it. R1's `i_status_received` is the second most important single feature; it separates inserted F01 and credit-memo invoices (outside the cohort), so it explains out-of-scope behaviour more than cohort correctness.
- R4: removing `i_age_days` costs 5 cases but its permutation importance is about zero, so the retrain difference is within what the 15-candidate selection spread shows (3 to 6 cases); not a firm reliance. A date-only model for invoice-primary cohort cases (31 validation cases, F06 against legitimate) gets balanced accuracy 0.712 (leakage test: 0.756), and the full model gets 1.000 on the same cases through allocation amounts. So R4 exists as a weak signal but the baseline does not need it.
- Entity type only (HistGradientBoosting depth 3 on `e_*`, full label space) gets 0/123 on the cohort: it names the most common out-of-scope family for each type. Beside every headline number this is the R3 control; here it is far below 109.
- The baseline does not win through a residual: with the five groups removed it still scores 94/123, and the investigator, which does not use R1 to R4 as diagnostic evidence (its approvals check is a prerequisite, `dev-results.md` section 10), scores 119/123.

## 5. Limitations

- Development data on a synthetic benchmark; selection on validation (best of 15). Both systems were built with these labels in view. Results do not transfer to real exports.
- One snapshot and key per run; two keys gave the same cohort figures. Small cohort (123), small strata (batch 15, due date 9), so a one-case change moves a rate by 0.8 points.
- The label space for the baseline includes the out-of-scope families, which the investigator cannot name. That helps the baseline on some safety metrics (it can say F08) and it is not a like-for-like removal of that advantage. A baseline trained only on the 13 cohort labels would have no out-of-scope label to give and would be forced to answer everything as a cohort category; that variant was not run.
- Evidence retrieval is a re-implementation of the contract's requirement list against the snapshot and is weaker than a packet check (section 2). The primary outcome of the baseline is therefore an upper bound on what a packet-producing baseline would achieve under the stricter check, not a like-for-like number.
- Features were hand-chosen from the allowlist. A better feature set, early stopping, longer training or other models might do better; the grid is small. The abstention threshold uses a declared target precision (0.99) that is itself a policy choice.
- Permutation importance is conditional on correlated features; groups were tested by retraining as well as by permutation.
- Nothing was run on `test` or `challenge_test`; the sealed comparison (M7) must prepare its own inputs and apply the validated choices (model, grid winner, threshold rule) without refitting choices on that data.

## 6. Reproduce

```bash
uv run --group eval python evaluation/baselines/observable/run.py TAG   # TAG must be new; about 3 min
```

Needs the `eval` dependency group (scikit-learn 1.9.1, numpy 2.5.4, scipy 1.18.1 in `uv.lock`; not part of the runtime or dev groups) and the upstream checkout (`--upstream`, default `/home/charalambos/Documents/fin-tech-projects/reconcile/data/upstream/FinRCA-AI-Bench`). Compares with the investigator run named by `--investigator-run` (default `m2-dev-4`; its `cases.jsonl` must exist). Outputs under `artifacts/baselines/TAG/` (untracked): `metrics.json` (grid, selected model, every table above), `cases.jsonl` (one row per validation request: prediction, confidence, retrieval result), `prepared/`, `evaluator/`. Key at `data/private/TAG/key`. Snapshot content digest of `obs-2`: `9c13d820...b3d4`.
