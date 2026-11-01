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
