# Development results: deterministic investigator on FinRCA train and validation

Status: M2 task "run training/validation end-to-end and inspect error slices". Run tag `m2-dev-1`, code commit `c4479a2` (branch `m2-investigator`), evidence contract `evidence-contract-0.1`.

**Layout.** Sections 1 to 8 are **run 1** (tag `m2-dev-1`, 2026-11-01), kept as recorded. Section 9 is **run 2** (tag `m2-dev-3`, 2026-11-01), made after fixing the false all-clears that run 1 found (findings F1 to F3 in section 7). Section 10 is **run 3** (tag `m2-dev-4`, 2026-11-01), made after adding the approval and payment-journal guard for the F08 and F09 all-clears that remained. Where run 1 says "no limitation statement" or "clearing variants need no allocation", that describes `evidence-contract-0.1`.

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

## 9. Run 2 (`m2-dev-3`, 2026-11-01): after the all-clear fixes

Run tag `m2-dev-3`, policy `evidence-contract-0.2`, code commit `38b7bf2` plus the uncommitted working-tree changes described below (hash of `src/reconcile/**/*.py` `a9caef0b53839d841edce8049f41f3881136c9fe2f843ce1a808faa4355af3aa`), same method as section 2 (fresh key, in-container absence check, labels opened only after the packets were sealed). The snapshot content digest differs from run 1 (`3b060ddc...` against `ba9611b9...`) because the token key is new; the data are the same. `m2-dev-2` was run 1's key-independence re-run, so the next free tag was used. Same caveats as section 1: development data on a synthetic benchmark.

**What changed in the investigator** (all in `src/reconcile/diagnosis/`):
1. No invoice, no all-clear. Every `legitimate_*` payment variant and `no_exception` needs an allocation of the payment to an invoice in its own currency, and the allocations are cited. Weekend clearing, check clearing, net-fee accounting and accounted bank fee did not require it before. A completed, settled payment without an allocation now abstains with `payment_without_invoice_allocation` and a supported finding of that name (payment plus a count-0 search on `payment_allocations`). Pending, batch, reversal, split and due-date already required allocations (confirmed by test and by reading the code).
2. Every answered `legitimate_*` and `no_exception` packet carries one shared scope statement (`SCOPE_LIMITATION` in `policy.py`) saying what was checked and what was not (approval workflow, GL posting against the payment, vendor master data, PO matching).
3. When batch and a clearing variant both hold, the clearing variant is listed in `alternatives` (split already listed due-date). The winner is unchanged.

**New headline risk metric: out-of-scope failures given an all-clear** (`evaluation/dev_run.py`, `all_clear_on_out_of_scope_failures`): cases whose upstream label is a failure family outside the cohort and which the tool answered `legitimate_*` or `no_exception`. It sits next to the false-alarm rate in `report.txt`. Run 1's value was computed from its `cases.jsonl` with the same definition.

| Out-of-scope failure family | Run 1 train | Run 1 validation | Run 1 both | Run 2 train | Run 2 validation | Run 2 both |
|---|---:|---:|---:|---:|---:|---:|
| F05 payment without valid invoice | 28/53 | 11/18 | 39/71 | 0/53 | 0/18 | 0/71 |
| F08 approval workflow failure | 53/53 | 17/17 | 70/70 | 53/53 | 17/17 | 70/70 |
| F09 GL posting mismatch | 54/54 | 21/21 | 75/75 | 54/54 | 21/21 | 75/75 |
| the nine other outside families (F01 to F04, F07, F10, F11, F13, F15) | 0 | 0 | 0/564 | 0 | 0 | 0/564 |
| **All out-of-scope failures** | 135/634 = 0.213 | 49/225 = 0.218 | **184/859 = 0.214** | 107/634 = 0.169 | 38/225 = 0.169 | **145/859 = 0.169** |

All 71 F05 cases now abstain with `payment_without_invoice_allocation` (39 that were answered legitimate, plus the 32 that abstained as `required_records_missing`).

