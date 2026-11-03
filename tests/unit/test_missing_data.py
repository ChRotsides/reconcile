"""Values a real export may lack but FinRCA always has. A missing value must end
in an explicit non-answer, never in a verdict that depends on a search that
could not run (evidence contract, note of 2026-11)."""

import json
import stat
from pathlib import Path
from typing import Any

import pytest
from test_operational_adapter import (
    CUTOFF,
    EXPECTED,
    Rows,
    load,
    outcome,
    prepare,
    primary,
    run,
)

from reconcile.adapters.finrca import TABLES
from reconcile.adapters.operational import optional_columns
from reconcile.diagnosis.pipeline import MANIFEST_FILE, investigate
from reconcile.domain.contracts import InvestigationRequest, RecordRef

SCOPE = "These tables were not supplied with this data"


def with_null(rows: Rows, table: str, column: str) -> None:
    for row in rows[table]:
        row[column] = None


def unreferenced_null_debit(rows: Rows, currency: str) -> None:
    template = dict.fromkeys(c.name for c in TABLES["bank_transactions"].columns)
    template.update(
        bank_transaction_id="BT-NULL-CP",
        posted_date="2026-06-01",
        transaction_date="2026-06-01",
        transaction_type="ach debit",
        amount="1.00",
        currency=currency,
        direction="debit",
        status="posted",
    )
    rows["bank_transactions"].append(template)


def result(scenario: str, rows: Rows, tmp_path: Path) -> tuple[Any, ...]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return outcome(run(prepare(rows, tmp_path), tmp_path, *primary(scenario)))


def test_null_counterparty_on_batch_debit_abstains_instead_of_answering_f12(
    tmp_path: Path,
) -> None:
    scenario = "real_bank_batch_debit"
    rows = load(scenario)
    with_null(rows, "bank_transactions", "counterparty_token")
    assert result(scenario, rows, tmp_path) == (
        "abstained",
        None,
        "counterparty_unavailable",
    )


def test_f12_with_an_unmatched_debit_lacking_a_counterparty_abstains(
    tmp_path: Path,
) -> None:
    scenario = "f12_payment_missing_from_bank"
    rows = load(scenario)
    unreferenced_null_debit(rows, rows["payments"][0]["payment_currency"] or "")
    assert result(scenario, rows, tmp_path) == (
        "abstained",
        None,
        "counterparty_unavailable",
    )


def test_f12_with_no_payment_account_abstains(tmp_path: Path) -> None:
    scenario = "f12_payment_missing_from_bank"
    rows = load(scenario)
    with_null(rows, "payments", "bank_account_id")
    assert result(scenario, rows, tmp_path) == (
        "abstained",
        None,
        "counterparty_unavailable",
    )


def test_f12_still_answers_when_every_counterparty_is_known(tmp_path: Path) -> None:
    scenario = "f12_payment_missing_from_bank"
    assert result(scenario, load(scenario), tmp_path) == ("answered", "F12", None)


@pytest.mark.parametrize("how", ["debit", "account"])
def test_pending_settlement_is_not_an_all_clear_without_the_search(
    how: str, tmp_path: Path
) -> None:
    scenario = "pending_settlement"
    rows = load(scenario)
    if how == "debit":
        unreferenced_null_debit(rows, rows["payments"][0]["payment_currency"] or "")
    else:
        with_null(rows, "payments", "bank_account_id")
    assert result(scenario, rows, tmp_path) == (
        "abstained",
        None,
        "counterparty_unavailable",
    )


def test_reference_matched_double_payment_does_not_need_counterparties(
    tmp_path: Path,
) -> None:
    scenario = "f06_double_payment"
    rows = load(scenario)
    with_null(rows, "bank_transactions", "counterparty_token")
    with_null(rows, "payments", "bank_account_id")
    assert result(scenario, rows, tmp_path) == ("answered", "F06", None)


