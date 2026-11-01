"""The public scenario fixtures prepare cleanly and show the gaps they are built for."""

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reconcile.adapters.finrca import (
    TABLES,
    create_key_file,
    prepare_finrca_snapshot,
    quality_report,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "public"
EXPECTED_FILE = "expected.json"
CUTOFF = date(2026, 6, 30)
SCENARIOS = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
EXPECTED: dict[str, Any] = json.loads((FIXTURES / EXPECTED_FILE).read_text())


@pytest.fixture(scope="module")
def key_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("key") / "key"
    create_key_file(path)
    return path


@pytest.fixture(scope="module")
def prepared(
    key_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    root = tmp_path_factory.mktemp("prepared")
    out: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for name in SCENARIOS:
        manifest = prepare_finrca_snapshot(
            FIXTURES / name,
            root / name,
            key_path=key_path,
            source_revision=f"fixture:{name}",
            cutoff=CUTOFF,
        )
        out[name] = (
            manifest,
            quality_report(root / name / manifest["snapshot"]["file"]),
        )
    return out


def test_scenarios_exist() -> None:
    assert SCENARIOS


def test_expected_covers_exactly_the_scenarios() -> None:
    assert sorted(EXPECTED) == SCENARIOS


@pytest.mark.parametrize("name", SCENARIOS)
def test_no_expected_outcome_in_scenario_directory(name: str) -> None:
    files = sorted(p.name for p in (FIXTURES / name).iterdir())
    assert files == sorted(f"{t}.csv" for t in TABLES)


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario_prepares(
    name: str, prepared: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> None:
    manifest, _ = prepared[name]
    assert manifest["cutoff"] == CUTOFF.isoformat()
    for table in manifest["tables"].values():
        assert table["exact_duplicates_removed"] == 0


@pytest.mark.parametrize("name", SCENARIOS)
def test_quality_gaps_are_exactly_the_designed_ones(
    name: str, prepared: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> None:
    _, quality = prepared[name]
    found = {k: v["count"] for k, v in quality["gaps"].items() if v["count"]}
    assert found == EXPECTED[name]["quality_gaps"]


@pytest.mark.parametrize("name", SCENARIOS)
def test_cutoff_drops_and_row_counts(
    name: str, prepared: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> None:
    manifest, quality = prepared[name]
    dropped = {
        t: v["dropped_by_cutoff"]
        for t, v in manifest["tables"].items()
        if v["dropped_by_cutoff"]
    }
    assert dropped == EXPECTED[name]["dropped_by_cutoff"]
    for table, count in EXPECTED[name]["snapshot_rows"].items():
        assert quality["row_counts"][table] == count


@pytest.mark.parametrize("name", SCENARIOS)
def test_expected_entry_is_well_formed(name: str) -> None:
    e = EXPECTED[name]
    assert e["disposition"] in {"answered", "abstained", "out_of_scope"}
    if e["disposition"] == "answered":
        assert e["category"]
    else:
        assert e["category"] is None
    if e["disposition"] == "abstained":
        assert e["abstention_reason"]
    assert e["primary_entity"]["table"] in TABLES
    for item in e["evidence"]:
        assert item["table"] in TABLES
        assert item["id"]


ID_COLUMNS = {
    "payment_allocations": ("payment_id", "invoice_id"),
    "gl_entries": ("journal_id",),
}


def _raw_ids(name: str, table: str) -> set[str]:
    columns = ID_COLUMNS.get(table, (f"{table[:-1]}_id",))
    with (FIXTURES / name / f"{table}.csv").open(newline="") as f:
        return {"|".join(row[c] for c in columns) for row in csv.DictReader(f)}


@pytest.mark.parametrize("name", SCENARIOS)
def test_cited_ids_exist_in_the_raw_fixture(name: str) -> None:
    e = EXPECTED[name]
    for item in [e["primary_entity"], *e["evidence"]]:
        assert item["id"] in _raw_ids(name, item["table"]), item


VOCABULARY = {
    "payments": (
        "payment_method",
        {"ACH", "check", "virtual_card", "wire"},
    ),
    "invoices": (
        "status",
        {"approved", "canceled", "credit_applied", "paid", "received"},
    ),
    "bank_transactions": (
        "transaction_type",
        {
            "ACH debit",
            "bank fee",
            "card settlement",
            "check clearing",
            "payment reversal",
            "wire debit",
        },
    ),
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_column_values_stay_in_finrca_vocabulary(name: str) -> None:
    for table, (column, allowed) in VOCABULARY.items():
        with (FIXTURES / name / f"{table}.csv").open(newline="") as f:
            used = {row[column] for row in csv.DictReader(f)}
        assert used <= allowed, (name, table, used - allowed)
