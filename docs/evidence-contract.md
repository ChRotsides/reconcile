# Reconcile evidence contract (M1, version 0.1-draft)

Paths without a leading slash are relative to the upstream checkout (`data/upstream/FinRCA-AI-Bench`, revision `76c93ec`) unless they start with `docs/data-audit.md`, `PLAN.md`, `AGENTS.md` or `evaluation/`, which are Reconcile files. Upstream citations are `file:line` at the pinned revision.

## 1. Purpose and status

PLAN.md section 7 defines the primary outcome as the share of cases with the correct supported diagnosis **and** the required supporting evidence. The upstream evidence sets cannot be that definition for three groups of cases (`docs/data-audit.md` section 8):

- Every F12 and F14 set contains an anomaly-named audit event (71 of 71 and 76 of 76 development cases), and `audit_log` is excluded from inputs (section 3.1, L3).
- Pending cases cite only the payment ID, so the age and the absence of a bank row are not citable.
- RCA_000588 (F06) cites `BT_0426251`, posted after the cutoff.

This document is **our** contract for F06, F12, F14 and the ten payment-related legitimate variants. It says which observable records, calculations and bounded absence searches a correct, evidence-complete answer needs, using only the allowlisted columns of `docs/data-audit.md` section 3.1 and the section 6 cutoff rule. It replaces the upstream `evidence_ids` as the grading reference for these cases; upstream IDs remain a diagnostic.

Status: **draft, not yet declared.** It is validated on train and validation only (section 7), by `evaluation/evidence_contract_check.py`. It becomes the M1 contract when the parent records the decisions listed in the build report and the user accepts the M1 gate. It must be frozen before any use of the sealed challenge split (AGENTS.md section 7); the test split is already exposed (`docs/data-audit.md` section 11.3) and is a reproduction set only.

What the validation does and does not show. The contract is written from the upstream taxonomy and generator code, so a case that follows the generator's mutation will satisfy it by construction. The results in section 7 therefore show that the contract is **consistent with the data and that it separates the classes on the records**, not that the benchmark's economics are correct (same caveat as `docs/data-audit.md` section 9). One rule was refined after seeing a development result (section 4.1, committed versus allocated overpayment, RCA_000512); that is disclosed there and in section 9.

## 2. Principles

1. **Observable only.** Required records come from the section 3.1 allowlist. `audit_log` is not an input. Bank rows posted after 2026-06-30 and GL lines posted after 2026-06-30 do not exist for the investigator (section 6 rule; `PLAN.md` section 4: every accepted citation resolves under the cutoff policy).
2. **Exact money.** `Decimal` amounts with an explicit currency. Compare only within one currency. A difference is `ERP amount minus bank amount` (positive means the bank is short). Equality tolerance is one minor unit of the currency (JPY 1, others 0.01; `src/utils/money.py:11`), not a percentage.
3. **Absence is a bounded search, not a missing ID.** An absence claim cites a search record: table, key, predicate, cutoff and the observed count (0). It is evidence only when the search scope is stated, the snapshot is declared complete for that table, and re-running the query on the snapshot returns the stated count. The snapshot-completeness assumption is declared, not proven (spec lines 19 and 1046; open question 1).
4. **Distinct payments stay distinct.** No de-duplication by amount or date (`PLAN.md` section 4; audit section 10, item 3). Two equal-amount payments are a duplicate only if cumulative committed allocations exceed the invoice.
5. **Missing counterparts remain visible.** A payment without a bank row is a finding in every packet that touches it, even when the primary diagnosis is another class (RCA_000512, section 4.1).
6. **Bank-fee rows are not settlement evidence.** Rows with `transaction_type = "bank fee"` that carry a payment reference are fees, not the payment's cash movement (audit RCA_001156, spec line 725). They are used only by the fee contracts.
7. **Unsupported conclusions are abstentions.** Cross-currency comparisons, missing rows, shared references and post-cutoff rows give `abstained` or `not gradable`, never `legitimate` (`PLAN.md` section 1 and 4; `AGENTS.md` section 2).
8. **Root-cause narratives are not graded.** F06 "later run did not recognise the prior settlement", the three F12 sub-causes and the F14 "processor netted a fee" are unobservable once `audit_log` is dropped (audit section 10, item 1). Only the observable condition is graded.
9. **Parameters are declared, not tuned.** Section 6 lists every parameter with its source; none was fitted to labels.

## 3. Shared definitions

