"""CAMT.053 reader: invented statements, one rule per test."""

from datetime import date
from pathlib import Path

import pytest

from reconcile.adapters.camt053 import parse_xml, read_camt053, read_entries

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
    first_time: str = "00:00:00",
    last_time: str = "23:59:59",
    summary: str = "",
    balances: str = "",
) -> str:
    period = (
        f"<FrToDt><FrDtTm>{first}T{first_time}</FrDtTm>"
        f"<ToDtTm>{last}T{last_time}</ToDtTm></FrToDt>"
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
        f"{summary}{bal}{balances}{entries}</Stmt>"
    )


def balance(code: str, amount: str, ind: str, ccy: str = "EUR") -> str:
    return (
        f"<Bal><Tp><CdOrPrtry><Cd>{code}</Cd></CdOrPrtry></Tp>"
        f'<Amt Ccy="{ccy}">{amount}</Amt><CdtDbtInd>{ind}</CdtDbtInd>'
        "<Dt><Dt>2026-06-30</Dt></Dt></Bal>"
    )


def count(n: str) -> str:
    return f"<TxsSummry><TtlNtries><NbOfNtries>{n}</NbOfNtries></TtlNtries></TxsSummry>"


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


def test_several_txdtls_know_the_counterparty_only_if_all_agree(tmp_path: Path) -> None:
    same = txd("A", "de89 3704 0044 0532 0130 00") + txd("B", "DE89370400440532013000")
    rows = read_one(tmp_path, document(statement(entry("300.00", details=same))))
    assert rows[0]["counterparty_token"] == "DE89370400440532013000"
    partial = txd("A", "DE89370400440532013000") + txd("B")
    rows = read_one(tmp_path, document(statement(entry("300.00", details=partial))))
    assert rows[0]["counterparty_token"] is None
    differ = txd("A", "DE89370400440532013000") + txd(
        "B", "FR1420041010050500013M02606"
    )
    rows = read_one(tmp_path, document(statement(entry("300.00", details=differ))))
    assert rows[0]["counterparty_token"] is None


def test_a_two_second_gap_between_statements_is_refused(
    tmp_path: Path,
) -> None:
    a = document(
        statement(
            entry(svc="A"), "S1", "2026-06-01", "2026-06-15", last_time="11:59:58"
        )
    )
    b = document(
        statement(
            entry(svc="B", book="2026-06-20"),
            "S2",
            "2026-06-15",
            "2026-06-30",
            first_time="12:00:00",
        )
    )
    refused(tmp_path, a, b, match="gap")


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


@pytest.mark.parametrize("sts", ["", "<Sts>BOOOK</Sts>", "<Sts>booked</Sts>"])
def test_a_missing_or_unrecognised_entry_status_is_refused(
    sts: str, tmp_path: Path
) -> None:
    refused(
        tmp_path,
        document(statement(entry(sts=sts))),
        match=r"Ntry #1.*not one of \['BOOK', 'PDNG', 'INFO'\]",
    )


def test_info_entries_are_excluded_and_counted(tmp_path: Path) -> None:
    result = read_camt053(
        [
            write(
                tmp_path,
                "a.xml",
                document(
                    statement(
                        entry(svc="A") + entry(svc="B", sts="<Sts><Cd>INFO</Cd></Sts>")
                    )
                ),
            )
        ]
    )
    assert len(result.rows) == 1
    assert result.extras["entries_excluded_not_booked"] == {"INFO": 1}


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


def test_from_is_the_first_covered_day_and_the_latest_across_accounts(
    tmp_path: Path,
) -> None:
    a = document(statement(entry(svc="A"), "S1", "2026-06-01", "2026-06-30"))
    b = document(
        statement(
            entry(svc="B", book="2026-06-12"),
            "S2",
            "2026-06-10",
            "2026-06-30",
            account="DE00OTHER",
        )
    )
    one = read_camt053([write(tmp_path, "a.xml", a)])
    assert (one.bank_data_from, one.bank_data_through) == (
        date(2026, 6, 1),
        date(2026, 6, 30),
    )
    both = read_camt053([write(tmp_path, "a.xml", a), write(tmp_path, "b.xml", b)])
    assert both.bank_data_from == date(2026, 6, 10)  # account B has nothing before


