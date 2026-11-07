"""Operational snapshots: rows in contract names, source IDs unchanged.

The public fixtures are read as plain contract-named rows (no tokens) and must
give the same dispositions as the FinRCA path (expected.json is the label).
"""

import csv
import json
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
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
from reconcile.reports.explain import explain

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
    start: date = date(2026, 1, 1),
    through: date = date(2026, 7, 31),
    reference_matching: str = "normalised",
    fee_journals_typed: bool = True,
) -> dict[str, Any]:
    return prepare_operational_snapshot(
        rows,
        tmp_path / "snap",
        cutoff=cutoff,
        bank_data_from=start,
        bank_data_through=through,
        all_paying_accounts_covered=True,
        reference_matching=reference_matching,
        fee_journals_typed=fee_journals_typed,
        source_format="test",
        source_files={"payments.csv": "0" * 64},
    )


def run(
    manifest: dict[str, Any],
    tmp_path: Path,
    table: str,
    record: str,
    cutoff: date = CUTOFF,
) -> EvidencePacket:
    digest = manifest["snapshot"]["content_sha256"]
    request = InvestigationRequest(
        "req_test",
        digest,
        RecordRef(digest, table, record),
        "Check whether there is a reconciliation issue.",
        cutoff,
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


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_fixture_gives_the_expected_outcome_with_raw_ids(
    name: str, tmp_path: Path
) -> None:
    manifest = prepare(load(name), tmp_path)
    assert outcome(run(manifest, tmp_path, *primary(name))) == expected(name)


def test_f14_needs_the_declaration_that_fee_journals_are_typed(tmp_path: Path) -> None:
    rows = load("f14_amount_shortfall")
    untyped = prepare(rows, tmp_path / "no", fee_journals_typed=False)
    assert untyped["fee_journals_recognised"] is False
    packet = run(untyped, tmp_path / "no", *primary("f14_amount_shortfall"))
    assert outcome(packet) == ("abstained", None, "fee_journals_not_recognised")
    assert packet.diagnosis.findings == () and packet.recommendations == ()
    assert [c.calculation_id for c in packet.calculations] == ["delta"]
    assert packet.evidence  # kept
    typed = prepare(rows, tmp_path / "yes", fee_journals_typed=True)
    assert typed["fee_journals_recognised"] is True
    packet = run(typed, tmp_path / "yes", *primary("f14_amount_shortfall"))
    assert outcome(packet) == ("answered", "F14", None)


def test_one_typed_fee_journal_does_not_stand_for_the_whole_file(
    tmp_path: Path,
) -> None:
    # The old inference called fee journals recognised when any row was typed
    # bank_fee, so an unrelated typed journal hid that this payment's own fee
    # journal (xero_cashpaid) was not recognised: a confident F14.
    rows = load("net_fee_accounting")
    other = [
        {
            **r,
            "journal_id": "J_OTHER",
            "journal_line_id": f"J_OTHER_{n}",
            "source_transaction_id": "BT_OTHER",
        }
        for n, r in enumerate(rows["gl_entries"][:2])
        if r["transaction_type"] == "bank_fee"
    ]
    for r in rows["gl_entries"]:
        if r["transaction_type"] == "bank_fee":
            r["transaction_type"] = "xero_cashpaid"
    rows["gl_entries"].extend(other)
    assert other  # the control row exists
    manifest = prepare(rows, tmp_path, fee_journals_typed=False)
    assert outcome(run(manifest, tmp_path, *primary("net_fee_accounting"))) == (
        "abstained",
        None,
        "fee_journals_not_recognised",
    )


def test_the_net_fee_all_clear_is_unaffected_by_the_fee_journal_declaration(
    tmp_path: Path,
) -> None:
    for typed in (True, False):
        folder = tmp_path / str(typed)
        manifest = prepare(load("net_fee_accounting"), folder, fee_journals_typed=typed)
        assert manifest["fee_journals_recognised"] is typed
        assert outcome(run(manifest, folder, *primary("net_fee_accounting"))) == (
            "answered",
            "legitimate_net_fee_accounting",
            None,
        )


def test_a_manifest_without_the_key_still_answers_f14(tmp_path: Path) -> None:
    # Only a manifest that is not identified as operational (FinRCA) may lack the
    # fee key; an operational one without it is refused (see below).
    rows = load("f14_amount_shortfall")
    manifest = prepare(rows, tmp_path)
    path = tmp_path / "snap" / "manifest.json"
    bare = json.loads(path.read_text())
    del bare["fee_journals_recognised"], bare["adapter_version"]
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


def test_a_bank_row_after_the_common_horizon_is_dropped_not_refused(
    tmp_path: Path,
) -> None:
    # With several accounts the horizon is the earliest end, so a row of a longer
    # account lies after it and is dropped like any row after the cutoff. The
    # end-to-end version with two statements is in test_xero_camt_end_to_end.
    rows = load("f06_double_payment")
    manifest = prepare(
        rows, tmp_path, cutoff=date(2026, 6, 5), through=date(2026, 6, 5)
    )
    assert manifest["tables"]["bank_transactions"]["dropped_by_cutoff"] == 1


def test_coverage_declarations_are_recorded_and_checked(tmp_path: Path) -> None:
    rows = load("f12_payment_missing_from_bank")
    manifest = prepare(rows, tmp_path / "a", start=date(2026, 5, 1))
    assert manifest["bank_data_from"] == "2026-05-01"
    assert manifest["all_paying_accounts_covered"] is True
    assert manifest["reference_matching"] == "normalised"
    with pytest.raises(ValueError, match="bank_data_from 2026-07-01 is after"):
        prepare(rows, tmp_path / "b", start=date(2026, 7, 1), through=date(2026, 6, 30))
    with pytest.raises(ValueError, match="statements for every account"):
        prepare_operational_snapshot(
            rows,
            tmp_path / "c",
            cutoff=CUTOFF,
            bank_data_from=date(2026, 1, 1),
            bank_data_through=date(2026, 7, 31),
            all_paying_accounts_covered=False,
            reference_matching="exact",
            fee_journals_typed=True,
            source_format="t",
            source_files={},
        )
    with pytest.raises(ValueError, match="reference_matching must be one of"):
        prepare(rows, tmp_path / "d", reference_matching="fuzzy")


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
        bank_data_from=date(2026, 5, 1),
        bank_data_through=date(2026, 7, 15),
        all_paying_accounts_covered=True,
        reference_matching="exact",
        fee_journals_typed=False,
        source_format="unit_test",
        source_files={"payments.csv": "1" * 64},
        extras={"note": "x"},
    )
    on_disk = json.loads((tmp_path / "snap" / "manifest.json").read_text())
    assert on_disk == manifest
    assert manifest["format"] == "unit_test"
    assert manifest["bank_data_from"] == "2026-05-01"
    assert manifest["bank_data_through"] == "2026-07-15"
    assert manifest["reference_matching"] == "exact"
    assert manifest["fee_journals_recognised"] is False
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