**Cohort metrics did not regress.** Every cohort figure in section 4 is identical in run 2: answered 502/503, exact-category precision 485/502 = 0.966 (train 366/379, validation 119/123), failure versus no-failure 502/502, evidence-complete 502/502, correct and complete 485/503 = 0.964, search counts reproduce 502/502, false alarms 0/281 (0/526 over all legitimate cases), FX abstentions 30/30, the same 17 wrong-variant answers, the same one abstention (`unsupported_fx`, RCA_000568). The evaluator's evidence-completeness check now also requires the allocations for weekend and check clearing, net-fee accounting and accounted bank fee (the 0.2 contract); all 502 answered cohort packets still pass it. Dispositions over the 1,608 requests: run 1 answered 778, out_of_scope 720, abstained 110; run 2 answered 739, out_of_scope 720, abstained 149 (the 39 former F05 answers). The false-alarm rate is still 0 in both runs.

**Scope statement and overlap, measured on the packets.** Of 489 answered all-clear packets, 489 carry the scope statement (run 1: 0 of 528, with 306 having no limitation at all). Batch packets now list the overlapping clearing variant: 29 with weekend clearing and 8 with check clearing; the 62 split packets list due-date as before.

**Remaining all-clears on out-of-scope failures: 145, all F08 (70) and F09 (75).** Their records are internally clean on what this tool checks (matched debit, within the clearing limit, allocations and invoice totals consistent). An approval-workflow failure and a GL-posting mismatch are not visible in the records the investigator reads (it does not read `approval_events`, and it compares no GL entry with the payment other than the journals a variant names). The answers are the same as in run 1 (`no_exception` 28 and 29, batch 21 and 19, weekend 19 and 22, check 2 and 5) but each now states in `limitations` that approval workflow and GL posting were not checked. This lowers the chance that the answer is read as a general clean bill; it does not remove the false all-clear. Closing it needs approval and GL checks, which belong to a later milestone (`PLAN.md` section 6), or an abstention for payments touched by those families, which would need signals the contract does not define. F15 (29 answered F14) remains a failure called another failure class, not an all-clear (section 7 F5).

**Status of the run 1 findings.** F1 (clearing variants without an invoice): fixed. F2 (no limitation statement): fixed. F3 (overlap inconsistently listed): fixed. F4, F5, F6: unchanged.

Reproduce: `uv run python evaluation/dev_run.py run m2-dev-3` (tag must be new), artifacts under `artifacts/dev-runs/m2-dev-3/` (untracked).

## 10. Run 3 (`m2-dev-4`, 2026-11-01): after the approval and payment-journal guard

Run tag `m2-dev-4`, policy `evidence-contract-0.3`, same method as section 2. The run is key-independent in the same sense as before (a fresh key); the `m2-dev-3` tag belongs to run 2. Same caveats as section 1.

**What changed.** Before any answered `legitimate_*` or `no_exception` packet the investigator now confirms two generic prerequisites from allowlisted tables, and abstains with a specific reason if either fails (`docs/evidence-contract.md` section 5): every allocated invoice has a completed final approval (an approving event whose `new_status` is `approved`, at the highest level seen) dated on or before the payment date (`invoice_approval_not_completed`, `payment_before_approval`); and the payment has a balanced payment journal in the payment's currency equal to the payment within one minor unit (`payment_journal_missing`, `_unbalanced`, `_currency_differs`, `_amount_differs`). F08 and F09 are not diagnosed as categories. I read the upstream F08 and F09 generators only to confirm the rules would catch them (F08 removes the highest-level approving event, leaving only intermediate approvals; F09 posts a wrong amount on both journal lines); the rules do not use event names, memo text or sequence patterns.

**Out-of-scope failures given an all-clear (headline risk).**

| Family | Run 1 | Run 2 | Run 3 |
|---|---:|---:|---:|
| F05 payment without valid invoice | 39/71 | 0/71 | 0/71 |
| F08 approval workflow failure | 70/70 | 70/70 | **0/70** |
| F09 GL posting mismatch | 75/75 | 75/75 | **0/75** |
| the other nine outside families | 0/564 | 0/564 | 0/564 |
| **All** | 184/859 = 0.214 | 145/859 = 0.169 | **0/859 = 0.000** |

