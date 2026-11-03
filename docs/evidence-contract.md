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
2. **Exact money.** `Decimal` amounts with an explicit currency. Compare only within one currency. A difference is `ERP amount minus bank amount` (positive means the bank is short). Equality tolerance is one minor unit of the currency (JPY 1, others 0.01; `src/utils/money.py:11`), not a percentage. Journal amounts carry their own currency: a journal is compared only with an amount in the same currency, and a journal whose lines mix currencies is in no currency. Any comparison across currencies (invoice, payment, bank row, journal) is not gradable (checker reason `cross_currency_unsupported`), except the FX variant, which is a detection of the mismatch (section 5).
3. **Absence is a bounded search, not a missing ID.** An absence claim cites a search record: table, key, predicate, cutoff and the observed count (0). It is evidence only when the search scope is stated, the snapshot is declared complete for that table, and re-running the query on the snapshot returns the stated count. The snapshot-completeness assumption is declared, not proven (spec lines 19 and 1046; open question 1).
4. **Distinct payments stay distinct.** No de-duplication by amount or date (`PLAN.md` section 4; audit section 10, item 3). Two equal-amount payments are a duplicate only if cumulative committed allocations exceed the invoice.
5. **Missing counterparts remain visible.** A payment without a bank row is a finding in every packet that touches it, even when the primary diagnosis is another class (RCA_000512, section 4.1).
6. **Bank-fee rows are not settlement evidence.** Rows with `transaction_type = "bank fee"` that carry a payment reference are fees, not the payment's cash movement (audit RCA_001156, spec line 725). They are used only by the fee contracts.
7. **Unsupported conclusions are abstentions.** Cross-currency comparisons, missing rows, shared references and post-cutoff rows give `abstained` or `not gradable`, never `legitimate` (`PLAN.md` section 1 and 4; `AGENTS.md` section 2).
8. **Root-cause narratives are not graded.** F06 "later run did not recognise the prior settlement", the three F12 sub-causes and the F14 "processor netted a fee" are unobservable once `audit_log` is dropped (audit section 10, item 1). Only the observable condition is graded.
9. **Parameters are declared, not tuned.** Section 6 lists every parameter with its source; none was fitted to labels.
10. **No invoice, no approval or ledger posting, no all-clear; every all-clear states its scope.** Added in `evidence-contract-0.2` and extended in `-0.3` after the first development run (`docs/dev-results.md`, run 1) answered "legitimate" for payments that had no invoice and for failures of kinds this tool does not check. A `legitimate_*` payment variant or `no_exception` needs at least one allocation of the payment to an invoice in the payment's currency, cited in the packet (section 5). Every answered `legitimate_*` or `no_exception` packet carries the scope statement `SCOPE_LIMITATION` (`src/reconcile/diagnosis/policy.py`, versioned with the policy): what was checked (settlement and bank match, amounts, allocations and invoice totals, duplicates, cutoff, invoice approval effective on the payment date, payment journal equal to the payment) and what was not (approval authority limits and segregation of duties, other GL postings such as accounting period and accounts, vendor master data, PO matching). From `evidence-contract-0.3` every all-clear also passes prerequisites of section 5: an effective final approval and a matching payment journal; from `-0.4` also the invoice-total check on every path. Without them the case abstains; it is never called legitimate.

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
| Balanced journal of type T sourced to R | a `journal_id` that has an observable GL line with `transaction_type = T` and `source_transaction_id = R`, taken **complete** (every observable line of that `journal_id`, whatever its source or type). Two separate conditions, both required: (a) the complete journal is in one currency with total debit equal to total credit and greater than zero; (b) its *attributable lines*, those with `transaction_type = T` and `source_transaction_id = R`, are themselves balanced (debit equal to credit, greater than zero). The journal's **amount** is the total debit of the attributable lines; the whole journal's total is recorded beside it (`journal_total`) but is never compared with R. An unrelated line can therefore leave (a) unbalanced (abstain), or sit in the journal as a balanced pair without changing the amount; it can never make up a shortfall in the amount attributable to R (`evidence-contract-0.5`; in `-0.4` the whole total was compared, which let an unrelated EUR 20 pair hide a EUR 20 shortfall in a EUR 100 payment) |
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