# --- the snapshot's cutoff ---------------------------------------------------


@pytest.mark.parametrize("requested", [date(2026, 7, 10), date(2026, 6, 15)])
def test_a_request_cutoff_other_than_the_prepared_one_fails(
    requested: date, tmp_path: Path
) -> None:
    # Queries without a date bound (reference lookups) would read rows prepared
    # for a different cutoff: a June 30 snapshot holds no July debit, so a July 10
    # request would find a June 29 payment "missing" (F12).
    manifest = prepare(load("f12_payment_missing_from_bank"), tmp_path)
    packet = run(manifest, tmp_path, "payments", "PAY_1", cutoff=requested)
    assert outcome(packet) == ("failed", None, "cutoff_differs_from_snapshot")
    detail = packet.limitations[0]
    assert requested.isoformat() in detail and "2026-06-30" in detail
    assert packet.diagnosis.findings == () and packet.evidence == ()


# --- bank coverage start -----------------------------------------------------


@pytest.mark.parametrize(
    "scenario", ["f12_payment_missing_from_bank", "pending_settlement"]
)
def test_a_payment_before_the_bank_data_begins_is_not_called_missing(
    scenario: str, tmp_path: Path
) -> None:
    rows = load(scenario)
    paid = rows["payments"][0]["payment_date"]
    assert paid is not None
    first = date.fromisoformat(paid)
    covered = prepare(rows, tmp_path / "in", start=first)
    answered = run(covered, tmp_path / "in", *primary(scenario))
    assert outcome(answered) == expected(scenario)  # the day itself is covered
    late = prepare(rows, tmp_path / "out", start=first + timedelta(days=1))
    packet = run(late, tmp_path / "out", *primary(scenario))
    assert outcome(packet) == ("abstained", None, "payment_before_bank_coverage")
    assert packet.diagnosis.missing_information == (
        "bank statements covering the payment date",
    )
    assert packet.diagnosis.findings == () and packet.recommendations == ()
    assert packet.evidence and [c for c in packet.calculations] == list(
        answered.calculations
    )
    assert any(
        f"complete from {first + timedelta(days=1)} through 2026-07-31" in x
        for x in packet.limitations
    )