| Term | Definition (allowlisted columns only) |
|---|---|
| Observable bank row | `bank_transactions` row with `posted_date` on or before 2026-06-30 |
| Observable GL line | `gl_entries` row with `posting_date` on or before 2026-06-30 |
| Effective payment | `payments.payment_status = completed` and `settlement_status = settled` (spec F06 pseudocode, lines 400-404) |
| Principal bank row of payment P | observable bank row with `payment_reference = P.reference_number` and `transaction_type` not `bank fee` |
| Posted debit of P | principal bank row with `direction = debit` and `status = posted` |
| Unique reference | exactly one `payments` row has `reference_number = P.reference_number` (otherwise matching is ambiguous; spec line 749) |
| Fee row | observable bank row with `transaction_type = bank fee` |
| Balanced journal of type T | observable GL lines sharing one `journal_id`, all with `transaction_type = T`, with total debit equal to total credit and greater than zero. Its amount is the total debit |
| Business-day age | number of Monday-to-Friday days in the half-open interval (start, end]. No holiday calendar (spec line 17 of the audit; upstream `src/utils/dates.py:24-32` adds days the same way) |
| Clearing limit | ACH 3, wire 1, check 10, virtual card 2 business days (section 6, P1) |

The payment-to-bank link is the shared `payment_ref` namespace (`payments.reference_number` = `bank_transactions.payment_reference`). It is a relationship, not an identity proof (`PLAN.md` section 4).

## 4. Per-family contracts

### 4.1 F06: invoice paid twice (primary entity: invoice)

Grounding. Taxonomy lines 70-81: two payment runs settle one obligation beyond its balance; hard negative is a split whose allocations equal the invoice. Generator: `src/failures/f06_double_payment.py:22-24` (eligible invoice is already `paid` with allocations), `:35-43` (inserted payment copies the last payment and sets `payment_amount` to the invoice total), `:46-57` (full-value allocation), `:58` with `src/failures/base.py:197-262` (payment journal and posted bank debit added). Rules/SQL spec lines 383-440 (net paid over effective payments of the invoice currency; flag when net paid exceeds the total by more than the currency quantum). Invariants: distinguish reversals, instalments and duplicate committed payments (`PLAN.md` section 4).

Required records:

| Record | Table and relationship | Columns |
|---|---|---|
| The invoice | `invoices` by primary ID | `invoice_total`, `currency`, `status` |
| All allocations to it | `payment_allocations` where `invoice_id` = invoice | `payment_id`, `allocated_amount` |
| Every allocating payment | `payments` by `payment_id` | status columns, `payment_currency`, `payment_amount`, `reference_number` |
| The posted debit of each effective allocating payment | `bank_transactions` via `reference_number` (section 3) | `amount`, `currency`, `posted_date` |

Calculations:

- `allocated = sum(allocated_amount)` over allocations whose payment is effective and whose `payment_currency` equals the invoice currency.
- `committed = sum(allocated_amount)` over the subset of those allocations whose payment has a posted debit.
- `committed_overpayment = committed - invoice_total`.

Absence evidence: none required for the diagnosis. A payment that is allocated but has **no** observable posted debit is reported as a separate finding (not counted as committed) with its bounded search (principal bank rows for that reference, cutoff).

Evidence-complete (diagnosis "duplicate disbursement"): the invoice, every allocation, every allocating payment and the posted debit of every payment counted in `committed` are cited, and `committed_overpayment` is greater than one currency quantum. Citing only two of three payments, as upstream does in 11 of 76 cases, is incomplete.

Not gradable: `allocated - invoice_total` exceeds the quantum but `committed_overpayment` does not (the proof that the extra payment left the bank is missing or not yet posted at the cutoff). RCA_000588 is this case: the second check's debit `BT_0426251` posts 2026-07-01. Also not gradable: allocations or an allocating payment missing from the snapshot.

Contradiction (label says F06, records do not): `allocated` does not exceed the invoice total by more than the quantum.

Disclosure. The first version of this rule required every effective allocating payment to have a debit and marked RCA_000512 not gradable (its third, instalment payment `PAY_0421840` has no bank row). RCA_000512 has a committed overpayment of 1254.13 EUR from the two payments that do have debits, so the rule was changed to "committed versus allocated" and the bank-less payment is reported as a visible separate finding. That change was made after seeing the development result for one case.

