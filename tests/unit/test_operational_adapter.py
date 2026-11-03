"""Operational snapshots: rows in contract names, source IDs unchanged.

The public fixtures are read as plain contract-named rows (no tokens) and must
give the same dispositions as the FinRCA path (expected.json is the label).
"""

import csv
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reconcile.adapters.finrca import TABLES, prepare_finrca_snapshot
from reconcile.adapters.operational import prepare_operational_snapshot
from reconcile.diagnosis.pipeline import investigate
from reconcile.domain.contracts import (
    EvidencePacket,
    InvestigationRequest,
    RecordRef,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "public"
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected.json").read_text())
CUTOFF = date(2026, 6, 30)
Rows = dict[str, list[dict[str, str | None]]]


def load(name: str, drop: tuple[str, ...] = ()) -> Rows:
    out: Rows = {}
    for table in TABLES:
        if table in drop:
            continue
        with (FIXTURES / name / f"{table}.csv").open(newline="") as f:
            out[table] = [dict(r) for r in csv.DictReader(f)]
    return out


def prepare(
    rows: Rows,
    tmp_path: Path,
    *,
    cutoff: date = CUTOFF,
    through: date = date(2026, 7, 31),
) -> dict[str, Any]:
    return prepare_operational_snapshot(
        rows,
        tmp_path / "snap",
        cutoff=cutoff,
        bank_data_through=through,
        source_format="test",
        source_files={"payments.csv": "0" * 64},
    )


def run(
    manifest: dict[str, Any], tmp_path: Path, table: str, record: str
) -> EvidencePacket:
    digest = manifest["snapshot"]["content_sha256"]
    request = InvestigationRequest(
        "req_test",
        digest,
        RecordRef(digest, table, record),
        "Check whether there is a reconciliation issue.",
        CUTOFF,
        "policy_test",
    )
    return investigate(tmp_path / "snap" / "snapshot.sqlite", request)


def outcome(packet: EvidencePacket) -> tuple[str, str | None, str | None]:
    d = packet.diagnosis
    return d.disposition.value, d.category, d.reason


def expected(name: str) -> tuple[str, str | None, str | None]:
    e = EXPECTED[name]
    return e["disposition"], e["category"], e.get("abstention_reason")


def primary(name: str) -> tuple[str, str]:
    e = EXPECTED[name]["primary_entity"]
    return e["table"], e["id"]


def add_unrelated_fee_journal(rows: Rows) -> None:
    """A balanced bank_fee journal for some other bank row: proof that this data
    types fee journals, without touching the record under test."""
    currency = rows["payments"][0]["payment_currency"]
    for n, (account, debit, credit) in enumerate(
        (("650000", "5.00", "0.00"), ("100000", "0.00", "5.00")), start=1
    ):
        rows["gl_entries"].append(
            {
                "journal_id": "J_OTHER_FEE",
                "journal_line_id": f"J_OTHER_FEE_L{n}",
                "source_transaction_id": "BT_OTHER",
                "transaction_type": "bank_fee",
                "posting_date": "2026-06-03",
                "accounting_period": "2026-06",
                "gl_account": account,
                "debit": debit,
                "credit": credit,
                "currency": currency,
            }
        )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_fixture_gives_the_expected_outcome_with_raw_ids(
    name: str, tmp_path: Path
) -> None:
    rows = load(name)
    if name == "f14_amount_shortfall":  # this fixture has no fee journal at all
        add_unrelated_fee_journal(rows)
    manifest = prepare(rows, tmp_path)
    assert outcome(run(manifest, tmp_path, *primary(name))) == expected(name)


def test_f14_needs_proof_that_fee_journals_are_recognised(tmp_path: Path) -> None:
    rows = load("f14_amount_shortfall")
    manifest = prepare(rows, tmp_path / "none")
    assert manifest["fee_journals_recognised"] is False
    packet = run(manifest, tmp_path / "none", *primary("f14_amount_shortfall"))
    assert outcome(packet) == ("abstained", None, "fee_journals_not_recognised")
    assert packet.diagnosis.findings == () and packet.recommendations == ()
    assert [c.calculation_id for c in packet.calculations] == ["delta"]
    assert packet.evidence  # kept
    add_unrelated_fee_journal(rows)
    manifest = prepare(rows, tmp_path / "some")
    assert manifest["fee_journals_recognised"] is True
    packet = run(manifest, tmp_path / "some", *primary("f14_amount_shortfall"))
    assert outcome(packet) == ("answered", "F14", None)


def test_the_net_fee_all_clear_is_unaffected_by_the_fee_journal_flag(
    tmp_path: Path,
) -> None:
    manifest = prepare(load("net_fee_accounting"), tmp_path)
    assert manifest["fee_journals_recognised"] is True
    assert outcome(run(manifest, tmp_path, *primary("net_fee_accounting"))) == (
        "answered",
        "legitimate_net_fee_accounting",
        None,
    )


def test_a_manifest_without_the_key_still_answers_f14(tmp_path: Path) -> None:
    rows = load("f14_amount_shortfall")
    manifest = prepare(rows, tmp_path)
    path = tmp_path / "snap" / "manifest.json"
    bare = json.loads(path.read_text())
    del bare["fee_journals_recognised"]
    path.chmod(0o644)
    path.write_text(json.dumps(bare))
    assert outcome(run(manifest, tmp_path, *primary("f14_amount_shortfall"))) == (
        "answered",
        "F14",
        None,
    )


def test_source_ids_are_stored_unchanged(tmp_path: Path) -> None:
    prepare(load("f06_double_payment"), tmp_path)
    con = sqlite3.connect(tmp_path / "snap" / "snapshot.sqlite")
    assert [
        r[0] for r in con.execute("SELECT payment_id FROM payments ORDER BY 1")
    ] == [
        "PAY_1",
        "PAY_2",
    ]
    con.close()


def test_finrca_digest_is_unchanged(tmp_path: Path) -> None:
    # Pinned from the FinRCA adapter before the operational adapter existed, with
    # a fixed key; a change here means finrca.py behaviour changed.
    key = tmp_path / "key"
    key.write_text("ab" * 32 + "\n")
    pinned = {
        "f06_double_payment": (
            "a5883925ab8c9b2ec17561bf1b1c39ee82234b3d43333fff5c49a33c615dbc63"
        ),
        "weekend_clearing": (
            "87540568ff1f0330e132496048cc89528fc4fcea495c51654d4b743572a2dc89"
        ),
    }
    for name, digest in pinned.items():
        manifest = prepare_finrca_snapshot(
            FIXTURES / name,
            tmp_path / name,
            key_path=key,
            source_revision="x",
            cutoff=CUTOFF,
        )
        assert manifest["snapshot"]["content_sha256"] == digest


def test_row_order_does_not_change_the_digest(tmp_path: Path) -> None:
    rows = load("f06_double_payment")
    a = prepare(rows, tmp_path / "a")
    reversed_rows = {t: list(reversed(r)) for t, r in rows.items()}
    b = prepare(reversed_rows, tmp_path / "b")
    assert a["snapshot"]["content_sha256"] == b["snapshot"]["content_sha256"]


# --- bank_data_through (D160) ---------------------------------------------


def test_cutoff_after_bank_data_through_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bank_data_through"):
        prepare(
            load("f12_payment_missing_from_bank"),
            tmp_path,
            cutoff=CUTOFF,
            through=date(2026, 6, 29),
        )
    assert not (tmp_path / "snap").exists()


def test_bank_row_after_bank_data_through_is_refused(tmp_path: Path) -> None:
    rows = load("f06_double_payment")
    with pytest.raises(
        ValueError, match=r"bank_transactions row 2.*after bank_data_through"
    ):
        prepare(rows, tmp_path, cutoff=date(2026, 6, 5), through=date(2026, 6, 5))
    assert not list(tmp_path.glob(".prepare-*"))


def test_bank_rows_between_cutoff_and_bank_end_are_excluded_by_the_cutoff(
    tmp_path: Path,
) -> None:
    rows = load("f06_second_debit_post_cutoff")
    manifest = prepare(rows, tmp_path, through=date(2026, 7, 31))
    assert manifest["tables"]["bank_transactions"]["dropped_by_cutoff"] == 1
    assert manifest["bank_data_through"] == "2026-07-31"
    assert outcome(
        run(manifest, tmp_path, *primary("f06_second_debit_post_cutoff"))
    ) == expected("f06_second_debit_post_cutoff")


@pytest.mark.parametrize(
    "table",
    ["invoices", "payments", "payment_allocations", "bank_transactions", "gl_entries"],
)
def test_a_table_the_checks_need_must_be_supplied(table: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=f"required tables not supplied.*{table}"):
        prepare(load("f06_double_payment", drop=(table,)), tmp_path)


def test_unknown_table_is_refused(tmp_path: Path) -> None:
    rows = load("f06_double_payment")
    rows["audit_log"] = []
    with pytest.raises(ValueError, match="unknown tables.*audit_log"):
        prepare(rows, tmp_path)


def test_missing_approval_events_is_listed_and_no_all_clear_is_reached(
    tmp_path: Path,
) -> None:
    name = "weekend_clearing"
    with_approvals = prepare(load(name), tmp_path / "with")
    assert outcome(run_in(with_approvals, tmp_path / "with", name)) == expected(name)
    assert expected(name)[0] == "answered"

    without = prepare(load(name, drop=("approval_events",)), tmp_path / "without")
    assert "approval_events" in without["tables_not_supplied"]
    assert without["tables"].get("approval_events") is None
    disposition, category, reason = outcome(run_in(without, tmp_path / "without", name))
    assert disposition == "abstained" and category is None
    assert reason == "invoice_approval_not_completed"


def run_in(manifest: dict[str, Any], tmp_path: Path, name: str) -> EvidencePacket:
    return run(manifest, tmp_path, *primary(name))


# --- validation ------------------------------------------------------------


def test_conflicting_rows_with_one_key_are_refused_naming_the_rows(
    tmp_path: Path,
) -> None:
    rows = load("f06_double_payment")
    rows["payments"].append({**rows["payments"][0], "payment_amount": "999.00"})
    with pytest.raises(ValueError, match=r"conflicting rows.*row 1 and row 3"):
        prepare(rows, tmp_path)


def test_exact_repeat_is_removed_and_counted(tmp_path: Path) -> None:
    rows = load("f06_double_payment")
    rows["payments"].append(dict(rows["payments"][0]))
    manifest = prepare(rows, tmp_path)
    assert manifest["tables"]["payments"]["exact_duplicates_removed"] == 1
    assert manifest["tables"]["payments"]["rows_after"] == 2


def test_distinct_payments_with_identical_values_both_stay(tmp_path: Path) -> None:
    manifest = prepare(load("real_identical_distinct_payments"), tmp_path)
    assert manifest["tables"]["payments"]["rows_after"] == 2


@pytest.mark.parametrize(
    ("table", "column", "value", "message"),
    [
        ("payments", "payment_date", "20260601", "not an ISO date"),
        ("payments", "payment_date", "2026-6-1", "not an ISO date"),
        ("payments", "created_at", "2026-06-01T08:00:00+02:00", "not an ISO timestamp"),
        ("payments", "payment_amount", "1e3", "malformed amount"),
        ("payments", "payment_amount", "1,000.00", "malformed amount"),
        ("payments", "payment_amount", "10.005", "more than 2 decimal places"),
        ("payments", "payment_currency", "XYZ", "unknown or unsupported currency"),
        ("payments", "payment_status", "Completed", "is not one of"),
        ("payments", "settlement_status", "cleared", "is not one of"),
        ("bank_transactions", "status", "booked", "is not one of"),
        ("bank_transactions", "direction", "DEBIT", "is not one of"),
        ("bank_transactions", "transaction_type", "bank_fee", "exactly 'bank fee'"),
        ("bank_transactions", "transaction_type", "Payment Reversal", "exactly"),
        ("bank_transactions", "amount", "-1000.00", "direction carries the sign"),
        ("payments", "payment_method", "Check", "exactly 'check'"),
        ("payments", "payment_method", "ach", "exactly 'ACH'"),
        ("invoices", "status", "Paid", "exactly 'paid'"),
        ("payments", "unknown_column", "x", "unknown columns"),
    ],
)
def test_bad_values_are_refused_naming_table_row_and_column(
    table: str, column: str, value: str, message: str, tmp_path: Path
) -> None:
    rows = load("f06_double_payment")
    rows[table][0][column] = value
    with pytest.raises(ValueError, match=rf"{table} row 1.*{message}"):
        prepare(rows, tmp_path)
    assert not (tmp_path / "snap").exists()


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("bank_transactions", "direction"),
        ("bank_transactions", "status"),
        ("bank_transactions", "transaction_type"),
        ("bank_transactions", "posted_date"),
        ("payments", "payment_status"),
        ("payments", "settlement_status"),
        ("payments", "payment_date"),
        ("payments", "payment_amount"),
        ("payment_allocations", "allocation_date"),
        ("invoices", "invoice_total"),
    ],
)
def test_columns_whose_null_changes_a_verdict_are_required(
    table: str, column: str, tmp_path: Path
) -> None:
    rows = load("f06_double_payment")
    rows[table][0][column] = ""
    with pytest.raises(ValueError, match=rf"{table} row 1: {column} is empty"):
        prepare(rows, tmp_path)


