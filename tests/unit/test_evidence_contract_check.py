"""The evidence-contract checker agrees with the public fixtures' expected outcomes.

The checker (evaluation/evidence_contract_check.py) reads the raw FinRCA full/
layout, which the fixture directories share. Regression tests at the bottom use
small edits of those fixtures.
"""

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "public"
_spec = importlib.util.spec_from_file_location(
    "evidence_contract_check", ROOT / "evaluation" / "evidence_contract_check.py"
)
assert _spec and _spec.loader
ecc = importlib.util.module_from_spec(_spec)
sys.modules["evidence_contract_check"] = ecc
_spec.loader.exec_module(ecc)

EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected.json").read_text())
G, NG, C = ecc.G, ecc.NG, ecc.C

# scenario -> (checker call, entity, status, reason). One row per fixture.
CHECKS: dict[str, tuple[str, str, str, str]] = {
    "accounted_bank_fee": ("bank_fee", "BT_2", G, "fee_journal_equals_fee_row"),
    "batch_payment": ("batch", "PAY_1", G, "allocations_equal_payment_and_bank"),
    "check_clearing": ("check", "PAY_1", G, "within_clearing_window"),
    "due_date_payment": ("due_date", "INV_1", G, "paid_on_due_date_fully_allocated"),
    "f06_double_payment": ("f06", "INV_1", G, "committed_overpayment"),
    "f06_second_debit_post_cutoff": (
        "f06",
        "INV_1",
        NG,
        "overpayment_not_committed_bank_row_post_cutoff",
    ),
    "f12_payment_missing_from_bank": ("f12", "PAY_1", G, "absent_beyond_window"),
    "f14_amount_shortfall": ("f14", "PAY_1", G, "unexplained_shortfall"),
    "fx_settlement": ("fx", "PAY_1", G, "currency_mismatch_abstain"),
    "insufficient_in_window_missing_records": (
        "pending",
        "PAY_1",
        NG,
        "erp_side_incomplete",
    ),
    "insufficient_primary_entity_missing": (
        "bank_fee",
        "BT_1",
        NG,
        "bank_row_post_cutoff",
    ),
    "insufficient_shared_reference": (
        "f12",
        "PAY_1",
        NG,
        "reference_shared_by_payments",
    ),
    "net_fee_accounting": (
        "net_fee",
        "PAY_1",
        G,
        "difference_explained_by_fee_journal",
    ),
    "payment_reversal": ("reversal", "PAY_1", G, "debit_credit_journal_equal"),
    "pending_settlement": ("pending", "PAY_1", G, "absent_inside_window"),
    # Documented contract gap: the checker answers F12 for each batched payment.
    "real_bank_batch_debit": ("f12", "PAY_1", G, "absent_beyond_window"),
    # Not a gap: A3 finds the mis-referenced debit, so F12 is not gradable.
    "real_free_text_reference": (
        "f12",
        "PAY_1",
        NG,
        "unreferenced_bank_candidate",
    ),
    "real_truncated_bank_reference": (
        "f12",
        "PAY_1",
        NG,
        "unreferenced_bank_candidate",
    ),
    # Documented contract gap: A3 needs an equal amount; a netted fee hides it.
    "real_truncated_reference_netted_fee": (
        "f12",
        "PAY_1",
        G,
        "absent_beyond_window",
    ),
    # No exception (contract 5.1): matched, in the window, invoice not over-allocated.
    "real_identical_distinct_payments": (
        "no_exception",
        "PAY_1",
        G,
        "matched_no_exception",
    ),
    "real_netted_fee_no_journal": ("f14", "PAY_1", G, "unexplained_shortfall"),
    "real_partial_payment": ("no_exception", "PAY_1", G, "matched_no_exception"),
    # Documented contract gap: re-payment leaves the invoice paid.
    "real_reversal_then_repayment": ("reversal", "PAY_1", C, "invoice_still_paid"),
    "split_payment": ("split", "INV_1", G, "allocations_equal_invoice"),
    "weekend_clearing": ("clearing", "PAY_1", G, "within_clearing_window"),
}


def run(s: Any, call: str, entity: str) -> Any:
    if call == "f06":
        return ecc.check_f06(s, entity)
    if call == "f12":
        return ecc.check_absence(s, entity, want_old=True)
    if call == "pending":
        return ecc.check_absence(s, entity, want_old=False)
    if call == "f14":
        return ecc.check_f14(s, entity, variant="f14")
    if call == "net_fee":
        return ecc.check_f14(s, entity, variant="net_fee")
    if call == "bank_fee":
        return ecc.check_bank_fee(s, entity)
    if call == "check":
        return ecc.check_clearing(s, entity, "check")
    if call == "clearing":
        return ecc.check_clearing(s, entity, "ACH")
    return getattr(ecc, f"check_{call}")(s, entity)


