# Input mapping: Xero and CAMT.053 onto the evidence-contract inputs

**Status:** M1 draft, **provisional**. No practitioner has been consulted yet (`docs/practitioner-findings.md`), so the shortlist itself (`docs/export-formats.md` section 5) may change. This document states what the cited specifications show, and nothing more. Everything else is marked **unverified**. Specifications show which fields a format can carry, not what a real export contains.

It answers the M1 gate item in `PLAN.md` section 6: "a written mapping shows each shortlisted format can populate the contract, or records which fields it lacks". It does **not** change the contract (`docs/evidence-contract.md`) or the adapter.

## 1. Sources and conventions

| Short name | Source (all checked 2026-11-01) |
|---|---|
| Xero spec, `X:<line>` | `xero_accounting.yaml` of https://github.com/XeroAPI/Xero-OpenAPI at commit `df43c6dd06c0eccffdf09297dcae3b0dbcf3a498` (v19.1.0, MIT). Line numbers are in that file. Re-read for this mapping from a copy outside the repository. |
| ASN, `ASN <iso-ref>` | ASN Bank camt.053 description, https://www.asnbank.nl/downloads/formaatbeschrijving-camt053.html (camt.053.001.02). `<iso-ref>` is the "ISO Referentie" number in its tables (for example 2.80). Text is Dutch; the Dutch is translated here. |
| ING | ING NL sample files, https://assets.ing.com/m/19ba05999348c5c7/original/Sample-Files-CAMT.pdf (camt.053.001.02). The samples are illustrations; they do not show every entry type. |
| WBC | Westpac BankRec camt.053.001.02 page, https://bankrec.westpac.com.au/docs/statements/iso/cash-management. One bank's dialect. |

The ISO 20022 catalogue was not reachable, so element semantics are only what these three sources state. Each bank may differ (`docs/export-formats.md` section 1).

Status vocabulary (per mapping line):

- **direct**: a field with the same meaning exists in the cited source.
- **transform**: a field exists, but a documented change is needed (sign, units, date type, token form).
- **partial**: the source carries only part of the meaning, or carries it only in some entries or for some banks.
- **missing**: the cited source has no such field.
- **unverified**: the field exists, or might, but the cited sources do not say enough to rely on its meaning.

Contract check abbreviations: F06 duplicate payment; F12 payment missing from bank; F14 amount mismatch; PEND pending settlement; WKND weekend clearing; CHK check clearing; DUE due-date payment; SPLIT split payment; BATCH batch payment (one payment, several invoices); REV payment reversal; NETFEE net-fee accounting; FEEROW accounted bank fee; FX FX settlement (abstention only).

Why Xero + CAMT.053: Xero is the accounting side of the contract (`invoices`, `payments`, `payment_allocations`, `gl_entries`), and CAMT.053 is the bank side (`bank_transactions`, and `bank_statements`, which no check uses). Each shortlisted format covers only one side.

## 2. What the evidence contract actually uses

Derived from `docs/evidence-contract.md` sections 3 to 5 and cross-checked against the columns that `evaluation/evidence_contract_check.py` reads (its `ALLOWED` dict and its row accesses). The adapter's `TABLES` (`src/reconcile/adapters/finrca.py`) is the full snapshot.

**Used by at least one check (the mapping targets):**

| Table | Used columns |
|---|---|
| `invoices` | `invoice_id`, `due_date`, `currency`, `invoice_total`, `status` (status only by REV: allocated invoices must not be `paid`) |
| `payments` | `payment_id`, `payment_date`, `payment_method`, `payment_currency`, `payment_amount`, `payment_status`, `settlement_status`, `reference_number`, `bank_account_id` (only the A3 search, as the vendor-side account to compare with `counterparty_token`) |
| `payment_allocations` | `payment_id`, `invoice_id`, `allocated_amount` |
| `bank_transactions` | `bank_transaction_id`, `posted_date`, `transaction_type` (only to tell `bank fee` and `payment reversal` rows from the rest), `amount`, `currency`, `direction`, `status`, `counterparty_token` (A3 only), `payment_reference` |
| `gl_entries` | `journal_id`, `transaction_type` (`payment`, `bank_fee`, `payment_reversal`), `source_transaction_id`, `posting_date`, `debit`, `credit`, `currency`. `journal_line_id` is used only as the row identity |

**In the snapshot but used by no check (not mapped here, listed so nothing is silently claimed):**

- Whole tables: `vendors`, `purchase_orders`, `po_lines`, `invoice_lines`, `bank_statements`, `vendor_change_log`, `approval_events`.
- Columns of used tables: `invoices` (`vendor_id`, `po_id`, `invoice_date`, `received_date`, `subtotal`, `tax`, `shipping`, `payment_terms`); `payments` (`vendor_id`, `created_at`); `payment_allocations.allocation_date`; `bank_transactions` (`bank_account_id`, `transaction_date`); `gl_entries` (`accounting_period`, `gl_account`).

Two of those matter later and are discussed in section 6: `bank_statements` (completeness evidence) and `gl_entries.gl_account` (the only way to recognise a fee journal in a real ledger).

## 3. Mapping tables

### 3.1 `invoices` from Xero (Invoice, `Type` = ACCPAY, X:22390)

| Column | Xero source | Transformation | Status |
|---|---|---|---|
| `invoice_id` | `Invoice.InvoiceID` (X:22516, uuid) | Tokenise like any identifier. | direct |
| `due_date` | `Invoice.DueDate` (X:22413) | Xero dates carry the `x-is-msdate` marker and the spec examples show `/Date(1550793549320+0000)/` (X:139); the field description says `YYYY-MM-DD`. Parse both forms; reject anything else. Time zone of a millisecond value: unverified. | transform |
| `currency` | `Invoice.CurrencyCode` (X:22434) | None, validated against the currency table. | direct |
| `invoice_total` | `Invoice.Total` (X:22489, tax inclusive: SubTotal + TotalTax + RoundingAmount) | JSON number (double) to `Decimal`: parse the raw JSON text, never through a float. The spec says `Total` is computed and, when listing invoices (GET), the write-time validation described there does not apply (X:22489); whether a listed `Total` can differ from the by-ID value: unverified. | transform |
| `status` | `Invoice.Status` (X:22442): DRAFT, SUBMITTED, DELETED, AUTHORISED, PAID, VOIDED | Contract needs only "is `paid`": PAID gives `paid`. Other contract values (`approved`, `received`, `canceled`, `credit_applied`) have no one-to-one Xero value; the spec gives no definitions of the codes beyond the names. `AmountDue` (X:22552) = 0 is an alternative test of "paid", not verified to coincide with PAID. | partial |

Also on the Xero side and not in the contract: `AmountPaid` (X:22558, "sum of payments received"), `AmountCredited` (X:22569, credit notes, overpayments and prepayments applied), `FullyPaidOnDate` (X:22564). See section 6.

### 3.2 `payments` from Xero (Payment, plus BatchPayment)

Unit mismatch. A Xero `Payment` names one invoice (`Payment.Invoice`, X:23927) and one `Amount` (X:23957). The contract's `payments` row can carry several allocations (BATCH: "payment, two or more allocations"). The nearest Xero object for such a payment is a `BatchPayment` (X:21021; `Payments[]` X:21063; `TotalAmount` X:21082; description at X:565: "bundle multiple bills or invoices into one payment transaction ... a single payment in Xero can be reconciled with a single transaction on the bank statement"), whose member `Payment`s carry `BatchPaymentID` (X:24008, "Present if the payment was created as part of a batch"). This reading is an interpretation (assumption A030 below); the spec does not say that a batch is a "payment" in the contract's sense.