Ambiguities. Instalments, prepayment, on-account credit and intentional overpayment cannot be told apart from a duplicate except by allocation arithmetic (audit section 10, items 2 and 3). The data has no repeated-ingestion examples, so "same payment ingested twice" is not exercised. RCA_000578's original payment has a bank debit (19366.98) different from its payment amount (8544.41); the contract checks that a posted debit exists, not that its amount equals the payment (open question 5).

### 4.2 F12: ERP payment missing from bank (primary entity: payment)

Grounding. Taxonomy lines 148-159; generator `src/failures/f12_erp_payment_missing_bank.py:15-22` (a settled payment with exactly one ordinary, non-fee bank row), `:34` (the bank row is deleted; payment, allocations and GL are left), `:35-36` (audit event, not observable here). Spec lines 712-749 (completed/settled payment, no non-fee bank row carrying the reference posted or returned by the cutoff, business-day age strictly greater than the method limit; a reference shared by several payments gives "insufficient"). Hard negative: submitted payment one day before the cutoff (taxonomy line 158).

Required records:

| Record | Table and relationship | Columns |
|---|---|---|
| The payment | `payments` by primary ID | method, date, currency, amount, status columns, `reference_number`, `bank_account_id` |
| Its allocation(s) | `payment_allocations` by `payment_id` | at least one row |
| Its payment journal | `gl_entries` where `source_transaction_id` = payment and `transaction_type = payment`, observable | a balanced journal |

Calculations: business-day age from `payment_date` to the cutoff; compare `age > limit(method)` (strict).

Absence evidence (all three must be empty, each cited as a search record):

- A1, reference search: observable `bank_transactions` rows with `payment_reference` = the payment reference and any type except `bank fee`, any status, any direction. Count must be 0. Fee rows are listed but do not count.
- A2, reference uniqueness: `payments` rows with this `reference_number`. Count must be 1.
- A3, unreferenced candidate search: observable non-fee debit rows whose `payment_reference` matches no payment, with equal currency, equal amount and `counterparty_token` equal to the payment's `bank_account_id`. Count must be 0 (otherwise the missing row may be a mis-referenced one, which this contract does not decide). No date window is used, so no extra parameter exists.

Evidence-complete: the payment, allocation(s), payment journal and the three search records are cited, with the age and limit calculation, and `age > limit`.

Not gradable: A2 or A3 not empty; a bank row for the reference exists only after the cutoff (it is indistinguishable from a missing row: audit section 6); no allocation or payment journal observable.

Contradiction: A1 not empty (a bank row exists); `age <= limit`; payment not effective.

Ambiguities. The sub-cause (queue timeout, file not transmitted, batch rejected) is stated only by the excluded event name and is not graded. A real analyst may read a fee row as partial settlement (RCA_001156); the contract says it is not. A3 found no candidate for any of the 71 development cases, so it is declared but not exercised by the data.

### 4.3 F14: bank/ERP amount mismatch (primary entity: payment)

Grounding. Taxonomy lines 174-185; generator `src/failures/f14_bank_erp_amount.py:15-25` (payment with a single ordinary bank row, no fee row for the reference, amount above 100), `:26-29` (bank amount lowered by 15, 20, 25 or 35 units, same currency), `:30-32` (audit event, not observable). Spec lines 817-865 (exactly one non-fee posted bank debit; difference compared with balanced `bank_fee` journals sourced to that bank row; match if equal within the quantum). Hard negative: the same 25-unit difference with a balanced fee journal (taxonomy line 184; `src/failures/hard_negatives.py:314-359`).

Required records:

| Record | Table and relationship | Columns |
|---|---|---|
| The payment | `payments` | `payment_amount`, `payment_currency`, `reference_number` |
| The unique posted debit | `bank_transactions` via reference (section 3) | `amount`, `currency`, `direction`, `status` |
| Fee journals of the bank row | `gl_entries` where `source_transaction_id` = bank transaction and `transaction_type = bank_fee`, observable | balanced journals (possibly none) |

Calculations (one currency): `delta = payment_amount - bank_amount`; `explained = sum(amount of balanced bank_fee journals sourced to the bank row)`; `unexplained = |delta - explained|`. Diagnosis "unexplained shortfall" requires `delta > quantum` and `unexplained > fee tolerance` (section 6, P3).

Absence evidence: the search record "observable `gl_entries` with `transaction_type = bank_fee` and `source_transaction_id` = this bank transaction: count 0" (so `explained = 0`).

Evidence-complete: payment, bank row, the delta calculation and the GL absence search record. The fee size (15 to 35) is not evidence either way (L7).

