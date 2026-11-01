# Leakage test on the prepared snapshot (M1)

Status: run on 2026-11-01 against FinRCA revision `76c93ec` (`full/` tables, train and validation labels only). Code: `evaluation/leakage_check.py` (evaluation side; reads labels; never imported by `src/reconcile/`). This is an engineering measurement, not proof that no other leak exists.

## 1. Question and verdict

Question (PLAN.md M1, task 2): after `prepare_finrca_snapshot`, can anyone recover class or generator-order signals from allowed fields only?

Verdict, against the M1 gate wording "no recoverable class or order signal, or each residual declared": **row order, token order and inserted-payment identity are not recoverable. Five residuals remain and are declared below (section 6). The gate holds only in its "residual declared" form.** The strict PLAN.md test, "a simple classifier must not beat the base rate", is not met everywhere. Four places beat it, each named and classified here:

| # | Where it beats the base rate | Strength | Type |
|---|---|---|---|
| R1 | Inserted invoices: `invoices.status`, absent `approval_events`, absent allocations | 147/147 found, 0/8000 organic flagged | generator artifact; outside F06/F12/F14 |
| R2 | `gl_entries` FX-settlement codes | 30/30 FX cases; 1 of the other 508 dev cases carries them (an F06 invoice) | partly real evidence, class-pure by generation |
| R3 | Entity type (invoice, payment, bank_transaction) | balanced accuracy 0.512 vs 0.250 chance | request-level input, class-correlated (L9) |
| R4 | Invoice-primary `invoice_date` (age at cutoff), F06 vs legitimate | validation balanced accuracy 0.756 vs null95 0.712, n=31 | not explained by the injector selection rules (section 5.2); open, small sample |
| R5 | Order-correlated amounts and dates | rho up to +0.45 (`changed_at`), +0.29 (`payment_amount`) | weak order proxy; no class use found |

## 2. Method

1. **Prepare.** `prepare_finrca_snapshot` on `full/` with cutoff 2026-06-30 and a throwaway key (SHA-256 of a fixed seed string, in a temp directory outside the prepared directory). The key is used only to map a raw ID to its token so that labels can be attached. It never enters a feature.
2. **Features** come only from `snapshot.sqlite`. Two groups:
   - `own.*`: columns of the primary entity's own row, plus its vendor's master-data columns. Token columns are reduced to null or non-null. Dates become weekday, month, age at cutoff and gaps. Money becomes magnitude (decimal exponent) and a round-amount flag.
   - `rel.*`: observable evidence joined by shared tokens: allocation count and sum against the amount, bank rows with the same payment reference (count, amount against payment, status, type, lag, currency), GL codes sourced to the entity, its payments and their bank rows, approval event count, payment statuses.
   `own.*` is the leakage set: it is what a classifier sees without doing the investigator's work. `rel.*` is the evidence a real investigator is meant to use. A flag on `rel.*` is expected where the class has a genuine observable.
3. **Models**, all fitted on training data only and scored on held-out data.
   - Single-field rule: for each feature value, the class with the highest class-normalised share. Every column is tested alone, so a leaking field is named.
   - A depth-3 class-balanced Gini decision tree on `feature == value` splits (about 30 lines of stdlib code).
4. **Metric.** Balanced accuracy (mean per-class recall); chance is 1/k. The family task also reports plain accuracy against the best constant on validation (the largest class share), which is the generous base rate.
5. **Threshold (what counts as a signal).** A field is flagged when its held-out balanced accuracy exceeds the 95th percentile of the best-single-field balanced accuracy under 200 label permutations. This is a family-wise null: choosing the best of about 30 fields is paid for. Flags are then read by hand and classified (section 5). A flag is not automatically a leak. A flag on a field that carries real financial evidence for the class is the expected result.
6. **Splits.** Inserted-row tasks: 5-fold cross-validation (fixed shuffle seed), 6200 payments and 8147 invoices. Family task: train 58+55+58+235 cases, validation 18+16+18+80.
7. **Cohort.** F06, F12, F14 and the ten payment-related NO_FAILURE variants (pending, weekend, check, due-date, split, batch, reversal, net-fee, accounted-fee, FX), labelled `LEGIT`. The primary entity comes from the label file (`primary_entity`), i.e. the entity named in the question. 538 cases, all found in the snapshot.

### Classifier choice

