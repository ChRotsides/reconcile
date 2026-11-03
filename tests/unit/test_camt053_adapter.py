"""CAMT.053 reader: invented statements, one rule per test."""

from datetime import date
from pathlib import Path

import pytest

from reconcile.adapters.camt053 import parse_xml, read_camt053

NS = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"
OWN = "NL91ABNA0417164300"


def entry(
    amount: str = "100.00",
    ind: str = "DBIT",
    book: str = "2026-06-03",
    svc: str | None = "SVC-1",
    ntry_ref: str | None = None,
    sts: str = "<Sts>BOOK</Sts>",
    ccy: str = "EUR",
    details: str = "",
    extra: str = "",
    code: str = "",
) -> str:
    return (
        "<Ntry>"
        + (f"<NtryRef>{ntry_ref}</NtryRef>" if ntry_ref else "")
        + f'<Amt Ccy="{ccy}">{amount}</Amt><CdtDbtInd>{ind}</CdtDbtInd>{extra}{sts}'
        + f"<BookgDt><Dt>{book}</Dt></BookgDt>"
        + (f"<AcctSvcrRef>{svc}</AcctSvcrRef>" if svc else "")
        + code
        + f"<NtryDtls>{details}</NtryDtls></Ntry>"
    )


def txd(e2e: str | None = None, iban: str | None = None, side: str = "CdtrAcct") -> str:
    refs = f"<Refs><EndToEndId>{e2e}</EndToEndId></Refs>" if e2e else ""
    party = (
        f"<RltdPties><{side}><Id><IBAN>{iban}</IBAN></Id></{side}></RltdPties>"
        if iban
        else ""
    )
    return f"<TxDtls>{refs}{party}</TxDtls>"


def statement(
    entries: str,
    sid: str = "S1",
    first: str | None = "2026-06-01",
    last: str | None = "2026-06-30",
    account: str = OWN,
    ccy: str | None = "EUR",
    balance_date: str | None = None,
) -> str:
    period = (
        f"<FrToDt><FrDtTm>{first}T00:00:00</FrDtTm><ToDtTm>{last}T23:59:59</ToDtTm></FrToDt>"
        if first
        else ""
    )
    bal = (
        "<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp>"
        '<Amt Ccy="EUR">1.00</Amt><CdtDbtInd>CRDT</CdtDbtInd>'
        f"<Dt><Dt>{balance_date}</Dt></Dt></Bal>"
        if balance_date
        else ""
    )
    currency = f"<Ccy>{ccy}</Ccy>" if ccy else ""
    return (
        f"<Stmt><Id>{sid}</Id>{period}<Acct><Id><IBAN>{account}</IBAN></Id>{currency}</Acct>"
        f"{bal}{entries}</Stmt>"
    )


def document(*statements: str, ns: str = NS) -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><Document xmlns="{ns}">'
        f"<BkToCstmrStmt>{''.join(statements)}</BkToCstmrStmt></Document>"
    )


def write(tmp_path: Path, name: str, xml: str) -> Path:
    path = tmp_path / name
    path.write_text(xml, encoding="utf-8")
    return path


def read_one(tmp_path: Path, xml: str, **kw: object) -> list[dict[str, str | None]]:
    return read_camt053([write(tmp_path, "a.xml", xml)], **kw).rows  # type: ignore[arg-type]


def refused(tmp_path: Path, *xml: str, match: str, **kw: object) -> str:
    files = [write(tmp_path, f"f{i}.xml", x) for i, x in enumerate(xml)]
    with pytest.raises(ValueError, match=match) as e:
        read_camt053(files, **kw)  # type: ignore[arg-type]
    return str(e.value)


def test_a_booked_entry_becomes_one_row(tmp_path: Path) -> None:
    details = txd("E2E-1", "DE89370400440532013000")
    rows = read_one(tmp_path, document(statement(entry(details=details))))
    assert rows == [
        {
            "bank_transaction_id": "SVC-1",
            "counterparty_token": "DE89370400440532013000",
            "payment_reference": "E2E-1",
            "bank_account_id": OWN,
            "transaction_date": None,
            "posted_date": "2026-06-03",
            "transaction_type": "bank debit",
            "amount": "100.00",
            "currency": "EUR",
            "direction": "debit",
            "status": "posted",
        }
    ]


def test_several_txdtls_are_one_row_with_no_reference(tmp_path: Path) -> None:
    details = txd("A", "DE89370400440532013000") + txd("B", "DE89370400440532013000")
    result = read_camt053(
        [
            write(
                tmp_path, "a.xml", document(statement(entry("300.00", details=details)))
            )
        ]
    )
    assert (
        len(result.rows) == 1
    )  # an entry is never split, so amounts are never divided
    assert result.rows[0]["amount"] == "300.00"
    assert result.rows[0]["payment_reference"] is None
    assert result.extras["payment_reference_source"] == {"none_several_TxDtls": 1}