Not gradable: reference not unique; no or several posted debits for the reference; bank amount above the ERP amount (a different condition, not covered); bank row posted after the cutoff.

Contradiction: `delta <= quantum` (no mismatch) or a balanced fee journal explains the difference.

Ambiguities. The journal link is to the bank row, as in the generator. A fee booked against the payment ID would not be found (declared limitation, not exercised by the data). The processor's reason for the shortfall is unobservable. Cross-currency F14 does not occur in development data (audit section 7); a cross-currency pair is handled by section 7.

## 5. Legitimate variants and abstention

Each contract has the same shape: required records, calculations, evidence-complete definition. A legitimate conclusion is a supported financial conclusion only when its contract holds; otherwise the answer is an abstention (`PLAN.md` section 4).

| Variant (upstream) | Primary entity | Required records | Calculations that must hold | Absence evidence | Not gradable |
|---|---|---|---|---|---|
| Pending settlement (`hard_negatives.py:284-312`) | payment | payment (status not final or inside window), allocation, payment journal | `age <= limit(method)` | A1-A3 empty (as F12) | A2/A3 not empty; bank row post-cutoff |
| Weekend clearing (`:165-184`) | payment (ACH) | payment, unique posted debit | amounts equal; `bdays(payment_date, posted_date) <= 3` | none | no posted debit; reference shared |
| Check clearing (`:222-237`) | payment (check) | payment, unique posted debit | amounts equal; `bdays(payment_date, posted_date) <= 10` | none | as above |
| Due-date payment (`:260-282`) | invoice | invoice, allocations, effective payments, their posted debits | some payment has `payment_date = due_date`; each debit equals its payment amount; allocations do not exceed the total | none | a payment lacks a debit |
| Split payment (`:143-163`) | invoice | invoice, two or more allocations, effective payments, a posted debit per payment | `sum(allocations) = invoice_total` (quantum); each debit equals its payment amount | none | a leg lacks a debit |
| Batch payment (`:121-141`) | payment | payment, two or more allocations, invoices, posted debit | `sum(allocations) = payment_amount = bank amount`; no invoice is over-allocated | none | no debit |
| Payment reversal (`:504-569`) | payment | payment marked reversed, posted debit, posted credit of type `payment reversal` with the same reference, balanced `payment_reversal` journal sourced to the payment, allocated invoices not `paid` | debit = credit = journal amount = payment amount; credit not earlier than debit | none | credit or journal missing or post-cutoff |
| Net-fee accounting (`:314-359`) | payment | payment, unique posted debit, balanced `bank_fee` journal(s) sourced to the debit | `delta > quantum` and `|delta - explained| <= tolerance` | none | as F14 |
| Accounted bank fee (`:239-258`) | bank transaction (fee row) | fee row, the one payment its reference resolves to, balanced `bank_fee` journal sourced to the fee row | journal amount = fee row amount | none | reference not a unique payment; journal post-cutoff |
| FX settlement (`:571-617`) | payment | payment and its unique posted debit | `payment_currency != bank currency` (detection only) | none | none; see below |

Boundary rule: failures use a strict test (`age > limit`, spec line 725), legitimate clearing uses the complement (`<= limit`). The two never both hold. In development data all 65 weekend and check clearing cases sit exactly at the limit, and one pending wire payment is exactly at its limit (section 7), so the boundary is exercised.

Notes. The `payment_reversal`, `bank_fee` and `fx_settlement` journal types are class-correlated ledger structure kept by the section 3.1 allowlist (L6, open residual). The contract uses `payment_reversal` and `bank_fee` as accounting records; it does not use `fx_settlement` or the `799100-FX-SETTLEMENT` account at all. The due-date contract does not check the approval timeline that upstream lists as required (`approval_events`): approval order violates upstream's own invariant checks in 90 cases (audit section 10, item 9). Intent ("scheduled") is not recorded for split, batch and due-date cases; legitimacy rests on arithmetic only.

### FX and out-of-scope behaviour

FX legitimate cases cannot be verified: the conversion rate exists only in the excluded `audit_log`, the FX journal posts a 1.00 placeholder, and there is no rate table (audit section 7; spec lines 1047 and 1114). `PLAN.md` section 4 requires abstention.

A **correct abstention** on a pair whose payment and bank currencies differ has all of:

- disposition `abstained` with reason `unsupported_fx` (not `answered`, not `legitimate`, not `F14`);
- cites the payment ID and the bank transaction ID;
- reports both amounts with their own currency, unconverted, and states that no conversion path exists in the allowed inputs;
- reports the missing information (documented rate or settlement statement);
- performs no cross-currency subtraction and states no shortfall.

Any `answered` diagnosis, including "legitimate" or "F14", on such a pair is a wrong answer. Grading is therefore on disposition and citations, not on a category. The 30 development FX cases are gradable only as abstentions, count as abstained and not as successes for the primary metric, and are reported as a separate slice (audit section 7; open question 4). The validation check only detects the currency mismatch; it does not evaluate any investigator output.

## 6. Policy parameters

| ID | Parameter | Declared value | Grounding |
|---|---|---|---|
| P1 | Clearing limit in business days | ACH 3, wire 1, check 10, virtual card 2 | `config.yaml:38-40` (ACH, wire, check); spec line 745 (virtual card, absent from config); generator `src/failures/base.py:236` and `src/generators/bank_generator.py:31`. Weekdays only, no holidays |
| P2 | Clearing grace | 0 business days; failure when `age > limit` | Spec line 745 and line 1032 |
| P3 | Fee tolerance | 0 beyond one currency minor unit (JPY 1, others 0.01); no percentage and no fee-size cap | Spec lines 832 and 859 (delta must equal the journal within the quantum); `src/utils/money.py:11`. Fee sizes seen (15 to 35) are not a parameter (L7) |
| P4 | Amount equality tolerance | one currency minor unit | Same as P3 |
| P5 | Cutoff | 2026-06-30, snapshot-only | `docs/data-audit.md` section 6; `config.yaml:6` |
| P6 | Window for the unreferenced-candidate search (A3) | none (any date up to the cutoff) | Chosen to avoid a tunable window; amount, currency and counterparty must all be equal. Declared, not exercised (zero hits) |
| P7 | Snapshot completeness | every allowlisted table is complete through the cutoff | Spec lines 19 and 1046; an assumption, not verified |

No parameter was fitted to labels. The values come from the upstream specification fixed before test evaluation, or are the strictest available choice (equality at the quantum, no window).

## 7. Validation results (train and validation only)

Command: `python3 -I evaluation/evidence_contract_check.py --upstream <checkout>`. The script reads `train/rca_ground_truth.jsonl`, `validation/rca_ground_truth.jsonl` and the raw `data/benchmark/full` tables. It keeps only allowlisted columns, drops 2 bank rows (`BT_0426166`, `BT_0426251`) and 5 GL sources with post-cutoff lines (it applies the cutoff logically, it does not remap). Two runs produced identical output.

"Gradable" means the contract's required records exist, its absence searches are clean and its calculations agree with the label. "Contradiction" means the records are complete but the calculation disagrees with the label. "Not gradable" means a required record or search scope is missing or ambiguous.

| Family or variant | Cases | Gradable | Not gradable | Contradiction |
|---|---:|---:|---:|---:|
| F06 | 76 | 75 | 1 | 0 |
| F12 | 71 | 71 | 0 | 0 |
| F14 | 76 | 76 | 0 | 0 |
| legitimate_accounted_bank_fee | 23 | 23 | 0 | 0 |
| legitimate_batch_payment | 26 | 26 | 0 | 0 |
| legitimate_check_clearing | 27 | 27 | 0 | 0 |
| legitimate_due_date_payment | 34 | 34 | 0 | 0 |
| legitimate_fx_settlement (as abstention) | 30 | 30 | 0 | 0 |
| legitimate_net_fee_accounting | 33 | 33 | 0 | 0 |
| legitimate_payment_reversal | 32 | 32 | 0 | 0 |
| legitimate_pending_settlement | 33 | 33 | 0 | 0 |
| legitimate_split_payment | 39 | 35 | 0 | 4 |
| legitimate_weekend_clearing | 38 | 38 | 0 | 0 |
| total | 538 | 533 | 1 | 4 |

By split: F06 train 57 of 58 gradable (the not-gradable case, RCA_000588, is in train) and validation 18 of 18; split payment train 31 of 35 gradable with all 4 contradictions in train, validation 4 of 4. The script prints the full per-split table.

