"""Invented Xero and CAMT.053 files through the whole path: files, snapshot,
investigation. The expected outcomes were measured on these fixtures and are
reported in docs/input-mapping.md section 10; none is a claim about real data."""

import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reconcile.adapters.camt053 import read_camt053
from reconcile.adapters.operational import prepare_operational_snapshot
from reconcile.adapters.xero import KINDS, read_xero
from reconcile.adapters.xero_camt import prepare_xero_camt
from reconcile.diagnosis.pipeline import investigate
from reconcile.domain.contracts import InvestigationRequest, RecordRef

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "xero_camt"
CUTOFF = date(2026, 6, 30)
METHODS = {"090": "ACH", "091": "check"}

# name: (table, record, disposition, category, abstention reason)
Outcome = tuple[str, str, str, str | None, str | None]
AS_PREPARED: dict[str, Outcome] = {
    "F06": ("invoices", "inv-f06", "answered", "F06", None),
    "SPLIT": (
        "invoices",
        "inv-spl",
        "abstained",
        None,
        "invoice_approval_not_completed",
    ),
    "DUE": ("invoices", "inv-due", "abstained", None, "invoice_approval_not_completed"),
    "F12": ("payments", "pay-f12", "abstained", None, "counterparty_unavailable"),
    "PEND": ("payments", "pay-pend", "abstained", None, "counterparty_unavailable"),
    "WKND": (
        "payments",
        "pay-wknd",
        "abstained",
        None,
        "invoice_approval_not_completed",
    ),
    "CHK": ("payments", "pay-chk", "abstained", None, "invoice_approval_not_completed"),
    "BATCH": ("payments", "bat-1", "abstained", None, "invoice_approval_not_completed"),
    "FX": ("payments", "pay-fx", "abstained", None, "unsupported_fx"),
    "F14": ("payments", "pay-f14", "abstained", None, "fee_journals_not_recognised"),
    "NETFEE": (
        "payments",
        "pay-net",
        "abstained",
        None,
        "fee_journals_not_recognised",
    ),
    "FEEROW": (
        "bank_transactions",
        "SVC-FEE-2",
        "abstained",
        None,
        "accounted_fee_journal_missing",
    ),
    "REV": ("payments", "pay-rev", "abstained", None, "required_records_missing"),
    "NO_COUNTERPARTY": (
        "payments",
        "pay-unm",
        "abstained",
        None,
        "counterparty_unavailable",
    ),
    "NO_METHOD": ("payments", "pay-nom", "abstained", None, "required_field_missing"),
    "CREDIT_NOTE": (
        "payments",
        "pay-crd",
        "abstained",
        None,
        "invoice_approval_not_completed",
    ),
}

# The same rows with an approval for every invoice added by hand, to show what
# the rows allow once approvals exist. Xero and CAMT.053 do not carry approvals.
WITH_APPROVALS: dict[str, Outcome] = {
    "F06": AS_PREPARED["F06"],
    "SPLIT": ("invoices", "inv-spl", "answered", "legitimate_split_payment", None),
    "DUE": ("invoices", "inv-due", "answered", "legitimate_due_date_payment", None),
    "WKND": ("payments", "pay-wknd", "answered", "legitimate_weekend_clearing", None),
    "CHK": ("payments", "pay-chk", "answered", "legitimate_check_clearing", None),
    "BATCH": ("payments", "bat-1", "abstained", None, "payment_journal_missing"),
    "F14": AS_PREPARED["F14"],
    "NETFEE": AS_PREPARED["NETFEE"],
    "REV": AS_PREPARED["REV"],
    "NO_COUNTERPARTY": AS_PREPARED["NO_COUNTERPARTY"],
    "NO_METHOD": AS_PREPARED["NO_METHOD"],
    "CREDIT_NOTE": ("payments", "pay-crd", "answered", "no_exception", None),
}


def run(
    snapshot: Path, digest: str, outcome: Outcome
) -> tuple[str, str | None, str | None]:
    table, record = outcome[0], outcome[1]
    request = InvestigationRequest(
        "req_test", digest, RecordRef(digest, table, record), "check", CUTOFF, "p"
    )
    d = investigate(snapshot, request).diagnosis
    return d.disposition.value, d.category, d.reason