Not gradable: `allocated - invoice_total` exceeds the quantum but `committed_overpayment` does not (the proof that the extra payment left the bank is missing or not yet posted at the cutoff). RCA_000588 is this case: the second check's debit `BT_0426251` posts 2026-07-01. Also not gradable: allocations or an allocating payment missing from the snapshot; and an effective same-currency allocating payment that has a posted debit by a reference that another payment also uses (the debit cannot be attributed to one payment, section 2 principle 7; `reference_shared_by_payments`).

Contradiction (label says F06, records do not): `allocated` does not exceed the invoice total by more than the quantum.

Disclosure. The first version of this rule required every effective allocating payment to have a debit and marked RCA_000512 not gradable (its third, instalment payment `PAY_0421840` has no bank row). RCA_000512 has a committed overpayment of 1254.13 EUR from the two payments that do have debits, so the rule was changed to "committed versus allocated" and the bank-less payment is reported as a visible separate finding. That change was made after seeing the development result for one case.

Ambiguities. Instalments, prepayment, on-account credit and intentional overpayment cannot be told apart from a duplicate except by allocation arithmetic (audit section 10, items 2 and 3). The data has no repeated-ingestion examples, so "same payment ingested twice" is not exercised. RCA_000578's original payment has a bank debit (19366.98) different from its payment amount (8544.41); the contract as first written checked that a posted debit exists, not that its amount equals the payment (open question 5). See the next note.

Commitment needs a supporting debit (2026-11-03). An allocation counts in `committed` only when its payment's posted debit equals the payment amount within one quantum. Otherwise a payment allocated 100.00 whose only debit is 0.01 would count as 100.00 paid out, and two such payments could raise `committed_overpayment` that the bank never saw. Where an over-allocated invoice has such a payment among those with a debit, the investigator abstains `debit_differs_from_payment` (the reason the legitimate variants already use) and no F06 is answered. The abstention records the difference as the calculation `debit_minus_payment:<payment>`; an answered F06 enforces the same check without recording a calculation, so its packet is unchanged (the debit and payment are cited records). Effect on FinRCA: no outcome changed on train (0 of 1192) or validation (0 of 416), measured by the parent. `POLICY_VERSION` is unchanged because no policy value changes.

### 4.2 F12: ERP payment missing from bank (primary entity: payment)

Grounding. Taxonomy lines 148-159; generator `src/failures/f12_erp_payment_missing_bank.py:15-22` (a settled payment with exactly one ordinary, non-fee bank row), `:34` (the bank row is deleted; payment, allocations and GL are left), `:35-36` (audit event, not observable here). Spec lines 712-749 (completed/settled payment, no non-fee bank row carrying the reference posted or returned by the cutoff, business-day age strictly greater than the method limit; a reference shared by several payments gives "insufficient"). Hard negative: submitted payment one day before the cutoff (taxonomy line 158).

Required records:

| Record | Table and relationship | Columns |
|---|---|---|
| The payment | `payments` by primary ID | method, date, currency, amount, status columns, `reference_number`, `bank_account_id` |
| Its allocation(s) | `payment_allocations` by `payment_id` | at least one row |
| Its payment journal | `gl_entries` where `source_transaction_id` = payment and `transaction_type = payment`, observable | a balanced journal |

Calculations: business-day age from `payment_date` to the cutoff; compare `age > limit(method)` (strict).

Absence evidence (each cited as a search record; A1 = 0, A2 = 1, A3 = 0):

- A1, reference search: observable `bank_transactions` rows with `payment_reference` = the payment reference and any type except `bank fee`, any status, any direction. Count must be 0. Fee rows are listed but do not count.
- A2, reference uniqueness: `payments` rows with this `reference_number`. Count must be 1.
- A3, unreferenced candidate search: observable non-fee debit rows whose `payment_reference` matches no payment, with equal currency, equal amount and `counterparty_token` equal to the payment's `bank_account_id`. Count must be 0 (otherwise the missing row may be a mis-referenced one, which this contract does not decide). No date window is used, so no extra parameter exists.