def test_coverage_start_does_not_touch_an_answer_that_needs_a_bank_row(
    tmp_path: Path,
) -> None:
    # The F06 payments are dated before this start, but each has its debit.
    rows = load("f06_double_payment")
    manifest = prepare(rows, tmp_path, start=date(2026, 6, 20))
    assert outcome(run(manifest, tmp_path, *primary("f06_double_payment"))) == expected(
        "f06_double_payment"
    )


# --- reference matching (review item 5) ----------------------------------------


def case_collision(rows: Rows) -> None:
    """Payment P (reference RunA) and an unrelated debit to another account whose
    reference differs from it only in case."""
    rows["payments"][0]["reference_number"] = "RunA"
    rows["bank_transactions"][0]["payment_reference"] = "RUNA"
    rows["bank_transactions"][0]["counterparty_token"] = "SOMEONE_ELSE"


def test_normalised_matching_lets_a_case_collision_clear_a_payment(
    tmp_path: Path,
) -> None:
    # Documents the hazard that made matching a declared choice.
    rows = load("check_clearing")
    case_collision(rows)
    manifest = prepare(rows, tmp_path, reference_matching="normalised")
    assert outcome(run(manifest, tmp_path, "payments", "PAY_1")) == (
        "answered",
        "legitimate_check_clearing",
        None,
    )


def test_exact_matching_does_not_clear_a_payment_on_a_case_collision(
    tmp_path: Path,
) -> None:
    rows = load("check_clearing")
    case_collision(rows)
    manifest = prepare(rows, tmp_path, reference_matching="exact")
    assert manifest["reference_matching"] == "exact"
    assert "trim surrounding whitespace only" in manifest["normalisation"]["references"]
    # No bank row carries P's reference: P is missing from the bank, not cleared.
    assert outcome(run(manifest, tmp_path, "payments", "PAY_1")) == (
        "answered",
        "F12",
        None,
    )


def test_exact_matching_still_trims_and_normalises_accounts(tmp_path: Path) -> None:
    rows = load("f06_double_payment")
    rows["payments"][0]["reference_number"] = "  REF_1 "
    rows["payments"][0]["bank_account_id"] = "ba v1"
    manifest = prepare(rows, tmp_path, reference_matching="exact")
    con = sqlite3.connect(tmp_path / "snap" / "snapshot.sqlite")
    assert con.execute(
        "SELECT reference_number, bank_account_id FROM payments "
        "WHERE payment_id='PAY_1'"
    ).fetchone() == ("REF_1", "BAV1")
    con.close()
    assert outcome(run(manifest, tmp_path, "invoices", "INV_1")) == expected(
        "f06_double_payment"
    )


# --- F06 commitment (review item 10) -------------------------------------------


def test_f06_does_not_count_an_allocation_whose_debit_is_far_below_the_payment(
    tmp_path: Path,
) -> None:
    rows = load("f06_double_payment")
    rows["bank_transactions"][1]["amount"] = "0.01"  # PAY_2 is 1000.00
    manifest = prepare(rows, tmp_path)
    packet = run(manifest, tmp_path, "invoices", "INV_1")
    assert outcome(packet) == ("abstained", None, "debit_differs_from_payment")


