# Development results: deterministic investigator on FinRCA train and validation

Status: M2 task "run training/validation end-to-end and inspect error slices". Run tag `m2-dev-1`, code commit `c4479a2` (branch `m2-investigator`), evidence contract `evidence-contract-0.1`.

## 1. What this is and is not

- These are **development-set numbers on a synthetic benchmark** (FinRCA, revision `76c93ec`). The train and validation splits were used to write the evidence contract and the investigator, so they are reproduction and development data, not unseen data. Nothing here predicts performance on the test split, on `challenge_test`, or on real exports.
- The contract was written from the upstream generator and taxonomy (`docs/evidence-contract.md` section 1). A case that follows the generator's mutation satisfies it by construction. High agreement therefore shows the investigator implements the contract and that the contract separates these classes on these records. It does not show the benchmark's economics are realistic.
- `test` and `challenge_test` were not prepared, opened, listed or counted. The top-level combined label files were not opened.
- No claim is made beyond the numbers below.

## 2. Method

`evaluation/dev_run.py run TAG` (evaluator side, standard library plus the preparation adapters):

1. Creates a fresh token key in `data/private/TAG/key`, outside every prepared directory.
2. Prepares the FinRCA snapshot (cutoff 2026-06-30) into `artifacts/dev-runs/TAG/prepared/snapshot`.
3. Prepares requests for `train` (1,192) and `validation` (416) into separate inference directories (`prepared/requests/<split>`) and evaluator directories (`evaluator/<split>/links.jsonl`). Every case of the split is requested, including the families outside the cohort.
4. Builds the inference image and runs both splits through `deploy/inference/run_inference.sh` (container: `--network none --read-only`, three mounts). It first runs the in-container absence check with the real key, both `links.jsonl`, the upstream checkout, `challenge_test` and `evaluation/` as absent paths; all 20 listed paths were absent and the 3 mounted inputs present (`absence-check.txt`).
5. Only after both `packets.jsonl` files exist and their digests are in `run-manifest.json` does `evaluate` open `train/rca_ground_truth.jsonl` and `validation/rca_ground_truth.jsonl`, join packets to cases through the links, and compute metrics. `evaluate` refuses to run if a packets file no longer matches its manifest digest.
6. Refuses an existing tag. Re-running with a different key (tag `m2-dev-2`) gave byte-identical metrics (apart from the tag), so the numbers do not depend on the key.

Run manifest (`m2-dev-1`): code commit `c4479a2352d24d536177f3f781a0b231ee3f8dd0`; hash of `src/reconcile/**/*.py` `bb2d7f9bc5a0247cc8689a79508bd51abf01457f96ab7608089955953037da9c` (the working tree may differ from the commit; no git command was run); snapshot content digest `ba9611b9ce1f4ba8ab9bf3d7114958af2eddf710d57b91fc941ee68c0201568a`; policy version `evidence-contract-0.1`; image `reconcile-inference:dev` ID `sha256:e99f500dda78c6c4b64510dfd303d92785f9f7b0a8c99de080c6d6a30da42456`; packets digests train `6adcae54...83df89`, validation `dc5b93a7...a4cd58c`; timing: snapshot 7.6 s, inference 1.9 s (train) and 0.9 s (validation). The image ID changes on every rebuild of identical source (m2-dev-1 and m2-dev-2 have different IDs), so it identifies the run, not the code.

## 3. Cohort, mapping and metric definitions

**Cohort** (`docs/data-audit.md` section 2): labels F06, F12, F14 and the ten payment-related legitimate variants (pending settlement, weekend clearing, check clearing, due-date payment, split payment, batch payment, payment reversal, net-fee accounting, accounted bank fee, FX settlement). 538 cases (`docs/evidence-contract.md` section 7). Reported separately:

| Group | Cases | Treatment |
|---|---:|---|
| Gradable cohort | 503 | all answer metrics below |
| FX settlement | 30 | a correct outcome is an abstention with reason `unsupported_fx`; reported as its own slice, never a success of the answer metrics |
| Contradicted (4 split cases: RCA_001631, RCA_001791, RCA_002127, RCA_002223) | 4 | listed, not scored |
| Not gradable (RCA_000588, duplicate debit posts after the cutoff) | 1 | listed, not scored |
| Out-of-scope families | 1,070 | 859 failure cases (F01 to F05, F07 to F11, F13, F15) and 211 AP-side legitimate cases (credit memo, exact PO match, PO tolerance, split cost centre, invoice cancellation, pre-invoice terms change); reported separately |