By split in run 3: train 0/634, validation 0/225. F08 now abstains with `invoice_approval_not_completed` (70), F09 with `payment_journal_amount_differs` (74) or `invoice_approval_not_completed` (1); the 29 F15 cases still answered F14 are a failure called another failure class, not an all-clear.

**Cohort comparison, run 2 to run 3 (gradable cohort, 503 cases).**

| Metric | Run 2 | Run 3 |
|---|---:|---:|
| Answered (coverage) | 502/503 = 0.998 | 497/503 = 0.988 |
| Abstained | 1 | 6 |
| Exact-category precision | 485/502 = 0.966 | 481/497 = 0.968 |
| Failure vs no-failure accuracy, answered | 502/502 | 497/497 |
| Evidence-complete among answered | 502/502 | 497/497 |
| Correct and complete / cases | 485/503 = 0.964 | 481/503 = 0.956 |
| False alarms, cohort legitimate | 0/281 | 0/281 |
| False alarms, all legitimate | 0/526 | 0/526 |
| Wrong-variant answers | 17 | 16 |

**The guard costs five cohort answers.** Every cohort case whose disposition changed (all five were answered in run 2, abstained in run 3; no case changed the other way):

| Case | Label | Run 2 | Run 3 | Why (from the packet and the upstream records) |
|---|---|---|---|---|
| RCA_001679 | split payment | split | abstained `payment_journal_amount_differs` | journal 844.51 USD against payment 814.38 USD (ratio 1.037) |
| RCA_001759 | split payment | split | abstained `payment_journal_amount_differs` | journal 6849.03 against payment 6604.66 (1.037) |
| RCA_002175 | split payment | split | abstained `payment_journal_amount_differs` | journal 811.42 against payment 782.47 (1.037) |
| RCA_002212 | due-date payment | split (a wrong-variant answer) | abstained `payment_journal_amount_differs` | journal 4961.50 against payment 4784.47 (1.037) |
| RCA_002182 | net-fee accounting | net-fee | abstained `invoice_approval_not_completed` | the invoice's only approval event is `submitted`; no approving event |

All five are genuine prerequisite failures in the records, not false abstentions of the rule: in the upstream labels the payment of each of the four journal cases is also the primary payment of an F09 case (RCA_000832, RCA_000857, RCA_000874, RCA_000889 respectively, matching the 1.037 ratio the F09 generator applies), and the invoice of RCA_002182 is also the invoice of the F08 case RCA_000722 (its approval event was removed). That is, the benchmark's injected failures overlap the records of five "legitimate" cases, and the labels of those five ignore it. The guard is right about the records and wrong against the label; I report them as regressions of the label-based metrics, not as defects. It cannot be told apart from a real defect in a genuine legitimate case without more data.

Also changed outside the cohort: the 2 AP-side legitimate answers that were wrong-variant answers are now abstentions (RCA_002091 `invoice_approval_not_completed`, RCA_001996 `payment_journal_amount_differs`); I did not trace them to F08 or F09 overlaps. Out-of-scope failure families: 145 former all-clears are now abstentions (F08 70, F09 75). Dispositions over the 1,608 requests: answered 739 to 587, abstained 149 to 301, out_of_scope 720 unchanged.

**Leakage residual R1.** Inserted F01 and credit-memo invoices have no approval events. The guard abstains for any all-clear that would rest on such an invoice. In this run no answered packet of a cohort or AP-side legitimate case was lost to a missing-events invoice other than the five above and the two AP-side cases, so R1 did not cost cohort answers; those invoices are F01 and credit-memo records, outside the cohort.

**Remaining all-clears on out-of-scope failures: none.** The guard is a necessary check for those two families, not a diagnosis: a failure of a kind the guard cannot see (for example a wrong accounting period, F10, or a vendor change, F11) would still be answered legitimate if its other records were clean. In this run none of F10, F11 or the other families was answered at all (they are out of scope or abstain for other reasons), so the measured risk is 0/859; that is a development-set measurement on this benchmark only.

Reproduce: `uv run python evaluation/dev_run.py run m2-dev-4`.