def test_every_scenario_has_a_check() -> None:
    assert sorted(CHECKS) == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_checker_result_on_fixture(name: str) -> None:
    call, entity, status, reason = CHECKS[name]
    res = run(ecc.load_dir(FIXTURES / name), call, entity)
    assert (res.status, res.reason) == (status, reason)
    assert entity == EXPECTED[name]["primary_entity"]["id"]


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_checker_agrees_with_expected_disposition(name: str) -> None:
    e = EXPECTED[name]
    status = CHECKS[name][2]
    gap = e["known_contract_gap"]
    if gap is not None:
        # The contract and the expected outcome differ, as documented.
        assert gap["current_contract_outcome"]
        assert (e["disposition"], status) in {("abstained", G), ("abstained", C)}
    elif name == "fx_settlement":
        # Detection only: the checker finds the mismatch; the answer is an abstention.
        assert (e["disposition"], status) == ("abstained", G)
    elif e["disposition"] == "answered":
        assert status == G
    elif e["disposition"] == "abstained":
        assert status == NG
    else:  # out_of_scope: no failure predicate may fire
        assert status == C
        s = ecc.load_dir(FIXTURES / name)
        assert ecc.fired(s, set(s.payments) | set(s.invoices)) == set()


@pytest.mark.parametrize(
    "name", ["real_truncated_bank_reference", "real_free_text_reference"]
)
def test_mangled_reference_is_not_gradable_and_not_a_gap(name: str) -> None:
    e = EXPECTED[name]
    assert e["known_contract_gap"] is None
    assert e["abstention_reason"] == "reference_unmatched_candidate_bank_row"
    s = ecc.load_dir(FIXTURES / name)
    res = ecc.check_absence(s, "PAY_1", want_old=True)
    assert (res.status, res.reason) == (NG, "unreferenced_bank_candidate")
    # The window and the missing exact match alone would be F12; only A3 stops it.
    assert [x["count"] for x in e["absence_searches"]] == [0, 1, 1]


def test_truncated_reference_with_netted_fee_is_a_documented_gap() -> None:
    e = EXPECTED["real_truncated_reference_netted_fee"]
    assert e["known_contract_gap"]["gap"]
    assert (e["disposition"], e["abstention_reason"]) == (
        "abstained",
        "reference_unmatched_nearby_debit",
    )
    assert [x["count"] for x in e["absence_searches"]] == [0, 1, 0]
    s = ecc.load_dir(FIXTURES / "real_truncated_reference_netted_fee")
    res = ecc.check_absence(s, "PAY_1", want_old=True)
    assert (res.status, res.reason) == (G, "absent_beyond_window")  # wrong answer


# --- regression tests: hand-edited fixtures --------------------------------


def edited(tmp_path: Path, base: str, *edits: tuple[str, str, str]) -> Any:
    """Copy a fixture, apply (file, old, new) text edits, load it."""
    dest = tmp_path / base
    shutil.copytree(FIXTURES / base, dest)
    for filename, old, new in edits:
        path = dest / filename
        text = path.read_text()
        assert old in text, (filename, old)
        path.write_text(text.replace(old, new))
    return ecc.load_dir(dest)


def test_f06_shared_reference_does_not_count_one_debit_twice(tmp_path: Path) -> None:
    # The exact review scenario: the second debit row is removed.
    s = edited(
        tmp_path,
        "f06_double_payment",
        ("payments.csv", "REF_2", "REF_1"),
        (
            "bank_transactions.csv",
            "BT_2,BA_V1,REF_2,ACC_1,2026-06-10,2026-06-10,"
            "ACH debit,1000.00,EUR,debit,posted\n",
            "",
        ),
    )
    assert ecc.check_f06(s, "INV_1").status == NG


def test_net_fee_journal_in_other_currency_does_not_explain(tmp_path: Path) -> None:
    # Finding 3: a balanced USD 20 fee journal must not explain a EUR 20 shortfall.
    s = edited(
        tmp_path,
        "net_fee_accounting",
        ("gl_entries.csv", ",EUR\n", ",USD\n"),
    )
    assert ecc.check_f14(s, "PAY_1", variant="net_fee").status == NG
    assert ecc.check_f14(s, "PAY_1", variant="f14").status == NG