def test_several_txdtls_use_the_batch_reference(tmp_path: Path) -> None:
    details = "<Btch><PmtInfId>RUN-7</PmtInfId></Btch>" + txd("A") + txd("B")
    rows = read_one(tmp_path, document(statement(entry(details=details))))
    assert rows[0]["payment_reference"] == "RUN-7"


def test_an_entry_without_txdtls_uses_the_batch_reference(tmp_path: Path) -> None:
    details = "<Btch><PmtInfId>RUN-7</PmtInfId><NbOfTxs>2</NbOfTxs></Btch>"
    rows = read_one(tmp_path, document(statement(entry(details=details))))
    assert rows[0]["payment_reference"] == "RUN-7"


def test_a_batch_reference_does_not_replace_a_single_txdtls_reference(
    tmp_path: Path,
) -> None:
    details = "<Btch><PmtInfId>RUN-7</PmtInfId></Btch>" + txd("MINE")
    assert (
        read_one(tmp_path, document(statement(entry(details=details))))[0][
            "payment_reference"
        ]
        == "MINE"
    )


def test_notprovided_counts_as_absent(tmp_path: Path) -> None:
    rows = read_one(tmp_path, document(statement(entry(details=txd("NOTPROVIDED")))))
    assert rows[0]["payment_reference"] is None


def test_reference_fields_are_tried_in_the_configured_order(tmp_path: Path) -> None:
    details = (
        "<TxDtls><Refs><InstrId>INSTR-1</InstrId></Refs>"
        "<RmtInf><Ustrd>part one</Ustrd><Ustrd>part two</Ustrd></RmtInf></TxDtls>"
    )
    xml = document(statement(entry(details=details)))
    both = read_one(tmp_path, xml, reference_fields=["EndToEndId", "InstrId", "Ustrd"])
    assert both[0]["payment_reference"] == "INSTR-1"
    ustrd_first = read_one(tmp_path, xml, reference_fields=["Ustrd", "InstrId"])
    assert ustrd_first[0]["payment_reference"] == "part one part two"
    only_e2e = read_one(tmp_path, xml)  # default: EndToEndId only, no silent fallback
    assert only_e2e[0]["payment_reference"] is None


def test_structured_creditor_reference(tmp_path: Path) -> None:
    details = (
        "<TxDtls><RmtInf><Strd><CdtrRefInf><Ref>RF18</Ref></CdtrRefInf></Strd>"
        "</RmtInf></TxDtls>"
    )
    rows = read_one(
        tmp_path,
        document(statement(entry(details=details))),
        reference_fields=["CdtrRefInf"],
    )
    assert rows[0]["payment_reference"] == "RF18"


def test_unknown_reference_field_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bank_reference_fields"):
        read_camt053(
            [write(tmp_path, "a.xml", document(statement(entry())))],
            reference_fields=["Name"],
        )


def test_counterparty_follows_direction_and_never_a_name(tmp_path: Path) -> None:
    both = (
        "<TxDtls><RltdPties><Cdtr><Nm>Some Name</Nm></Cdtr>"
        "<CdtrAcct><Id><IBAN>CRED1</IBAN></Id></CdtrAcct>"
        "<DbtrAcct><Id><IBAN>DEBT1</IBAN></Id></DbtrAcct></RltdPties></TxDtls>"
    )
    debit = read_one(tmp_path, document(statement(entry(details=both))))
    credit = read_one(tmp_path, document(statement(entry(ind="CRDT", details=both))))
    assert debit[0]["counterparty_token"] == "CRED1"
    assert credit[0]["counterparty_token"] == "DEBT1"
    name_only = (
        "<TxDtls><RltdPties><Cdtr><Nm>Some Name</Nm></Cdtr></RltdPties></TxDtls>"
    )
    assert (
        read_one(tmp_path, document(statement(entry(details=name_only))))[0][
            "counterparty_token"
        ]
        is None
    )


def test_not_booked_entries_are_excluded_and_counted(tmp_path: Path) -> None:
    result = read_camt053(
        [
            write(
                tmp_path,
                "a.xml",
                document(
                    statement(
                        entry(svc="A")
                        + entry(svc="B", sts="<Sts>PDNG</Sts>")
                        + entry(svc="C", sts="<Sts><Cd>BOOK</Cd></Sts>")
                    )
                ),
            )
        ]
    )
    assert [r["bank_transaction_id"] for r in result.rows] == ["A", "C"]
    assert result.extras["entries_excluded_not_booked"] == {"PDNG": 1}