Evidence-complete: the payment, allocation(s), payment journal and the three search records are cited, with the age and limit calculation, and `age > limit`.

Not gradable: A2 not equal to 1 (reference shared) or A3 not empty; a bank row for the reference exists only after the cutoff (it is indistinguishable from a missing row: audit section 6); no allocation or payment journal observable.

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

Absence evidence: the search record "observable `gl_entries` with `transaction_type = bank_fee` and `source_transaction_id` = this bank transaction: count 0" (so `explained = 0`). The search counts raw GL rows, so a fee-typed journal that is unbalanced or has zero total is not absent: the case is not gradable (`invalid_fee_journal`). The same holds for a malformed `payment_reversal` journal (`invalid_reversal_journal`) and for the fee journal of an accounted bank fee.

Evidence-complete: payment, bank row, the delta calculation and the GL absence search record. The fee size (15 to 35) is not evidence either way (L7).

Not gradable: reference not unique; no or several posted debits for the reference; bank amount above the ERP amount (a different condition, not covered); bank row posted after the cutoff; the bank row or a fee journal in a currency other than the payment's; a partial fee journal (below).

Partial fee journal rule (decision recorded in the build report). The absence search above must return count 0 for an "unexplained shortfall" to be evidence-complete. If any balanced `bank_fee` journal is sourced to the bank row but `|delta - explained|` still exceeds the quantum (for example a 25 shortfall with a 10 fee journal), the search count is not 0 and the diagnosis cannot cite it, so the result is not gradable (`partial_fee_journal`), not F14 and not legitimate. If the journals explain the whole difference it stays a contradiction of the F14 label.

Contradiction: `delta <= quantum` (no mismatch) or a balanced fee journal explains the difference.

Ambiguities. The journal link is to the bank row, as in the generator. A fee booked against the payment ID would not be found (declared limitation, not exercised by the data). The processor's reason for the shortfall is unobservable. Cross-currency F14 does not occur in development data (audit section 7); a cross-currency pair is handled by section 7.

## 5. Legitimate variants and abstention

Each contract has the same shape: required records, calculations, evidence-complete definition. A legitimate conclusion is a supported financial conclusion only when its contract holds; otherwise the answer is an abstention (`PLAN.md` section 4).

| Variant (upstream) | Primary entity | Required records | Calculations that must hold | Absence evidence | Not gradable |
|---|---|---|---|---|---|
| Pending settlement (`hard_negatives.py:284-312`) | payment | payment (status not final or inside window), an allocation to an invoice in the payment's currency, payment journal | `age <= limit(method)` | A1 = 0, A2 = 1, A3 = 0 (as F12) | A2 not 1 or A3 not empty; bank row post-cutoff |
| Weekend clearing (`:165-184`) | payment (ACH) | payment, unique posted debit, an allocation to an invoice in the payment's currency | amounts equal; `bdays(payment_date, posted_date) <= 3` | none | no posted debit; reference shared |
| Check clearing (`:222-237`) | payment (check) | payment, unique posted debit, an allocation to an invoice in the payment's currency | amounts equal; `bdays(payment_date, posted_date) <= 10` | none | as above |
| Due-date payment (`:260-282`) | invoice | invoice, allocations, effective payments, their posted debits | some payment has `payment_date = due_date`; each debit equals its payment amount; `sum(allocations) = invoice_total` (quantum). Full allocation is required: a partially allocated invoice is not "paid", so a legitimate conclusion lacks complete support, and partial payments are `out_of_scope` pending the user's decision (A041) | none | a payment lacks a debit |
| Split payment (`:143-163`) | invoice | invoice, two or more allocations, effective payments, a posted debit per payment | `sum(allocations) = invoice_total` (quantum); each debit equals its payment amount (within one quantum, P4) | none | a leg lacks a debit |
| Batch payment (`:121-141`) | payment | payment, two or more allocations, invoices, posted debit | `sum(allocations) = payment_amount = bank amount`; no invoice is over-allocated | none | no debit |
| Payment reversal (`:504-569`) | payment | payment marked reversed, posted debit, posted credit of type `payment reversal` with the same reference, balanced `payment_reversal` journal sourced to the payment, allocated invoices not `paid` | debit = credit = journal amount = payment amount; credit not earlier than debit | none | credit or journal missing or post-cutoff |
| Net-fee accounting (`:314-359`) | payment | payment, unique posted debit, balanced `bank_fee` journal(s) sourced to the debit, an allocation to an invoice in the payment's currency | `delta > quantum` and `|delta - explained| <= tolerance` | none | as F14 |
| Accounted bank fee (`:239-258`) | bank transaction (fee row) | fee row, the one payment its reference resolves to, balanced `bank_fee` journal sourced to the fee row, an allocation of that payment to an invoice in its currency; fee row, payment and journal in one currency | journal amount = fee row amount | none | reference not a unique payment; journal post-cutoff |
| FX settlement (`:571-617`) | payment | payment and its unique posted debit | `payment_currency != bank currency` (detection only) | none | none; see below |