| Column | Xero source | Transformation | Status |
|---|---|---|---|
| `payment_id` | `Payment.PaymentID` (X:24003); for a batch row, `BatchPayment.BatchPaymentID` (X:21046) | Which of the two is the contract's payment depends on A030. | transform, unverified |
| `payment_date` | `Payment.Date` (X:23948, "Date the payment is being made"); `BatchPayment.Date` (X:21054) | The date the payment is recorded to be made in the ledger, not a bank date (D036). Backdating is possible; the spec gives no creation date for a payment, only `UpdatedDateUTC` (X:23992, last update). Same date-format note as 3.1. | transform |
| `payment_method` (ACH, wire, check, virtual_card) | none. `Payment.PaymentType` (X:23979) is only the receivable/payable class (ACCPAYPAYMENT and similar). `Payment.Account` (X:23943) is the paying account; `Account.BankAccountType` (X:20454) is BANK, CREDITCARD, PAYPAL or NONE. | The method cannot be derived from Xero. The clearing limit (P1) is per method, so F12, PEND, WKND and CHK need it from elsewhere (section 4, gap G3). | missing |
| `payment_currency` | no `CurrencyCode` on `Payment` (checked: the property list X:23924-24023 has none). `Payment.Invoice.CurrencyCode` (X:22434) is the likely currency of `Payment.Amount`; `Payment.BankAmount` (X:23962) is "in the currency of the bank account"; the bank account's currency is `Account.CurrencyCode` (X:20463). | Currency of `Amount` is not stated; the description "must be less than or equal to the outstanding amount owing on the invoice" (X:23957) implies the invoice currency. Unverified (A031). | unverified |
| `payment_amount` | `Payment.Amount` (X:23957); `BankAmount` (X:23962); `CurrencyRate` (X:23952, "only used for non base currency invoices"); batch: `BatchPayment.TotalAmount` (X:21082) | `Decimal`, as in 3.1. For F14 and NETFEE the comparison is with the bank amount; with equal currencies `Amount` and `BankAmount` should be equal (unverified). | transform |
| `payment_status`, `settlement_status` | `Payment.Status` (X:23973): AUTHORISED or DELETED only; `Payment.IsReconciled` (X:23970); `BatchPayment.Status` (X:21075, AUTHORISED or DELETED) | There are no pending, settled, reversed or failed values. "Effective" (completed and settled) can only be approximated as AUTHORISED. `IsReconciled` is Xero's own reconciliation flag, an accounting-side fact (D036); it must not stand in for "settled" (a payment with a bank row that nobody matched is `IsReconciled` false, and that is exactly the case an exception queue contains). "Reversed" has no value: DELETED removes a payment but the spec gives no reversal link. | partial |
| `reference_number` | `Payment.Reference` (X:23967, "An optional description for the payment e.g. Direct Debit"); `Payment.Details` (X:24019, "The information to appear on the supplier's bank account"); batch: `BatchPayment.Details` (X:21039, non-NZ, "sent to the org's bank as a reference"), `Reference`/`Particulars`/`Code` (NZ), `Narrative` (UK, X:21043 "Only shows on the statement line in Xero") | The reference is optional free text and is not stated to be unique. It is not stated to be what the bank statement carries (section 4, gap G1). Normalise (trim, case, internal spaces) before tokenising into `payment_ref`; the rule for which Xero field is "the" reference is a decision that needs practitioner input. | partial |
| `bank_account_id` (vendor-side account, A3 only) | `Payment.BankAccountNumber` (X:24013, "The suppliers bank account number the payment is being made to"); `Contact.BankAccountDetails` (X:21295) | Format of the number (IBAN or national) is not stated; to compare with a CAMT counterparty IBAN both sides need a normaliser. | partial |

### 3.3 `payment_allocations` from Xero

| Column | Xero source | Transformation | Status |
|---|---|---|---|
| `payment_id`, `invoice_id` | `Payment.PaymentID` and `Payment.Invoice` (one invoice per `Payment`, X:23927); also readable from the other side as `Invoice.Payments[]` (X:22534) | One Xero `Payment` yields one allocation row. A payment against several invoices is several `Payment` rows, tied together only by `BatchPaymentID` (A030). | transform |
| `allocated_amount` | `Payment.Amount` (X:23957) | Currency per 3.2 (unverified). Credit notes, overpayments and prepayments applied to an invoice are `Allocation` objects (X:21816; `Invoice`, `Amount`, `Date`), not `Payment`s; they are summed in `Invoice.AmountCredited` (X:22569). The contract counts payments only, so those settlements are invisible to it unless added. | partial |

### 3.4 `gl_entries` from Xero (Journals endpoint, X:9383)

The Journals endpoint is read-only, supports `offset` by `JournalNumber` and a `paymentsOnly` cash-basis filter (X:9394-9406); accrual basis is the default.

| Column | Xero source | Transformation | Status |
|---|---|---|---|
| `journal_id`, `journal_line_id` | `Journal.JournalID` (X:22774); `JournalLine.JournalLineID` (X:22837) | Tokenise. | direct |
| `source_transaction_id` | `Journal.SourceID` (X:22794, "The identifier for the source transaction (e.g. InvoiceID)") | The spec's only example is an invoice. For a payment journal (`SourceType` ACCPAYPAYMENT) whether `SourceID` is the `PaymentID` or the `InvoiceID` is **not stated**. For a fee booked as spend money (`SourceType` CASHPAID or EXTERNALSPENDMONEY, X:22798 enum) the source would be a Xero `BankTransaction`, not a bank statement row. Unverified (A027, A036). | unverified |
| `transaction_type` (`payment`, `bank_fee`, `payment_reversal`) | `Journal.SourceType` (X:22798): ACCPAYPAYMENT, APCREDITPAYMENT, CASHPAID, TRANSFER, MANJOURNAL and others | `payment` is plausibly ACCPAYPAYMENT. There is no value for a bank fee or a reversal: a fee is an ordinary spend-money journal and cannot be told from other spend by `SourceType`. The account (`JournalLine.AccountCode` X:22847, `AccountType` X:22851, `AccountName` X:22854) would be needed, and the contract does not use `gl_account`. What Xero posts when a payment is deleted: not stated. | partial for `payment`; missing for `bank_fee`, `payment_reversal` |
| `posting_date` | `Journal.JournalDate` (X:22778, "Date the journal was posted"); `Journal.CreatedDateUTC` (X:22785) | D045 uses the posting date as the availability date for the cutoff. In Xero the two can differ and `JournalDate` is the user-visible accounting date (backdating is not excluded by the spec). Which one is the contract's `posting_date` is a decision (A034). | transform, unverified |
| `debit`, `credit` | `JournalLine.NetAmount` (X:22862, "positive for a debit and negative for a credit"), `GrossAmount` (X:22868, NetAmount + TaxAmount), `TaxAmount` (X:22874) | Split by sign into two non-negative columns. Whether the sum of `NetAmount` or of `GrossAmount` over a journal is zero is not stated; the contract's "balanced journal" needs one of them to be. Unverified. | transform, unverified |
| `currency` | none on `Journal` or `JournalLine` (checked: property lists X:22771-22879). Organisation base currency: `Organisation.BaseCurrency` (X:23180), a different endpoint (X:10943). | Assume journal amounts are in the base currency (A031). | missing in the object; unverified |