def test_payment_without_reference_abstains_and_does_not_fail(tmp_path: Path) -> None:
    scenario = "f12_payment_missing_from_bank"
    rows = load(scenario)
    with_null(rows, "payments", "reference_number")
    packet = run(prepare(rows, tmp_path), tmp_path, *primary(scenario))
    assert outcome(packet) == ("abstained", None, "required_field_missing")
    assert "reference_number" in packet.diagnosis.missing_information[0]
    assert "reference_number" in packet.limitations[0]


def snapshot_file(tmp_path: Path) -> Path:
    return tmp_path / "snap" / "snapshot.sqlite"


def test_manifest_coverage_notes_reach_the_packet(tmp_path: Path) -> None:
    scenario = "f06_double_payment"
    rows = load(scenario, drop=("approval_events",))
    manifest = prepare(rows, tmp_path)
    assert manifest["tables_not_supplied"] == ["approval_events"]
    packet = run(manifest, tmp_path, *primary(scenario))
    assert (
        f"{SCOPE}, so a search of them finding nothing says nothing about them: "
        "approval_events." in packet.limitations
    )
    assert (
        "Bank data was declared complete through 2026-07-31 by whoever prepared "
        "the data."
    ) in packet.limitations


def test_notes_are_added_to_a_packet_whichever_path_produced_it(
    tmp_path: Path,
) -> None:
    rows = load("f12_payment_missing_from_bank", drop=("approval_events",))
    manifest = prepare(rows, tmp_path)
    digest = manifest["snapshot"]["content_sha256"]
    request = InvestigationRequest(
        "r", digest, RecordRef(digest, "payments", "NOPE"), "check", CUTOFF, "p"
    )
    packet = investigate(snapshot_file(tmp_path), request)
    assert packet.diagnosis.reason == "primary_entity_not_in_snapshot"
    assert any(x.startswith(SCOPE) for x in packet.limitations)


def test_a_finrca_shaped_manifest_adds_no_note(tmp_path: Path) -> None:
    scenario = "f06_double_payment"
    manifest = prepare(load(scenario), tmp_path)
    assert manifest["tables_not_supplied"] == []
    path = tmp_path / "snap" / MANIFEST_FILE
    bare = json.loads(path.read_text())
    del bare["tables_not_supplied"], bare["bank_data_through"]
    path.chmod(0o644)
    path.write_text(json.dumps(bare))
    packet = run(manifest, tmp_path, *primary(scenario))
    assert not any(
        "not supplied" in x or "declared complete" in x for x in packet.limitations
    )
    # With the keys present only the bank-date note appears (nothing unsupplied).
    path.write_text(json.dumps({**bare, "bank_data_through": "2026-07-31"}))
    packet = run(manifest, tmp_path, *primary(scenario))
    assert not any("not supplied" in x for x in packet.limitations)
    assert any("declared complete" in x for x in packet.limitations)


def test_snapshot_directory_is_private(tmp_path: Path) -> None:
    prepare(load("f06_double_payment"), tmp_path)
    assert stat.S_IMODE((tmp_path / "snap").stat().st_mode) == 0o700


_BASELINES: dict[str, tuple[Any, ...]] = {}


def baseline(scenario: str, factory: pytest.TempPathFactory) -> tuple[Any, ...]:
    if scenario not in _BASELINES:
        _BASELINES[scenario] = result(scenario, load(scenario), factory.mktemp("b"))
    return _BASELINES[scenario]


def operational_optional() -> list[tuple[str, str]]:
    cols = []
    for table in load("f06_double_payment"):
        cols += [(table, c) for c in sorted(optional_columns(table))]
    return cols


@pytest.mark.parametrize(("table", "column"), operational_optional())
def test_nulling_an_optional_column_never_creates_or_changes_an_answer(
    table: str, column: str, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """For every public fixture: unchanged, or a non-answered packet. A NULL that
    makes a non-answered case answered, or changes an answered category, fails."""
    bad = []
    for scenario in sorted(EXPECTED):
        base = load(scenario)
        if not base[table]:
            continue
        before = baseline(scenario, tmp_path_factory)
        nulled = load(scenario)
        with_null(nulled, table, column)
        after = result(scenario, nulled, tmp_path / "b" / scenario)
        if after != before and after[0] == "answered":
            bad.append((scenario, before, after))
    assert not bad