**Category mapping.** Expected category for a gradable case: F06, F12 or F14 for those labels; for a legitimate variant `legitimate_<variant>` (the investigator uses the same names). `no_exception` is not a variant: on a legitimate cohort case it counts as a wrong category (kind `legit_variant_called_no_exception`) but as correct on the failure-versus-no-failure axis. This did not occur. Exact category is the headline; the coarse axis is reported next to it.

**Definitions.**
- Answer precision = correct-category answers / answered. Coverage = answered / cohort cases. Abstention rate = abstained / cohort cases (out-of-scope and failed are reported with their own counts).
- False-alarm rate = legitimate cases (label `NO_FAILURE`) with an answered F06, F12 or F14 / all legitimate cases. Reported for the cohort variants and for the AP-side variants separately and together.
- Evidence completeness is computed from the packet and the prepared snapshot against `docs/evidence-contract.md`, for the category the packet claims. Upstream `evidence_ids` are not used. Per category the evaluator lists the contract's required items and checks each is cited (a record is cited when the packet has an evidence item for that table and record ID; a search is cited when a search item has the table, the stated count and the predicate fragments the contract names):
  - F12 and pending: payment, every allocation, balanced payment journal(s), searches A1 (count 0), A2 (count 1), A3 (count 0), and a business-day-age count from the payment that is above (F12) or at most (pending) the limit.
  - F14: payment, the unique posted debit, a calculation citing both, and the GL absence search (count 0, `bank_fee`, the bank row).
  - F06: invoice, every allocation, every allocating payment, the posted debit of each effective same-currency allocating payment, and a calculation citing the invoice and an allocation.
  - Weekend and check clearing: payment, unique posted debit, a calculation citing both, a day count from the payment.
  - Due-date and split: invoice, every allocation, every effective allocating payment, their posted debits.
  - Batch: payment, every allocation, every allocated invoice, the posted debit.
  - Reversal: payment, posted debit, posted credit(s), balanced `payment_reversal` journal(s), allocated invoices.
  - Net-fee: payment, unique posted debit, every balanced `bank_fee` journal of that debit.
  - Accounted bank fee: fee row, the resolved payment, balanced `bank_fee` journal(s).
  - `no_exception`: payment, unique posted debit, every allocation, a calculation, a day count, every allocated invoice, every effective allocation to those invoices, and equal-twin payments when any exist.
  The checks are my reading of the contract text (for example "every allocation", and a day count for clearing variants). Where the contract is not explicit I chose the stricter reading.
- Primary outcome = correct and evidence-complete / gradable cohort cases (abstentions and out-of-scope count as non-successes).
- Extra check: every absence search in every answered packet was re-run on the snapshot; the stated count reproduced in all of them.
- Negative control: for each answered, complete packet, deleting evidence items one at a time makes the check fail for at least one item, in 100% of packets (13 categories tested). So 100% completeness is not a vacuous check.

## 4. Headline results (gradable cohort, 503 cases)

| Metric | Train | Validation | Both |
|---|---:|---:|---:|
| Cohort cases | 380 | 123 | 503 |
| Answered (coverage) | 379/380 = 0.997 | 123/123 = 1.000 | 502/503 = 0.998 |
| Abstained | 1/380 | 0/123 | 1/503 |
| Out-of-scope or failed | 0 | 0 | 0 |
| Answer precision (exact category) | 366/379 = 0.966 | 119/123 = 0.968 | 485/502 = 0.966 |
| Failure vs no-failure accuracy, answered | 379/379 | 123/123 | 502/502 |
| Evidence-complete among answered | 379/379 | 123/123 | 502/502 |
| Correct and evidence-complete / cohort | 366/380 = 0.963 | 119/123 = 0.968 | 485/503 = 0.964 |
| False alarms, cohort legitimate cases (gradable) | 0/210 | 0/71 | 0/281 |
| False alarms, cohort legitimate incl. FX and the 4 contradicted | 0/235 | 0/80 | 0/315 |
| False alarms, AP-side legitimate (out of scope) | 0/152 | 0/59 | 0/211 |
| False alarms, all legitimate cases | 0/387 | 0/139 | 0/526 |
| Pending-settlement legitimate cases answered as failure | 0/27 | 0/6 | 0/33 |
| FX cases abstained with `unsupported_fx` | 21/21 | 9/9 | 30/30 |

The 13 train and 4 validation wrong answers are all confusions between two legitimate variants, never a failure called legitimate or the reverse (section 6). Abstention reasons in the cohort: `unsupported_fx` 1 (RCA_000568, train). All 30 FX abstentions cite the payment and a bank row in another currency.

Per family or variant (cases / answered / correct / evidence-complete correct; train then validation):