**Allocation requirement and scope statement (`evidence-contract-0.2`).** Every payment variant needs an allocation of the payment to an invoice in the payment's currency, and the allocations are cited (batch, reversal and `no_exception` required them from the start; weekend clearing, check clearing, net-fee accounting and accounted bank fee from `-0.2`; pending settlement from `-0.4`, where an allocation only to an invoice in another currency abstains `unsupported_fx`). A completed, settled payment with no allocation is never answered legitimate or `no_exception`: the packet abstains with `payment_without_invoice_allocation` and a supported finding of that name, citing the payment and a bounded absence search on `payment_allocations` (count 0), so the analyst sees the missing allocation. If every allocation is to an invoice in another currency the reason is `unsupported_fx`. The invoice-primary variants (split, due-date) need effective allocated payments by construction. Every answered `legitimate_*` and `no_exception` packet carries the scope statement of principle 10 in `limitations`.

**All-clear prerequisites (`evidence-contract-0.5`).** Before any answered `legitimate_*` or `no_exception` packet (payment-, bank-row- and invoice-primary; for an invoice, each effective allocated payment), three generic finance rules (four checks) are checked from allowlisted tables, on every path to an all-clear (one shared step, not a check per variant). They run after every other check, so earlier abstention reasons keep their precedence. Within the guard the order is allocations (invoice totals, then payment total), approval, GL posting. The guard findings are `invoice_totals_not_exceeded`, `payment_not_over_allocated`, `final_approval_before_payment` and `payment_journal_matches_payment`.

