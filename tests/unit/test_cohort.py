"""The shared cohort module (evaluation/cohort.py): groups, category mapping,
the label-conflict rule, and that both scoring scripts use it."""

import ast
import importlib
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from reconcile.adapters.finrca import _load_key, _Tokens, create_key_file

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
cohort = importlib.import_module("cohort")

SCORERS = [
    ROOT / "evaluation" / "dev_run.py",
    ROOT / "evaluation" / "baselines" / "observable" / "run.py",
]
SHARED_NAMES = {
    "FAMILY_KEYS",
    "VARIANT_CATEGORY",
    "CONTRADICTED",
    "NOT_GRADABLE",
    "CHECKED_FAMILIES",
    "group_of",
    "label_conflicts",
    "label_candidates",
    "conflict_report",
}


def defined_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


@pytest.mark.parametrize("path", SCORERS, ids=lambda p: p.name)
def test_scorers_import_the_cohort_instead_of_defining_it(path: Path) -> None:
    assert not defined_names(path) & SHARED_NAMES
    imported = {
        a.name
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Import)
        for a in node.names
    }
    assert "cohort" in imported


def test_groups() -> None:
    g = cohort.group_of
    assert g("F12_ERP_PAYMENT_MISSING_FROM_BANK", "x", "RCA_1") == "cohort"
    assert g("NO_FAILURE", "legitimate_batch_payment", "RCA_1") == "cohort"
    assert g("NO_FAILURE", "legitimate_batch_payment", "RCA_1", {"RCA_1"}) == (
        "label_conflict"
    )
    # a verified conflict is in the headline view, and only there and in the
    # sensitivity view's complement
    assert "label_conflict" in cohort.COHORT_VIEWS["all_cases"]
    assert (
        "label_conflict"
        not in cohort.COHORT_VIEWS["sensitivity_without_verified_conflicts"]
    )
    assert g("NO_FAILURE", "legitimate_fx_settlement", "RCA_1", {"RCA_1"}) == "fx"
    assert g("NO_FAILURE", "legitimate_split_payment", "RCA_001631") == "contradicted"
    assert g("F06_INVOICE_PAID_TWICE", "x", "RCA_000588") == "not_gradable"
    assert g("F01_DUPLICATE_INVOICE", "x", "RCA_1") == "oos_failure"
    assert g("NO_FAILURE", "legitimate_exact_po_match", "RCA_1") == "oos_legit"


def test_views_and_category_mapping() -> None:
    assert list(cohort.COHORT_VIEWS)[0] == "all_cases"  # the headline comes first
    assert cohort.COHORT_VIEWS["all_cases"] == ("cohort", "label_conflict")
    assert cohort.COHORT_VIEWS["sensitivity_without_verified_conflicts"] == ("cohort",)
    assert cohort.expected_category("F14_BANK_ERP_AMOUNT_MISMATCH", "x") == "F14"
    assert (
        cohort.expected_category("NO_FAILURE", "legitimate_weekend_clearing")
        == "legitimate_weekend_clearing"
    )
    assert cohort.expected_category("NO_FAILURE", "legitimate_fx_settlement") is None
    assert cohort.is_no_failure("no_exception") and cohort.is_failure("F12")


# ------------------------------------------------------- label-conflict rule


def label(
    failure: str, primary: str, evidence: list[str], affected: list[str]
) -> dict[str, Any]:
    return {
        "failure_type": failure,
        "primary_entity": {"id": primary},
        "evidence_ids": evidence,
        "affected_entities": [{"id": a} for a in affected],
    }


def conflicts_of(
    tmp_path: Path,
    labels: dict[str, dict[str, Any]],
    allocations: list[tuple[str, str]],
) -> Any:
    key = tmp_path / "key"
    create_key_file(key)
    tok = _Tokens(_load_key(key))

    def t(raw: str) -> str:
        return tok.token("payment" if raw.startswith("PAY_") else "invoice", raw)

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE payment_allocations (payment_id TEXT, invoice_id TEXT)")
    con.executemany(
        "INSERT INTO payment_allocations VALUES (?, ?)",
        [(t(p), t(i)) for p, i in allocations],
    )
    return cohort.label_candidates(labels, con, key)


ALLOC = [("PAY_A", "INV_1"), ("PAY_B", "INV_1")]
LEGIT_B = label("NO_FAILURE", "PAY_B", ["PAY_B"], ["PAY_B"])


def test_defect_on_a_record_of_the_legitimate_case_is_a_conflict_candidate(
    tmp_path: Path,
) -> None:
    labels = {
        "L": label("NO_FAILURE", "INV_1", ["INV_1", "PAY_A", "PAY_B"], ["INV_1"]),
        "D": label("F09_GL_POSTING_MISMATCH", "PAY_A", ["PAY_A"], ["PAY_A"]),
    }
    candidates, review = conflicts_of(tmp_path, labels, ALLOC)
    assert [x["failure_case"] for x in candidates["L"]] == ["D"]
    assert candidates["L"][0]["shared"] == ["PAY_A"]
    assert review == {}