| Label | Train | Validation |
|---|---|---|
| F06 | 57 / 56 / 56 / 56 | 18 / 18 / 18 / 18 |
| F12 | 55 / 55 / 55 / 55 | 16 / 16 / 16 / 16 |
| F14 | 58 / 58 / 58 / 58 | 18 / 18 / 18 / 18 |
| accounted bank fee | 19 / 19 / 19 / 19 | 4 / 4 / 4 / 4 |
| batch payment | 11 / 11 / 11 / 11 | 15 / 15 / 15 / 15 |
| check clearing | 23 / 23 / 18 / 18 | 4 / 4 / 4 / 4 |
| due-date payment | 25 / 25 / 20 / 20 | 9 / 9 / 8 / 8 |
| net-fee accounting | 22 / 22 / 22 / 22 | 11 / 11 / 11 / 11 |
| payment reversal | 23 / 23 / 23 / 23 | 9 / 9 / 9 / 9 |
| pending settlement | 27 / 27 / 27 / 27 | 6 / 6 / 6 / 6 |
| split payment (excl. 4 contradicted) | 31 / 31 / 31 / 31 | 4 / 4 / 4 / 4 |
| weekend clearing | 29 / 29 / 26 / 26 | 9 / 9 / 6 / 6 |
| FX settlement (slice) | 21 abstained | 9 abstained |

Dispositions for all 1,608 requests: answered 778, out_of_scope 720, abstained 110, failed 0. Train: 588 / 519 / 85 / 0. Validation: 190 / 201 / 25 / 0.

### Contradicted and not-gradable cases

| Case | Label | Investigator outcome |
|---|---|---|
| RCA_000588 | F06, not gradable | abstained, `commitment_not_proven_at_cutoff` (not answered; correct for a not-gradable case) |
| RCA_001631 | split, contradicted | out_of_scope, `no_in_scope_pattern` |
| RCA_001791 | split, contradicted | out_of_scope, `no_in_scope_pattern` |
| RCA_002223 | split, contradicted | out_of_scope, `no_in_scope_pattern` |
| RCA_002127 | split, contradicted | abstained, `debit_differs_from_payment` |

None was answered, so none was called legitimate. The contract proposes that a contradicted case be answered from the records; the investigator instead declines (out of scope for under-allocated invoices). Whether that is acceptable is a policy decision for the parent.

## 5. Out-of-scope families

These cases are not in any cohort metric. Disposition counts (both splits):

| Group | Cases | answered | out_of_scope | abstained |
|---|---:|---:|---:|---:|
| Failure families outside the cohort | 859 | 213 | 574 | 72 |
| AP-side legitimate variants | 211 | 63 | 143 | 5 |

Reasons for non-answers: `no_in_scope_pattern` (435 + 143), `entity_type_not_supported_yet` (71, GL journal entities), `bank_row_type_not_supported_yet` (68, F13), `bank_amount_above_payment` (38), `required_records_missing` (32), `primary_entity_not_in_snapshot` (2), `debit_differs_from_payment` (4), `unsupported_fx` (1).

Answered outside the cohort, by what the investigator said:

| Label | Answered / cases | Categories given |
|---|---:|---|
| F05 payment without valid invoice | 39 / 71 | weekend clearing 30, check clearing 9 |
| F08 approval workflow failure | 70 / 70 | no_exception 28, batch 21, weekend 19, check 2 |
| F09 GL posting mismatch | 75 / 75 | no_exception 29, weekend 22, batch 19, check 5 |
| F15 incorrect payment-bank match | 29 / 67 | F14 29 |
| AP-side legitimate (exact PO match 27, split cost centre 20, pre-invoice terms change 16) | 63 / 211 | due-date payment 39, split payment 24 |

Reading: no legitimate case was called a failure (0/526 overall). But **184 of the 859 failure-family cases outside the cohort were called legitimate or "no exception"** (127 named legitimate variant, 57 `no_exception`), and 29 F15 cases were called F14. These are misclassifications of failures the tool does not cover; the packets are internally supported, but the label says a failure exists. `no_exception` packets carry the limitation that no covered check fired; the named legitimate packets do not (finding F2). The 63 AP-side answers name a payment variant (due-date or split payment) on a legitimate case of another variant; that is a variant mismatch, not a failure call.

## 6. Error slices

In-scope (cohort, FX, contradicted and not-gradable) cases that are wrong, abstained or not answered, with cause. There are 18 cohort cases plus the 5 special cases above; no FX case and no failed execution.