1. *Allocations: invoice totals, duplicates and payment total.* (i) For each invoice the payment is allocated to, the open balance `invoice_total - sum(effective allocations to it)` is not below `-quantum` (calculation `invoice_open_balance:<invoice>`, citing the invoice and every effective allocation to it); all in the payment's currency. Two effective payments that together overpay one invoice therefore never give a clearing, net-fee, fee, pending, batch, reversal or `no_exception` all-clear: abstain `invoice_over_allocated` (`unsupported_fx` or `required_records_missing` if the set cannot be assessed). This is the "invoice totals, duplicates" part of the scope statement. (ii) The payment's own allocations (all of them, to every invoice) sum to no more than `payment_amount` plus one quantum (calculation `payment_minus_allocations:<payment>`, citing the payment and each allocation; finding `payment_not_over_allocated`). Each invoice may be fine and the payment still over-allocated (EUR 100 allocated 100 to invoice A and 5 to B); this abstains `payment_over_allocated` with a supported finding of that name. An allocation total below the payment is not an over-allocation (a partial allocation is the batch variant's concern, `allocations_differ_from_payment`).
2. *Approval, effective at the payment date.* Each invoice the payment is allocated to has an approval that is effective on `payment_date`. Take the invoice's `approval_events` dated (`event_timestamp`, date part) on or before `payment_date`. A *final approval* is an event with `action` `approved` or `auto_approved` and `new_status` `approved` (the workflow's terminal state, not an intermediate `pending_level_N`) at the highest `approval_level` among those events. The approval is effective if the latest of those events (by `event_timestamp`) is a final approval, so a later rejection, cancellation, reopening or re-submission on or before the payment date revokes it, and a revocation after the payment date does not matter. Events that share the latest timestamp have no order (the opaque `approval_event_id` is not an order and is never used), so the approval is effective only if every one of them is a final approval. No final approval at all on or before the payment date: abstain `invoice_approval_not_completed`, citing the payment, those events, and a count-0 search whose predicate is exactly the test applied (invoice, `action`, `new_status`, `CAST(approval_level AS INTEGER) >= <highest level>`, event date on or before `payment_date`); the search can be re-run to give the stated count. A final approval exists only after the payment date: abstain `payment_before_approval`, citing the earliest such event. A final approval exists on or before the payment date but the latest event is not one (revoked): abstain `invoice_approval_not_effective`, citing the last final approval and the latest event(s). A paid invoice with no approval events at all (for example upstream's inserted F01 and credit-memo invoices, leakage residual R1) therefore abstains; that is intended, the tool cannot see its approval.
3. *GL posting.* The payment has at least one payment journal (`gl_entries`, `transaction_type = 'payment'`, sourced to the payment, dated by the cutoff), each complete journal (section 3, condition (a)) balanced and in the payment's currency, its payment-sourced lines balanced (condition (b)), and `payment_amount - sum(journal amounts)` within one quantum, where a journal amount is the total of its payment-sourced lines only. The calculation operands are those attributable totals, as recorded in the cited journal item. Missing: `payment_journal_missing` (with a count-0 search); unbalanced (whole journal or payment-sourced lines) or mixed currency: `payment_journal_unbalanced`; another currency than the payment: `payment_journal_currency_differs`; amount differs: `payment_journal_amount_differs`, citing the calculation `payment_minus_payment_journals`. Pending settlement, batch, reversal and the other variants that already needed the payment journal reuse this one rule.

Evidence identity. A journal item's id is `journal:<journal_id>:<source>:<gl_type>` and its values record `source` and `gl_type`, because its `amount` depends on that attribution: payments sharing one journal each get their own item (before, the first citation's amount was reused for the second payment). The ledger raises if an evidence id is re-recorded with different values or timestamps; re-citing the same record with identical values is allowed and keeps the first relationship text, which is a label, not a fact.

Every absence search a packet emits states the predicate the code tested, so that re-running it on the snapshot reproduces the stated count (principle 3); `tests/unit/test_investigator.py` re-runs every cited search on every fixture and on mutated approval cases.

The rules say nothing about approval authority (is the approver allowed for the amount) or about accounting period and accounts; the scope statement says so. They do not diagnose approval-workflow or GL-posting failures as categories; they only keep the tool from calling such a payment clean.

Overlap. When more than one payment variant's contract holds on the same records, the batch variant still wins (section 5.1) and the packet lists every other variant that holds in `alternatives`: batch lists check clearing (method `check`) or weekend clearing (ACH across a weekend); split lists due-date. Which variant wins does not change.

Boundary rule: failures use a strict test (`age > limit`, spec line 725), legitimate clearing uses the complement (`<= limit`). The two never both hold. In development data all 65 weekend and check clearing cases sit exactly at the limit, and one pending wire payment is exactly at its limit (section 7), so the boundary is exercised.

Currency. Every variant above compares amounts only within one currency; a payment, bank row, invoice or journal in another currency makes the case not gradable, never a contradiction or a pass. A reversal credit must be `direction = credit` and `status = posted`.

Notes. The `payment_reversal`, `bank_fee` and `fx_settlement` journal types are class-correlated ledger structure kept by the section 3.1 allowlist (L6, open residual). The contract uses `payment_reversal` and `bank_fee` as accounting records; it does not use `fx_settlement` or the `799100-FX-SETTLEMENT` account at all. The due-date contract does not check the approval timeline that upstream lists as required (`approval_events`): approval order violates upstream's own invariant checks in 90 cases (audit section 10, item 9). Intent ("scheduled") is not recorded for split, batch and due-date cases; legitimacy rests on arithmetic only.

### 5.1 No exception (answered)

Added after the M1 gate (user decision, 2026-11-01; supersedes the provisional `out_of_scope` of A041). A **payment-primary** request whose records show no exception is answered with category `no_exception`. It is not a clean bill: it says that none of the checks in this contract fires, nothing more. It needs all of:

- the payment, `completed` and `settled`, with a unique reference (A2 count 1) and exactly one posted principal debit in the payment's currency;
- `|payment_amount - debit| <= quantum` (calculation `delta`);
- `bdays(payment_date, posted_date) <= clearing limit` of the method (a day count, section 5.3), and the debit not dated before the payment;
- at least one allocation of the payment, and for every allocated invoice the open balance `invoice_total - sum(effective allocations to it)` is not below `-quantum` (calculation `invoice_open_balance`, citing the invoice and every effective allocation to it). A partial payment leaves a positive balance and is a no exception; an invoice over-allocated by all effective payments is not (abstain `invoice_over_allocated`);
- equal-looking payments (same vendor, amount, date, currency) are cited as `equal_twin` records and stay distinct payments; they are never merged or counted as a duplicate (principle 4).

Named variants take precedence when they apply to the same records: two or more invoices allocated is the batch variant; method `check` is check clearing; method `ACH` whose clearing interval contains a Saturday or Sunday is weekend clearing. Every other matched payment within its limit is `no_exception`. A matched debit posted after the limit abstains (`clearing_lag_exceeds_limit`): it is not a failure class and not clean. This split between the named variants and `no_exception` is a design choice, not a contract finding: the original table tests only `lag <= limit`, which would also describe every ordinary ACH payment as weekend clearing. Not gradable (abstain): no allocation to an invoice in the payment's currency (`payment_without_invoice_allocation`, replacing `required_records_missing` for this case in `evidence-contract-0.2`), invoice incomplete (`required_records_missing`), several debits or a shared reference (`ambiguous_reference`), cross-currency (`unsupported_fx`).

### 5.2 Known contract gaps: detected, abstain

The shapes below would be answered wrongly if only sections 4 and 5 were applied. The investigator detects them and abstains with its own reason until practitioner input refines them (user decision, 2026-11-01). Cited evidence includes the payments and the bank row involved and the arithmetic that triggered the detection.

| Reason | Detection (after A1 = 0, A2 = 1, records present, A3 = 0) | Wrong answer avoided |
|---|---|---|
| `bank_batch_grouping_unmodelled` | an unreferenced debit of the same currency and counterparty, inside the clearing window (P8), equals the sum of this payment and one or more other effective payments of that counterparty that have no bank row | F12 for each member |
| `reference_unmatched_nearby_debit` | such a debit exists but equals no sum; the difference to the payment amount is reported | F12 where a netted or truncated settlement exists |
| `reference_unmatched_candidate_bank_row` | A3 is not empty (equal amount, currency, counterparty; section 4.2) | F12 |
| `reversal_followed_by_repayment` | the reversal's invoice is `paid` and another effective payment is allocated to it | calling the reversal confirmed, or a duplicate |
| `reversal_invoice_still_paid` | the reversal's invoice is `paid` with no other effective allocation | confirming the reversal |
| `batch_search_limit_exceeded` | more than 12 unbanked payments to check for a batch | a silent partial search |
| `invoice_approval_not_completed`, `payment_before_approval`, `invoice_approval_not_effective` | an otherwise answered all-clear whose allocated invoice has no final approval on or before the payment date, only one after it, or one revoked by a later event on or before the payment date (section 5) | an all-clear on a payment released without a valid approval |
| `payment_over_allocated` | an otherwise answered all-clear whose payment is allocated for more than its amount across invoices (section 5) | an all-clear that funds invoices with value the payment lacks |
| `invoice_over_allocated` | an otherwise answered all-clear whose allocated invoice is over-allocated by the effective payments, on any path (section 5) | an all-clear on a duplicate or overpaid payment |
| `payment_journal_missing`, `payment_journal_unbalanced`, `payment_journal_currency_differs`, `payment_journal_amount_differs` | an otherwise answered all-clear whose payment journal is absent, unbalanced, in another currency or not equal to the payment (section 5) | an all-clear on a payment the ledger records wrongly |
| `payment_without_invoice_allocation` | a completed, settled payment that is otherwise a legitimate variant or `no_exception` (matched debit, within limit, fee journal where required) but has no allocation to an invoice | an all-clear on a payment without a valid invoice (upstream F05) |