def test_f06_packets_keep_their_citations_and_the_abstention_carries_the_calculation(
    tmp_path: Path,
) -> None:
    manifest = prepare(load("f06_double_payment"), tmp_path / "ok")
    packet = run(manifest, tmp_path / "ok", "invoices", "INV_1")
    assert outcome(packet) == ("answered", "F06", None)
    assert packet.diagnosis.findings[0].calculation_ids == (
        "allocated",
        "committed",
        "invoice_total_minus_committed",
    )
    assert not any(
        c.calculation_id.startswith("debit_minus") for c in packet.calculations
    )
    rows = load("f06_double_payment")
    rows["bank_transactions"][1]["amount"] = "0.01"
    manifest = prepare(rows, tmp_path / "bad")
    packet = run(manifest, tmp_path / "bad", "invoices", "INV_1")
    assert [
        c.calculation_id
        for c in packet.calculations
        if c.calculation_id.startswith("debit_minus")
    ] == ["debit_minus_payment:PAY_2"]


# --- fee journal in another currency (review item 18) --------------------------


def usd_fee_journal(rows: Rows) -> None:
    for r in rows["gl_entries"]:
        if r["transaction_type"] == "bank_fee":
            r["currency"] = "USD"


@pytest.mark.parametrize(
    ("scenario", "table", "record"),
    [
        ("net_fee_accounting", "payments", "PAY_1"),
        ("accounted_bank_fee", "bank_transactions", "BT_2"),
    ],
)
def test_a_fee_journal_in_another_currency_abstains_as_fx(
    scenario: str, table: str, record: str, tmp_path: Path
) -> None:
    rows = load(scenario)
    usd_fee_journal(rows)
    manifest = prepare(rows, tmp_path)
    assert outcome(run(manifest, tmp_path, table, record)) == (
        "abstained",
        None,
        "unsupported_fx",
    )


# --- operational manifests must carry their declarations (review 2, D) ---------


def edit_manifest(tmp_path: Path, change: Any) -> None:
    path = tmp_path / "snap" / "manifest.json"
    doc = json.loads(path.read_text())
    change(doc)
    path.chmod(0o644)
    path.write_text(json.dumps(doc))


def f12_with(tmp_path: Path, change: Any) -> EvidencePacket:
    manifest = prepare(load("f12_payment_missing_from_bank"), tmp_path)
    edit_manifest(tmp_path, change)
    return run(manifest, tmp_path, "payments", "PAY_1")


def test_a_legacy_operational_manifest_must_be_prepared_again(tmp_path: Path) -> None:
    # operational-1 manifests could hold an inferred fee flag, no coverage start
    # and no account declaration, and so reproduce the false F12 and F14 answers.
    def legacy(doc: dict[str, Any]) -> None:
        doc["adapter_version"] = "operational-1"
        del doc["bank_data_from"], doc["all_paying_accounts_covered"]

    packet = f12_with(tmp_path, legacy)
    assert outcome(packet) == ("failed", None, "snapshot_manifest_unreadable")
    detail = packet.limitations[0]
    assert "operational-1" in detail and "prepare the data again" in detail


@pytest.mark.parametrize(
    "key",
    [
        "cutoff",
        "bank_data_from",
        "bank_data_through",
        "all_paying_accounts_covered",
        "fee_journals_recognised",
        "adapter_version",
    ],
)
def test_an_operational_manifest_missing_a_declaration_fails(
    key: str, tmp_path: Path
) -> None:
    packet = f12_with(tmp_path, lambda doc: doc.pop(key))
    if key == "adapter_version":  # then it is not identified as operational
        assert outcome(packet) == ("answered", "F12", None)
        return
    assert outcome(packet) == ("failed", None, "snapshot_manifest_unreadable")
    assert key in packet.limitations[0]
    assert "prepare the data again" in packet.limitations[0]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("all_paying_accounts_covered", False),
        ("all_paying_accounts_covered", "true"),
        ("fee_journals_recognised", "yes"),
        ("bank_data_from", "2026-13-01"),
    ],
)
def test_an_operational_manifest_with_a_bad_declaration_fails(
    key: str, value: object, tmp_path: Path
) -> None:
    packet = f12_with(tmp_path, lambda doc: doc.__setitem__(key, value))
    assert outcome(packet) == ("failed", None, "snapshot_manifest_unreadable")
    assert key in packet.limitations[0]


def test_a_cutoff_beyond_the_declared_bank_data_fails(tmp_path: Path) -> None:
    packet = f12_with(
        tmp_path, lambda doc: doc.__setitem__("bank_data_through", "2026-06-29")
    )
    assert outcome(packet) == ("failed", None, "snapshot_manifest_unreadable")
    assert "cutoff 2026-06-30" in packet.limitations[0]
    assert "bank_data_through 2026-06-29" in packet.limitations[0]