| Option | Result on the family task (validation) | Cost |
|---|---|---|
| stdlib single-field rules and depth-3 tree | own-row: 55/132, balanced 0.563; all fields: 115/132, balanced 0.947 | none |
| scikit-learn `DecisionTreeClassifier(max_depth=3, class_weight="balanced")` on one-hot features | identical: 55/132, 0.563 and 115/132, 0.947 | new dependency (numpy, scipy) |
| scikit-learn logistic regression, balanced | own-row: accuracy 0.462, balanced 0.481; all fields: 1.000 | same |

The scikit-learn comparison was run once with `uv run --with scikit-learn --with numpy` on a scratch script, so no dependency was added to the repository. It reproduces the stdlib tree exactly and the logistic regression finds nothing more in the own-row set. The stdlib implementation is therefore kept (AGENTS.md section 2: prefer the standard library). Revisit if a later check needs calibrated probabilities or many more features.

## 3. Results

Full output: `uv run python evaluation/leakage_check.py --upstream <checkout>` (about 45 s). Tables below are copied from it (key seed `leakage-test-key-0`).

### 3.1 Inserted rows

"Found" is inserted rows flagged by the rule; "organic flagged" is false positives. Precision is TP/(TP+FP).

**Payments** (inserted = raw reference above 6042, L12): 200 of 6200, base rate 0.032, null95 0.544.

| Field (CV balanced acc) | Found | Organic flagged | Precision | Reading |
|---|---|---|---|---|
| `rel.pay_n_alloc` / `rel.pay_alloc_vs_amount` (0.750) | 100/200 | 0/6000 | 1.000 | No allocation row. Real evidence of F05 (payment without an invoice); see 5.1 |
| `own.pay_amount_mag` (0.557) | 183/200 | 4806/6000 | 0.037 | Lift 1.15 over base rate. Negligible |
| `own.pay_age` (0.550) | 85/200 | 1951/6000 | 0.042 | Lift 1.3. Negligible |
| every other field | at or below null95 | | | |

Tree on all payment fields: 0.751 (all of it from the allocation field). Tree on own-row fields only: **0.564**. Bank rows carrying an inserted payment's reference: best field `bank_amount_mag` 0.564 (182/198 found, 4713/5963 flagged, precision 0.037), tree 0.542. GL lines sourced to inserted payments: best field `gl_age` 0.546, tree 0.561 (400 inserted of 12484).

Subgroup test (inserted payments that have an allocation, 100, against the 6000 organic payments; the 100 without an allocation are dropped; same single-field and tree method, 5-fold CV, null95 0.563):

| Feature set | Tree (CV balanced) | Best single fields (balanced) |
|---|---|---|
| All payment fields | 0.579 | `rel.pay_n_alloc` 0.590 (100/100 found, 4920/6000 organic flagged, precision 0.020 vs base rate 0.016; FLAG), `own.pay_age` 0.550, `own.pay_amount_mag` 0.548 (not flagged) |
| Own row only | 0.485 | `own.pay_age` 0.550, `own.pay_amount_mag` 0.548 (nothing above null95) |

Findings: from the payment's own row (and vendor data) these 100 are not separable. With joined fields the only flag is `rel.pay_n_alloc`, which the inserted payments all have as exactly one allocation while about 18% of organic payments (1080 of 6000) have two or more; precision 0.020 against a 0.016 base rate is a lift of about 1.2, so no usable signal. Bank-row and GL-line features were tested only in the pooled 200-payment runs above (best balanced accuracy 0.564 and 0.561), not re-run on this subgroup. No additional signal detected by these experiments; untested: interactions beyond depth 3, and bank-row and ledger features restricted to the 100-payment subgroup. The other 100 inserted payments are marked only by the genuine absence of an allocation.

**Invoices** (inserted = raw ID above `INV_0428000`): 147 of 8147, base rate 0.018, null95 0.554.

| Field | CV balanced acc | Found | Organic flagged |
|---|---|---|---|
| `own.inv_status` | 1.000 | 147/147 | 0/8000 |
| `rel.inv_n_approval` (approval_events) | 1.000 | 147/147 | 0/8000 |
| `rel.inv_gl_types` | 1.000 | 147/147 | 0/8000 |
| `rel.inv_n_alloc`, `rel.inv_alloc_vs_total`, `rel.inv_n_payments` | 0.875 | 147/147 | 2000/8000 |
| `own.inv_has_po` | 0.592 | 83/147 | 3040/8000 |
| other own-row fields | at or below null95 | | |

