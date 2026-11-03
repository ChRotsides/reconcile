"""Xero reader: invented saved objects, one rule per test."""

import json
from pathlib import Path
from typing import Any

import pytest

from reconcile.adapters.xero import KINDS, XeroData, read_xero, xero_date

DAY = "/Date(1780272000000+0000)/"  # 2026-06-01T00:00:00Z


def invoice(iid: str = "inv-1", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "Type": "ACCPAY",
        "InvoiceID": iid,
        "CurrencyCode": "EUR",
        "Total": 100.0,
        "Status": "AUTHORISED",
        "DueDate": DAY,
        "AmountCredited": 0.0,
    }
    return base | kw


def payment(pid: str = "pay-1", inv: str = "inv-1", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "PaymentID": pid,
        "Date": DAY,
        "Amount": 100.0,
        "Reference": "REF-1",
        "PaymentType": "ACCPAYPAYMENT",
        "Status": "AUTHORISED",
        "IsReconciled": False,
        "Account": {"Code": "090"},
        "Invoice": {"InvoiceID": inv, "Type": "ACCPAY"},
    }
    return base | kw


def journal(jid: str = "j-1", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "JournalID": jid,
        "JournalDate": DAY,
        "SourceID": "pay-1",
        "SourceType": "ACCPAYPAYMENT",
        "JournalLines": [
            {"JournalLineID": f"{jid}-1", "AccountCode": "210", "NetAmount": 100.0},
            {"JournalLineID": f"{jid}-2", "AccountCode": "090", "NetAmount": -100.0},
        ],
    }
    return base | kw


def batch(bid: str = "bat-1", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "BatchPaymentID": bid,
        "Date": DAY,
        "Type": "PAYBATCH",
        "Status": "AUTHORISED",
        "TotalAmount": 300.0,
        "Reference": "BATCH-1",
        "Account": {"Code": "090"},
        "Payments": [
            {"PaymentID": "m-1", "Amount": 100.0, "Invoice": {"InvoiceID": "inv-1"}},
            {"PaymentID": "m-2", "Amount": 200.0, "Invoice": {"InvoiceID": "inv-2"}},
        ],
    }
    return base | kw


def save(
    folder: Path,
    *,
    invoices: list[dict[str, Any]] | None = None,
    payments: list[dict[str, Any]] | None = None,
    batches: list[dict[str, Any]] | None = None,
    journals: list[dict[str, Any]] | None = None,
    raw: dict[str, str] | None = None,
) -> dict[str, list[Path]]:
    content = {
        "Invoices": invoices if invoices is not None else [invoice()],
        "Payments": payments if payments is not None else [payment()],
        "BatchPayments": batches if batches is not None else [],
        "Journals": journals if journals is not None else [],
    }
    files = {}
    for kind in KINDS:
        path = folder / f"{kind}.json"
        path.write_text((raw or {}).get(kind) or json.dumps({kind: content[kind]}))
        files[kind] = [path]
    return files


def read(folder: Path, **kw: Any) -> XeroData:
    options: dict[str, Any] = {
        "base_currency": "EUR",
        "method_by_account": {"090": "ACH"},
    }
    files = save(
        folder,
        **{
            k: kw.pop(k)
            for k in ("invoices", "payments", "batches", "journals", "raw")
            if k in kw
        },
    )
    return read_xero(files, **(options | kw))


def refused(folder: Path, match: str, **kw: Any) -> str:
    with pytest.raises(ValueError, match=match) as e:
        read(folder, **kw)
    return str(e.value)


# --- dates


@pytest.mark.parametrize(
    ("raw", "iso"),
    [
        ("/Date(1780272000000+0000)/", "2026-06-01"),
        ("/Date(0+0000)/", "1970-01-01"),
        ("/Date(-86400000+0000)/", "1969-12-31"),
        ("2026-06-01", "2026-06-01"),
    ],
)
def test_both_xero_date_forms_are_accepted(raw: str, iso: str) -> None:
    assert xero_date(raw) == iso


@pytest.mark.parametrize(
    "raw",
    [
        "/Date(1780272000000+0100)/",  # an offset: the spec does not give its meaning
        "/Date(1780272000000)/",
        "/Date(1780275600000+0000)/",  # 01:00 UTC, not a date
        "2026-06-01T00:00:00",
        "01/06/2026",
        "2026-02-30",
        "",
        None,
        20260601,
    ],
)
def test_other_date_forms_are_refused(raw: object) -> None:
    with pytest.raises(Exception, match="date"):
        xero_date(raw)