def test_the_operational_version_is_defined_where_inference_can_read_it() -> None:
    from reconcile.adapters.operational import ADAPTER_VERSION
    from reconcile.diagnosis.policy import OPERATIONAL_ADAPTER_VERSION

    assert ADAPTER_VERSION == OPERATIONAL_ADAPTER_VERSION == "operational-2"


# --- F06 and the payment's own amount (review 2, A) ----------------------------


def one_payment_allocated_twice_over(rows: Rows) -> None:
    """Invoice 1000.00; one payment of 1000.00 with a 1000.00 debit, but allocated
    2000.00 to the invoice. Only 1000.00 left the bank."""
    rows["payments"] = rows["payments"][:1]
    rows["bank_transactions"] = rows["bank_transactions"][:1]
    rows["payment_allocations"] = [
        {**rows["payment_allocations"][0], "allocated_amount": "2000.00"}
    ]
    rows["gl_entries"] = [
        r for r in rows["gl_entries"] if r["source_transaction_id"] == "PAY_1"
    ]


def test_f06_does_not_count_an_allocation_larger_than_its_payment(
    tmp_path: Path,
) -> None:
    rows = load("f06_double_payment")
    one_payment_allocated_twice_over(rows)
    manifest = prepare(rows, tmp_path)
    packet = run(manifest, tmp_path, "invoices", "INV_1")
    assert outcome(packet) == ("abstained", None, "payment_over_allocated")
    calc = next(
        c for c in packet.calculations if c.calculation_id.startswith("payment_minus")
    )
    assert str(calc.result) == "-1000.00 EUR"


def test_f06_does_not_count_money_the_payment_spreads_over_other_invoices(
    tmp_path: Path,
) -> None:
    # PAY_2 (1000.00) is allocated 1000.00 here and 1000.00 to another invoice.
    rows = load("f06_double_payment")
    other = {**rows["invoices"][0], "invoice_id": "INV_2"}
    rows["invoices"].append(other)
    rows["payment_allocations"].append(
        {**rows["payment_allocations"][1], "invoice_id": "INV_2"}
    )
    manifest = prepare(rows, tmp_path)
    packet = run(manifest, tmp_path, "invoices", "INV_1")
    assert outcome(packet) == ("abstained", None, "payment_over_allocated")


def test_f06_is_unchanged_when_each_payment_covers_its_allocations(
    tmp_path: Path,
) -> None:
    manifest = prepare(load("f06_double_payment"), tmp_path)
    packet = run(manifest, tmp_path, "invoices", "INV_1")
    assert outcome(packet) == ("answered", "F06", None)
    assert not any(
        c.calculation_id.startswith("payment_minus") for c in packet.calculations
    )


# --- wording against constructed cases and serialisation (review 2 test gaps) ---


def nearby_debit_rows() -> Rows:
    """Payment 100 with unbanked payments 30 and 50 to the same account, and a
    debit of 80 with a truncated reference: 80 equals 30 + 50, which excludes the
    primary payment, so the search (combinations that include it) finds nothing."""
    rows = load("real_truncated_reference_netted_fee")
    first = rows["payments"][0]
    first["payment_amount"] = "100.00"
    rows["invoices"][0]["invoice_total"] = "100.00"
    rows["payment_allocations"][0]["allocated_amount"] = "100.00"
    for r in rows["gl_entries"]:
        r["debit" if r["debit"] != "0.00" else "credit"] = "100.00"
    for n, amount in ((2, "30.00"), (3, "50.00")):
        rows["payments"].append(
            {**first, "payment_id": f"PAY_{n}", "reference_number": f"OTHER-{n}",
             "payment_amount": amount}
        )  # fmt: skip
    rows["bank_transactions"][0]["amount"] = "80.00"
    return rows


def test_a_nearby_debit_equal_to_other_payments_is_described_as_bounded(
    tmp_path: Path,
) -> None:
    manifest = prepare(nearby_debit_rows(), tmp_path)
    packet = run(manifest, tmp_path, "payments", "PAY_1")
    assert outcome(packet) == ("abstained", None, "reference_unmatched_nearby_debit")
    text = " ".join(explain(packet))
    assert "this payment together with some of the other unbanked payments" in text
    assert "among the combinations this tool tests" in text
    assert "equals no combination of payments" not in text