def test_unrecognised_payment_method_is_listed_and_the_investigator_abstains(
    tmp_path: Path,
) -> None:
    rows = load("f12_payment_missing_from_bank")
    rows["payments"][0]["payment_method"] = "SEPA"
    manifest = prepare(rows, tmp_path)
    assert manifest["unrecognised_payment_methods"] == {"SEPA": 1}
    assert outcome(run(manifest, tmp_path, "payments", "PAY_1")) == (
        "abstained",
        None,
        "unknown_payment_method",
    )


# Measured, not assumed: NULL in a column the checks can live without gives an
# explicit non-answer for the case it matters to, never a wrong answer.
@pytest.mark.parametrize(
    ("scenario", "table", "column", "result"),
    [
        (
            "f12_payment_missing_from_bank",
            "payments",
            "payment_method",
            ("abstained", None, "unknown_payment_method"),
        ),
        (
            "f12_payment_missing_from_bank",
            "payments",
            "reference_number",
            ("abstained", None, "required_field_missing"),
        ),
        (
            "f06_double_payment",
            "bank_transactions",
            "payment_reference",
            ("abstained", None, "commitment_not_proven_at_cutoff"),
        ),
        (
            "f06_double_payment",
            "payments",
            "payment_method",
            ("answered", "F06", None),
        ),
        (
            "f06_double_payment",
            "invoices",
            "status",
            ("answered", "F06", None),
        ),
    ],
)
def test_optional_null_never_gives_a_wrong_answer(
    scenario: str,
    table: str,
    column: str,
    result: tuple[str, str | None, str | None],
    tmp_path: Path,
) -> None:
    rows = load(scenario)
    for row in rows[table]:
        row[column] = None
    manifest = prepare(rows, tmp_path)
    assert outcome(run(manifest, tmp_path, *primary(scenario))) == result


