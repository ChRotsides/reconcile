# Public scenario fixtures

Small scenarios invented for this project (decision D030: no upstream FinRCA row is copied). Each directory is one scenario in the FinRCA `full/` table layout: every table that `reconcile.adapters.finrca.TABLES` reads is present, with only the allowlisted columns, so it goes through `prepare_finrca_snapshot` unchanged. Cutoff for all scenarios: 2026-06-30. All amounts are invented. Vendor `V_1`, our bank account `ACC_1`.

`expected.json` plays the label role. It is the only place that holds expected outcomes; scenario directories contain data only (`tests/unit/test_fixtures.py` checks this). An investigator must never read it. Per scenario it gives:

- `primary_entity`: table and raw fixture ID of the request's entity;
- `disposition`, and `category` (answered) or `abstention_reason` (abstained);
- `evidence`: required records by raw fixture ID. Allocation IDs are `payment_id|invoice_id`; journals are cited by `journal_id`;
- `calculations`, `absence_searches` (with the observed count), `must_not` (conclusions that would be wrong);
- `quality_gaps`: the exact non-zero relationship gaps `quality_report` must show; `dropped_by_cutoff`; `snapshot_rows` (rows that must survive preparation);
- `known_contract_gap`: set only where the current contract would answer wrongly. The contract is not changed.

Every fixture that ends in an all-clear (`legitimate_*` or `no_exception`) carries an approval that is effective on the payment date for each allocated invoice, no over-allocated invoice, and a balanced payment journal equal to the payment (the prerequisites of section 5, `evidence-contract-0.5`). Section numbers refer to `docs/evidence-contract.md`. Clearing days use Monday to Friday only (June 2026 starts on a Monday).

## Contract scenarios

| Directory | Tests | Rule |
|---|---|---|
| `f06_double_payment` | Two effective payments each allocate the full invoice and both debits posted: committed overpayment 1000.00 | 4.1 |
| `f06_second_debit_post_cutoff` | Second payment's debit posts 2026-07-01, so it is not in the snapshot: allocated overage but nothing proven committed. Abstain | 4.1 not gradable |
| `f12_payment_missing_from_bank` | Settled ACH payment, no bank row, age far above 3 business days, A1 to A3 empty | 4.2 |
| `f14_amount_shortfall` | One posted debit 25.00 below the payment, no `bank_fee` journal | 4.3 |
| `pending_settlement` | Payment 1 business day old, settlement `pending`, no bank row, journal and allocation present | 5 pending settlement |
| `weekend_clearing` | ACH paid Friday, debit posts Wednesday: lag exactly 3 | 5 weekend clearing |
| `check_clearing` | Check paid Monday, debit posts 10 business days later: lag exactly 10 | 5 check clearing |
| `due_date_payment` | Payment dated on the invoice due date, debit equals payment | 5 due-date |
| `split_payment` | Two payments, allocations sum to the invoice total, one debit each | 5 split |
| `batch_payment` | One payment allocated to two invoices, allocations = payment = debit | 5 batch |
| `payment_reversal` | Reversed payment, posted debit, matching `payment reversal` credit, `payment_reversal` journal, invoice not paid | 5 reversal |
| `net_fee_accounting` | Debit 20.00 below payment, explained by a balanced `bank_fee` journal sourced to the debit | 5 net fee |
| `accounted_bank_fee` | Primary entity is a `bank fee` row whose reference resolves to one payment, with a balanced fee journal | 5 accounted fee |
| `fx_settlement` | EUR payment, USD debit. Abstain `unsupported_fx`; no conversion or shortfall | 5 FX |

## Insufficient evidence (abstain)

| Directory | Tests | Rule |
|---|---|---|
| `insufficient_primary_entity_missing` | The primary bank transaction posts 2026-07-01 and is absent from the snapshot; its payment looks like F12 but must not be diagnosed from the missing row | 2 principle 1 |
| `insufficient_shared_reference` | Two payments share one reference (A2 count 2); no bank row | 4.2 not gradable |
| `insufficient_in_window_missing_records` | F12-looking payment still inside its window (age 1 of 3), with no allocation and no payment journal. The contract calls an in-window payment with all records `legitimate_pending_settlement` (see `pending_settlement`), so the abstention here comes from the missing required records, not from the window alone | 4.2 and 5 pending |

## Realistic-shape scenarios