### 3.5 `bank_transactions` from CAMT.053 (`Ntry` and `Ntry/NtryDtls/TxDtls`)

Entry-level elements are under `BkToCstmrStmt/Stmt/Ntry`. Where a batch entry holds several `TxDtls`, the contract row can be the entry or each `TxDtls`; that is gap G2 (section 4).

| Column | CAMT.053 element path and citation | Transformation | Status |
|---|---|---|---|
| `bank_transaction_id` | No element is documented as a globally unique row ID. `Ntry/NtryRef` (ASN 2.77: "unique sequence number for sorting", `[0..1]`, up to 12 characters; ING samples show 27-character values), `Ntry/AcctSvcrRef` (ING samples; not in the ASN table, WBC omits it for external entries), `TxDtls/Refs/TxId` (ASN 2.149). | Use a derived key of statement `Stmt/Id` (ASN 2.1; ING) plus the entry's own reference, falling back to position. Overlapping statement files would repeat entries, so the key must be stable across re-exports, or D064 ("dedupe exact repeats") will treat a repeat as a new row, or genuine equal debits as repeats. Whether `NtryRef`/`AcctSvcrRef` is unique across statements: unverified (G9). | transform, unverified |
| `posted_date` | `Ntry/BookgDt/Dt` (ASN 2.82; ING; WBC "Booking Date"), `[0..1]` | Booking date is the best match for "bank posting date". It is not the date the analyst could see the row (file creation: `GrpHdr/CreDtTm` ASN 1.2, `Stmt/CreDtTm`, ASN statement table). Also `Ntry/ValDt/Dt` (ASN 2.83; ING; WBC "Value Date"), a separate field that can differ (the three sources' examples are same-day). A022 treats `posted_date` as availability; with CAMT that is unverified (G6). | transform, unverified |
| `amount` | `Ntry/Amt` (ASN 2.78; ING; WBC) | Decimal text with dot separator (ASN). Sign: `docs/export-formats.md` section 3.2 says `Amt` is always positive. The ASN text re-read for this mapping states no sign rule, so the adapter should reject a negative `Amt` and not assume. Scale by currency (ASN says "2 decimals" in a EUR context). | transform |
| `currency` | `Ntry/Amt/@Ccy` (ING; WBC `Ccy="AUD"`); statement account currency `Stmt/Acct/Ccy` (ASN 2.10, `[0..1]`) | Entry attribute wins; reject when it disagrees with the account currency. | direct |
| `direction` | `Ntry/CdtDbtInd` CRDT or DBIT (ASN 2.79) | CRDT gives `credit`, DBIT gives `debit`, read from the account holder's side (perspective not stated in the three sources; unverified). **Reversal entries**: ASN 2.80 says `RvslInd` is "YES" for return, refund, reversal or recall, but none of the three sources says whether `CdtDbtInd` on such an entry shows the original direction or the economic direction. Do not map REV until this is checked against the ISO definition (A032). | transform, unverified |
| `status` | `Ntry/Sts` (ASN 2.81: "always BOOK"; ING `BOOK`); `Ntry/RvslInd` (ASN 2.80, `[0..1]`) and `TxDtls/RtrInf/Rsn/Cd` (ASN 2.293-2.305, return reason code; WBC adds `RtrInf/AddtInf` "Original Transaction Id" for NPP returns and reversals) | `posted` = `Sts` BOOK. The contract's `returned` (a payment bounced back) has no single element: it is `RvslInd` plus `RtrInf` plus an opposite entry, shape varying by bank. Pending items are absent from booked statements (ASN: `Sts` always BOOK), so a "pending bank row" never exists, which matches the contract (bank rows are `posted` or `returned`). | partial |
| `transaction_type` | `Ntry/BkTxCd/Domn/Cd`, `Fmly/Cd`, `Fmly/SubFmlyCd` (ING; WBC), and the bank's own `BkTxCd/Prtry/Cd` (ASN 2.98, 4-digit; ING `00100`, WBC `013`) | Map code to the contract's classes: fee row and reversal row matter, the rest do not. WBC lists a fee as domain CAMT, family ACCB, sub-family CHRG (code 015, debit), and cheque dishonour, SCT credit return and reversal rows under PMNT. The ISO code lists were not reachable. A mapping table is per bank (the Prtry code is bank-defined). | transform |
| `counterparty_token` | For a DBIT entry the creditor: `TxDtls/RltdPties/CdtrAcct/Id/IBAN` (ASN 2.205) or `Cdtr/Nm` (ASN 2.204); for CRDT the debtor (ASN 2.201-2.202) | ING's international-payment sample carries only `Cdtr/Nm`, no account, so the field is bank- and product-dependent. Normalise to the same token form as `payments.bank_account_id`. | partial |
| `payment_reference` | `TxDtls/Refs/EndToEndId` (ASN 2.148, "unique payment reference", `[0..1]`, up to 35 characters); also `Refs/InstrId` (ASN 2.147), `Refs/TxId` (2.149), `Refs/PmtInfId` (2.146), `RmtInf/Ustrd` (ASN 2.235, up to 140 characters, repeatable), `RmtInf/Strd/CdtrRefInf/Ref` (ASN 2.262; ING shows `<Ref>TFS2009357398867`) | Pick one element per rule and keep which one it was. Presence is not guaranteed: in the ING samples `EndToEndId` appears in the direct-debit, iDeal and cash-balancing entries but not in the SEPA credit transfer sample shown; WBC says `EndToEndId` is "typically passed through the banking system from the initiating payment file" and, for bulk settlements, is filled only "if available" from the payables batch. Truncation or rewriting by the bank: unverified. | partial |

### 3.6 `bank_statements` from CAMT.053 (unused by any check, listed for the completeness gap)

`bank_statement_id` from `Stmt/Id` (ASN 2.1; ING), `bank_account_id` from `Stmt/Acct/Id/IBAN` (ASN 2.10; ING), `statement_date` from the balance date `Stmt/Bal/Dt/Dt` (ASN 2.36) or the period `Stmt/FrToDt` (ING). CAMT also carries what the snapshot dropped (D046): opening and closing booked balances (`Bal` with `Tp/CdOrPrtry/Cd` OPBD and CLBD, ASN 2.23-2.35, per-currency by construction because `Bal/Amt` has `@Ccy`) and entry counts and sums (`TxsSummry/TtlNtries/NbOfNtries` and `Sum`, `TtlCdtNtries`, `TtlDbtNtries`, in the ING sample only, not in the ASN table). These are the only documented completeness evidence in any shortlisted format (see G10).

## 4. Gaps

IDs are used in section 5 and 6.

| ID | Gap | What the cited sources show |
|---|---|---|
| G1 | Payment to bank-row reference link | Xero's payment reference is optional free text (X:23967) or a batch `Details` sent to the bank (X:21039). CAMT carries a per-payment reference only if the payer's file set it and the bank passes it through (`EndToEndId`, ASN 2.148; WBC). Nothing in either source says the two strings are equal. Xero exposes no bank statement line to match against (the spec has no statement-line path; the only bank-named paths are `/BankTransactions`, `/BankTransfers` and `/Reports/BankSummary`, and the word "statement" appears only in batch-payment field descriptions), so the contract's A1 and A2 searches rest on an unverified equality. |
| G2 | Batch grouping | Xero: `BatchPayment` bundles payments into one bank transaction (X:565). CAMT: `Ntry/NtryDtls/Btch` with `NbOfTxs`, `TtlAmt`, `PmtInfId` (ASN 2.136-2.141; ING shows `PmtInfId` and `NbOfTxs`, without `TtlAmt`, and no `TxDtls` under it). WBC can replace a bulk `TxDtls` with one per payment ("debulked payables enrichment"). The contract has no bank-batch concept; its BATCH variant is one payment with several invoices. A batched payment run therefore shows one bank debit for many Xero payments, so an A1 reference search per payment finds nothing and would flag a settled payment as missing. |
| G3 | Payment method | Not in Xero (3.2). Derivable from a bank row's `BkTxCd` (WBC cheque codes ICHQ/RCHQ) only when a bank row exists; F12 and PEND concern payments without one. |
| G4 | Fee journal sourced to the bank row (A027) | Xero `SourceID` identifies a Xero source transaction (X:22794), and a Xero `BankTransaction` (X:20611) is an accounting record, not a statement line. No element links a journal or `BankTransaction` to a CAMT entry; only the boolean `IsReconciled` (X:20636) exists. CAMT shows fees as separate entries with their own transaction code in at least WBC (CAMT/ACCB/CHRG); `Chrgs` was not found in the ASN guide or ING samples; a netted fee shown inside the entry is unverified (A009). |
| G5 | Reversals | Xero: payment `Status` DELETED, no reversal link, journal effect not stated. CAMT: `RvslInd` (ASN 2.80), `RtrInf` reason (ASN 2.293), WBC `RtrInf/AddtInf` original transaction id (NPP only). Direction semantics of reversal entries unverified (A032). |
| G6 | Dates | Xero has an accounting date plus a creation timestamp on journals; CAMT has `BookgDt`, `ValDt` and file creation time. The contract's single `posted_date` and `posting_date` are availability proxies by assumption (A022, D045). |
| G7 | Clearing/pending status | Neither format has a pending state for the payment (Xero) or the bank row (CAMT, booked only). The contract's PEND check relies on window arithmetic and absence, not on a status, so it survives; its `payment_status`/`settlement_status` inputs do not. |
| G8 | Currency semantics | Payment currency and journal currency are not stated fields in Xero (3.2, 3.4). |
| G9 | Bank-row identity and repeated imports | No documented globally unique row key in CAMT (3.5). |
| G10 | Completeness (A025, P7) | CAMT carries opening/closing balances and entry counts per statement; Xero's Accounting API does not carry the bank side. The contract has no input for either. |

## 5. Gradability per evidence-contract check

Definitions. **Gradable (conditional)**: every record the check needs has a documented source in Xero or CAMT.053, and the check can run if gap G1 holds in the practitioner's data (the payment reference equals a CAMT reference). **Partial**: the check can run in a bounded or changed form, but at least one required record has no documented source and needs a stated substitute or a contract change. **Not gradable as written**: a required record has no documented source and the contract's own rules (absence evidence needs a stated scope, `docs/evidence-contract.md` principle 3) leave no substitute inside the current contract.

| Check | Verdict | Fields that decide it |
|---|---|---|
| F06 | Gradable (conditional) | Invoice, allocations (Xero `Payment.Amount` per invoice), payments (`Status` AUTHORISED, weaker than "settled", but the test is the bank debit), posted debit of each payment (G1). Without G1 every case becomes the RCA_000588 shape: overage proven on the Xero side, commitment unproven. Repeated import of one CAMT statement (G9) would fake a duplicate debit; the contract's D064 identity must use a stable bank key. |
| SPLIT | Gradable (conditional) | Sum of allocations vs invoice total (Xero); each debit equals its payment (G1). |
| DUE | Gradable (conditional) | `Invoice.DueDate` equals `Payment.Date`; each debit equals its payment (G1). |
| F12 | Partial | Reference search A1/A2 (G1: optional, non-unique Xero reference); method for the age limit missing (G3); payment journal sourced to the payment (`SourceID` unverified, 3.4); A3 needs the counterparty account on both sides (partial both). Batched payments produce false absences (G2). Completeness of the statement set must be declared (G10). |
| PEND | Partial | As F12; additionally the window test needs the method (G3). `payment_status` has no pending value, but the contract allows "inside window". |
| WKND | Partial | Method (ACH) not in Xero; derivable from the matched bank row's `BkTxCd` for this check because a debit exists (per-bank code table). Lag is `BookgDt` minus `Payment.Date` (G6; no holiday calendar, A026). Debit located via G1. |
| CHK | Partial | Same as WKND, with cheque codes (WBC ICHQ). |
| BATCH | Partial | The contract's "payment with two or more allocations" equals a Xero `BatchPayment` only under A030. `TotalAmount` (X:21082) = sum of member payments = CAMT entry amount, but linking a batch to its entry needs `BatchPayment.Details`/`Reference` equal to `PmtInfId` or `Ustrd` (unverified), or an amount-and-date match (a new heuristic). |
| FX | Partial | Detection of "payment currency differs from bank currency" needs `payment_currency` (G8) and `Ntry/Amt/@Ccy`. Xero's `BankAmount` and `CurrencyRate` (X:23962, X:23952) are information the abstention rule (D059) currently ignores. |
| F14 | Not gradable as written | The delta is computable (Xero `Amount` vs `Ntry/Amt`, once G1 holds). The diagnosis needs the absence search "no balanced `bank_fee` journal sourced to this bank row" (A027). Xero cannot source a journal to a CAMT entry and cannot label a journal as a fee (G4). A relaxed search (any spend journal for the delta on the bank account in a date window) is a new parameter, a contract change. |
| NETFEE | Not gradable as written | Needs a fee journal sourced to the bank row (G4) and the fee visible against the debit; `Chrgs` not found (A009). |
| FEEROW | Not gradable as written | The fee row itself is findable in CAMT by `BkTxCd` (WBC CAMT/ACCB/CHRG); resolving its reference to one payment is bank-specific and unverified (fee rows normally refer to the account, not the payment: unverified); the journal sourced to the fee row has no Xero equivalent (G4). |
| REV | Not gradable as written | ERP side: payment "marked reversed" and a `payment_reversal` journal sourced to the payment have no Xero field (G5, 3.2, 3.4). Bank side: debit plus a reversal credit exist (`RvslInd`, BkTxCd), but direction semantics (A032) and the reference link from the credit to the original debit (WBC states one only for NPP) are unverified. |

Tally (13 checks): gradable (conditional on G1) 3: F06, SPLIT, DUE. Partially gradable 6: F12, PEND, WKND, CHK, BATCH, FX. Not gradable as written 4: F14, NETFEE, FEEROW, REV.

Nothing here is a measured result. It is an inventory of documented fields. Whether G1 holds in real data is the single largest unknown, and no source I could read answers it.

### 5.1 Specific questions from the task

- **Bank row to payment link.** Xero side: `Payment.Reference` (optional), `Payment.Details`, `BatchPayment.Details/Reference/Narrative`. Bank side: `EndToEndId`, `InstrId`, `TxId`, `Ustrd`, `Strd/CdtrRefInf/Ref`, `AcctSvcrRef`, `NtryRef`. The equality is unverified for every pair. Treat "reference present, unique, equal" as a precondition the adapter measures and reports per file (share of payments with a non-empty, unique reference; share of bank debits with a non-empty reference).
- **Fee journals sourced to the bank row (A027).** Cannot be satisfied from Xero + CAMT.053: there is no link between a Xero source transaction and a CAMT entry, and no fee marker on a journal (G4). A027 stays "holds in FinRCA; unverified for real ledgers", and the mapping adds: not representable in the shortlisted pair.
- **Posted versus value date (A022).** CAMT has both (`BookgDt`, `ValDt`) plus the file creation time; the contract has one `posted_date`. Mapping `BookgDt` to `posted_date` is the nearest, but A022's claim (an analyst can see the row from that date) is a statement about visibility, which `BookgDt` does not establish; file creation time is closer to it. On the Xero side the same issue is `JournalDate` versus `CreatedDateUTC`.
- **Clearing status.** Xero has none for the bank; CAMT has booked entries only. The contract's clearing checks are windows over dates and absence, so they do not need a status, but they do need method (G3).
- **Batch grouping.** Present in both formats, absent from the contract (G2). It creates false F12 flags if ignored.
- **Reversals.** Both formats have something (Xero `Status` DELETED; CAMT `RvslInd`/`RtrInf`/transaction codes), neither maps cleanly to the contract's REV conditions (G5).

## 6. Concepts the formats have that the contract lacks

The contract is not changed here. "Consider later" means: raise it when the practitioner interviews and the M2 design say it matters. The M1 contract-design task that is still open ("avoid fields only FinRCA provides", `PLAN.md` M1) is where the choice belongs.

| Concept | Format source | Contract today | Consider later? |
|---|---|---|---|
| Batch grouping (bank-side one entry, many payments) | CAMT `Btch/NbOfTxs/TtlAmt/PmtInfId` (ASN 2.136-2.141; ING); Xero `BatchPaymentID` (X:24008), `BatchPayment.TotalAmount` (X:21082) | None (BATCH is multi-invoice, not multi-payment) | Yes, before any real-data use. Without it F12/PEND on batched runs give wrong absences. |
| Reversal/return indicator and reason | CAMT `RvslInd` (ASN 2.80), `RtrInf/Rsn/Cd` (ASN 2.305); WBC original-transaction id | One `status` value `returned` and a `payment reversal` type | Yes, for REV and F11 neighbours. Verify direction semantics first (A032). |
| Value date alongside booking date | CAMT `ValDt` (ASN 2.83) | `posted_date` only (`transaction_date`, the FinRCA initiation date, is unused) | Yes, with A022 (which date drives the clearing lag and the cutoff). |
| Statement balances, entry counts, period | CAMT `Bal` OPBD/CLBD (ASN 2.23-2.35), ING `TxsSummry`, `FrToDt` | `bank_statements` has ids and dates only (D046 dropped the money); no check uses it | Yes. It is the documented answer to open question 1 and A025 (completeness of absence evidence): continuity of closing and opening balances and entry counts can be tested per account and currency. |
| Reference kinds (EndToEndId, InstrId, TxId, PmtInfId, Ustrd, structured creditor reference, AcctSvcrRef) | CAMT (3.5) | One `payment_reference` | Yes: keep which kind matched; a match on free text is weaker than on `EndToEndId`. |
| Standard transaction codes | CAMT `BkTxCd` Domn/Fmly/SubFmlyCd (ING; WBC) | FinRCA text types (`ACH debit`, `bank fee`, ...) | Yes: it is the realistic source of fee, reversal and method classes. |
| `IsReconciled` | Xero `Payment` X:23970, `BatchPayment` X:21094, `BankTransaction` X:20636 | None | Maybe, as an ERP-side hint only, never as settlement evidence (D036). A payment `IsReconciled` true also tells an analyst where to look; false does not mean missing. |
| Currency rate and bank-currency amount | Xero `CurrencyRate`, `BankAmount`; CAMT `AmtDtls/InstdAmt` (ASN 2.156) | FX abstains (D059) | Later. It could make some FX pairs gradable; ERP-computed rates are not bank evidence. |
| Other settlement channels | Xero `Invoice.AmountCredited`, `Allocation` (credit notes, overpayments, prepayments) | Payments only | Yes for F06 (an invoice can be settled partly by credit). |
| Creation timestamps | Xero `Journal.CreatedDateUTC` (X:22785), `Payment.UpdatedDateUTC` (X:23992) | Posting date as availability (D045) | Yes, with A022/A034. |
| Account classification of journal lines | Xero `JournalLine.AccountCode/AccountType` (X:22847, X:22851) | `gl_account` dropped from the checks | Yes. The only way to recognise a fee journal in a real ledger. |
| Charges element | CAMT `Chrgs`: not found in ASN or ING | none | Unknown; check in real files (A009). |

## 7. Bank CSV fallback: what a minimal CSV must contain

No standard exists (`docs/export-formats.md` section 3.1). QBO's import accepts Date/Description/Amount (or a credit/debit pair) only (Intuit article cited there). That minimum **cannot** serve any contract check reliably: it has no row id, no currency, no status, no separate reference, no type. It would force parsing the Description for everything. The columns each check needs from a CSV (a CSV file standing in for CAMT.053; the accounting side is still Xero or another export):

| Needed column | Required by | Notes |
|---|---|---|
| Row id, stable across re-exports | all (D064 identity, citations) | If absent, adapter builds a key from account, date, amount and a row counter; two equal debits then collide with repeats, so ask the practitioner. |
| Account (own account id/IBAN) | all with several accounts | |
| Booking date | all (cutoff, clearing lag) | One date format per file; document the date type (booking or value). |
| Value date | WKND, CHK if the lag is measured on it | Optional unless practitioners say otherwise (A022). |
| Amount, with either a signed convention or a debit/credit pair, plus currency per row or per file | all | `Decimal` text; document the decimal separator. |
| Direction | F06, REV | Derived from sign or from the debit/credit columns. |
| Reference in its own column (or a documented pattern in the description) | F06, F12, PEND, WKND, CHK, DUE, SPLIT, F14, NETFEE, REV, FEEROW | G1. A full-length reference (not a truncated description) is needed; MT940 shows a 16-character field at BNP (`docs/export-formats.md` section 3.3). |
| Transaction type or code | F12 (fee rows must not count), F14, NETFEE, FEEROW, REV, and method for WKND/CHK | At least: fee, reversal/return, other. |
| Counterparty account | F12 A3 | Optional: without it A3 cannot run and F12 stays `abstained` where a candidate might exist. |
| Status (booked, returned) | F06, REV | Or a declaration that the file holds booked entries only. |
| Batch id and member count or total | F12, PEND, BATCH | G2. Without it, batched debits cannot be told from missing settlements. |
| Original reference of a reversal | REV | |
| Statement period and opening/closing balance | A025/P7 completeness | Per account and currency. |

Checks a CSV with only Date, Description, Amount can attempt: none to a stated standard. It can support an analyst view, with every absence claim labelled unscoped.

## 8. Proposed decision and assumption entries (for the parent to record)

Numbers are provisional; the parent assigns the final ones (the log currently ends at D065 and A028).

```markdown
### D066 — Input mapping is provisional and the contract is unchanged
- Date / milestone / agent: 2026-11-01 / M1 / Sonnet builder
- Decision: `docs/input-mapping.md` records which contract inputs Xero and CAMT.053 can populate (13 checks: 3 gradable conditional on a payment-to-bank reference link, 6 partial, 4 not gradable as written). The evidence contract and adapter are not changed by it. Gaps G1 to G10 are carried to the practitioner questions and the M2 design.
- Where: docs/input-mapping.md
- Context: PLAN.md section 6 M1 gate: "a written mapping shows each shortlisted format can populate the contract, or records which fields it lacks".
- Alternatives considered:
  1. Change the contract now to fit Xero and CAMT — rejected: no practitioner has confirmed the shortlist; the contract is validated on FinRCA only.
  2. Write the mapping and leave the contract — chosen.
- Grounded reason: PLAN.md section 6/M1 gate; the Xero spec at df43c6d and the ASN, ING and Westpac camt.053 descriptions (checked 2026-11-01); mapping lines cite them.
- Assumptions: A029, A030, A031, A032, A033, A034, A035
- Revisit if: practitioners use different formats (A007), or real exports show the reference link or fee journals (A029, A027).
- Status: active

### A029 — Xero payment reference equals a CAMT reference
- Assumption: For a payment made through the shortlisted pair, the Xero `Payment.Reference`/`Details` (or `BatchPayment.Details`) text reappears unchanged in a CAMT element (`EndToEndId`, `Ustrd`, `Strd` reference), unique per payment.
- Relied on by: D066, the A1/A2 searches of D056
- Status: unverified (neither source states it; Xero's field is optional free text, X:23967)
- How to verify: practitioner interview, or one matched Xero-and-bank file pair; report the share of payments with a unique non-empty reference.

### A030 — A Xero Payment is one allocation; a BatchPayment is the contract's multi-allocation payment
- Assumption: One Xero `Payment` (single `Invoice`, X:23927) maps to one `payment_allocations` row and one contract payment; a `BatchPayment` (X:21021) with several member payments maps to a contract payment with several allocations (BATCH).
- Relied on by: D066, the BATCH, SPLIT and F06 mappings
- Status: unverified (interpretation of the spec; a batch is described as one payment reconcilable to one bank transaction, X:565)
- How to verify: inspect a batch payment and its bank line in a Xero demo or test organisation, or ask a practitioner.

### A031 — Currency of Xero amounts
- Assumption: `Payment.Amount` is in the invoice currency, `BankAmount` in the bank account's currency, and journal line amounts are in the organisation's base currency.
- Relied on by: D066, the payment_currency and gl_entries.currency mappings
- Status: unverified (no currency field on `Payment`, `Journal` or `JournalLine`; the descriptions only imply it)
- How to verify: a multi-currency Xero test organisation, or the Xero developer documentation read in a browser.

### A032 — Direction of a CAMT reversal entry
- Assumption: Not made. The mapping leaves it open whether an entry with `RvslInd` YES shows the original or the economic direction in `CdtDbtInd`.
- Relied on by: D066, the REV mapping
- Status: unverified (ASN 2.80 says only "YES for return, refund, reversal or recall")
- How to verify: the ISO 20022 catalogue definition of ReversalIndicator, or real files.

### A033 — Booking date as posted_date
- Assumption: Mapping `Ntry/BookgDt` to `posted_date` keeps the clearing-lag checks meaningful, and a different date (`ValDt`) would not change a verdict by more than the one-day granularity; extends A022.
- Relied on by: D045, D054, D066
- Status: unverified
- How to verify: compare `BookgDt`, `ValDt` and file creation time in real CAMT.053 files from practitioners.

### A034 — Xero journal availability date
- Assumption: `Journal.JournalDate` can be used as the ledger line's availability date for the cutoff rule; `CreatedDateUTC` is the alternative if backdating occurs.
- Relied on by: D045, D066
- Status: unverified (X:22778 and X:22785 differ in meaning; the spec does not discuss backdating)
- How to verify: a Xero test organisation with a backdated journal, or practitioner input.

### A035 — Payment method for clearing limits
- Assumption: Where Xero lacks `payment_method`, it can be supplied per Xero bank account or batch, or read from the CAMT `BkTxCd` of the matched row.
- Relied on by: D066, the P1 clearing limits (D054)
- Status: unverified (no Xero field; CAMT codes are bank-specific)
- How to verify: practitioner interviews on how they pay (batch files, cheques, cards) and real CAMT code values.
```

Also: A009 (fees in CAMT.053) has partial support: the Westpac code table lists a fee under CAMT/ACCB/CHRG (code 015). One bank; leave status unverified and add that evidence line. A027 additionally: "not representable in Xero + CAMT.053 as documented".

## 9. What this does not show

- Real exports. The Xero spec lists fields, not how a Xero organisation uses them; the three CAMT sources are two bank descriptions and one sample file.
- Whether any practitioner uses these formats. The pending practitioner input can change the shortlist and the verdicts.
- Anything for QuickBooks Online, NetSuite, Sage, SAP, MT940 or BAI2; they were compared in `docs/export-formats.md` only.
- Any run of the adapter or the checks against Xero or CAMT data. No such file was loaded; no fixture was built.

## 10. Measured behaviour (M5)

**Evidence status: synthetic fixtures and two public samples. No real export was read and no practitioner has confirmed any mapping.** The fixtures in `fixtures/xero_camt/` are invented by this project in the published shapes. Where a fixture "works", that shows the mapping code and the investigator handle that shape, not that a real Xero organisation or bank produces it. Sections 1 to 9 are unchanged; this section reports what the code of M5 does, so a prediction in section 5 can be compared with a measurement.

### 10.1 What exists

| Piece | Path |
|---|---|
| Xero reader (saved `Invoices`, `Payments`, `BatchPayments`, `Journals` JSON) | `src/reconcile/adapters/xero.py` |
| CAMT.053 reader (XML) | `src/reconcile/adapters/camt053.py` |
| Config, and the call into `prepare_operational_snapshot` | `src/reconcile/adapters/xero_camt.py`, `prepare_xero_camt(config_path, out_dir, *, cutoff)` |
| Invented organisation, 18 invoices, 21 payment objects, 2 statements | `fixtures/xero_camt/` (with `config.toml`) |
| Tests | `tests/unit/test_xero_adapter.py`, `test_camt053_adapter.py`, `test_xero_camt_end_to_end.py`; `tests/integration/test_public_format_samples.py` (skips without the downloaded samples) |

There is no live API call: the input is files the user saved. The fixture folder sits beside, not inside, `fixtures/public/`, because `tests/unit/test_fixtures.py` treats every folder in `fixtures/public/` as a FinRCA scenario with a row in `expected.json`.

### 10.2 Interpretations the code makes

Each is a choice, not a fact from a specification, unless a citation says otherwise. "Unverified" means no cited source states it.

Xero:

1. **Status.** `AUTHORISED` gives `payment_status` `completed` and `settlement_status` `settled`, meaning only "the ledger records this payment as made". `DELETED` gives `reversed`/`reversed`. Xero has no pending value (X:23973), so a pending all-clear can never come from the status; `legitimate_pending_settlement` can still be reached by window arithmetic (10.3). `IsReconciled` is never read for settlement (D036); a test sets it both ways and expects no change. Unverified: that `AUTHORISED` is a good stand-in for "made".
2. **A030 applied.** A `PAYBATCH` `BatchPayment` is one contract payment (`payment_id` = `BatchPaymentID`, amount = `TotalAmount`, which must equal the sum of its members, else refused). Its member payments become allocations (two members for one invoice are summed). A `Payment` carrying `BatchPaymentID` is not also a payments row; it must be listed by the batch and have the batch's status, else refused. The member `PaymentID`s are kept in the manifest extras only. Still unverified (A030): that a batch is a "payment" in the contract's sense.
3. **Currency (A031).** Payment currency is the invoice's; a batch whose invoices have several currencies is refused. Journal currency is the config `base_currency`. Unverified. Bank-account currency is not available on a `Payment`, so an invoice in a foreign currency yields a currency mismatch against the bank debit, which the investigator abstains on as FX.
4. **Dates.** `/Date(ms+0000)/` at UTC midnight and `YYYY-MM-DD` are accepted. Any other offset, a time of day, or any other text is refused: the spec does not say what an offset means. `allocation_date` is the payment's date. `JournalDate` is the posting date (A034 unverified).
5. **Which invoices and payments.** Only `Type` `ACCPAY` invoices, `PaymentType` `ACCPAYPAYMENT` payments and `PAYBATCH` batches are used. The rest are counted in the manifest (`extras.xero`). Invoices of other statuses are kept with the Xero status in lower case (`paid` only for `PAID`). A payment for an invoice that is not an `ACCPAY` invoice in the files is refused.
6. **Method (G3).** Not in Xero. Config `payment_method_by_account` maps a Xero account code (`Payment.Account.Code`) to `ACH`, `wire`, `check` or `virtual_card`; `default_payment_method` covers the rest; otherwise the method is empty and the investigator abstains (`required_field_missing`). The CAMT `BkTxCd` is not used to infer a method (A035 stays unverified).
7. **Reference.** Config `payment_reference_field` is `Reference` (default) or `Details`, for payments and batches alike. No fallback between them. An empty value is NULL.
8. **Payee account.** `payments.bank_account_id` is `Payment.BankAccountNumber`; it is NULL for a batch (members may pay different suppliers). Its format (IBAN or national) is not stated (3.2), so it only matches a CAMT counterparty if both are IBANs.
9. **Journals.** `SourceID` is stored unchanged; `SourceType` `ACCPAYPAYMENT` gives `payment`, assuming `SourceID` is the `PaymentID` (A027/A036 unverified). Every other `SourceType` becomes `xero_<type in lower case>` so it can never match `payment`. `NetAmount` is split by sign into `debit` and `credit`; a journal whose lines do not sum to zero, or that has no lines, is counted in the manifest, not refused. Fee and reversal journals are not representable (G4, G5).
10. **No approvals.** The Xero spec has no approval history, so `approval_events` is not supplied and every all-clear abstains. This is the correct behaviour for these inputs.
11. **Credit notes and overpayments** (`AmountCredited`) are invisible to the contract. Invoices with a non-zero value are counted in `extras.xero`.
12. **Pagination.** `Invoices*.json`, `Payments*.json` and so on in `xero_dir` are all read; an exact repeat across files is dropped and counted, a different object with the same ID is refused. All four kinds must have at least one file (an empty `[]` response is fine), so a missing export is not read as no records.

CAMT.053:

13. **XML safety.** A `DOCTYPE` or entity declaration is refused by an expat handler, with tests for a billion-laughs file and an external entity. Only namespaces `urn:iso:std:iso:20022:tech:xsd:camt.053.001.*` and a root `Document` are accepted; elements are matched by local name.
14. **Rows.** One row per booked `Ntry` (`Sts` `BOOK`, also nested `Sts/Cd`); other entries are counted and excluded. An entry is never split into its `TxDtls`. `Amt` must be a plain non-negative decimal, `@Ccy` must equal `Stmt/Acct/Ccy` when that is stated, `CdtDbtInd` gives the direction. `BookgDt` is `posted_date`, `ValDt` is `transaction_date`. A booking date outside its statement's stated period is refused.
15. **Row ID.** `AcctSvcrRef`, else `NtryRef`, else `<Stmt/Id>:<position>`; the manifest counts which was used. Two entries with the same ID are refused naming both (a repeated `NtryRef` across statements would otherwise merge two real debits). Unverified (G9): that `AcctSvcrRef` is unique.
16. **Reference.** Config `bank_reference_fields` (default `["EndToEndId"]`; choices `EndToEndId`, `InstrId`, `TxId`, `PmtInfId`, `Ustrd`, `CdtrRefInf`), first non-empty wins; repeated `Ustrd` or `CdtrRefInf` are joined by a space. `NOTPROVIDED` counts as absent. **Assumption, not confirmed in a source I read:** the ING sample PDF does not contain the word; I rely on remembered SEPA practice for it. With no `TxDtls`, or several, the row's reference is `Btch/PmtInfId` when there is one, else NULL, so the investigator's batch guard applies.
17. **Counterparty.** For a debit, the creditor account IBAN; for a credit, the debtor account IBAN; NULL when absent or when several `TxDtls` name different accounts. Never a name.
18. **Type.** `bank fee` when `BkTxCd` domain/family/sub-family matches config `fee_codes` (default `CAMT/ACCB/CHRG`, from the Westpac table in section 1: one bank's code list); `payment reversal` when `RvslInd` is `true`, `1` or `yes`; otherwise `bank debit` or `bank credit`. An entry that is both a fee and a reversal is refused. A032 stays unverified: the direction of a reversal entry is taken as written.
19. **Coverage (D160).** Each statement's period is `Stmt/FrToDt` (dates of `FrDtTm` and `ToDtTm` as written, no zone conversion), else the date of the `CLBD` balance as a one-day period. Per account and currency the periods must follow without a gap and without overlap. An exact repeat of a statement already read (same `Stmt/Id`, same content) is dropped and counted; the same `Stmt/Id` with different content is refused. Gap and overlap errors name both statements. `bank_data_through` is the earliest last day across accounts. A statement with neither period nor balance date is refused unless the config states `bank_data_through`; a config date later than the statements show is refused. Consequence: a multi-day statement that states only a closing balance date is read as one day and may be refused as a gap.
20. **Not checked:** a payment's own bank account (Xero `Account`) against the statement IBAN, so a statement of the wrong account would not be noticed.

### 10.3 Measured behaviour per check

Method: `prepare_xero_camt` on the fixtures, cutoff 2026-06-30, then `investigate` on one record per check. "As prepared" is exactly what the Xero and CAMT files give. "Approvals added" is the same rows plus one hand-written `approved` event per invoice, prepared directly (`tests/unit/test_xero_camt_end_to_end.py`, `WITH_APPROVALS`); it shows what the mapped rows allow once an approval source exists, and is not something these formats supply. The payment reference is equal on both sides by construction in the fixtures, so gap G1 is not tested by them.

| Check | Section 5 prediction | Fixture record | As prepared | Approvals added | Against the prediction |
|---|---|---|---|---|---|
| F06 | Gradable (conditional on G1) | invoice `inv-f06` | answered `F06` | answered `F06` | As predicted, given equal references. On the payment, `pay-f06-1`, it abstains `invoice_over_allocated`. |
| SPLIT | Gradable (conditional) | invoice `inv-spl` | abstained `invoice_approval_not_completed` | answered `legitimate_split_payment` | Rows suffice; the all-clear needs approvals the formats do not carry. |
| DUE | Gradable (conditional) | invoice `inv-due` | abstained `invoice_approval_not_completed` | answered `legitimate_due_date_payment` | Same. |
| F12 | Partial | payment `pay-f12` | abstained `counterparty_unavailable` | abstained `counterparty_unavailable` | Answered `F12` when the one unreferenced debit with no counterparty (`pay-unm`'s) is removed (tested). Without counterparty data on every in-window debit, F12 abstains. |
| PEND | Partial | payment `pay-pend` | abstained `counterparty_unavailable` | abstained `counterparty_unavailable` | Same cause; without that debit, answered `legitimate_pending_settlement` although Xero has no pending status (tested). |
| WKND | Partial (method missing) | payment `pay-wknd` | abstained `invoice_approval_not_completed` | answered `legitimate_weekend_clearing` | The method came from the config account mapping, not from `BkTxCd`. |
| CHK | Partial | payment `pay-chk` | abstained `invoice_approval_not_completed` | answered `legitimate_check_clearing` | Same; the account code `091` was mapped to `check` by the config. |
| BATCH | Partial (link needs an unverified field match) | payment `bat-1` | abstained `invoice_approval_not_completed` | abstained `payment_journal_missing` | The reference link worked through `Btch/PmtInfId`. It stops at the journal: Xero journals are sourced to the member `PaymentID`s, not to the `BatchPaymentID`, so the batch has no payment journal. Not in the section 5 prediction. |
| FX | Partial | payment `pay-fx` | abstained `unsupported_fx` | abstained `unsupported_fx` | As predicted (abstention only). |
| F14 | Not gradable as written | payment `pay-f14` | answered `F14` | answered `F14` | **Differs.** The code answers, because "no balanced `bank_fee` journal" is always true when no Xero journal can be typed as a fee. The answer means "shortfall with no fee journal the tool can recognise", which the analyst must read that way. |
| NETFEE | Not gradable as written | payment `pay-net` | answered `F14` | answered `F14` | **Differs, and misleading.** The fixture has a spend-money journal for the 20.00 fee (`CASHPAID`, typed `xero_cashpaid`); the tool still says an unexplained shortfall. As predicted, the fee journal is not representable; the prediction did not say the output would be a confident F14. |
| FEEROW | Not gradable as written | bank row `SVC-FEE-2` | abstained `accounted_fee_journal_missing` | abstained `accounted_fee_journal_missing` | As predicted. |
| REV | Not gradable as written | payment `pay-rev` | abstained `required_records_missing` | abstained `required_records_missing` | As predicted; never answered, in either mode (tested, A032). |

Shapes outside the 13:

| Shape | Record | Result |
|---|---|---|
| Reference not matched, no counterparty data on either side | `pay-unm` | abstained `counterparty_unavailable`, both modes |
| Payment with no method (Xero account code not in the config) | `pay-nom` | abstained `required_field_missing`, both modes |
| Credit note applied to an invoice (`AmountCredited` 100.00, paid 300.00 of 400.00) | `pay-crd` | as prepared: abstained on approvals; approvals added: answered `no_exception`, with the 100.00 credit invisible to the contract |
| Statement 2 entry dated after the cutoff | bank row `SVC-JUL-1` | dropped by the cutoff (1 row), counted in the manifest; `bank_data_through` is 2026-07-31 |

What this shows and does not show:

- The checks that ended in an answer used references that match by construction. Whether real Xero references equal real CAMT references (G1, A029) was not tested.
- Every legitimate all-clear (`legitimate_*`) is unreachable from these two formats alone, because they carry no approval history. A real user needs a third source for approvals, or the tool gives no all-clear. This is the largest gap the measurement found.
- Two answers (`F14` on the F14 and NETFEE shapes) rest on the absence of something Xero cannot hold. They are the places where a user could be misled; a decision on whether to abstain for this source is open (not taken here).
- The batch case is blocked only by the journal link, not by the reference link.

### 10.4 Public samples run

Downloaded into `data/public-samples/` (ignored by git; never committed). Raw files:

| File | Source | sha256 |
|---|---|---|
| `xero_accounting.yaml` | Xero-OpenAPI at `df43c6dd06c0eccffdf09297dcae3b0dbcf3a498` (MIT) | `1afca0717bb0d323210c0f88f3802684c068059f6e5161ee24ddde7ee6294a64` |
| `ing-sample-camt.pdf` | https://assets.ing.com/m/19ba05999348c5c7/original/Sample-Files-CAMT.pdf (licence not stated: kept local) | `23ef81661e51ef4cc6a23d25e2e80a5353a4b068e8ae8366c5020dc9adf7d052` |

Derived local files (two throwaway scripts outside the repository, not part of the product): the `GET` 200 response example of `/Invoices`, `/Payments`, `/BatchPayments`, `/Journals` read with PyYAML with its timestamp resolver switched off (so dates stay text) and written as JSON into `xero_examples/`; the PDF turned to text with `pdftotext -layout` (poppler 24.02), the one complete `<Document>` copied to `ing_camt053_sample.xml`, and the seven separate `<Ntry>` examples wrapped in a minimal statement as `ing_ntry_snippets.xml`. The PDF text shows those seven examples with all element names in lower case (`<ntry>`, `ccy=`); I restored the usual camel case from a name list written from the first sample, so that file is a reconstruction, not the bank's file. Page-break form feeds were removed.

Results (`tests/integration/test_public_format_samples.py`, structural facts only):

- **Xero spec examples.** All four files read without error: 3 invoices, 2 payments, 1 batch, 4 journals. They hold **no payable** object (all invoices are `ACCREC`, the payments are `ACCRECPAYMENT` and `ARCREDITPAYMENT`, the batch is `RECBATCH`), so the payables path of the reader was **not exercised** by public data. 9 journal lines were mapped, none typed `payment`, every journal sums to zero in `NetAmount`. The spec's own currency is `NZD`, which `Money` does not list, so preparing the examples is refused naming `NZD`. The `ACCRECPAYMENT` journal's `SourceID` equals the `PaymentID` in the payments example (the two examples are consistent with each other). This is weak support for A036, for a receivable payment in documentation examples, not for payables.
- **ING full sample** (one batch credit entry, `NL08INGB0000001234`): 1 row. `AcctSvcrRef` is the ID, `Btch/PmtInfId` the reference (no `TxDtls`), the amount 270.00 EUR credit, `bank_data_through` 2014-01-03 from `FrToDt`. The statement also holds `PRCD`, `OPBD`, `CLBD`, `CLAV` balances and `TxsSummry` (9 entries); the reader does not check the one entry present against `NbOfNtries` 9 or the balances. That sample is an excerpt, so such a check would refuse it; whether to add it is open.
- **ING entry examples** (7 entries): 7 rows, 4 credits and 3 debits, all with `AcctSvcrRef`; references from `EndToEndId` (4), `Ustrd` (2) and none (1). One entry (20534.21 CHF) disagrees with an EUR account when the wrapper states `Ccy` EUR; it is refused as designed. The wrapper used for the run states no account currency, because the example set mixes currencies, which no single account statement does. The `Ustrd` of one entry has a line break, which the operational step normalises.
- **No sample failed a parser for a reason that was the parser's fault.** The two failures met on the way were mine: form feeds copied from the PDF (removed), and a currency conflict I had introduced in the wrapper (removed, then tested separately as above).

### 10.5 Not tested

- Any real Xero or CAMT.053 export, and whether practitioners use these formats at all (section 9 still applies).
- G1: that a Xero reference equals a CAMT reference. The fixtures set them equal.
- Paginated or very large Xero files, credit-note payments, multi-currency organisations with `BankAmount` different from `Amount` (the manifest does not even count them), batches in a foreign currency.
- CAMT versions other than `.02` in a real file (`.08` is only accepted in a unit test), `Chrgs`, `AmtDtls`, `RtrInf`, multi-account files.
- A fee or reversal journal posted from the CAMT row (impossible in the pair, G4 and G5).
- Whether `AUTHORISED` is a good stand-in for "the payment was made" (interpretation 1), and whether `JournalDate` or `CreatedDateUTC` is the availability date (A034).
- The `NOTPROVIDED` rule against a primary source (interpretation 16).
- Approvals: no source is supplied, so no `legitimate_*` conclusion was reached without a hand-written approval table.