# --- normalisation (D158) --------------------------------------------------


def test_references_and_accounts_compare_equal_after_normalisation(
    tmp_path: Path,
) -> None:
    rows = load("f06_double_payment")
    rows["payments"][0]["reference_number"] = "  ref_1 "
    rows["bank_transactions"][0]["payment_reference"] = "Ref_1"
    rows["payments"][0]["bank_account_id"] = "ba v1"
    manifest = prepare(rows, tmp_path)
    assert outcome(run(manifest, tmp_path, "invoices", "INV_1")) == expected(
        "f06_double_payment"
    )
    con = sqlite3.connect(tmp_path / "snap" / "snapshot.sqlite")
    stored = con.execute(
        "SELECT reference_number, bank_account_id FROM payments "
        "WHERE payment_id='PAY_1'"
    ).fetchone()
    con.close()
    assert stored == ("REF_1", "BAV1")
    norm = manifest["normalisation"]
    assert "uppercase" in norm["references"] and norm["original_text_kept"] is False
    assert "payments.reference_number" in norm["columns"]


def test_internal_whitespace_in_a_reference_is_collapsed(tmp_path: Path) -> None:
    rows = load("f06_double_payment")
    rows["payments"][0]["reference_number"] = "REF  \t 1"
    rows["bank_transactions"][0]["payment_reference"] = "ref 1"
    manifest = prepare(rows, tmp_path)
    con = sqlite3.connect(tmp_path / "snap" / "snapshot.sqlite")
    assert con.execute(
        "SELECT reference_number FROM payments WHERE payment_id='PAY_1'"
    ).fetchone() == ("REF 1",)
    con.close()
    assert manifest["reference_quality"]["payments"]["with_unique_reference"] == 2