@pytest.mark.parametrize(
    ("first_time", "last_time", "start", "end"),
    [
        ("00:00:00", "23:59:59", date(2026, 6, 1), date(2026, 6, 30)),
        ("00:00:00", "23:59:59.500", date(2026, 6, 1), date(2026, 6, 30)),
        # No margin: a statement that starts at 00:00:59 leaves 00:00:00-00:00:58
        # of June 1 unexplained, and one that ends at 23:59:00 leaves the last minute.
        ("00:00:59", "23:59:59", date(2026, 6, 2), date(2026, 6, 30)),
        ("00:00:00", "23:59:00", date(2026, 6, 1), date(2026, 6, 29)),
        ("00:01:00", "23:58:59", date(2026, 6, 2), date(2026, 6, 29)),
        ("12:00:00", "12:00:00", date(2026, 6, 2), date(2026, 6, 29)),
    ],
)
def test_a_partly_covered_first_or_last_day_is_not_counted(
    first_time: str, last_time: str, start: date, end: date, tmp_path: Path
) -> None:
    xml = document(
        statement(
            entry(book="2026-06-30"),
            first_time=first_time,
            last_time=last_time,
        )
    )
    result = read_camt053([write(tmp_path, "a.xml", xml)])
    assert (result.bank_data_from, result.bank_data_through) == (start, end)
    assert len(result.rows) == 1  # an entry on the partial day is still read


def test_adjacent_statements_that_split_a_day_cover_it(tmp_path: Path) -> None:
    # S1 ends at 11:59:59 and S2 starts at 12:00:00 on June 15: no overlap, no
    # gap, and the union covers the whole day.
    a = document(
        statement(
            entry(svc="A", book="2026-06-10"),
            "S1",
            "2026-06-01",
            "2026-06-15",
            last_time="11:59:59",
        )
    )
    b = document(
        statement(
            entry(svc="B", book="2026-06-20"),
            "S2",
            "2026-06-15",
            "2026-06-30",
            first_time="12:00:00",
        )
    )
    result = read_camt053([write(tmp_path, "a.xml", a), write(tmp_path, "b.xml", b)])
    assert (result.bank_data_from, result.bank_data_through) == (
        date(2026, 6, 1),
        date(2026, 6, 30),
    )
    assert len(result.rows) == 2


def test_statements_that_really_overlap_in_time_are_refused(tmp_path: Path) -> None:
    a = document(
        statement(
            entry(svc="A", book="2026-06-10"),
            "S1",
            "2026-06-01",
            "2026-06-15",
            last_time="12:00:00",
        )
    )
    b = document(
        statement(
            entry(svc="B", book="2026-06-20"),
            "S2",
            "2026-06-15",
            "2026-06-30",
            first_time="06:00:00",
        )
    )
    message = refused(tmp_path, a, b, match="overlaps")
    assert "'S1'" in message and "'S2'" in message


def test_statements_that_touch_at_one_instant_overlap(tmp_path: Path) -> None:
    a = document(
        statement(
            entry(svc="A"), "S1", "2026-06-01", "2026-06-15", last_time="12:00:00"
        )
    )
    b = document(
        statement(
            entry(svc="B", book="2026-06-20"),
            "S2",
            "2026-06-15",
            "2026-06-30",
            first_time="12:00:00",
        )
    )
    refused(tmp_path, a, b, match="overlaps")


def test_a_gap_that_a_partial_day_leaves_is_refused(tmp_path: Path) -> None:
    a = document(
        statement(
            entry(svc="A"), "S1", "2026-06-01", "2026-06-10", last_time="12:00:00"
        )
    )
    b = document(
        statement(entry(svc="B", book="2026-06-12"), "S2", "2026-06-11", "2026-06-30")
    )
    # S1 covers June 10 only until noon, S2 starts June 11: June 10 is a gap.
    refused(tmp_path, a, b, match="gap")


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
    refused(tmp_path, xml, match="bank_data_from", bank_data_through=date(2026, 6, 30))
    result = read_camt053(
        [write(tmp_path, "a.xml", xml)],
        bank_data_from=date(2026, 6, 1),
        bank_data_through=date(2026, 6, 30),
    )
    assert (result.bank_data_from, result.bank_data_through) == (
        date(2026, 6, 1),
        date(2026, 6, 30),
    )