| Directory | Tests | Rule |
|---|---|---|
| `real_identical_distinct_payments` | Two payments with the same amount, date and vendor, on two invoices, each with its own debit. Both must survive preparation; no duplicate finding. Answered `no_exception` with the twin cited and kept distinct | 5.1; 2 principle 4 |
| `real_bank_batch_debit` | One 1500.00 debit settles three payments (400, 600, 500) and carries a reference no payment has. **Current contract answers F12 for all three, wrongly**, because it has no batch concept. Target: abstain `bank_batch_grouping_unmodelled` | `docs/input-mapping.md` G2 |
| `real_truncated_bank_reference` | Payment reference `VENDOR-PAY-2026-0001`; the bank row carries only its first 16 characters (as MT940 :61: does), so exact matching fails. A1 is empty, but A3 finds the debit (same amount, currency, counterparty), so the **current contract is not gradable, which is the right answer**; no contract gap. Target: abstain `reference_unmatched_candidate_bank_row`. A wrong answer is F12 | 4.2 A3; `docs/export-formats.md` 3.3; G1 |
| `real_truncated_reference_netted_fee` | The truncated reference of the case above plus a debit 12.50 below the payment and no fee journal. A3 requires an equal amount, so it finds no candidate. **Current contract answers F12 wrongly** (false positive: the debit exists). Target: abstain `reference_unmatched_nearby_debit` | 4.2 A3; G1, G4 |
| `real_free_text_reference` | Bank reference is free text containing the payment reference (`PAYMENT REF_1 INV 123`). Same shape and outcome as the truncated case. The contract would answer F12 wrongly only if the debit amount also differed (A3 empty); not exercised | 4.2 A3; G1 |
| `real_payment_without_allocation` | The `weekend_clearing` payment with its allocation removed: settled, debit matches, inside the limit, but no invoice. Must abstain `payment_without_invoice_allocation`, never `legitimate_weekend_clearing` or `no_exception` | 5 (no invoice, no all-clear) |
| `real_approval_missing` | The `weekend_clearing` payment whose invoice shows only an intermediate approval (`pending_level_2`, no `approved` event). Must abstain `invoice_approval_not_completed` | 5 (all-clear prerequisites) |
| `real_gl_payment_journal_mismatch` | The `weekend_clearing` payment whose balanced payment journal posts 829.60 against a payment of 800.00. Must abstain `payment_journal_amount_differs` | 5 (all-clear prerequisites) |
| `real_approval_revoked` | The `weekend_clearing` payment whose invoice was approved, then rejected before the payment date (latest event is a rejection). Must abstain `invoice_approval_not_effective` | 5 (all-clear prerequisites) |
| `real_check_double_payment` | Two check payments, each allocated the full 1200.00 of one invoice, both debits posted. Payment-primary on the first: must abstain `invoice_over_allocated`, never `legitimate_check_clearing` | 5 (all-clear prerequisites, invoice totals) |
| `real_partial_payment` | Invoice 1000.00 (status `approved`, see below) paid 400.00, debit 400.00. Not F06, not F14, not a missing settlement. Answered `no_exception` (open balance 600.00 reported) | 5.1; 4.1 |
| `real_netted_fee_no_journal` | Debit 12.50 below the payment and no fee journal for it; a fee journal for another payment's debit must not explain it. F14 "unexplained shortfall"; the fee is unobservable and must not be asserted | 4.3 |
| `real_reversal_then_repayment` | Reversed payment (debit, credit, journal), then a second payment settles the same invoice. Not a duplicate. **The reversal contract requires the invoice not to be `paid`, so the current contract does not confirm it.** Target (user decision, 2026-11-01): abstain `reversal_followed_by_repayment` until practitioner input | 5 reversal; 4.1 |

## Deferred (no scenario yet)

Truncated and free-text references are covered above, because the adapter hashes any reference string. Not covered:

- mixed date formats and bank-statement dates that differ from booking dates (G6): deferred, pending a plan decision (the adapter requires ISO dates by design);
- other messy reference shapes, such as case or spacing differences, which need normalisation rules that do not exist yet (G1);
- bank rows without a stable ID, or repeated statement overlaps (G9);
- batch ID and member count columns on the bank side (G2 beyond the amount sum shown above).

## Column vocabulary

`payment_method` is one of `ACH`, `check`, `virtual_card`, `wire` (the clearing limits are keyed by it). `invoices.status` is one of `approved`, `canceled`, `credit_applied`, `paid`, `received`. `bank_transactions.transaction_type` is one of `ACH debit`, `bank fee`, `card settlement`, `check clearing`, `payment reversal`, `wire debit`, matching the payment method on principal rows. `tests/unit/test_fixtures.py` pins all three. FinRCA has no partly-paid invoice status, so `real_partial_payment` uses `approved` (the invoice is still open); the partial state is visible only through allocations versus the invoice total. `payment_status` / `settlement_status`: a reversed payment is `reversed` / `reversed` (47 of 47 reversed payments in the FinRCA `full/payments.csv` have both; the two reversal fixtures use that pair). The `pending` values remain unverified against FinRCA.

## Other limits

- Payment method and clearing status come from fixed FinRCA columns (G3, G7); real exports lack them.
- Expected `quality_gaps` are facts of the snapshot, not diagnoses; an F12 payment and a pending one differ only in `effective_payment_without_bank_row`.
- The two no-exception shapes were `out_of_scope` at M1 (assumption A041). After the M1 gate the user decided they are answered `no_exception` (contract section 5.1); the three known-gap shapes abstain (section 5.2).
- Expected values for calculations named in `business_days` are day counts (section 5.3). `clearing_limit_*` entries are the declared limit carried by the count, not a separate calculation.