| Slice | Cases | Investigator output | Cause |
|---|---|---|---|
| check clearing labeled, called batch | train RCA_001682, 001746, 001826, 001858, 002050 | `legitimate_batch_payment` | Contract overlap. The payment (method check) is allocated to 2 invoices and clears exactly at the 10-day limit, so the batch and the check-clearing contracts both hold. Contract section 5.1 gives batch precedence. No alternative is listed in the packet. |
| weekend clearing labeled, called batch | train RCA_001696, 001920, 002032; validation RCA_001536, 001712, 002128 | `legitimate_batch_payment` | Same: ACH payment allocated to 2 invoices. |
| due-date labeled, called split | train RCA_001604, 001812, 001844, 001908, 002116; validation RCA_002212 | `legitimate_split_payment`, alternative `legitimate_due_date_payment` listed | Invoice paid by 2 payments that sum to the total, one on the due date; both contracts hold. Split has precedence; the due-date variant is listed as an alternative. |
| F06 abstained | train RCA_000568 | abstained, `unsupported_fx` | The second allocating payment is EUR 3,064.92 but its bank debit is USD 3,310.11. The investigator abstains on the cross-currency debit. `evaluation/evidence_contract_check.py` counts this case gradable because the contract (section 4.1) is silent on the currency of the debit. Contract gap; the abstention is conservative. |

Counts by reason (cohort non-answers): `unsupported_fx` 1. Counts by wrong kind: `wrong_legit_variant` 17. By direction: legit variant to another legit variant 17; failure called legitimate 0; legitimate called failure 0.

Every one of the 17 wrong answers is supported by the records for the category it gave (evidence-complete under the contract). The label's own category is also supported, so these are ambiguities of the label space, not unsupported claims.

## 7. Investigator and contract findings

Report only; nothing in `src/` was changed. Evidence for each is from run `m2-dev-1`.

- **F1. Clearing variants answer "legitimate" for payments with no invoice allocation (contract gap, investigator behaviour).** All 39 answered F05 cases (28 train, 11 validation) have zero allocations (checked on the snapshot). The packet (for example the one for RCA_000472) cites only the payment, its bank row, `delta` and the day count, and says `legitimate_check_clearing` or `legitimate_weekend_clearing`. The contract's clearing rows (section 5) require no allocation, while `no_exception` (section 5.1) does and abstains with `required_records_missing` without one (the other 32 F05 cases). The same payment shape gets a legitimate answer or an abstention depending on its method and calendar.
- **F2. Named legitimate packets carry no limitation statement.** Of the answered packets, `legitimate_weekend_clearing` (103), `legitimate_batch_payment` (77), `legitimate_check_clearing` (38), `legitimate_net_fee_accounting` (33), `legitimate_payment_reversal` (32) and `legitimate_accounted_bank_fee` (23) have an empty `limitations` list, while `no_exception`, pending, split and due-date packets state the scope. With F1 and section 5, a reader sees a named "legitimate" category on 88 F08 and F09 failures (and `no_exception` on 57 more) with nothing in the 88 saying that approvals and GL posting were not checked.
- **F3. Overlap between variants is handled inconsistently.** Split lists due-date as an alternative; batch lists no clearing alternative although the day count holds in the 11 batch-versus-clearing cases.
- **F4. Contradicted cases are not answered from the records** (section 4), unlike the contract's proposed handling (`docs/evidence-contract.md` section 8).
- **F5. F15 cases look like F14.** 29 of 67 F15 cases satisfy the F14 contract and are answered F14 with complete evidence; 38 abstain with `bank_amount_above_payment`. This is a scope limit (the swapped-reference condition is not detectable by the F14 check), not a code defect.
- **F6. The inference image ID is not reproducible** across rebuilds of identical source (build metadata), so it cannot stand as a code identity.

No case ended `failed`, no packet exceeded its limits, and no evidence item was dated after the cutoff (the packet constructor enforces this).

## 8. Reproduce

```bash
cd <repo>   # worktree with the same code; docker required
uv run python evaluation/dev_run.py run TAG          # TAG must be new; ~15 s
uv run python evaluation/dev_run.py evaluate TAG     # recompute metrics from existing packets
```

Outputs under `artifacts/dev-runs/TAG/` (untracked): `run-manifest.json`, `absence-check.txt`, `metrics.json`, `report.txt`, `cases.jsonl` (one row per case), `errors.json`, `prepared/`, `inference/`, `evaluator/`. The key is at `data/private/TAG/key`. Upstream checkout default: `/home/charalambos/Documents/fin-tech-projects/reconcile/data/upstream/FinRCA-AI-Bench` (`--upstream` to change).