# --- invoices


def test_invoice_row_and_status(tmp_path: Path) -> None:
    data = read(
        tmp_path,
        invoices=[
            invoice(),
            invoice("inv-2", Status="PAID"),
            invoice("inv-3", Status="VOIDED"),
        ],
        payments=[],
    )
    assert data.tables["invoices"][0] == {
        "invoice_id": "inv-1",
        "due_date": "2026-06-01",
        "currency": "EUR",
        "invoice_total": "100.0",
        "status": "authorised",
    }
    assert [r["status"] for r in data.tables["invoices"]] == [
        "authorised",
        "paid",
        "voided",
    ]


def raw_invoice(total: str) -> str:
    return (
        '{"Invoices": [{"Type":"ACCPAY","InvoiceID":"i","CurrencyCode":"EUR",'
        f'"Total":{total},"Status":"PAID"}}]}}'
    )


def test_numbers_never_pass_through_a_float(tmp_path: Path) -> None:
    text = raw_invoice("9007199254740993.01")
    data = read(tmp_path, raw={"Invoices": text}, payments=[])
    # a float would print 9007199254740994.0
    assert data.tables["invoices"][0]["invoice_total"] == "9007199254740993.01"


def test_exponent_form_is_written_plainly(tmp_path: Path) -> None:
    text = raw_invoice("1E+2")
    data = read(tmp_path, raw={"Invoices": text}, payments=[])
    assert data.tables["invoices"][0]["invoice_total"] == "100"


def test_nan_is_refused(tmp_path: Path) -> None:
    text = '{"Invoices": [{"Type":"ACCPAY","InvoiceID":"i","Total":NaN}]}'
    refused(tmp_path, "not valid JSON", raw={"Invoices": text}, payments=[])


def test_receivable_invoices_are_counted_not_used(tmp_path: Path) -> None:
    data = read(
        tmp_path, invoices=[invoice(), invoice("ar", Type="ACCREC")], payments=[]
    )
    assert [r["invoice_id"] for r in data.tables["invoices"]] == ["inv-1"]
    assert data.extras["invoices_other_types_skipped"] == {"ACCREC": 1}


def test_credit_applied_to_an_invoice_is_counted(tmp_path: Path) -> None:
    data = read(
        tmp_path,
        invoices=[invoice(), invoice("inv-2", AmountCredited=5.0)],
        payments=[],
    )
    assert data.extras["invoices_with_nonzero_AmountCredited"] == 1


# --- payments


def test_payment_row_and_allocation(tmp_path: Path) -> None:
    data = read(tmp_path, payments=[payment(BankAccountNumber="DE89 3704")])
    assert data.tables["payments"] == [
        {
            "payment_id": "pay-1",
            "bank_account_id": "DE89 3704",
            "reference_number": "REF-1",
            "payment_date": "2026-06-01",
            "payment_method": "ACH",
            "payment_currency": "EUR",
            "payment_amount": "100.0",
            "payment_status": "completed",
            "settlement_status": "settled",
        }
    ]
    assert data.tables["payment_allocations"] == [
        {
            "payment_id": "pay-1",
            "invoice_id": "inv-1",
            "allocated_amount": "100.0",
            "allocation_date": "2026-06-01",
        }
    ]


def test_payment_currency_comes_from_the_invoice(tmp_path: Path) -> None:
    data = read(
        tmp_path,
        invoices=[invoice(CurrencyCode="USD")],
        payments=[payment(BankAmount=92.0)],
    )
    assert data.tables["payments"][0]["payment_currency"] == "USD"
    assert data.tables["payments"][0]["payment_amount"] == "100.0"


@pytest.mark.parametrize("reconciled", [True, False])
def test_is_reconciled_never_decides_settlement(
    tmp_path: Path, reconciled: bool
) -> None:
    row = read(tmp_path, payments=[payment(IsReconciled=reconciled)]).tables[
        "payments"
    ][0]
    assert (row["payment_status"], row["settlement_status"]) == ("completed", "settled")


def test_deleted_is_reversed(tmp_path: Path) -> None:
    row = read(tmp_path, payments=[payment(Status="DELETED")]).tables["payments"][0]
    assert (row["payment_status"], row["settlement_status"]) == ("reversed", "reversed")


def test_an_unknown_payment_status_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, "Status 'PENDING'", payments=[payment(Status="PENDING")])