Tree on own-row fields: 1.000, driven by `status` alone (inserted invoices are `received` or `credit_applied`; organic are `paid`, `approved` or `canceled`; the audit recorded this at section 3.1 as a residual). **Inserted invoices are fully recoverable.** This is residual R1.

### 3.2 Order

Row order and token order against raw file position (every raw file is ascending by ID). Flag threshold |rho| > 4/sqrt(n).

| Table | Rows | Spearman, rowid | Spearman, token | Concordant pairs | 4/sqrt(n) |
|---|---:|---:|---:|---:|---:|
| vendors | 500 | -0.029 | -0.029 | 0.491 | 0.179 |
| purchase_orders | 5000 | +0.006 | +0.006 | 0.503 | 0.057 |
| po_lines | 11231 | -0.003 | -0.003 | 0.499 | 0.038 |
| invoices | 8147 | -0.007 | -0.007 | 0.498 | 0.044 |
| invoice_lines | 18299 | -0.013 | -0.002 | 0.495 | 0.030 |
| payments | 6200 | -0.009 | -0.009 | 0.497 | 0.051 |
| payment_allocations | 7180 | -0.004 | n/a (composite key) | 0.498 | 0.047 |
| bank_transactions | 6261 | +0.003 | +0.003 | 0.498 | 0.051 |
| bank_statements | 57 | -0.284 | -0.284 | 0.403 | 0.530 |
| vendor_change_log | 186 | +0.027 | +0.027 | 0.508 | 0.293 |
| approval_events | 17429 | +0.010 | +0.010 | 0.504 | 0.030 |
| gl_entries | 46755 | +0.006 | +0.001 | 0.502 | 0.018 |

Concordant pairs are the share of 100000 random pairs ordered the same way (0.5 is no relation). Nothing is flagged. Two more keys (`leakage-test-key-1`, `-2`) give the same picture: the largest |rho| is 0.107 (`vendor_change_log`, n=186, threshold 0.293). The small tables move with the key, as chance predicts. **No row-order or token-order signal.**

Attribute columns against raw order (columns with 20+ distinct values; the tables list only the flagged ones, the output has all):

| Table.column | Spearman | 4/sqrt(n) |
|---|---:|---:|
| vendor_change_log.changed_at | +0.454 | 0.293 |
| invoices.shipping | -0.346 | 0.044 |
| invoice_lines.quantity | -0.338 | 0.030 |
| payments.payment_amount | +0.285 | 0.051 |
| bank_transactions.amount | +0.275 | 0.051 |
| gl_entries.credit | +0.205 | 0.018 |
| payment_allocations.allocation_date | +0.193 | 0.047 |
| invoice_lines.unit_price | +0.156 | 0.030 |
| payment_allocations.allocated_amount | +0.146 | 0.047 |
| payments.payment_date, created_at | +0.129 | 0.051 |
| bank_transactions.transaction_date, posted_date | +0.118 | 0.051 |
| invoices.tax | -0.109 | 0.044 |
| gl_entries.posting_date | +0.102 | 0.018 |

These are real but weak: a rho of 0.3 recovers little of a row's rank. Dates rising with ID is ordinary (IDs are issued over time). Amounts, quantities and shipping correlating with ID is a property of the generator (R5). They matter only if they let an observer separate inserted from organic rows, and section 3.1 shows they do not for payments, bank rows and ledger lines.

### 3.3 Family

Base rate on validation (always `LEGIT`): 80/132 = 0.606 accuracy; balanced accuracy 0.250. Class counts train/validation: F06 58/18, F12 55/16, F14 58/18, LEGIT 235/80.

| Feature set | Tree, validation accuracy | Balanced accuracy | Best single field (balanced) | null95 |
|---|---|---|---|---|
| A: entity type alone | 38/132 | 0.512 | `entity_type` 0.512 | 0.316 |
| B: own row and vendor fields (leakage set) | 55/132 | 0.563 | `own.pay_settlement`, `own.pay_status` 0.547; `own.pay_age` 0.524; `own.pay_method` 0.514 | 0.362 |
| C: all observable fields | 115/132 | 0.947 | `rel.pay_n_bank` 0.791; `rel.pay_bank_vs_amount` 0.637; `rel.pay_gl_types` 0.603 | 0.367 |