@pytest.fixture(scope="module")
def prepared(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    out = tmp_path_factory.mktemp("snap") / "out"
    manifest = prepare_xero_camt(FIXTURE / "config.toml", out, cutoff=CUTOFF)
    return out / "snapshot.sqlite", manifest


@pytest.mark.parametrize("name", sorted(AS_PREPARED))
def test_outcome_as_prepared(name: str, prepared: tuple[Path, dict[str, Any]]) -> None:
    snapshot, manifest = prepared
    digest = manifest["snapshot"]["content_sha256"]
    outcome = AS_PREPARED[name]
    assert run(snapshot, digest, outcome) == outcome[2:]


@pytest.mark.parametrize("name", ["F14", "NETFEE"])
def test_the_shortfall_is_kept_as_evidence_when_fees_cannot_be_ruled_out(
    name: str, prepared: tuple[Path, dict[str, Any]]
) -> None:
    snapshot, manifest = prepared
    digest = manifest["snapshot"]["content_sha256"]
    _, record = AS_PREPARED[name][:2]
    request = InvestigationRequest(
        "req_test", digest, RecordRef(digest, "payments", record), "check", CUTOFF, "p"
    )
    packet = investigate(snapshot, request)
    assert [c.calculation_id for c in packet.calculations] == ["delta"]
    assert packet.diagnosis.findings == () and packet.recommendations == ()
    assert packet.diagnosis.missing_information == (
        "journals recognisable as bank fees, so a fee can be ruled out",
    )


def build_with_approvals(
    out: Path, drop: frozenset[str] = frozenset(), *, approved: bool = True
) -> tuple[Path, str]:
    xero = read_xero(
        {k: [FIXTURE / "xero" / f"{k}.json"] for k in KINDS},
        base_currency="EUR",
        method_by_account=METHODS,
    )
    bank = read_camt053(
        [FIXTURE / "bank" / "2026-06.xml", FIXTURE / "bank" / "2026-07.xml"]
    )
    approvals = [
        {
            "approval_event_id": f"a-{inv['invoice_id']}",
            "invoice_id": inv["invoice_id"],
            "approval_level": "1",
            "approver_role": "manager",
            "action": "approved",
            "event_timestamp": "2026-05-20T10:00:00",
            "previous_status": "submitted",
            "new_status": "approved",
        }
        for inv in xero.tables["invoices"]
    ]
    assert bank.bank_data_from is not None and bank.bank_data_through is not None
    rows = [r for r in bank.rows if r["bank_transaction_id"] not in drop]
    manifest = prepare_operational_snapshot(
        {
            **xero.tables,
            "bank_transactions": rows,
            **({"approval_events": approvals} if approved else {}),
        },
        out,
        cutoff=CUTOFF,
        bank_data_from=bank.bank_data_from,
        bank_data_through=bank.bank_data_through,
        all_paying_accounts_covered=True,
        reference_matching="exact",
        fee_journals_typed=False,
        source_format="test",
        source_files={},
    )
    return out / "snapshot.sqlite", manifest["snapshot"]["content_sha256"]


@pytest.fixture(scope="module")
def with_approvals(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    return build_with_approvals(tmp_path_factory.mktemp("snap2") / "out")


@pytest.mark.parametrize("name", sorted(WITH_APPROVALS))
def test_outcome_once_approvals_exist(
    name: str, with_approvals: tuple[Path, str]
) -> None:
    snapshot, digest = with_approvals
    outcome = WITH_APPROVALS[name]
    assert run(snapshot, digest, outcome) == outcome[2:]


def test_one_debit_without_counterparty_stops_every_unmatched_payment_search(
    tmp_path: Path, with_approvals: tuple[Path, str]
) -> None:
    # F12 and PEND abstained above only because the unreferenced debit of
    # pay-unm has no counterparty account, so the A3 search cannot be run.
    snapshot, digest = build_with_approvals(tmp_path / "out", frozenset({"SVC-UNM-1"}))
    assert run(snapshot, digest, AS_PREPARED["F12"]) == ("answered", "F12", None)
    assert run(snapshot, digest, AS_PREPARED["PEND"]) == (
        "answered",
        "legitimate_pending_settlement",
        None,
    )


def test_without_the_unmatched_debit_pending_still_needs_approvals(
    tmp_path: Path,
) -> None:
    # Removing the debit alone is not enough for the pending all-clear: the
    # formats carry no approvals (section 10.3 of docs/input-mapping.md).
    snapshot, digest = build_with_approvals(
        tmp_path / "out", frozenset({"SVC-UNM-1"}), approved=False
    )
    assert run(snapshot, digest, AS_PREPARED["F12"]) == ("answered", "F12", None)
    assert run(snapshot, digest, AS_PREPARED["PEND"]) == (
        "abstained",
        None,
        "invoice_approval_not_completed",
    )


def test_a_reversal_shape_is_never_answered(
    prepared: tuple[Path, dict[str, Any]], with_approvals: tuple[Path, str]
) -> None:
    # A032: the direction of a reversal entry is unverified, so no answer may
    # rest on it.
    snapshot, manifest = prepared
    assert (
        run(snapshot, manifest["snapshot"]["content_sha256"], AS_PREPARED["REV"])[0]
        != "answered"
    )
    snapshot2, digest2 = with_approvals
    assert run(snapshot2, digest2, AS_PREPARED["REV"])[0] != "answered"


def test_missing_data_is_never_a_clean_conclusion(
    prepared: tuple[Path, dict[str, Any]],
) -> None:
    snapshot, manifest = prepared
    digest = manifest["snapshot"]["content_sha256"]
    for name in ("NO_COUNTERPARTY", "NO_METHOD", "F12", "PEND"):
        disposition, category, _ = run(snapshot, digest, AS_PREPARED[name])
        assert (disposition, category) == ("abstained", None)


def test_manifest_states_what_was_read_and_what_is_missing(
    prepared: tuple[Path, dict[str, Any]],
) -> None:
    _, m = prepared
    assert m["format"] == "xero_camt053"
    assert m["bank_data_from"] == "2026-06-01"
    assert m["bank_data_through"] == "2026-07-31"
    assert m["all_paying_accounts_covered"] is True
    assert m["reference_matching"] == "exact"
    assert "approval_events" in m["tables_not_supplied"]
    assert m["tables"]["bank_transactions"]["dropped_by_cutoff"] == 1
    assert m["fee_journals_recognised"] is False  # no Xero journal is typed bank_fee
    assert m["tables"]["payments"]["rows_after"] == 18  # 17 single payments + 1 batch
    assert m["extras"]["xero"]["invoices_other_types_skipped"] == {"ACCREC": 1}
    assert m["extras"]["xero"]["invoices_with_nonzero_AmountCredited"] == 1
    assert m["extras"]["camt053"]["bank_transaction_id_source"] == {
        "AcctSvcrRef": 17,
        "NtryRef": 1,
        "Stmt/Id:position": 1,
    }
    assert set(m["source_file_sha256"]) >= {"bank/2026-06.xml", "xero/Payments.json"}


def test_snapshot_keeps_xero_ids_and_does_not_type_other_journals_as_payments(
    prepared: tuple[Path, dict[str, Any]],
) -> None:
    snapshot, _ = prepared
    con = sqlite3.connect(snapshot)
    try:
        types = dict(
            con.execute(
                "select source_transaction_id, transaction_type from gl_entries "
                "where transaction_type != 'payment'"
            ).fetchall()
        )
        assert types == {"inv-ar-1": "xero_accrec", "SVC-NET-1": "xero_cashpaid"}
        assert con.execute(
            "select count(*) from payments where payment_id in ('pay-b1','pay-b2')"
        ).fetchone() == (0,)
    finally:
        con.close()


# --- config handling


def copy_fixture(tmp_path: Path) -> Path:
    dest = tmp_path / "org"
    shutil.copytree(FIXTURE, dest)
    return dest


def edit_config(folder: Path, old: str, new: str) -> None:
    path = folder / "config.toml"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def prepare(folder: Path, tmp_path: Path) -> dict[str, Any]:
    return prepare_xero_camt(folder / "config.toml", tmp_path / "snap", cutoff=CUTOFF)


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("version = 1", "version = 1\nsurprise = 1", "unknown keys"),
        ("version = 1", "version = 2", "version"),
        ('xero_dir = "xero"', 'xero_dir = "../xero"', "relative path inside"),
        ('xero_dir = "xero"', 'xero_dir = "/etc"', "relative path inside"),
        ('"bank/2026-06.xml"', '"bank/missing.xml"', "not a file"),
        ('base_currency = "EUR"', "", "base_currency.*required"),
        ('"090" = "ACH"', '"090" = "ach"', "not among"),
        (
            'payment_reference_field = "Reference"',
            'payment_reference_field = "Narrative"',
            "payment_reference_field",
        ),
        (
            'bank_reference_fields = ["EndToEndId"]',
            'bank_reference_fields = ["Name"]',
            "bank_reference_fields",
        ),
        (
            "version = 1",
            'version = 1\nbank_data_through = "30/06/2026"',
            "bank_data_through",
        ),
        (
            "version = 1",
            "version = 1\nbank_data_through = 2026-07-31T10:00:00",
            "bank_data_through: must be a date",
        ),
        (
            "version = 1",
            "version = 1\nbank_data_from = 2026-06-01T10:00:00",
            "bank_data_from: must be a date",
        ),
        (
            "statements_cover_all_paying_accounts = true",
            "statements_cover_all_paying_accounts = false",
            "statements_cover_all_paying_accounts.*every account payments",
        ),
        (
            "statements_cover_all_paying_accounts = true",
            'statements_cover_all_paying_accounts = "yes"',
            "statements_cover_all_paying_accounts.*must be true",
        ),
        (
            'reference_matching = "exact"',
            'reference_matching = "fuzzy"',
            "reference_matching.*exact.*normalised",
        ),
        ('reference_matching = "exact"', "", "reference_matching.*is required"),
        (
            'bank_reference_fields = ["EndToEndId"]',
            "bank_reference_fields = 3",
            "bank_reference_fields",
        ),
        (
            "version = 1",
            'version = 1\nbank_data_from = "2026-05-01"',
            "bank_data_from 2026-05-01 is earlier than the statements show",
        ),
    ],
)
def test_config_errors_are_refused(
    tmp_path: Path, old: str, new: str, match: str
) -> None:
    folder = copy_fixture(tmp_path)
    edit_config(folder, old, new)
    with pytest.raises(ValueError, match=match):
        prepare(folder, tmp_path)