def test_other_payment_types_are_counted_not_used(tmp_path: Path) -> None:
    data = read(
        tmp_path,
        payments=[
            payment(),
            payment("cn", PaymentType="APCREDITPAYMENT"),
            payment("ar", PaymentType="ACCRECPAYMENT"),
        ],
    )
    assert [r["payment_id"] for r in data.tables["payments"]] == ["pay-1"]
    assert data.extras["payments_other_types_skipped"] == {
        "ACCRECPAYMENT": 1,
        "APCREDITPAYMENT": 1,
    }


def test_a_payment_for_an_unknown_invoice_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, "pay-1.*invoice nope", payments=[payment(inv="nope")])


def test_a_negative_amount_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, "negative", payments=[payment(Amount=-1.0)])


def test_method_comes_from_the_account_code_or_the_default_or_nothing(
    tmp_path: Path,
) -> None:
    pays = [
        payment("a"),
        payment("b", Account={"Code": "555"}),
        payment("c", Account={}),
    ]
    none = read(tmp_path, payments=pays).tables["payments"]
    assert [r["payment_method"] for r in none] == ["ACH", None, None]
    default = read(tmp_path, payments=pays, default_method="wire").tables["payments"]
    assert [r["payment_method"] for r in default] == ["ACH", "wire", "wire"]


def test_an_unknown_method_in_the_config_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, "not among", method_by_account={"090": "Ach"})


def test_reference_field_is_chosen_without_fallback(tmp_path: Path) -> None:
    pays = [payment(Reference="R", Details="D"), payment("p2", Reference="")]
    assert [
        r["reference_number"] for r in read(tmp_path, payments=pays).tables["payments"]
    ] == ["R", None]
    details = read(tmp_path, payments=pays, reference_field="Details").tables[
        "payments"
    ]
    assert [r["reference_number"] for r in details] == ["D", None]
    with pytest.raises(ValueError, match="reference_field"):
        read(tmp_path, reference_field="Narrative")


# --- batches


def batch_inputs() -> dict[str, Any]:
    return {
        "invoices": [invoice(), invoice("inv-2", Total=200.0)],
        "payments": [
            payment("m-1", BatchPaymentID="bat-1", Amount=100.0),
            payment("m-2", "inv-2", BatchPaymentID="bat-1", Amount=200.0),
        ],
        "batches": [batch()],
    }


def test_a_batch_is_one_payment_with_its_members_as_allocations(tmp_path: Path) -> None:
    data = read(tmp_path, **batch_inputs())
    assert [r["payment_id"] for r in data.tables["payments"]] == ["bat-1"]
    row = data.tables["payments"][0]
    assert (row["payment_amount"], row["reference_number"], row["payment_method"]) == (
        "300.0",
        "BATCH-1",
        "ACH",
    )
    assert [
        (r["payment_id"], r["invoice_id"], r["allocated_amount"])
        for r in data.tables["payment_allocations"]
    ] == [("bat-1", "inv-1", "100.0"), ("bat-1", "inv-2", "200.0")]
    assert data.extras["batch_member_payment_ids"] == {"bat-1": ["m-1", "m-2"]}


def test_a_batch_total_that_differs_from_its_members_is_refused(tmp_path: Path) -> None:
    inputs = batch_inputs()
    inputs["batches"] = [batch(TotalAmount=301.0)]
    refused(tmp_path, "TotalAmount 301.0 differs from the sum", **inputs)


def test_a_batch_with_two_currencies_is_refused(tmp_path: Path) -> None:
    inputs = batch_inputs()
    inputs["invoices"] = [invoice(), invoice("inv-2", Total=200.0, CurrencyCode="USD")]
    refused(tmp_path, "several currencies", **inputs)


def test_a_member_whose_batch_is_absent_is_refused(tmp_path: Path) -> None:
    inputs = batch_inputs()
    inputs["batches"] = []
    refused(tmp_path, "m-1.*names batch bat-1", **inputs)


def test_a_member_not_listed_by_its_batch_is_refused(tmp_path: Path) -> None:
    inputs = batch_inputs()
    inputs["payments"].append(payment("m-3", BatchPaymentID="bat-1"))
    refused(tmp_path, "m-3.*not among its Payments", **inputs)


def test_a_member_status_that_differs_from_the_batch_is_refused(tmp_path: Path) -> None:
    inputs = batch_inputs()
    inputs["payments"][0]["Status"] = "DELETED"
    refused(tmp_path, "differs from the batch", **inputs)