def test_bank_fee_journal_in_other_currency_not_gradable(tmp_path: Path) -> None:
    s = edited(tmp_path, "accounted_bank_fee", ("gl_entries.csv", ",EUR\n", ",USD\n"))
    assert ecc.check_bank_fee(s, "BT_2").status == NG


def test_split_across_currencies_not_gradable(tmp_path: Path) -> None:
    # Payments and debits in USD against a EUR invoice: amounts are not comparable.
    s = edited(
        tmp_path,
        "split_payment",
        ("payments.csv", ",EUR,", ",USD,"),
        ("bank_transactions.csv", ",EUR,", ",USD,"),
    )
    assert ecc.check_split(s, "INV_1").status == NG


def test_due_date_across_currencies_not_gradable(tmp_path: Path) -> None:
    s = edited(
        tmp_path,
        "due_date_payment",
        ("payments.csv", ",EUR,", ",USD,"),
        ("bank_transactions.csv", ",EUR,", ",USD,"),
    )
    assert ecc.check_due_date(s, "INV_1").status == NG


def test_batch_across_currencies_not_gradable(tmp_path: Path) -> None:
    s = edited(
        tmp_path,
        "batch_payment",
        ("payments.csv", ",EUR,", ",USD,"),
        ("bank_transactions.csv", ",EUR,", ",USD,"),
    )
    assert ecc.check_batch(s, "PAY_1").status == NG


def test_reversal_credit_must_be_posted_credit(tmp_path: Path) -> None:
    # Finding 4: a returned credit, or a debit-direction row, is not a reversal credit.
    returned = edited(
        tmp_path / "a",
        "payment_reversal",
        ("bank_transactions.csv", "credit,posted", "credit,returned"),
    )
    assert ecc.check_reversal(returned, "PAY_1").status != G
    wrong_way = edited(
        tmp_path / "b",
        "payment_reversal",
        (
            "bank_transactions.csv",
            "payment reversal,600.00,EUR,credit",
            "payment reversal,600.00,EUR,debit",
        ),
    )
    assert ecc.check_reversal(wrong_way, "PAY_1").status != G


def test_f14_partial_fee_journal_is_not_gradable(tmp_path: Path) -> None:
    # Finding 10: a 25 shortfall with a 10 fee journal. The F14 absence search
    # (fee journals sourced to the debit: count 0) is false, so it is not gradable.
    fee = (
        "J_FEE_1,J_FEE_1_L1,BT_1,bank_fee,2026-06-03,2026-06,650000,10.00,0.00,EUR\n"
        "J_FEE_1,J_FEE_1_L2,BT_1,bank_fee,2026-06-03,2026-06,100000,0.00,10.00,EUR\n"
    )
    s = edited(tmp_path, "f14_amount_shortfall")
    assert ecc.check_f14(s, "PAY_1", variant="f14").status == G
    gl = tmp_path / "f14_amount_shortfall" / "gl_entries.csv"
    gl.write_text(gl.read_text() + fee)
    s = ecc.load_dir(tmp_path / "f14_amount_shortfall")
    res = ecc.check_f14(s, "PAY_1", variant="f14")
    assert (res.status, res.reason) == (NG, "partial_fee_journal")


def test_unbalanced_fee_journal_is_not_absent(tmp_path: Path) -> None:
    # An unbalanced bank_fee journal still makes the fee-journal search count 1.
    s = edited(tmp_path, "f14_amount_shortfall")
    gl = tmp_path / "f14_amount_shortfall" / "gl_entries.csv"
    gl.write_text(
        gl.read_text()
        + "J_FEE_1,J_FEE_1_L1,BT_1,bank_fee,2026-06-03,2026-06,650000,10.00,0.00,EUR\n"
    )
    s = ecc.load_dir(tmp_path / "f14_amount_shortfall")
    res = ecc.check_f14(s, "PAY_1", variant="f14")
    assert (res.status, res.reason) == (NG, "invalid_fee_journal")


def test_unbalanced_fee_journal_on_fee_row_not_gradable(tmp_path: Path) -> None:
    s = edited(
        tmp_path, "accounted_bank_fee", ("gl_entries.csv", "15.00,EUR\n", "5.00,EUR\n")
    )
    assert ecc.check_bank_fee(s, "BT_2").status == NG


def test_unbalanced_reversal_journal_not_gradable(tmp_path: Path) -> None:
    s = edited(
        tmp_path, "payment_reversal", ("gl_entries.csv", "0.00,600.00", "0.00,500.00")
    )
    assert ecc.check_reversal(s, "PAY_1").status == NG
