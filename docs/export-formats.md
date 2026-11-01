# Export formats: accounting and bank statements

**Status:** M0 research draft. All checks were made on 2026-11-01. The recommendation is provisional: the final choice depends on practitioner input (`docs/practitioner-findings.md`), and no practitioner has been consulted yet.

Rules used: only primary sources (vendor documentation or API specifications, standards bodies, banks' own format descriptions). Anything not confirmed from such a source is marked **unverified**. No sample files were downloaded into the repository. Specifications show the fields a format can carry, not what real exports contain.

## 1. Source access limits

| Source | Result on 2026-11-01 |
|---|---|
| iso20022.org, swift.com, developer.sage.com | HTTP 403 (bot protection). Not bypassed. |
| api.sap.com (SAP Business Accelerator Hub) and parts of help.sap.com | Sign-in required. |
| Xero Central CSV import article | Page opened but its sections were collapsed; column list not read. |
| SWIFT MT940 standard text | Not reachable. MT940 facts below come from one bank's implementation guide and are bank-dialect facts only. |

So: ISO 20022 element semantics are taken from two banks' camt.053 guides (ASN Bank, Westpac) and one sample file (ING), not from the ISO catalogue. Sage API fields are unverified.

## 2. Accounting systems

Fields below are what the object can carry. "Reference" fields differ in meaning by system, so a mapping needs a practitioner check.

### 2.1 Xero

- **Sources:** Official OpenAPI spec for the Accounting API, v19.1.0, MIT licence, repository last pushed 2026-10-29, commit `df43c6dd06c0eccffdf09297dcae3b0dbcf3a498`: https://github.com/XeroAPI/Xero-OpenAPI (file `xero_accounting.yaml`). The developer.xero.com reference pages render by script and could not be read as text; the spec is the source for everything below. Checked 2026-11-01.
- **Obtained by:** API (OAuth, `/Invoices`, `/Payments`, `/BatchPayments`, `/BankTransactions`, `/BankTransfers`, `/Reports/BankSummary` exist in the spec). UI export paths: **unverified**.
- **Supplier bill (Invoice, `Type` ACCPAY):** `InvoiceNumber`, `Reference`, `Date`, `DueDate`, `Total`, `AmountDue`, `AmountPaid`, `AmountCredited`, `CurrencyCode`, `CurrencyRate`, `Status` (DRAFT, SUBMITTED, DELETED, AUTHORISED, PAID, VOIDED), `FullyPaidOnDate`, `Payments[]`, `Prepayments[]`, `Overpayments[]`, `CreditNotes[]`, `PlannedPaymentDate` (bills), `Contact`.
  - The spec describes `InvoiceNumber` and `Reference` as "ACCREC" (sales invoice) fields. Which field holds the supplier's invoice number on a bill is **not stated in the spec**. Unverified; check with a practitioner or a test organisation.
- **Bill payment (Payment):** `PaymentID`, `Date`, `Amount`, `BankAmount` (amount in the bank account's currency), `CurrencyRate`, `Reference`, `Details`, `Status` (AUTHORISED or DELETED only), `IsReconciled`, `PaymentType`, `Account`, invoice link (`Invoice`, `InvoiceNumber`), `BatchPaymentID` ("present if the payment was created as part of a batch"), `UpdatedDateUTC`, `BankAccountNumber`, `Particulars`.
- **Batch payment (BatchPayment):** `BatchPaymentID`, `Type` (PAYBATCH for bills), `Date`, `TotalAmount`, `Payments[]`, `Status`, `IsReconciled`, `Reference`/`Details`/`Narrative` (sent to the bank as reference; shown in Xero's bank reconciliation matching screen). Region-specific field use (NZ, UK, other) per spec.
- **Bank transaction:** `BankTransactionID`, `Type`, `Date`, `Reference` (only SPEND and RECEIVE), `Status`, `IsReconciled`, `Total`, `CurrencyCode`, `CurrencyRate`, `Contact`, `LineItems`.
- **Reversal/void:** Invoice `Status` VOIDED/DELETED; Payment `Status` DELETED. No reversal link between records is documented.
- **Missing:** The Accounting API spec has no bank statement line endpoint (only `/Reports/BankSummary`), so the bank-side statement line as Xero holds it is not in this spec. Whether a separate API exposes it is **unverified**. Fees are not a separate field; they would be a separate bank transaction or payment line (not documented as such). Pending status exists only as bill/payment status, not as bank clearing status.
- **Samples:** The OpenAPI spec contains example payloads (586 `example:` entries counted), MIT licence. Xero Demo Company: **unverified**.

### 2.2 QuickBooks Online

- **Sources:** Intuit Developer API reference, rendered in a browser on 2026-11-01: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/bill, `.../billpayment`, `.../purchase`. Licence for the page samples: not stated.
- **Obtained by:** API (`Bill`, `BillPayment`, `Purchase`, plus reports such as TransactionListByVendor, VendorBalanceDetail and APAgingDetail named in the entity list). Whether and how those reports export from the UI: **unverified**.
- **Bill:** `Id`, `VendorRef`, `DocNumber` (max 21 characters, "reference number for the transaction"; duplicates raise an error unless `include=allowduplicatedocnum` is set), `TxnDate`, `DueDate`, `TotalAmt` (read only), `Balance` ("0 indicates the bill is fully paid"), `CurrencyRef`, `ExchangeRate`, `HomeBalance`, `LinkedTxn[]` (PurchaseOrder, BillPaymentCheck, ReimburseCharge), `PrivateNote`, `SalesTermRef`, `APAccountRef`.
- **BillPayment:** `Id`, `VendorRef`, `PayType` (Check or CreditCard), `TotalAmt`, `TxnDate`, `DocNumber`, `PrivateNote`, `CurrencyRef`, `ExchangeRate`, `Line[]` each with `Amount` and `LinkedTxn` (`TxnId`, `TxnType` Bill, VendorCredit or JournalEntry), `CheckPayment` (`BankAccountRef`, `PrintStatus`) or `CreditCardPayment`. One BillPayment can pay several bills through its lines. If `TotalAmt` exceeds the lines, the excess is treated as a credit.
- **Purchase (expense/cheque/card spend):** `PaymentType` Cash, Check or CreditCard, `AccountRef`, `Line[]`, `TotalAmt`.
- **Missing:** No reconciled/cleared flag appears on the Bill, BillPayment or Purchase reference pages (checked by reading the first two and searching the Purchase text for "reconcil", "cleared", "bank feed"; no match). Bank statement lines as QBO holds them are not exposed on these pages. No batch identifier beyond multi-line BillPayment. No fee field. Void exists as an operation ("Void a billpayment"); reversal linkage not documented.
- **Bank CSV import into QBO (relevant for what practitioners may already have):** required columns Date, Description, Amount; credit/debit split layout also described. Source: https://quickbooks.intuit.com/learn-support/en-us/help-article/import-transactions/common-errors-importing-bank-transactions-using/L02IgW462_US_en_US (checked 2026-11-01).
- **Samples:** Single sample objects inside each reference page. Licence not stated.

### 2.3 NetSuite

- **Sources:** SOAP Schema Browser, release 2026.1: `.../srbrowser/Browser2026_1/schema/record/vendorbill.html` and `.../vendorpayment.html` under https://system.netsuite.com/help/helpcenter/en_US/ ; Oracle help: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_N3695867.html (Vendor Payment), `.../section_1508961121.html` (default bank parsers), `.../article_1211120446.html` (CAMT.053 parser). Checked 2026-11-01.
- **Obtained by:** SOAP/REST APIs, saved searches and CSV export from the UI (UI export steps: **unverified**).
- **Vendor bill:** `tranId` ("Reference No.", "such as the vendor's invoice number"), `entity` (vendor), `tranDate`, `dueDate`, `userTotal` (amount), `currency`, `exchangeRate`, `status`, `approvalStatus`, `paymentHold`, `discountAmount`, `discountDate`, `terms`, `memo`, `installmentList`, `postingPeriod`, `subsidiary`.
- **Vendor payment:** `entity` (payee), `account` (bank account), `apAcct`, `tranDate`, `total`, `currency`, `exchangeRate`, `tranId` ("Check #"), `status`, `approvalStatus`, `memo` ("shows the vendor's invoice number or your account number"), `applyList` (Oracle help: bills the payment applies to, keyed by the bill and optionally its line), `creditList`, `isInTransitPayment`, `toAch`, `toBePrinted`, `voidJournal` (link to voiding journal), `balance`, `availableBalance`.
- **Missing:** No cleared/reconciled field on the vendor payment schema page. Bank-side matching is described under bank reconciliation, not on this record (field names **unverified**). No batch ID field on the payment record. No fee field.
- **Bank statement import:** The Bank Statement Parsers SuiteApp lists default-parser formats BAI2 (prior-day only; limited to five named US banks), CAMT.053.001.06, CSV (template), OFX, QFX; QIF unsupported; MT940 is **not** listed on that page. A separate CAMT.053 parser plug-in page states versions 001.02 and 001.08. The two pages disagree on versions, so which applies depends on the parser used. The SuiteApp does not create transactions from imported statements (per search summary of Oracle help; page not fully read: **unverified**).
- **Samples:** None found in a primary source.

### 2.4 Sage

- **Sources:** Sage Accounting help via gb-kb.sage.com: https://gb-kb.sage.com/portal/app/portlets/results/viewsolution.jsp?solutionid=222001000101106 (export bank transactions) and `...solutionid=222001000101121` (Unreconciled Bank Transactions report). Checked 2026-11-01.
- **Obtained by:** UI. Banking > account > select transactions > CSV or PDF icon. Reports > All reports > Unreconciled Bank Transactions (end date and bank account) with CSV and PDF export. The articles do not list columns.
- **API (Sage Business Cloud Accounting v3.1: purchase invoices, payments, bank reconciliations):** developer.sage.com returned HTTP 403, so **all API field names are unverified**. Sage Intacct and other Sage products are out of this review.
- **Missing / unverified:** field lists, reference fields, batch payment, fee handling, void/reversal.
- **Samples:** None found in a primary source.

### 2.5 SAP

- **Sources:** SAP Help Portal, S/4HANA Cloud Public Edition 2608, "Read Header Data of a Single Supplier Invoice": https://help.sap.com/docs/SAP_S4HANA_CLOUD/bb9f1469daf04bd894ab2167f8132a1a/234dd499fa1a4b0fb1708ece5c655d6e.html ; "Importing Bank Statements" (on-premise 2025 FPS02): https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/3cb1182b4a184bdd93f8d62e3f1f0741/4db73e226f795e98e10000000a15822b.html . Checked 2026-11-01.
- **Obtained by:** OData service `API_SUPPLIERINVOICE_PROCESS_SRV`, entity `A_SupplierInvoice` (key `SupplierInvoice` plus `FiscalYear`). Table/report exports and payment-run data: **unverified**.
- **Supplier invoice header (fields in the SAP sample response):** `SupplierInvoice`, `FiscalYear`, `CompanyCode`, `DocumentDate`, `PostingDate`, `SupplierInvoiceIDByInvcgParty` (supplier's invoice number), `InvoicingParty`, `DocumentCurrency`, `InvoiceGrossAmount`, `PaymentTerms`, `DueCalculationBaseDate`, `CashDiscount1Percent/Days`, `CashDiscount2Percent/Days`, `NetPaymentDays`, `PaymentBlockingReason`, `AccountingDocumentType`, `BPBankAccountInternalID`, `DirectQuotedExchangeRate`, `PaymentMethod`, `PaymentReference`, `InvoiceReference`, `AssignmentReference`, `SupplierInvoiceIsCreditMemo`, `InvoiceReceiptDate`; items under `to_SuplrInvcItemPurOrdRef`, `to_SupplierInvoiceItemGLAcct`, `to_SupplierInvoiceTax`, `to_SupplierInvoiceWhldgTax`.
- **Bank statements into SAP:** Formats processed directly include SWIFT MT940 and BAI; others are converted to MultiCash first. SAP states that SWIFT dialects differing from the norm are not supported, and that automatic assignment to open items depends on the note-to-payee text. Other SAP pages (search results) list CAMT.053.001.02 and .08 for import; those pages were not opened: **unverified**.
- **Missing / unverified:** payment document, payment-run and cleared-item APIs (the Business Accelerator Hub needs sign-in); reversal fields on payments; batch grouping. SAP landscapes vary widely, so an export is usually customer-specific.
- **Samples:** The SAP help page sample response (licence not stated).

## 3. Bank statement formats

### 3.1 Bank CSV

- **Standard:** None. Each bank defines its own columns. Variation shown by primary sources: QBO's import accepts Date/Description/Amount or a credit/debit split and requires dates in one format with no currency symbols (Intuit article above); NetSuite's default CSV parser requires NetSuite's own template; Sage describes CSV export without listing columns. Typical variation (column names, one signed amount versus debit/credit columns, date formats, decimal separators, free-text description holding all remittance data, no balance or status columns) is **unverified** as a general statement, as no single primary source covers it.
- **Obtained by:** Online-banking export. Content depends on the bank.
- **Relevant fields:** usually a date, a description, an amount. Value date, booking date, counterparty identifier, reversal indicator, batch grouping and status are present only if that bank adds them (unverified in general).
- **Samples:** Not applicable; each bank's own.

### 3.2 ISO 20022 CAMT.053 (BankToCustomerStatement)

- **Sources:** ASN Bank camt.053 description (based on the Dutch banks' implementation guideline, version 2.0 by file name; no date in the text): https://www.asnbank.nl/downloads/formaatbeschrijving-camt053.html ; Westpac BankRec camt.053.001.02 page: https://bankrec.westpac.com.au/docs/statements/iso/cash-management ; ING NL sample files (PDF created 2025-08-18; camt.053.001.02): https://assets.ing.com/m/19ba05999348c5c7/original/Sample-Files-CAMT.pdf . Checked 2026-11-01. The ISO catalogue was not reachable.
- **Obtained by:** bank download (e.g. EBICS, host-to-host, portal download). XML.
- **Fields per entry (`Ntry`):** `Amt` (always positive; `CdtDbtInd` CRDT or DBIT gives direction), `RvslInd` (optional; ASN: "YES" for return, refund, reversal or recall), `Sts` (ASN: always BOOK), `BookgDt`, `ValDt` (separate dates), `BkTxCd` (bank transaction code), `AcctSvcrRef` (bank's reference; in the ING sample), `NtryRef`.
- **Detail (`NtryDtls`):** `Btch` with `NbOfTxs`, `TtlAmt`, `PmtInfId` (batch / bulk grouping); one or more `TxDtls`, each with `Refs` (`EndToEndId`, `InstrId`, `TxId`, `MndtId`), `AmtDtls/InstdAmt` (instructed amount, for currency differences), `RmtInf` with `Ustrd` (free text, up to 140 characters per occurrence) or `Strd` (structured), related parties.
- **Missing:** Charges element (`Chrgs`) not present in the ASN guide or the ING sample, so fee handling is **unverified** (fees may be separate entries with their own transaction codes: unverified). Pending items are not in booked statements (ASN: Sts always BOOK). Real content depends on the bank: how much of the remittance is structured, whether `EndToEndId` is carried. Versions differ by bank (.02 in ING and Westpac; NetSuite lists .02, .06 and .08).
- **Samples:** ING sample PDF above (licence not stated); Westpac field descriptions (terms are in a separate Terms and Conditions PDF, not read).

### 3.3 SWIFT MT940

- **Sources:** BNP Paribas (Germany) "Detailed Reporting Characteristics" appendix (upload path dated 2022-04): https://www.bnpparibas.de/app/uploads/sites/21/2022/04/mt940-details.pdf . SAP statement on MT940 above. Checked 2026-11-01. The SWIFT standard itself was not reachable, so everything below is how one bank uses it.
- **Obtained by:** bank download or SWIFT/host-to-host delivery. Text format.
- **Fields:** statement line `:61:` with value date, entry date, debit/credit mark, amount, transaction type code, "Reference for the Account Owner" (`16x`; BNP: leading 16 characters of the client's EndToEndId, or the PaymentInformationId for a bulk debit), bank reference, supplementary details. `:86:` carries bank-defined detail: BNP uses keyword blocks such as `/EREF/35x/` (the full EndToEndId, mandatory in the BNP outgoing-payment layouts read, pages 6 and 15), `/PREF/35x/` (PaymentInformationId of a bulk; mandatory on the bulk-debit layout, page 9),  `/TYPE/` (entry code and label, with a distinct code for reversal of an outgoing payment) and `/OCMT/` (original amount and currency when converted). Charges: `/CHGS/3!a15d/` is defined only in the BNP layout for an incoming international payment (unitary booking, section 3, page 12), as conditional (C): "Charges applied to the booking of the transaction by the correspondent bank. The amount is calculated by the difference between the initiated payment and the received amount." The guide gives no condition for it there. A general remark (page 3) says that when currency conversion is present `/OCMT/`, `/CHGS/` and `/EXCH/` go in the first three GVC fields. I found no `/CHGS/` in the outgoing-payment or bulk layouts read, so it is not shown as a fee carrier for those. Batched debits appear as one line with supplementary text "BATCHBOOKING" at BNP.
- **Missing:** Remittance text is free-form and dialect-specific. The `:61:` reference is 16 characters, so it holds at most the leading 16 characters of the EndToEndId; at BNP the full EndToEndId (35 characters) is in `:86:` `/EREF/`, and bulk bookings carry the PaymentInformationId in `/PREF/`. That is one bank's dialect, not the SWIFT standard; other banks may carry less in `:86:` (unverified). A parser must read `:86:` keywords, not just `:61:`. SAP notes dialects not supported by SAP. Pending status: not part of end-of-day statements (the BNP guide notes a funds code applies only to intraday MT942).
- **Samples:** none obtained from a primary source.

### 3.4 BAI2

- **Source:** BAI "Cash Management Balance Reporting Specifications, Version 2, Technical Reference Manual", October 2005: https://www.bai.org/docs/default-source/libraries/site-general-downloads/cash_management_2005.pdf . Checked 2026-11-01; read from a copy kept outside the repository. All rights reserved, so reuse of its text or examples needs BAI permission.
- **Obtained by:** US bank information-reporting download. Comma-delimited text with record types 01 (file header), 02 (group header), 03 (account identifier and summary/status), 16 (transaction detail), 88 (continuation), plus trailers.
- **Fields (record 16):** type code (appendix of codes, including ACH reversal, check reversal, credit/debit reversal, overdraft fee, return item fee, miscellaneous fees), amount (unsigned, no decimal point; direction comes from the type code; currency from the group or account record), funds type (including value date and time, `V`), bank reference number, customer reference number (check number for some type codes), free text (not starting with "/"; extended with 88 records).
- **Missing:** Only two reference fields plus free text; no structured remittance, no batch grouping inside the standard (item counts in summaries only), no pending status beyond funds availability. The standard is a 2005 manual; ANSI X9.121 is described by a bank guide as its successor (secondary; not verified).
- **Samples:** Examples inside the specification (copyrighted). Bank-specific specifications exist but were not reviewed.

## 4. Comparison

Y = documented carrier of that information in the sources above; P = partial or free text only; ? = unverified; N = not found.

| Format | Reference to payment / remittance | Invoice reference | Fees | Value vs booking date | Counterparty ID | Pending/booked | Reversal | Batch grouping | Obtain | Public sample |
|---|---|---|---|---|---|---|---|---|---|---|
| Xero (API) | Y `Payment.Reference`, `Details` | P `InvoiceNumber`/`Reference` (bill field unclear) | N | N for bank dates (`Date` is the accounting payment date; no bank value/booking date in the spec) | Y `Contact` | P bill/payment status only, not bank clearing; `IsReconciled` is Xero's own reconciliation flag | P status only | Y `BatchPaymentID` | API | Y spec examples, MIT |
| QBO (API) | P `DocNumber`, `PrivateNote` | P `Bill.DocNumber`, `LinkedTxn` | N | N | Y `VendorRef` | Balance only; reconciled N | P void operation | P multi-line BillPayment | API | P doc samples |
| NetSuite | P `tranId`, `memo` | Y `tranId` on bill | N | N | Y `entity` | P `status` is an accounting-side transaction state, not bank pending/booked; bank clearing status unverified | P `voidJournal` | N | API/UI search | N |
| Sage | ? | ? | ? | ? | ? | UI report of unreconciled items | ? | ? | UI CSV/PDF | N |
| SAP | Y `PaymentReference`; payment doc ? | Y `SupplierInvoiceIDByInvcgParty`, `InvoiceReference` | ? | ? Not verified: `DocumentDate`/`PostingDate` are supplier-invoice dates, not bank value/booking dates; do not use in clearing-window calculations | Y `InvoicingParty` | P `PaymentBlockingReason` is an accounting-side payment block, not bank pending/booked; bank clearing status unverified | P `SupplierInvoiceIsCreditMemo`; reversal ? | ? | OData / reports ? | P help sample |
| Bank CSV | P free text | P free text | P if a row | ? | ? | ? | ? | ? | UI | per bank |
| CAMT.053 | Y `EndToEndId`, `AcctSvcrRef`, `Ustrd`/`Strd` | P inside `RmtInf` | ? | Y `BookgDt` and `ValDt` | Y related parties | Booked only | Y `RvslInd` | Y `Btch`, `NbOfTxs`, `TtlAmt` | Bank download | Y ING sample (licence not stated) |
| MT940 | P `:61:` ref (16 chars, truncated EndToEndId); `:86:` `/EREF/` (35) at BNP, bank-specific | P `:86:` | P at BNP: `:86:` `/CHGS/` (conditional), incoming international unitary bookings only; not found in the other BNP layouts read; separate fee entries unverified | Y value and entry date | P `:86:` | Booked only | P bank code in `:86:` | P bank-specific | Bank download | N |
| BAI2 | P two reference fields + text | P text | Y fee type codes | Y value date (funds type V) | N | Funds availability only | Y reversal type codes | N | Bank download | Examples in copyrighted spec |

## 5. Provisional recommendation

Support first, subject to practitioner input:

- **Accounting export: Xero** (Accounting API objects: Invoice ACCPAY, Payment, BatchPayment, BankTransaction).
  - Grounded reasons: the only candidate with a machine-readable, MIT-licensed specification I could read in full, which lets the input contract and fixtures be derived from published field definitions; it is the only candidate where the payment-to-batch link (`BatchPaymentID`) and a per-payment reconciled flag (`IsReconciled`) are documented; `BankAmount` and `CurrencyRate` are documented on the payment, which bears on amount differences.
  - Known gaps: no fee field; no bank statement line in the Accounting API spec; supplier-invoice-number field for bills unclear in the spec.
  - Alternatives: **QBO** has a clean bill-to-payment link (`LinkedTxn`) but no documented reconciled flag, batch ID or bank-side data in the pages read. **NetSuite** is rich on bills and applies but its payment schema has no documented cleared flag and no batch ID. **SAP** is likely common in larger firms but field-level payment and clearing data could not be read, and exports vary by customer. **Sage** API could not be read at all.
- **Bank format: ISO 20022 CAMT.053.**
  - Grounded reasons: it is the only candidate documenting structured end-to-end references, a reversal indicator, separate booking and value dates, and batch totals (`Btch`/`NbOfTxs`/`TtlAmt`) in one record (ASN guide, ING sample); it is supported by NetSuite (CAMT.053 parsers) and SAP (per search results; unverified), and by multiple banks' published guides.
  - Known gaps: booked entries only; fee handling unverified; remittance quality depends on the bank; versions differ.
  - Alternatives: **MT940** is widely imported (SAP, per its help page) but remittance sits in bank-specific `:86:` text, and the `:61:` reference field is only 16 characters (at BNP the full EndToEndId is also in `:86:` `/EREF/`, so truncation is a per-field, per-bank matter). **BAI2** is US-focused, with a reversal and fee code list but only two reference fields and a restrictive copyright. **Bank CSV** is the most commonly available, but has no standard, so each bank layout needs its own mapping; if practitioners only have CSV, it may have to come first.

**This is a recommendation, not a decision.** The M0 plan bases the choice on what practitioners use. If they use QBO with bank CSV, that outranks the ranking above. Record the final choice as a decision once at least two practitioners have answered the format question.