def test_a_symlink_cannot_leave_the_folder(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    outside = tmp_path / "outside.xml"
    shutil.copy(folder / "bank" / "2026-07.xml", outside)
    (folder / "bank" / "2026-07.xml").unlink()
    (folder / "bank" / "2026-07.xml").symlink_to(outside)
    with pytest.raises(ValueError, match="leaves the config's folder"):
        prepare(folder, tmp_path)


def test_a_missing_xero_endpoint_file_is_refused(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    (folder / "xero" / "Journals.json").unlink()
    with pytest.raises(ValueError, match="no Journals file"):
        prepare(folder, tmp_path)


def test_overlapping_bank_files_are_refused_naming_both(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    text = (folder / "bank" / "2026-07.xml").read_text()
    (folder / "bank" / "extra.xml").write_text(
        text.replace("STMT-2026-07", "STMT-OTHER").replace(
            "<ElctrncSeqNb>2", "<ElctrncSeqNb>3"
        )
    )
    edit_config(folder, '"bank/2026-07.xml"]', '"bank/2026-07.xml", "bank/extra.xml"]')
    with pytest.raises(ValueError, match="overlaps") as e:
        prepare(folder, tmp_path)
    assert "STMT-2026-07" in str(e.value) and "STMT-OTHER" in str(e.value)


def test_the_same_statement_file_twice_is_dropped(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    shutil.copy(folder / "bank" / "2026-06.xml", folder / "bank" / "again.xml")
    edit_config(folder, '"bank/2026-07.xml"]', '"bank/2026-07.xml", "bank/again.xml"]')
    manifest = prepare(folder, tmp_path)
    assert (
        len(manifest["extras"]["camt053"]["statements_dropped_as_exact_repeats"]) == 1
    )
    assert manifest["tables"]["bank_transactions"]["rows_after"] == 18


def test_the_config_may_name_the_bank_end_date_for_a_statement_without_a_period(
    tmp_path: Path,
) -> None:
    folder = copy_fixture(tmp_path)
    for name in ("2026-06.xml", "2026-07.xml"):
        path = folder / "bank" / name
        text = path.read_text()
        start, end = text.index("<FrToDt>"), text.index("</FrToDt>") + len("</FrToDt>")
        path.write_text(text[:start] + text[end:])
    with pytest.raises(ValueError, match="states no period"):
        prepare(folder, tmp_path)
    edit_config(folder, "version = 1", 'version = 1\nbank_data_through = "2026-07-31"')
    with pytest.raises(ValueError, match="bank_data_from"):
        prepare(folder, tmp_path)
    edit_config(folder, "version = 1", 'version = 1\nbank_data_from = "2026-06-01"')
    manifest = prepare(folder, tmp_path)
    assert (manifest["bank_data_from"], manifest["bank_data_through"]) == (
        "2026-06-01",
        "2026-07-31",
    )


def test_bad_data_in_several_files_is_reported_together(tmp_path: Path) -> None:
    folder = copy_fixture(tmp_path)
    path = folder / "xero" / "Payments.json"
    doc = json.loads(path.read_text())
    doc["Payments"][0]["Date"] = "01/06/2026"
    doc["Payments"][1]["Status"] = "PENDING"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="2 problem") as e:
        prepare(folder, tmp_path)
    assert "Payments.json Payments pay-f06-1" in str(e.value)
    assert "Payments.json Payments pay-f06-2" in str(e.value)


# --- coverage across accounts and from the start of the statements


def other_account_statement(last: str) -> str:
    """A second account's statement, June 1 to `last` (invented IBAN)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">'
        "<BkToCstmrStmt>"
        "<Stmt><Id>STMT-B</Id>"
        f"<FrToDt><FrDtTm>2026-06-01T00:00:00</FrDtTm><ToDtTm>{last}T23:59:59</ToDtTm></FrToDt>"
        "<Acct><Id><IBAN>DE00OTHERACCOUNT0001</IBAN></Id><Ccy>EUR</Ccy></Acct>"
        '<Ntry><Amt Ccy="EUR">5.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>BOOK</Sts>'
        "<BookgDt><Dt>2026-06-05</Dt></BookgDt><AcctSvcrRef>SVC-B-1</AcctSvcrRef></Ntry>"
        "</Stmt></BkToCstmrStmt></Document>"
    )


def test_two_accounts_with_different_end_dates_prepare_at_the_common_horizon(
    tmp_path: Path,
) -> None:
    # Account A's statements run to July 31 and hold a July row; account
    # B's end June 20. The horizon is June 20: A's later rows are past the cutoff
    # and dropped, not refused as lying beyond the data.
    folder = copy_fixture(tmp_path)
    (folder / "bank" / "b.xml").write_text(other_account_statement("2026-06-20"))
    edit_config(folder, '"bank/2026-07.xml"]', '"bank/2026-07.xml", "bank/b.xml"]')
    manifest = prepare_xero_camt(
        folder / "config.toml", tmp_path / "snap", cutoff=date(2026, 6, 20)
    )
    assert manifest["bank_data_through"] == "2026-06-20"
    assert manifest["bank_data_from"] == "2026-06-01"
    assert manifest["tables"]["bank_transactions"]["dropped_by_cutoff"] >= 1
    assert manifest["tables"]["bank_transactions"]["rows_after"] >= 2


def test_statements_that_start_after_a_payment_stop_it_being_called_missing(
    tmp_path: Path,
) -> None:
    # Only the July statement is supplied: coverage starts July 1. The June 4
    # payment has no bank row because the June statement is not here, not
    # because the bank never paid it.
    folder = copy_fixture(tmp_path)
    edit_config(folder, '"bank/2026-06.xml", "bank/2026-07.xml"', '"bank/2026-07.xml"')
    manifest = prepare_xero_camt(
        folder / "config.toml", tmp_path / "snap", cutoff=date(2026, 7, 31)
    )
    assert manifest["bank_data_from"] == "2026-07-01"
    snapshot = tmp_path / "snap" / "snapshot.sqlite"
    digest = manifest["snapshot"]["content_sha256"]
    for record in ("pay-f12", "pay-pend"):
        request = InvestigationRequest(
            "r",
            digest,
            RecordRef(digest, "payments", record),
            "check",
            date(2026, 7, 31),
            "p",
        )
        d = investigate(snapshot, request).diagnosis
        assert (d.disposition.value, d.reason) == (
            "abstained",
            "payment_before_bank_coverage",
        )


def test_an_omitted_half_second_cannot_make_a_settled_payment_look_missing(
    tmp_path: Path,
) -> None:
    # Two statements leave 11:59:59.250-11:59:59.750 on June 15 uncovered. A payment
    # that settled then must not be answered F12: the data is refused instead.
    from test_camt053_adapter import june_15_pair

    folder = copy_fixture(tmp_path)
    one, two = june_15_pair()
    (folder / "bank" / "2026-06.xml").write_text(one)
    (folder / "bank" / "2026-07.xml").write_text(two)
    with pytest.raises(ValueError, match="fractional"):
        prepare(folder, tmp_path)
    assert not (tmp_path / "snap").exists()