def test_a_config_start_earlier_than_the_statements_is_refused(tmp_path: Path) -> None:
    refused(
        tmp_path,
        document(statement(entry())),
        match="bank_data_from 2026-05-01 is earlier than the statements show",
        bank_data_from=date(2026, 5, 1),
    )


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


# --- completeness


def test_a_stated_entry_count_must_match_the_entries_present(tmp_path: Path) -> None:
    xml = document(statement(entry(), summary=count("9")))
    message = refused(tmp_path, xml, match="'S1'.*states 9 entries.*contains 1")
    assert "incomplete" in message
    ok = document(statement(entry(), summary=count("1")))
    assert len(read_one(tmp_path, ok)) == 1


def test_the_count_includes_entries_that_are_not_booked(tmp_path: Path) -> None:
    xml = document(
        statement(entry() + entry(svc="B", sts="<Sts>PDNG</Sts>"), summary=count("2"))
    )
    assert len(read_one(tmp_path, xml)) == 1


def test_a_count_that_is_not_a_number_is_refused(tmp_path: Path) -> None:
    xml = document(statement(entry(), summary=count("many")))
    refused(tmp_path, xml, match="not a count")


def test_opening_plus_credits_minus_debits_must_equal_closing(tmp_path: Path) -> None:
    entries = entry("100.00", "DBIT", svc="A") + entry("30.00", "CRDT", svc="B")
    good = balance("OPBD", "1000.00", "CRDT") + balance("CLBD", "930.00", "CRDT")
    assert len(read_one(tmp_path, document(statement(entries, balances=good)))) == 2
    bad = balance("OPBD", "1000.00", "CRDT") + balance("CLBD", "900.00", "CRDT")
    message = refused(
        tmp_path,
        document(statement(entries, balances=bad)),
        match=r"opening 1000.00 \+ booked credits 30.00 - booked debits 100.00 = 930",
    )
    assert "closing balance is 900.00" in message


def test_balance_signs_and_the_previous_closing_balance_are_respected(
    tmp_path: Path,
) -> None:
    # An overdrawn account: opening -50 (PRCD, debit), a credit of 80, closing +30.
    balances = balance("PRCD", "50.00", "DBIT") + balance("CLBD", "30.00", "CRDT")
    xml = document(statement(entry("80.00", "CRDT"), balances=balances))
    assert len(read_one(tmp_path, xml)) == 1
    wrong = balance("PRCD", "50.00", "CRDT") + balance("CLBD", "30.00", "CRDT")
    refused(
        tmp_path,
        document(statement(entry("80.00", "CRDT"), balances=wrong)),
        match="closing balance",
    )


def test_balances_are_summed_without_a_float(tmp_path: Path) -> None:
    entries = "".join(entry("0.10", "DBIT", svc=f"E{i}") for i in range(3))
    balances = balance("OPBD", "1.00", "CRDT") + balance("CLBD", "0.70", "CRDT")
    assert len(read_one(tmp_path, document(statement(entries, balances=balances)))) == 3


def test_pending_entries_do_not_count_towards_the_balance(tmp_path: Path) -> None:
    entries = entry("100.00", svc="A") + entry("500.00", svc="B", sts="<Sts>PDNG</Sts>")
    balances = balance("OPBD", "1000.00", "CRDT") + balance("CLBD", "900.00", "CRDT")
    assert len(read_one(tmp_path, document(statement(entries, balances=balances)))) == 1


def test_balances_in_another_currency_are_not_checked(tmp_path: Path) -> None:
    balances = balance("OPBD", "1.00", "CRDT", "USD") + balance(
        "CLBD", "999.00", "CRDT", "USD"
    )
    assert len(read_one(tmp_path, document(statement(entry(), balances=balances)))) == 1