Set B's tree is below the constant-`LEGIT` accuracy (55/132 against 80/132) because it is class-balanced, but its balanced accuracy beats chance 0.25. That margin comes from R3 (entity type) and from payment status. Set C's tree is a readable statement of the evidence contract: `inv_alloc_vs_total == gt` gives F06; no bank row and age over one day gives F12; no bank row and age up to one day gives the pending legitimate case; bank amount lower than the payment gives F14.

Because entity type is part of the request and a class-correlated input in itself, the sharper test conditions on it:

| Stratum | Chance | Own-row tree (validation) | Own-row best fields vs null95 | All-observable tree |
|---|---|---|---|---|
| Payment-primary (F12 16, F14 18, LEGIT 63) | 0.333 | 38/97, balanced 0.457 | best `own.pay_age` 0.425; null95 0.473; **nothing flagged** | 97/97, balanced 1.000 |
| Invoice-primary (F06 18, LEGIT 13) | 0.500 | 23/31, balanced 0.756 (best constant 18/31) | `own.inv_age` 0.756 vs null95 0.712: **flagged** | 31/31, balanced 1.000 |

Within payment-primary cases the primary payment's own row, its vendor's master data and its age do not separate F12, F14 and legitimate cases. Within invoice-primary cases `invoice_date` does (R4). Invoice age at cutoff by class, train plus validation:

| Age at cutoff | F06 (76) | due-date and split payment (73) |
|---|---:|---:|
| 90 days or less | 17 | 4 |
| 91 to 365 days | 27 | 52 |
| over 365 days | 32 | 17 |

Fields flagged in the all-observable set, by stratum, with classification in section 5: payment stratum `rel.pay_bank_vs_amount` 0.878, `rel.pay_bank_type` 0.720, `rel.pay_bank_cur_eq` 0.714, `rel.pay_n_bank` 0.714, `rel.pay_bank_lag` 0.704, `rel.pay_bank_status` 0.667, `rel.pay_gl_types` and `rel.pay_gl_n` 0.487; invoice stratum `rel.inv_alloc_vs_total` 1.000, `rel.inv_n_alloc`, `rel.inv_n_payments`, `rel.inv_pays_with_bank` 0.808, `rel.inv_pay_gap` 0.801, `own.inv_age` 0.756.

Own-row payment status in the payment stratum: `completed` for all of F12 (71), F14 (76), batch, check, FX, net-fee and weekend cases; `submitted` for all 33 pending cases and `reversed` for all 32 reversal cases. `own.pay_age` is up to one day for all 33 pending cases and none of the others.

### 3.4 Declared residuals

**FX-settlement codes** (`gl_entries.transaction_type = fx_settlement`, `gl_account = 799100-FX-SETTLEMENT`). In the snapshot: 94 lines with the type, 47 with the account, 47 distinct source rows (one payment each). Presence on the primary entity, its payments or their bank rows, dev cases:

| Class | Primary entity | Cases with an FX code |
|---|---|---|
| F06 | invoice | 1/76 (one invoice whose other payment is an FX case) |
| F12 | payment | 0/71 |
| F14 | payment | 0/76 |
| LEGIT other variants | payment, invoice, bank_transaction | 0/285 |
| LEGIT FX variant | payment | 30/30 |

Rule "FX code present implies legitimate": 30/31 correct among cases with a code, 30/30 recall for the FX variant. The code also appears in the GL account set `799100-FX-SETTLEMENT+799199-FX-CLEARING`, which occurs only in the FX variant. The evidence contract does not use these codes and abstains on FX (`docs/evidence-contract.md`); the code is a residual that a system could exploit, not one the contract relies on.

Other class-pure ledger codes, from the same output (payment-primary dev cases): `payment_reversal` journals appear in 32/32 reversal cases and nowhere else; the `bank_fee` journal (with account `710000-BANK-FEES`) appears in 33/33 net-fee cases and in one F12 case. These are the records the contract uses on purpose.

**approval_events and inserted invoices.** Organic invoices: 8000/8000 have approval events. Inserted invoices: 0/147. Dev cases with an invoice as primary entity and no approval events: F01 73/73 and `legitimate_credit_memo` 35/35. No F06 case is affected (all 76 F06 invoices are organic), and neither F01 nor credit memos are in the cohort. A request whose primary entity is an invoice can still be classified as "inserted, therefore F01 or credit memo, therefore not F06" from this absence and from `status`.