def test_bank_transaction_id_sources(tmp_path: Path) -> None:
    result = read_camt053(
        [
            write(
                tmp_path,
                "a.xml",
                document(
                    statement(
                        entry(svc="SVC")
                        + entry(svc=None, ntry_ref="NTRY")
                        + entry(svc=None)
                    )
                ),
            )
        ]
    )
    assert [r["bank_transaction_id"] for r in result.rows] == ["SVC", "NTRY", "S1:3"]
    assert result.extras["bank_transaction_id_source"] == {
        "AcctSvcrRef": 1,
        "NtryRef": 1,
        "Stmt/Id:position": 1,
    }


def test_a_repeated_bank_transaction_id_is_refused_naming_both(tmp_path: Path) -> None:
    message = refused(
        tmp_path,
        document(statement(entry(svc="X") + entry(svc="X"))),
        match="also used by",
    )
    assert "Ntry #1" in message and "Ntry #2" in message


def test_negative_amount_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, document(statement(entry("-5.00"))), match="negative")


def test_currency_disagreeing_with_the_account_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        document(statement(entry(ccy="USD"))),
        match="USD differs from Stmt/Acct/Ccy EUR",
    )


def test_a_non_decimal_amount_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, document(statement(entry("1,00"))), match="not a plain decimal")


def test_value_date_is_the_transaction_date(tmp_path: Path) -> None:
    xml = document(
        statement(entry()).replace(
            "</BookgDt>", "</BookgDt><ValDt><DtTm>2026-06-04T10:00:00</DtTm></ValDt>"
        )
    )
    row = read_one(tmp_path, xml)[0]
    assert (row["posted_date"], row["transaction_date"]) == ("2026-06-03", "2026-06-04")


def bk_tx_cd(domain: str, family: str, sub: str) -> str:
    return (
        f"<BkTxCd><Domn><Cd>{domain}</Cd><Fmly><Cd>{family}</Cd>"
        f"<SubFmlyCd>{sub}</SubFmlyCd></Fmly></Domn></BkTxCd>"
    )


def test_fee_code_and_reversal_types(tmp_path: Path) -> None:
    fee = bk_tx_cd("CAMT", "ACCB", "CHRG")
    other = bk_tx_cd("PMNT", "ICDT", "ESCT")
    xml = document(
        statement(
            entry(svc="F", code=fee)
            + entry(svc="O", code=other)
            + entry(svc="R", ind="CRDT", extra="<RvslInd>true</RvslInd>")
            + entry(svc="C", ind="CRDT")
        )
    )
    types = {
        r["bank_transaction_id"]: r["transaction_type"] for r in read_one(tmp_path, xml)
    }
    assert types == {
        "F": "bank fee",
        "O": "bank debit",
        "R": "payment reversal",
        "C": "bank credit",
    }


def test_fee_codes_come_from_the_config(tmp_path: Path) -> None:
    code = bk_tx_cd("PMNT", "MCOP", "OTHR")
    xml = document(statement(entry(code=code)))
    assert read_one(tmp_path, xml)[0]["transaction_type"] == "bank debit"
    custom = read_one(tmp_path, xml, fee_codes=[["PMNT", "MCOP", "OTHR"]])
    assert custom[0]["transaction_type"] == "bank fee"


def test_an_entry_that_is_both_fee_and_reversal_is_refused(tmp_path: Path) -> None:
    fee = bk_tx_cd("CAMT", "ACCB", "CHRG")
    refused(
        tmp_path,
        document(statement(entry(code=fee, extra="<RvslInd>true</RvslInd>"))),
        match="both a fee code and a reversal",
    )


# --- XML safety


BILLION_LAUGHS = (
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
    '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
    '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
    f'<Document xmlns="{NS}"><BkToCstmrStmt>&lol3;</BkToCstmrStmt></Document>'
)
EXTERNAL = (
    '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/hostname">]>'
    f'<Document xmlns="{NS}"><BkToCstmrStmt>&x;</BkToCstmrStmt></Document>'
)


@pytest.mark.parametrize("xml", [BILLION_LAUGHS, EXTERNAL])
def test_doctype_and_entities_are_refused(tmp_path: Path, xml: str) -> None:
    refused(tmp_path, xml, match="DOCTYPE or entity")


def test_a_harmless_doctype_is_also_refused() -> None:
    with pytest.raises(ValueError, match="DOCTYPE"):
        parse_xml(b'<!DOCTYPE Document><Document xmlns="' + NS.encode() + b'"/>')


def test_other_namespaces_and_roots_are_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        document(
            statement(entry()), ns="urn:iso:std:iso:20022:tech:xsd:camt.052.001.02"
        ),
        match="not urn:iso",
    )
    refused(
        tmp_path,
        f'<Other xmlns="{NS}"/>',
        match="root element is 'Other'",
    )