def test_reference_without_normalisation_would_not_match(tmp_path: Path) -> None:
    # Control: different text stays different, so the test above is not vacuous.
    rows = load("f06_double_payment")
    rows["bank_transactions"][0]["payment_reference"] = "REF_1X"
    manifest = prepare(rows, tmp_path)
    assert outcome(run(manifest, tmp_path, "invoices", "INV_1")) != expected(
        "f06_double_payment"
    )


# --- manifest ---------------------------------------------------------------


def test_manifest_records_what_a_report_needs(tmp_path: Path) -> None:
    rows = load("real_bank_batch_debit", drop=("approval_events",))
    manifest = prepare_operational_snapshot(
        rows,
        tmp_path / "snap",
        cutoff=CUTOFF,
        bank_data_through=date(2026, 7, 15),
        source_format="unit_test",
        source_files={"payments.csv": "1" * 64},
        extras={"note": "x"},
    )
    on_disk = json.loads((tmp_path / "snap" / "manifest.json").read_text())
    assert on_disk == manifest
    assert manifest["format"] == "unit_test"
    assert manifest["bank_data_through"] == "2026-07-15"
    assert manifest["tables_not_supplied"][-1] == "approval_events"
    assert manifest["source_file_sha256"] == {"payments.csv": "1" * 64}
    assert manifest["extras"] == {"note": "x"}
    assert set(manifest["snapshot"]) == {"file", "content_sha256", "file_sha256"}
    assert manifest["adapter_version"] and manifest["identifiers"]
    quality = json.loads((tmp_path / "snap" / "quality.json").read_text())
    assert quality["row_counts"]["payments"] == 3


def test_reference_quality_counts(tmp_path: Path) -> None:
    rows = load("insufficient_shared_reference")
    rows["payments"].append(
        {**rows["payments"][0], "payment_id": "PAY_9", "reference_number": ""}
    )
    manifest = prepare(rows, tmp_path)
    q = manifest["reference_quality"]
    assert q["payments"] == {
        "total": 3,
        "with_reference": 2,
        "with_unique_reference": 0,
    }
    assert q["bank_debits"] == {"total": 0, "with_reference": 0}


def test_existing_output_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "snap").mkdir()
    with pytest.raises(FileExistsError):
        prepare(load("f06_double_payment"), tmp_path)