Specificity of the failure predicates (cases per predicate that fire on a case's own records):

| Case group | F06 | F12 | F14 |
|---|---:|---:|---:|
| F06 (76) | 75 | 1 | 0 |
| F12 (71) | 0 | 71 | 0 |
| F14 (76) | 0 | 0 | 76 |
| any legitimate variant (462 cases) | 0 | 0 | 1 |

The one F06 case that also fires F12 is RCA_000512, whose third payment has no bank row (visible by design). The one legitimate case that fires F14 is RCA_002127 (section 8).

Clearing boundary: F12 age minus limit is at least 3 (maximum 369); pending is between -9 and 0, with one wire payment exactly at its limit; all 65 weekend and check clearing cases are at the limit exactly (lag minus limit 0). Unreferenced non-fee bank rows: 100, none matching any payment on currency, amount and counterparty.

Coverage of upstream evidence: for every gradable case outside FX, every non-event upstream evidence ID is in the contract's record set (0 uncovered of 503). The 30 FX cases are uncovered by design (they cite the fx journal and the audit event, which the contract does not use).

## 8. Cases not gradable or contradicted

| Case | Family or variant | Result | Reason |
|---|---|---|---|
| RCA_000588 | F06 | not gradable | Second check payment `PAY_0426188` is allocated but its debit `BT_0426251` posts 2026-07-01, after the cutoff. Allocation overage exists (10339.14) but is not committed in the observable snapshot. The published evidence set cannot be satisfied (audit section 8, defect 3) |
| RCA_001631 | split | contradiction | Leg `PAY_0423567` is `completed`/`failed` with a returned bank row; effective allocations are below the invoice total. Also evidence of F11 case RCA_001084 |
| RCA_001791 | split | contradiction | First leg `PAY_0422720` is `reversed` (case RCA_001545) |
| RCA_002223 | split | contradiction | Leg `PAY_0420267` is `failed`/`returned` (evidence of F11 case RCA_001007) |
| RCA_002127 | split | contradiction | Leg bank debits (2804.61, 1869.74) differ from the payments (2921.32, 1947.54) and post before the payments' dates. The same payments and banks are cited by F15 cases RCA_001410 and RCA_001464 (swapped references; I did not verify the swap) |

Handling of contradictions. A contradiction is a case where the observable records refute the upstream label. The investigator must answer from the records (its own calculation), not abstain, and the case must not count as an investigator failure. Proposed treatment: report contradicted cases as a separate list with their denominators, keep them out of the release-cohort denominator of the primary metric, and show the metric with and without them. This is a policy to be recorded (decision proposal in the build report), not something this document settles. The audit already notes 25 records shared between conflicting labels (section 10, item 6); this script checks only the payment-related variants.

## 9. Open questions

1. **Snapshot completeness.** Absence evidence is only as good as the declared assumption that every allowlisted table is complete through the cutoff (P7). A real export may be partial; the contract needs a completeness input (row counts or statement totals per account and period) before it is used outside FinRCA.
2. **Post-cutoff look-alikes.** A payment whose bank row posts after the cutoff is indistinguishable from F12 (audit section 6). The contract marks it not gradable only when the late row is visible in the raw data; the investigator cannot see it. RCA_000588 shows the cost in one development case; the number in holdout is unknown.
3. **Refinement after a result.** The F06 committed-versus-allocated rule (section 4.1) was changed after RCA_000512 failed the first version. Its effect is one case (74 plus 1 gradable, was 74). The challenge split must not be used to confirm it.
4. **FX in the release cohort.** Are the 47 FX legitimate cases in or out of the cohort denominator, and are they reported as an abstention slice (audit open question 4)?
5. **F06 bank amount.** The contract does not require the duplicate's bank debit to equal its payment. RCA_000578 shows a first-leg debit of 19366.98 against a payment of 8544.41. Should a debit amount mismatch downgrade a payment from "committed" to "ambiguous"?
6. **Fee journal link.** F14 and net-fee contracts look for fee journals sourced to the bank row only, as the generator does. Real ERPs often book fees against the payment or a batch. Needs the practitioner interviews and the shortlisted export formats.
7. **Reversal and fee journal types.** `payment_reversal`, `bank_fee` and `fx_settlement` are class-correlated ledger types (L6). The leakage test (M1 task 2) may force a coarser ledger representation, which would change the reversal and fee contracts.
8. **Approval timeline.** The due-date variant's upstream evidence tables include `approval_events`, which this contract does not use (section 5).
9. **Holiday calendar.** Weekdays only; a bank holiday inside a clearing window changes age by one day. All 65 weekend and check clearing cases are at the limit exactly, so a holiday calendar would turn several of them into contradictions.
10. **Contracts for the other 6 legitimate and 12 failure families** are not defined here (`SCOPE.md`).