def test_defect_reached_only_through_allocation_of_a_named_payment(
    tmp_path: Path,
) -> None:
    """The F09 payment is allocated to the invoice the legitimate case names
    (the due-date shape): the legitimate case's records include it."""
    labels = {
        "L": label("NO_FAILURE", "INV_1", ["INV_1", "PAY_B"], ["INV_1"]),
        "D": label("F09_GL_POSTING_MISMATCH", "PAY_A", ["PAY_A"], ["PAY_A"]),
    }
    assert "L" in conflicts_of(tmp_path, labels, ALLOC)[0]


def test_failure_that_only_lists_the_shared_invoice_is_a_review_candidate(
    tmp_path: Path,
) -> None:
    """The failure case's defect is payment A; the invoice is only an affected
    entity, and legitimate payment B (allocated to it) is a separate payment."""
    labels = {
        "L": LEGIT_B,
        "D": label(
            "F14_BANK_ERP_AMOUNT_MISMATCH", "PAY_A", ["PAY_A"], ["PAY_A", "INV_1"]
        ),
    }
    candidates, review = conflicts_of(tmp_path, labels, ALLOC)
    assert candidates == {}
    assert [x["failure_case"] for x in review["L"]] == ["D"]
    assert review["L"][0]["shared"] == ["INV_1", "PAY_B"]


def test_overlap_reached_through_allocations_on_both_sides_is_a_review_candidate(
    tmp_path: Path,
) -> None:
    """C names only payment B, D names only payment A, both allocated to invoice
    I: neither names the shared record, and the defect (A) is not in C's
    records. The overlap is reported, not dropped."""
    labels = {
        "L": label("NO_FAILURE", "PAY_B", ["PAY_B"], []),
        "D": label("F14_BANK_ERP_AMOUNT_MISMATCH", "PAY_A", ["PAY_A"], []),
        "O": label("F01_DUPLICATE_INVOICE", "INV_1", [], []),  # only gives INV_1 a name
    }
    candidates, review = conflicts_of(tmp_path, labels, ALLOC)
    assert candidates == {}
    assert [x["failure_case"] for x in review["L"]] == ["D"]
    assert review["L"][0]["shared"] == ["INV_1"]


def test_an_evidence_named_record_is_only_a_candidate_never_an_exclusion(
    tmp_path: Path,
) -> None:
    """Finding 4 of review round 2: moving the shared invoice into the failure
    case's evidence IDs makes the pair a candidate. The rule never excludes: the
    group function moves a case only when it is in the verified set."""
    labels = {
        "L": LEGIT_B,
        "D": label("F14_BANK_ERP_AMOUNT_MISMATCH", "PAY_A", ["PAY_A", "INV_1"], []),
    }
    candidates, _ = conflicts_of(tmp_path, labels, ALLOC)
    assert "L" in candidates
    assert cohort.group_of("NO_FAILURE", "legitimate_pending_settlement", "L") == (
        "cohort"
    )


def test_unchecked_family_is_neither(tmp_path: Path) -> None:
    labels = {
        "L": label("NO_FAILURE", "INV_1", ["INV_1", "PAY_A"], ["INV_1"]),
        "D": label("F10_WRONG_PERIOD", "PAY_A", ["PAY_A"], ["PAY_A"]),
    }
    candidates, review = conflicts_of(tmp_path, labels, ALLOC)
    assert candidates == {} and review == {}


def test_two_failure_cases_never_conflict_with_each_other(tmp_path: Path) -> None:
    labels = {
        "D1": label("F09_GL_POSTING_MISMATCH", "PAY_A", ["PAY_A"], ["PAY_A"]),
        "D2": label("F14_BANK_ERP_AMOUNT_MISMATCH", "PAY_A", ["PAY_A"], ["PAY_A"]),
    }
    candidates, review = conflicts_of(tmp_path, labels, ALLOC)
    assert candidates == {} and review == {}


# ------------------------------------------------------- verified conflicts


def test_verified_file_lists_the_five_parent_verified_pairs() -> None:
    v = cohort.verified_conflicts()
    pairs = {(c, e["failure_case"]) for c, es in v.items() for e in es}
    assert pairs == {
        ("RCA_001679", "RCA_000857"),
        ("RCA_001759", "RCA_000874"),
        ("RCA_002175", "RCA_000889"),
        ("RCA_002212", "RCA_000832"),
        ("RCA_002182", "RCA_000722"),
    }
    for es in v.values():
        for e in es:
            assert e["shared"] and e["defect"]


def test_a_verified_pair_that_is_not_a_candidate_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "key"
    create_key_file(key)
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE payment_allocations (payment_id TEXT, invoice_id TEXT)")
    labels = {"RCA_001679": LEGIT_B, "RCA_000857": label("F09_X", "PAY_Z", [], [])}
    with pytest.raises(ValueError, match="verified conflict RCA_001679"):
        cohort.conflict_report(labels, con, key)