def test_a_fee_journal_balanced_as_a_whole_with_an_invalid_attribution(
    tmp_path: Path,
) -> None:
    # The whole journal balances (20 = 20); the lines sourced to the bank row do
    # not (only the debit is), so "not balanced" alone would misdescribe it.
    rows = load("net_fee_accounting")
    credit = next(
        r
        for r in rows["gl_entries"]
        if r["transaction_type"] == "bank_fee" and r["credit"] != "0.00"
    )
    credit["source_transaction_id"] = "BT_OTHER"
    debit_total = sum(
        Decimal(r["debit"] or "0")
        for r in rows["gl_entries"]
        if r["journal_id"] == credit["journal_id"]
    )
    credit_total = sum(
        Decimal(r["credit"] or "0")
        for r in rows["gl_entries"]
        if r["journal_id"] == credit["journal_id"]
    )
    assert debit_total == credit_total  # balanced as a whole
    manifest = prepare(rows, tmp_path)
    packet = run(manifest, tmp_path, "payments", "PAY_1")
    assert outcome(packet) == ("abstained", None, "fee_journal_invalid")
    text = " ".join(explain(packet))
    assert "lines attributed to this bank row" in text
    assert "not balanced or mixes currencies" not in text


def test_every_packet_the_pipeline_post_processes_survives_a_json_round_trip(
    tmp_path: Path,
) -> None:
    made: dict[str, EvidencePacket] = {}
    typed_off = prepare(
        load("f14_amount_shortfall"), tmp_path / "a", fee_journals_typed=False
    )
    made["fee_journals_not_recognised"] = run(
        typed_off, tmp_path / "a", "payments", "PAY_1"
    )
    late = prepare(
        load("f12_payment_missing_from_bank"), tmp_path / "b", start=date(2026, 5, 5)
    )
    made["payment_before_bank_coverage"] = run(
        late, tmp_path / "b", "payments", "PAY_1"
    )
    made["cutoff_differs_from_snapshot"] = run(
        late, tmp_path / "b", "payments", "PAY_1", cutoff=date(2026, 7, 1)
    )
    rows = load("payment_reversal")
    for r in rows["invoices"]:
        r["status"] = ""
    nulled = prepare(rows, tmp_path / "c")
    made["required_field_missing"] = run(nulled, tmp_path / "c", "payments", "PAY_1")
    rows = load("f12_payment_missing_from_bank")
    rows["payments"][0]["bank_account_id"] = ""
    nocp = prepare(rows, tmp_path / "d")
    made["counterparty_unavailable"] = run(nocp, tmp_path / "d", "payments", "PAY_1")
    for reason, packet in made.items():
        assert packet.diagnosis.reason == reason, reason
        assert EvidencePacket.from_json(packet.to_json()) == packet, reason


# --- a bank reversal, credit or return contradicts settlement (review 3) ---------

CONTRADICTIONS = {
    "reversal_credit": {"transaction_type": "payment reversal", "direction": "credit"},
    "plain_credit": {"transaction_type": "ACH credit", "direction": "credit"},
    "returned_debit": {"transaction_type": "ACH debit", "status": "returned"},
}


def with_contradiction(rows: Rows, ref: str, kind: str, amount: str) -> None:
    row: dict[str, str | None] = {
        **rows["bank_transactions"][0],
        "bank_transaction_id": "BT_X",
        "payment_reference": ref,
        "posted_date": "2026-06-20",
        "transaction_date": "2026-06-20",
        "amount": amount,
        **CONTRADICTIONS[kind],
    }
    rows["bank_transactions"].append(row)


# scenario, table, record, reference of the payment to contradict, amount
PATHS = [
    ("f06_double_payment", "invoices", "INV_1", "REF_2", "1000.00"),
    ("split_payment", "invoices", "INV_1", "REF_2", "800.00"),
    ("due_date_payment", "invoices", "INV_1", "REF_1", "1500.00"),
    ("weekend_clearing", "payments", "PAY_1", "REF_1", "800.00"),
    ("check_clearing", "payments", "PAY_1", "REF_1", "1200.00"),
    ("net_fee_accounting", "payments", "PAY_1", "REF_1", "980.00"),
    ("accounted_bank_fee", "payments", "PAY_1", "REF_1", "1000.00"),
]