def test_read_entries_parses_an_incomplete_statement_but_gives_no_coverage(
    tmp_path: Path,
) -> None:
    xml = document(statement(entry(), summary=count("9")))
    path = write(tmp_path, "a.xml", xml)
    with pytest.raises(ValueError, match="states 9 entries"):
        read_camt053([path])
    result = read_entries([path])
    assert len(result.rows) == 1
    assert result.bank_data_from is None and result.bank_data_through is None
    # An unknown status is still refused: the entry could not be placed.
    bad = write(tmp_path, "b.xml", document(statement(entry(sts="<Sts>X</Sts>"))))
    with pytest.raises(ValueError, match="not one of"):
        read_entries([bad])


# --- balances are checked one currency at a time


def two_currency_statement(*entries: str) -> str:
    balances = (
        balance("OPBD", "1000.00", "CRDT")
        + balance("CLBD", "900.00", "CRDT")
        + balance("OPBD", "500.00", "CRDT", "USD")
        + balance("CLBD", "400.00", "CRDT", "USD")
    )
    return document(statement("".join(entries), ccy=None, balances=balances))


def test_a_complete_two_currency_statement_is_accepted(tmp_path: Path) -> None:
    eur = entry("100.00", "DBIT", svc="E")
    usd = entry("100.00", "DBIT", svc="U", ccy="USD")
    assert len(read_one(tmp_path, two_currency_statement(eur, usd))) == 2


def test_another_currency_cannot_satisfy_a_balance_equation(tmp_path: Path) -> None:
    # The EUR debit is missing; the USD debit must not stand in for it.
    usd = entry("100.00", "DBIT", svc="U", ccy="USD")
    refused(
        tmp_path,
        two_currency_statement(usd),
        match=r"opening 1000.00 \+ booked credits 0 - booked debits 0 = 1000.00.*(EUR)",
    )


def test_each_currency_is_validated_on_its_own(tmp_path: Path) -> None:
    eur = entry("100.00", "DBIT", svc="E")
    usd = entry("90.00", "DBIT", svc="U", ccy="USD")
    refused(tmp_path, two_currency_statement(eur, usd), match=r"\(USD\)")


# --- fractional seconds (review 3)


def june_15_pair() -> tuple[str, str]:
    """S1 ends 11:59:59.250 and S2 starts 11:59:59.750 on June 15: half a second
    that neither statement covers, in which a payment may have settled."""
    one = document(
        statement(
            entry(svc="A", book="2026-06-10"),
            "S1",
            "2026-06-01",
            "2026-06-15",
            last_time="11:59:59.250",
        )
    )
    two = document(
        statement(
            entry(svc="B", book="2026-06-20"),
            "S2",
            "2026-06-15",
            "2026-06-30",
            first_time="11:59:59.750",
        )
    )
    return one, two


def test_fractional_seconds_cannot_bridge_an_uncovered_interval(tmp_path: Path) -> None:
    one, two = june_15_pair()
    message = refused(tmp_path, one, two, match="fractional")
    assert "'S1'" in message and "'S2'" in message


@pytest.mark.parametrize("last_time", ["23:59:59.000", "23:59:59.999"])
def test_a_last_second_with_a_fraction_counts_as_that_second(
    last_time: str, tmp_path: Path
) -> None:
    xml = document(statement(entry(), last_time=last_time))
    assert read_camt053([write(tmp_path, "a.xml", xml)]).bank_data_through == date(
        2026, 6, 30
    )


def test_a_midnight_start_with_zero_fraction_counts_as_midnight(
    tmp_path: Path,
) -> None:
    xml = document(statement(entry(), first_time="00:00:00.000"))
    assert read_camt053([write(tmp_path, "a.xml", xml)]).bank_data_from == date(
        2026, 6, 1
    )


@pytest.mark.parametrize(
    ("first_time", "last_time"),
    [
        ("00:00:00.500", "23:59:59"),
        ("00:00:00", "23:59:58.500"),
        ("00:00:00", "12:00:00.1"),
    ],
)
def test_any_other_fraction_is_refused(
    first_time: str, last_time: str, tmp_path: Path
) -> None:
    xml = document(statement(entry(), first_time=first_time, last_time=last_time))
    refused(tmp_path, xml, match="'S1'.*fractional")