Missing values (2026-11-03). Real exports lack values FinRCA always has. Two rules apply only when a value is absent, so data without NULLs behaves as before. (1) A value the check needs that is NULL abstains `required_field_missing`, naming the column; it is not `failed malformed_record`, because the record is well formed and the value is simply not in the data. (2) The A3 search (section 4.2) needs the counterparty on both sides. If the payment has no `bank_account_id`, or any unreferenced non-fee debit of the payment's currency at or before the cutoff has no `counterparty_token`, the investigator abstains `counterparty_unavailable` before F12, the pending all-clear and the batch and nearby-debit checks, and cites no A3 search, because none ran. Reference-matched paths (for example F06) do not use A3 and are unaffected. A prepared snapshot's manifest may also declare `tables_not_supplied` and `bank_data_through`; when present, the packet carries a limitation for each. `POLICY_VERSION` is unchanged: neither rule changes a policy value or any outcome on data without NULLs.

Fee journals recognised (2026-11-03; changed the same day). F14 is "no balanced `bank_fee` journal sourced to the debit". A source that cannot type fee journals as `bank_fee` (a Xero export) makes that absence meaningless, so a prepared operational snapshot's manifest records `fee_journals_recognised`. It holds the preparer's **declaration** (`fee_journals_typed`): true means every bank-fee journal in the GL file is mapped to `bank_fee` and sourced to its bank row. It is no longer inferred from whether any row is typed `bank_fee`: one unrelated typed journal said nothing about the journal of the debit under investigation, which could be typed as something else and made F14 answer wrongly. The Xero path always declares false. When the manifest says `false` and the finished packet is answered `F14`, the investigator abstains `fee_journals_not_recognised` instead (missing information: journals recognisable as bank fees, so a fee can be ruled out). The evidence, calculations, day counts and limitations stay; the `unexplained_shortfall` finding and its recommendation are dropped because neither is established. A manifest without the key (FinRCA) is unchanged, and the net-fee all-clear needs a `bank_fee` journal anyway, so no other outcome moves. `POLICY_VERSION` is unchanged for that reason.

Cutoff, coverage and other declarations (2026-11-03). (1) A request's cutoff must equal the `cutoff` in the snapshot's manifest, else the packet is `failed cutoff_differs_from_snapshot` naming both dates: some queries (reference lookups) have no date bound, so a snapshot prepared for another cutoff would answer from a different cut of the data; investigating an earlier date means preparing the data again. A manifest without the key (older data) is not checked. (2) A prepared operational snapshot's manifest records `bank_data_from` and `bank_data_through` and `all_paying_accounts_covered` (a declaration that must be true). When `bank_data_from` is present and the finished packet is answered `F12` or `legitimate_pending_settlement`, and any cited payment is dated before it, the investigator abstains `payment_before_bank_coverage` (missing information: bank statements covering the payment date), keeping evidence and calculations and dropping the finding and recommendations: those conclusions rest on finding no bank row, which proves nothing before the bank data begins. The coverage note reads "Bank data was declared complete from X through Y, for every account payments are made from, by whoever prepared the data." (3) `reference_matching` (`exact`, trim only; or `normalised`, trim, collapse whitespace, uppercase) is the preparer's recorded choice; account columns are always normalised. (4) The reversal all-clear needs the invoice no longer `paid`; an invoice with no status abstains `required_field_missing` (it once read as "not paid"). (5) A fee journal in another currency than its payment or fee row abstains `unsupported_fx` before any arithmetic. `POLICY_VERSION` is unchanged: no policy value moves, and data prepared as FinRCA has none of these keys or NULLs.

F14 and net fee: a `bank_fee` journal sourced to the debit that is unbalanced or mixed-currency abstains (`fee_journal_invalid`); a balanced journal that does not equal the shortfall within one quantum (partial or oversized) abstains (`fee_journal_does_not_match_difference`). Only a zero journal count (G1 search) gives F14, and only a journal within the quantum gives net-fee accounting. A bank amount above the payment abstains (`bank_amount_above_payment`). A bank row that is not a posted debit abstains (`bank_row_not_a_posted_debit`). An accounted bank fee whose fee row is in another currency than its payment abstains `unsupported_fx`.

