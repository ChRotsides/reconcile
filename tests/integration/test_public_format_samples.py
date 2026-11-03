"""The readers run on public format samples, when they are on this machine.

The samples are not in the repository (`data/` is ignored): the Xero example
objects come from the MIT-licensed Xero-OpenAPI spec at commit df43c6d, and the
CAMT.053 entries from the ING sample PDF (licence not stated, so never copied
into the repository). Only structural facts are asserted. How the local files
were derived is in docs/input-mapping.md section 10."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from reconcile.adapters.camt053 import read_camt053, read_entries
from reconcile.adapters.operational import prepare_operational_snapshot
from reconcile.adapters.xero import KINDS, read_xero

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "public-samples"
XERO = SAMPLES / "xero_examples"
ING_FULL = SAMPLES / "ing_camt053_sample.xml"
ING_ENTRIES = SAMPLES / "ing_ntry_snippets.xml"

needs_xero = pytest.mark.skipif(
    not XERO.is_dir(), reason="Xero examples not downloaded"
)
needs_ing = pytest.mark.skipif(
    not (ING_FULL.is_file() and ING_ENTRIES.is_file()),
    reason="ING sample files not extracted",
)


@needs_xero
def test_xero_spec_examples_are_read_but_hold_no_payables() -> None:
    data = read_xero({k: [XERO / f"{k}.json"] for k in KINDS}, base_currency="NZD")
    assert data.extras["objects_read"] == {
        "Invoices": 3,
        "Payments": 2,
        "BatchPayments": 1,
        "Journals": 4,
    }
    # The GET examples are receivables only: no ACCPAY invoice, payment or batch.
    assert data.tables["invoices"] == []
    assert data.tables["payments"] == []
    assert data.extras["invoices_other_types_skipped"] == {"ACCREC": 3}
    assert data.extras["batches_other_types_skipped"] == {"RECBATCH": 1}
    assert len(data.tables["gl_entries"]) == 9
    assert data.extras["journals_not_summing_to_zero"] == []
    for row in data.tables["gl_entries"]:
        assert row["transaction_type"] != "payment"
        assert Decimal(str(row["debit"])) >= 0 and Decimal(str(row["credit"])) >= 0


@needs_xero
def test_the_xero_examples_prepare_with_nzd_but_hold_no_payables(
    tmp_path: Path,
) -> None:
    data = read_xero({k: [XERO / f"{k}.json"] for k in KINDS}, base_currency="NZD")
    manifest = prepare_operational_snapshot(
        {**data.tables, "bank_transactions": []},
        tmp_path / "snap",
        cutoff=date(2019, 1, 1),
        bank_data_from=date(2019, 1, 1),
        bank_data_through=date(2019, 1, 1),
        all_paying_accounts_covered=True,
        reference_matching="exact",
        fee_journals_typed=False,
        source_format="sample",
        source_files={},
    )
    assert manifest["money"]["currencies"] == ["NZD"]
    assert manifest["money"]["amounts_validated"] == 12
    assert manifest["tables"]["gl_entries"]["rows_after"] == 6  # 3 after the cutoff
    assert manifest["tables"]["payments"]["rows_after"] == 0


@needs_ing
def test_ing_full_statement_sample_is_refused_as_incomplete() -> None:
    # The sample is an excerpt: it states 9 entries and holds 1, and its balances
    # (opening 1000.00, closing 1605.00) do not follow from that one entry. Both
    # are refused, because a bank row missing from such a statement proves nothing.
    with pytest.raises(ValueError) as e:
        read_camt053([ING_FULL], reference_fields=["EndToEndId"])
    message = str(e.value)
    assert "2 problem(s)" in message
    assert "states 9 entries (TxsSummry/TtlNtries/NbOfNtries) but contains 1" in message
    assert (
        "opening 1000.00 + booked credits 270.00 - booked debits 0 = 1270.00, "
        "but the closing balance is 1605.00" in message
    )


@needs_ing
def test_ing_full_statement_sample_entry_parses_through_the_entry_reader() -> None:
    result = read_entries([ING_FULL], reference_fields=["EndToEndId"])
    assert len(result.rows) == 1
    row = result.rows[0]
    assert (row["amount"], row["currency"], row["direction"]) == (
        "270.00",
        "EUR",
        "credit",
    )
    assert row["payment_reference"] == "SCTBtchId20140103A2PSTN"  # Btch/PmtInfId
    assert row["bank_transaction_id"] == "3102766686999"  # AcctSvcrRef
    assert result.bank_data_through is None  # the entry reader gives no coverage


@needs_ing
def test_ing_entry_samples_are_read_one_row_each() -> None:
    result = read_camt053([ING_ENTRIES], reference_fields=["EndToEndId", "Ustrd"])
    assert len(result.rows) == 7
    assert (
        sorted(r["direction"] or "" for r in result.rows)
        == ["credit"] * 4 + ["debit"] * 3
    )
    assert result.extras["bank_transaction_id_source"] == {"AcctSvcrRef": 7}


@needs_ing
def test_a_foreign_currency_entry_in_a_euro_statement_is_refused() -> None:
    text = ING_ENTRIES.read_text(encoding="utf-8").replace(
        "</Acct>", "<Ccy>EUR</Ccy></Acct>"
    )
    copy = SAMPLES / "ing_ntry_snippets_with_ccy.xml"
    copy.write_text(text, encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="CHF differs from Stmt/Acct/Ccy EUR"):
            read_camt053([copy])
    finally:
        copy.unlink()