## 4. What the tests did not show

- No field of the primary payment (own row, vendor data) predicts F12, F14 or legitimate class. Nothing is flagged in the payment stratum.
- No snapshot row or token order reveals raw order. No column is a usable proxy for the inserted/organic boundary.
- None of the old audit leaks in section 4 (L1 case IDs, L3 anomaly audit events, L4/L5 audit timestamps, L12 references and order) reappears through the allowed fields. L12 specifically: no field separates payments above reference 6042 (section 3.1). The order of the `payment_ref` tokens themselves was not tested separately; the `payments` row above covers the `payment_id` token.

## 5. Classification of each signal

Reasoning rule: a signal is **genuine evidence** if an investigator with a real ledger and bank export would use it for the same reason (the fact is what makes the exception an exception, or what documents the legitimate explanation). It is a **generator artifact** if it separates the classes only because of how the benchmark builds or selects records and would not be a reliable cue in a real export.

### 5.1 Genuine evidence (expected, not leakage)

| Signal | Class it identifies | Why genuine |
|---|---|---|
| `rel.pay_n_bank == 0` with age over one day | F12 | The ERP payment has no bank row. This is the observable exception (the task's own example) |
| `rel.pay_n_bank == 0` with age up to one day | pending (legitimate) | An unsettled payment inside the clearing window is a normal state |
| `rel.pay_bank_vs_amount == lt` with no fee journal | F14 | Bank amount differs from the ERP amount with nothing recorded |
| `rel.pay_gl_types` containing `bank_fee` and bank amount lower | net-fee (legitimate) | A recorded fee journal explains the difference |
| `own.pay_status = reversed`; `payment_reversal` journal | reversal (legitimate) | The ledger documents the reversal |
| `own.pay_status = submitted`, `own.pay_age` up to one day | pending (legitimate) | The payment is not yet settled. (Caveat: all 33 pending payments are dated one day before the cutoff, L8, so the boundary is not exercised) |
| `rel.pay_bank_cur_eq = False` | FX (legitimate or unsupported) | Payment and bank currencies differ. The contract abstains on this |
| `rel.inv_alloc_vs_total == gt`, `rel.inv_n_payments` 2 or more, `rel.inv_pay_gap` | F06 | Allocated payments exceed the invoice total |
| `rel.pay_n_alloc == 0` | inserted F05 payment | A payment with no invoice allocation is that exception. Outside the three families |

### 5.2 Generator artifacts (leakage, declared)

| Signal | Why an artifact |
|---|---|
| R1. `invoices.status` of `received` or `credit_applied`; no `approval_events`; ledger types | Every organic invoice passes the approval workflow and is later paid or cancelled. A real export has received-but-unapproved invoices alongside approved ones. Here the injector's two kinds of invoice (F01 duplicates, credit memos) are the only ones outside the workflow, so the field identifies the tail |
| R2. FX codes | An FX posting is real evidence of an FX difference, so the fact is genuine. But in the benchmark only the 47 hard-negative payments ever use the codes, so "has an FX code" is perfect for the legitimate label. In a real ledger FX postings are routine and occur on failing payments too. The one F06 invoice with an FX code (an FX case on its other payment) shows the codes are not tied to the class by anything but construction |
| R3. Entity type | The template question names the entity type (L9), so it is available. F06 is always asked about an invoice and F12 and F14 about a payment because the injector's primary entity is fixed per class. A real request would not have the type fixed by the failure. The 23 `bank_transaction` cases are all legitimate (`accounted_bank_fee`) |
| R4. Invoice-primary `invoice_date` | The age of an invoice does not make a duplicate payment more or less likely. F06 invoices are spread across the whole two-year range; the legitimate due-date and split cases concentrate at 91 to 365 days. Injector code read at the pinned revision: F06 draws uniformly with `rng.choice` from invoices with `status == "paid"`, at least one allocation, and not already claimed (`src/failures/f06_double_payment.py:22-27`). Split payment draws uniformly from invoices with more than one allocation and not claimed (`hard_negatives.py:147-151`). Due-date payment draws uniformly from invoices with a payment on `due_date` and a bank row (`hard_negatives.py:264-273`). None of the three uses invoice date or age as a criterion. I also computed the candidate pools on `full/` (organic invoices, share by age 90 days or less / 91-365 / over 365): F06 pool 5939 invoices, 0.13 / 0.54 / 0.33; multi-allocation pool 1143, 0.13 / 0.53 / 0.34; paid-on-due-date pool 4319, 0.10 / 0.60 / 0.30. The pools are alike, so the rules do not explain the dev-case gap (F06 76 cases: 0.22 / 0.36 / 0.42; legitimate 73: 0.05 / 0.71 / 0.23). Not generator selection on date; remaining explanations are sampling variation (F06 91-365 band 27 observed against about 41 expected, roughly 3 sd) or the claim mechanism removing candidates, neither checked. Validation sample is small (n=31), which is why it only just clears the null |
| R5. Amounts, quantities, shipping against ID | Weak monotone trends with generation order. Not tied to a class by any financial reasoning. `changed_at` rising with change ID is ordinary (events are logged in time order) and is not an artifact |
| `own.pay_amount_mag`, `own.pay_age`, `bank_amount_mag`, `gl_age` (inserted rows) | Marginal flags with precision 0.037 to 0.042 against a 0.032 base rate. They are probably distribution noise from 200 inserted rows against 6000. Reported, not treated as signal |

## 6. Residuals carried forward

Each is declared for the M1 gate. None blocks the three-family investigation if the handling below is followed.

| ID | Residual | Handling |
|---|---|---|
| R1 | Inserted invoices recoverable from `status`, `approval_events`, allocations and ledger types | Exclude F01 and credit-memo cases from the cohort (they are outside the families). Do not claim invoice-level discrimination on a cohort that contains them |
| R2 | FX codes class-pure for the FX legitimate variant | The contract abstains on FX. Report the 30 FX cases as a separate abstention slice, and measure whether any investigator output depends on the code |
| R3 | Entity type correlated with class | Report results per entity-type stratum, and report a type-only baseline (balanced 0.512 overall) beside every headline number |
| R4 | `invoice_date` separates F06 from legitimate invoice-primary cases (validation balanced 0.756, n=31). Injector selection does not use date, so the cause is unexplained (chance or claim effects) | The investigator must not use invoice age as diagnostic evidence for F06. Report a date-only "leak baseline" for the invoice stratum beside every F06 result. Re-measure on a larger set before any claim. Open |
| R5 | Weak order proxies in amounts and dates | No action. Re-measure if the inserted-row test changes |

## 7. Limitations

- Measured on train and validation only. The sealed `challenge_test` was not opened or counted, and the `test` labels were not read. Whether R1 to R5 hold there is unknown; the tables are full-table facts only for R1 and R2.
- Small samples in the strata (validation 97 and 31 cases). The family-wise null is estimated from 200 permutations of validation labels; a flag near the null is weak.
- The single-field and depth-3 tree are weak attackers. A better model (gradient boosting, interactions across many fields) could find more. Absence of a flag is not proof of absence of signal; presence is.
- Features were chosen by hand from the allowlist. Raw token columns are reduced to null or non-null, so the test does not look for structure inside the 128-bit tokens (they are HMAC outputs and carry no order, by construction; the order test confirms it on the sorted key).
- `own.*` includes vendor master data and the payment date, which a real analyst would see too. The leakage set is therefore a conservative proxy for "what is knowable without joining evidence".
- The key is a test key derived from a seed. A production key would differ, and token-order statistics vary with it (checked on three seeds).
- The inserted-payment label is the L12 rule (reference above 6042). The invoice label is the ID rule (above `INV_0428000`). Both come from the audit, not from independent adjudication.
- FX cases were identified through the primary entity, its payments and their bank rows only. An FX posting on a payment that shares neither an invoice nor a reference with the case is not counted.
- Class-purity of ledger codes was checked on dev cases. Their population counts (47 FX payments) come from full tables and include test and challenge entities; no per-split count was derived.

## 8. Reproduce

```bash
uv run python evaluation/leakage_check.py \
  --upstream /path/to/data/upstream/FinRCA-AI-Bench      # about 45 s
uv run python evaluation/leakage_check.py --upstream ... --seed leakage-test-key-1   # other key
```

It prepares a snapshot in a temp directory with a seeded test key, prints every table in this document as Markdown, and deletes the snapshot and key on exit. Only `train/` and `validation/` `rca_ground_truth.jsonl` are opened for labels. The scikit-learn comparison in section 2 is not in the repository; it was a scratch script run with `uv run --with scikit-learn --with numpy`.