### 5.3 Business-day counts in the packet

`Calculation` is money-only. A business-day age or lag is a `DayCount` in the packet: it cites the start (an evidence record's timestamp field) and the end (another record's timestamp, or the request cutoff), carries the declared limit and the policy name it came from (`clearing_limit_ach`, ...), and the packet recomputes the count from the cited dates (Monday to Friday in the half-open interval, section 3). The comparison with the limit is a finding that cites the count. Chosen over a typed finding with only the dates cited, because the number can then be verified from the packet alone.

**One definition of a business day count.** `reconcile.domain.calendar.business_days(start, end)` counts the weekdays in `(start, end]`: the start day never counts, the end day counts if it is a weekday. A payment dated on day *s* cannot clear before the next business day, so the clearing limits (P1) are counted in days after the payment date, as upstream adds days (`src/utils/dates.py:24-32`). Boundary cases pinned by `tests/unit/test_day_count.py`: a Saturday or Sunday start counts the following weekdays only (Saturday to Monday is 1), a weekend end adds nothing (Friday to Sunday is 0), the same day is 0. The observable baseline must call this function rather than define its own count.

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
| P8 | Window for a "nearby" unreferenced debit (section 5.2) | posted on or after the payment date and within the method's clearing limit in business days | Reuses P1; no new number. A3 itself keeps no window (P6) |
| P9 | Policy version | `evidence-contract-0.5` (0.5: payment-attributable journal amount kept apart from whole-journal balance, payment-level over-allocation; 0.4: invoice totals on every all-clear path, approval effective at the payment date, complete journals, fee currency, pending allocation currency; 0.3 added the approval and journal prerequisites; 0.2 the allocation requirement and scope statement; 0.1 before); every packet records the version applied, the four clearing limits and the tolerance | `src/reconcile/diagnosis/policy.py`; values are P1 to P4 |

No parameter was fitted to labels. The values come from the upstream specification fixed before test evaluation, or are the strictest available choice (equality at the quantum, no window).

## 7. Validation results (train and validation only)

Command: `python3 -I evaluation/evidence_contract_check.py --upstream <checkout>`. The script reads `train/rca_ground_truth.jsonl`, `validation/rca_ground_truth.jsonl` and the raw `data/benchmark/full` tables. It keeps only allowlisted columns, drops 2 bank rows (`BT_0426166`, `BT_0426251`) and 5 GL sources with post-cutoff lines (it applies the cutoff logically, it does not remap). Two runs produced identical output. The cohort is 538 cases: 76 F06, 71 F12, 76 F14 and 315 cases in the ten legitimate variants (23+26+27+34+30+33+32+33+39+38). After the round-1 review fixes (shared references in F06, currency checks, posted-credit reversal, partial fee journals) the output is byte-identical to the earlier run, so none of the numbers below changed. The checker's predicates are pinned to the public fixtures by `tests/unit/test_evidence_contract_check.py`.

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
| any of the ten legitimate variants (315 cases) | 0 | 0 | 1 |

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

Handling of contradictions. A contradiction is a case where the observable records refute the upstream label. The investigator must answer from the records (its own calculation), not abstain, and the case must not count as an investigator failure. Proposed treatment: report contradicted cases as a separate list with their denominators, keep them out of the release-cohort denominator of the primary metric, and show the metric with and without them. This is a policy to be recorded (decision proposal in the build report), not something this document settles. The audit already notes 25 records shared between conflicting labels (section 10, item 6); this script checks only the payment-related variants. A second kind of contradiction is a shared record: a legitimate-labelled case whose payment or invoice is also named or reached (through allocations) by a case of a failure family the investigator checks (F05, F06, F08, F09, F12, F14, F15) cannot be right on both labels (for example the split cases RCA_001679, RCA_001759, RCA_002175 and the due-date case RCA_002212 against the F09 cases RCA_000857, RCA_000874, RCA_000889 and RCA_000832, and RCA_002182 against the F08 case RCA_000722); `evaluation/dev_run.py` flags these generically, keeps them out of the scored cohort and shows the metric both ways (`docs/dev-results.md`, run 3, label conflicts).

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