def test_a_newer_camt053_version_is_accepted(tmp_path: Path) -> None:
    xml = document(
        statement(entry()), ns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"
    )
    assert len(read_one(tmp_path, xml)) == 1


def test_malformed_xml_names_the_file(tmp_path: Path) -> None:
    assert "f0.xml" in refused(tmp_path, "<Document", match="not well-formed")


# --- coverage


def test_contiguous_statements_set_the_end_date(tmp_path: Path) -> None:
    a = document(statement(entry(svc="A"), "S1", "2026-06-01", "2026-06-30"))
    b = document(
        statement(entry(svc="B", book="2026-07-02"), "S2", "2026-07-01", "2026-07-15")
    )
    result = read_camt053([write(tmp_path, "a.xml", a), write(tmp_path, "b.xml", b)])
    assert result.bank_data_through == date(2026, 7, 15)


def test_through_is_the_earliest_last_day_across_accounts(tmp_path: Path) -> None:
    a = document(statement(entry(svc="A"), "S1", "2026-06-01", "2026-06-30"))
    b = document(
        statement(entry(svc="B"), "S2", "2026-06-01", "2026-06-20", account="DE00OTHER")
    )
    result = read_camt053([write(tmp_path, "a.xml", a), write(tmp_path, "b.xml", b)])
    assert result.bank_data_through == date(2026, 6, 20)


def test_a_gap_between_statements_is_refused_naming_both(tmp_path: Path) -> None:
    a = document(statement(entry(svc="A"), "S1", "2026-06-01", "2026-06-10"))
    b = document(
        statement(entry(svc="B", book="2026-06-15"), "S2", "2026-06-12", "2026-06-30")
    )
    message = refused(tmp_path, a, b, match="gap")
    assert "'S1'" in message and "'S2'" in message


def test_overlapping_statements_are_refused_naming_both(tmp_path: Path) -> None:
    a = document(statement(entry(svc=None), "S1", "2026-06-01", "2026-06-20"))
    b = document(
        statement(entry(svc=None, book="2026-06-25"), "S2", "2026-06-15", "2026-06-30")
    )
    message = refused(tmp_path, a, b, match="overlaps")
    assert "'S1'" in message and "'S2'" in message


def test_an_exact_repeat_of_a_statement_is_dropped_and_counted(tmp_path: Path) -> None:
    a = document(statement(entry(svc=None), "S1"))
    result = read_camt053([write(tmp_path, "a.xml", a), write(tmp_path, "b.xml", a)])
    assert len(result.rows) == 1  # a position-based ID would otherwise fake a 2nd debit
    assert len(result.extras["statements_dropped_as_exact_repeats"]) == 1


def test_the_same_statement_id_with_other_content_is_refused(tmp_path: Path) -> None:
    a = document(statement(entry("100.00"), "S1"))
    b = document(statement(entry("101.00"), "S1"))
    refused(tmp_path, a, b, match="same Stmt/Id")


def test_a_booking_date_outside_the_period_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        document(statement(entry(book="2026-07-01"))),
        match="outside the statement period",
    )


def test_balance_date_is_the_period_when_there_is_no_frtodt(tmp_path: Path) -> None:
    xml = document(statement(entry(), first=None, last=None, balance_date="2026-06-03"))
    assert read_camt053([write(tmp_path, "a.xml", xml)]).bank_data_through == date(
        2026, 6, 3
    )


def test_a_statement_with_no_period_needs_the_config_date(tmp_path: Path) -> None:
    xml = document(statement(entry(), first=None, last=None))
    refused(tmp_path, xml, match="states no period")
    result = read_camt053(
        [write(tmp_path, "a.xml", xml)], bank_data_through=date(2026, 6, 30)
    )
    assert result.bank_data_through == date(2026, 6, 30)


def test_a_config_date_later_than_the_statements_is_refused(tmp_path: Path) -> None:
    xml = document(statement(entry()))
    refused(
        tmp_path,
        xml,
        match="later than the statements show",
        bank_data_through=date(2026, 7, 31),
    )


def test_different_currencies_of_one_account_are_separate_coverage(
    tmp_path: Path,
) -> None:
    eur = document(statement(entry(svc="A"), "S1", "2026-06-01", "2026-06-30"))
    usd = document(
        statement(
            entry(svc="B", ccy="USD"), "S2", "2026-06-01", "2026-06-30", ccy="USD"
        )
    )
    result = read_camt053(
        [write(tmp_path, "a.xml", eur), write(tmp_path, "b.xml", usd)]
    )
    assert len(result.rows) == 2