@pytest.mark.parametrize("kind", sorted(CONTRADICTIONS))
@pytest.mark.parametrize(("scenario", "table", "record", "ref", "amount"), PATHS)
def test_a_reversed_or_returned_payment_is_never_counted_as_settled(
    scenario: str,
    table: str,
    record: str,
    ref: str,
    amount: str,
    kind: str,
    tmp_path: Path,
) -> None:
    rows = load(scenario)
    with_contradiction(rows, ref, kind, amount)
    manifest = prepare(rows, tmp_path)
    packet = run(manifest, tmp_path, table, record)
    assert outcome(packet) == ("abstained", None, "settlement_contradicted_by_bank")
    assert packet.diagnosis.missing_information == (
        "whether this payment's bank debit was reversed or returned",
    )
    assert any(e.evidence_id == "bank:BT_X" for e in packet.evidence)
    assert packet.diagnosis.findings == () or all(
        f.check_id != "committed_overpayment" for f in packet.diagnosis.findings
    )


def test_early_and_failure_exits_survive_a_json_round_trip(tmp_path: Path) -> None:
    from reconcile.retrieval.snapshot import Limits

    manifest = prepare(load("f06_double_payment"), tmp_path)
    snap = tmp_path / "snap" / "snapshot.sqlite"
    digest = manifest["snapshot"]["content_sha256"]

    def ask(record: str, snapshot: str = digest, limits: Limits | None = None) -> Any:
        request = InvestigationRequest(
            "r", snapshot, RecordRef(snapshot, "payments", record), "q", CUTOFF, "p"
        )
        return (
            investigate(snap, request, limits) if limits else investigate(snap, request)
        )

    made = {
        "primary_entity_not_in_snapshot": ask("NOPE"),
        "snapshot_mismatch": ask("PAY_1", snapshot="0" * 64),
    }
    made["retrieval limit"] = ask("PAY_1", limits=Limits(max_rows=1))
    snap.chmod(0o644)
    con = sqlite3.connect(snap)
    con.execute("INSERT INTO payments SELECT * FROM payments WHERE payment_id='PAY_1'")
    con.commit()
    con.close()
    made["malformed_record"] = ask("PAY_1")
    snap.write_bytes(b"not a database")
    made["snapshot_unreadable"] = ask("PAY_1")
    manifest_file = tmp_path / "snap" / "manifest.json"
    manifest_file.chmod(0o644)
    manifest_file.unlink()
    made["snapshot_manifest_unreadable"] = ask("PAY_1")
    for name, packet in made.items():
        assert EvidencePacket.from_json(packet.to_json()) == packet, name
    assert {p.diagnosis.reason for p in made.values()} >= {
        "primary_entity_not_in_snapshot",
        "snapshot_mismatch",
        "malformed_record",
        "snapshot_unreadable",
        "snapshot_manifest_unreadable",
    }
    assert made["retrieval limit"].diagnosis.disposition.value == "abstained"


@pytest.mark.parametrize(
    ("scenario", "total", "off_by_one_cent", "off_by_two_cents", "category"),
    [
        ("split_payment", "2000.00", "2000.01", "2000.02", "legitimate_split_payment"),
        (
            "due_date_payment",
            "1500.00",
            "1500.01",
            "1500.02",
            "legitimate_due_date_payment",
        ),
    ],
)
def test_the_tolerance_is_one_minor_unit_for_split_and_due_date(
    scenario: str,
    total: str,
    off_by_one_cent: str,
    off_by_two_cents: str,
    category: str,
    tmp_path: Path,
) -> None:
    # "Equal within the stated tolerance": allocations of 99.99 against an invoice
    # of 100.00 pass, two cents out do not.
    def answer(invoice_total: str, name: str) -> Any:
        rows = load(scenario)
        rows["invoices"][0]["invoice_total"] = invoice_total
        manifest = prepare(rows, tmp_path / name)
        return run(manifest, tmp_path / name, "invoices", "INV_1")

    assert outcome(answer(total, "a")) == ("answered", category, None)
    packet = answer(off_by_one_cent, "b")
    assert outcome(packet) == ("answered", category, None)
    assert "within the stated tolerance" in " ".join(explain(packet))
    assert outcome(answer(off_by_two_cents, "c"))[1] != category