def test_receivable_batches_are_counted_not_used(tmp_path: Path) -> None:
    data = read(
        tmp_path,
        batches=[batch("ar", Type="RECBATCH")],
        invoices=[invoice()],
        payments=[],
    )
    assert data.tables["payments"] == []
    assert data.extras["batches_other_types_skipped"] == {"RECBATCH": 1}


def test_two_members_for_one_invoice_are_summed_into_one_allocation(
    tmp_path: Path,
) -> None:
    inputs = batch_inputs()
    inputs["batches"] = [
        batch(
            Payments=[
                {
                    "PaymentID": "m-1",
                    "Amount": 100.0,
                    "Invoice": {"InvoiceID": "inv-1"},
                },
                {
                    "PaymentID": "m-2",
                    "Amount": 200.0,
                    "Invoice": {"InvoiceID": "inv-1"},
                },
            ]
        )
    ]
    inputs["payments"] = []
    data = read(tmp_path, **inputs)
    assert [r["allocated_amount"] for r in data.tables["payment_allocations"]] == [
        "300.0"
    ]


# --- journals


def test_journal_lines_split_by_sign_and_take_the_base_currency(tmp_path: Path) -> None:
    data = read(tmp_path, journals=[journal()])
    assert data.tables["gl_entries"] == [
        {
            "journal_id": "j-1",
            "journal_line_id": "j-1-1",
            "source_transaction_id": "pay-1",
            "transaction_type": "payment",
            "posting_date": "2026-06-01",
            "gl_account": "210",
            "debit": "100.0",
            "credit": "0",
            "currency": "EUR",
        },
        {
            "journal_id": "j-1",
            "journal_line_id": "j-1-2",
            "source_transaction_id": "pay-1",
            "transaction_type": "payment",
            "posting_date": "2026-06-01",
            "gl_account": "090",
            "debit": "0",
            "credit": "100.0",
            "currency": "EUR",
        },
    ]


def test_other_journal_sources_never_look_like_a_payment(tmp_path: Path) -> None:
    data = read(
        tmp_path,
        journals=[
            journal("a", SourceType="CASHPAID"),
            journal("b", SourceType="ACCPAY"),
            journal("c", SourceType="APCREDITPAYMENT"),
        ],
    )
    kinds = {r["transaction_type"] for r in data.tables["gl_entries"]}
    assert kinds == {"xero_cashpaid", "xero_accpay", "xero_apcreditpayment"}
    assert "payment" not in kinds


def test_unbalanced_and_empty_journals_are_reported(tmp_path: Path) -> None:
    unbalanced = journal("u")
    unbalanced["JournalLines"][1]["NetAmount"] = -99.0
    data = read(tmp_path, journals=[unbalanced, journal("e", JournalLines=[])])
    assert data.extras["journals_not_summing_to_zero"] == ["u"]
    assert data.extras["journals_without_lines"] == ["e"]


# --- refusals


def test_problems_are_collected_with_file_and_id(tmp_path: Path) -> None:
    message = refused(
        tmp_path,
        "2 problem",
        invoices=[invoice(DueDate="01/06/2026"), invoice("inv-2", Total="x")],
        payments=[],
    )
    assert "2 problem(s)" in message
    assert "Invoices.json Invoices inv-1" in message and "inv-2" in message


def test_an_unsaved_endpoint_is_refused(tmp_path: Path) -> None:
    files = save(tmp_path)
    files["Journals"] = []
    with pytest.raises(ValueError, match="no Journals file"):
        read_xero(files, base_currency="EUR")


def test_a_file_of_the_wrong_shape_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path, "expected an object with a 'Invoices' list", raw={"Invoices": "[]"}
    )


def test_repeats_across_files_are_removed_and_conflicts_refused(tmp_path: Path) -> None:
    files = save(tmp_path)
    second = tmp_path / "Invoices_page2.json"
    second.write_text(json.dumps({"Invoices": [invoice()]}))
    files["Invoices"].append(second)
    data = read_xero(files, base_currency="EUR")
    assert len(data.tables["invoices"]) == 1
    assert data.extras["exact_repeats_across_files"]["Invoices"] == 1
    second.write_text(json.dumps({"Invoices": [invoice(Total=101.0)]}))
    with pytest.raises(ValueError, match="differs from the same InvoiceID"):
        read_xero(files, base_currency="EUR")
