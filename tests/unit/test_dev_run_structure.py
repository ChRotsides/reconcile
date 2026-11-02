"""Review round 3, findings 15 (malformed packets) and 25 (one debit, two
payments). Every case goes through `case_row` and the primary metric."""

from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from test_dev_run_audit import CONFLICTS, FAMILY, score
from test_dev_run_completeness import EXPECTED, Packet, build_packets, check, mutated

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
D = importlib.import_module("dev_run")
M = importlib.import_module("metrics")

DELETE = object()


@pytest.fixture(scope="module")
def packets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Packet, Any]]:
    return build_packets(tmp_path_factory)


def scored(name: str, packet: Packet, entity: dict[str, str], snap: Any) -> Any:
    """`score` with an explicit primary entity (an edit may delete `request`)."""
    category = EXPECTED[name]["category"]
    failure = FAMILY.get(category)
    label = {
        "failure_type": failure or "NO_FAILURE",
        "scenario_variant": "x" if failure else category,
    }
    row = D.case_row("train", "c1", label, packet, entity, snap, CONFLICTS)
    row["primary"] = M.cohort_metrics([row])["correct_and_evidence_complete"]["n"]
    return row


def edit(packet: Packet, path: tuple[str | int, ...], value: object) -> Packet:
    p = copy.deepcopy(packet)
    node: Any = p
    for step in path[:-1]:
        node = node[step]
    if value is DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return p


# (fixture, path, replacement): one deletion or type change per field the
# scorer consumes, for each object kind.
MALFORMED = [
    ("weekend_clearing", ("diagnosis",), DELETE),
    ("weekend_clearing", ("diagnosis", "category"), 5),
    ("weekend_clearing", ("diagnosis", "reason"), 5),
    ("weekend_clearing", ("diagnosis", "findings"), "none"),
    ("weekend_clearing", ("diagnosis", "findings", 0, "check_id"), DELETE),
    ("weekend_clearing", ("diagnosis", "findings", 0, "state"), None),
    ("weekend_clearing", ("diagnosis", "findings", 0, "evidence_ids"), "abc"),
    ("weekend_clearing", ("diagnosis", "findings", 0, "evidence_ids"), [1]),
    ("weekend_clearing", ("diagnosis", "findings", 0, "calculation_ids"), None),
    ("weekend_clearing", ("diagnosis", "findings", 0, "calculation_ids"), DELETE),
    ("weekend_clearing", ("evidence", 0, "evidence_id"), 7),
    ("weekend_clearing", ("evidence", 0, "record", "table"), DELETE),
    ("weekend_clearing", ("evidence", 0, "record", "record_id"), 3),
    ("weekend_clearing", ("evidence", 0, "record", "snapshot"), DELETE),
    ("weekend_clearing", ("evidence", 0, "record"), "pay"),
    ("weekend_clearing", ("evidence", 0, "values"), ["x"]),
    ("weekend_clearing", ("evidence", 0, "values", "payment_amount"), 5),
    ("weekend_clearing", ("evidence", 0, "timestamps"), "2026-01-01"),
    ("weekend_clearing", ("evidence", 0, "search"), DELETE),
    ("weekend_clearing", ("evidence",), {"a": 1}),
    ("weekend_clearing", ("evidence", 0), "pay"),
    ("weekend_clearing", ("calculations", 0, "operation"), DELETE),
    ("weekend_clearing", ("calculations", 0, "operation"), "product"),
    ("weekend_clearing", ("calculations", 0, "operands"), "x"),
    ("weekend_clearing", ("calculations", 0, "operands", 0, "evidence_id"), DELETE),
    ("weekend_clearing", ("calculations", 0, "operands", 0, "field"), None),
    ("weekend_clearing", ("calculations", 0, "operands", 0, "amount"), 5),
    ("weekend_clearing", ("calculations", 0, "result"), DELETE),
    ("weekend_clearing", ("calculations", 0, "tolerance"), 5),
    ("weekend_clearing", ("calculations", 0, "tolerance"), DELETE),
    ("weekend_clearing", ("calculations", 0, "calculation_id"), DELETE),
    ("weekend_clearing", ("day_counts", 0, "result"), "3"),
    ("weekend_clearing", ("day_counts", 0, "limit"), DELETE),
    ("weekend_clearing", ("day_counts", 0, "limit_policy"), None),
    ("weekend_clearing", ("day_counts", 0, "start_evidence"), DELETE),
    ("weekend_clearing", ("day_counts", 0, "start_field"), 1),
    ("weekend_clearing", ("day_counts", 0, "end_evidence"), 1),
    ("weekend_clearing", ("day_counts", 0, "end_field"), DELETE),
    ("weekend_clearing", ("day_counts", 0, "calculation_id"), DELETE),
    ("weekend_clearing", ("limitations",), DELETE),
    ("weekend_clearing", ("request",), DELETE),
    ("weekend_clearing", ("request", "cutoff"), "not a date"),
    ("weekend_clearing", ("request", "cutoff"), DELETE),
    ("f12_payment_missing_from_bank", ("evidence", 1, "search", "predicate"), DELETE),
    ("f12_payment_missing_from_bank", ("evidence", 1, "search", "predicate"), 5),
    ("f12_payment_missing_from_bank", ("evidence", 1, "search", "table"), None),
    ("f12_payment_missing_from_bank", ("evidence", 1, "search", "observed_count"), "0"),
    (
        "f12_payment_missing_from_bank",
        ("evidence", 1, "search", "observed_count"),
        True,
    ),
    ("f12_payment_missing_from_bank", ("evidence", 1, "search"), "all"),
]


@pytest.mark.parametrize("name,path,value", MALFORMED)
def test_a_malformed_packet_is_an_unsuccessful_case_not_a_crash(
    packets: dict[str, tuple[Packet, Any]],
    name: str,
    path: tuple[str | int, ...],
    value: object,
) -> None:
    packet, snap = packets[name]
    assert score(name, packet, snap)["primary"] == 1
    p = edit(packet, path, value)
    row = scored(name, p, packet["request"]["primary_entity"], snap)
    assert row["well_formed"] is False and row["complete"] is False
    assert row["malformed_reason"] and "malformed_packet" in row["missing"]
    assert row["primary"] == 0


def test_a_malformed_answered_packet_stays_answered_for_the_hard_gate(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    row = score(
        "weekend_clearing", edit(packet, ("calculations", 0, "operation"), DELETE), snap
    )
    assert row["disposition"] == "answered"
    gates = D.hard_gates([row], {})
    assert gates["all_answered_packets_well_formed"] is False


def test_an_unreadable_disposition_is_a_failed_case(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    row = score("weekend_clearing", edit(packet, ("diagnosis",), DELETE), snap)
    assert row["disposition"] == "failed" and row["primary"] == 0


# ------------------------------------------------ 25: one debit, two payments


def share_one_debit(packet: Packet, snap: Any, tmp_path: Path) -> tuple[Packet, Any]:
    """The review's scenario on the split fixture: EUR 1600 invoice, two EUR 800
    payments with the same reference R, one posted EUR 800 debit. Every other
    figure is restated so the old checks all hold."""
    ev = {e["evidence_id"]: e for e in packet["evidence"]}
    pays = [e for e in ev.values() if e["record"]["table"] == "payments"]
    banks = [e for e in ev.values() if e["record"]["table"] == "bank_transactions"]
    big_pay = next(e for e in pays if e["values"]["payment_amount"] == "1200.00 EUR")
    small_pay = next(e for e in pays if e["values"]["payment_amount"] == "800.00 EUR")
    big_bank = next(e for e in banks if e["values"]["amount"] == "1200.00 EUR")
    small_bank = next(e for e in banks if e["values"]["amount"] == "800.00 EUR")
    ref = snap.payment(small_pay["record"]["record_id"])["reference_number"]
    kept = copy.deepcopy(packet)
    kept["evidence"] = [
        e for e in kept["evidence"] if e["evidence_id"] != big_bank["evidence_id"]
    ]
    text = json.dumps(kept).replace("1200.00 EUR", "800.00 EUR")
    text = text.replace("2000.00 EUR", "1600.00 EUR")
    # The second payment now cites the one debit.
    text = text.replace(big_bank["evidence_id"], small_bank["evidence_id"])
    p: Packet = json.loads(text)
    snap2 = mutated(
        snap,
        tmp_path,
        f"DELETE FROM bank_transactions WHERE bank_transaction_id ="
        f" '{big_bank['record']['record_id']}'",
        "UPDATE invoices SET invoice_total = '1600.00', subtotal = '1600.00'",
        "UPDATE payment_allocations SET allocated_amount = '800.00'"
        " WHERE allocated_amount = '1200.00'",
        "UPDATE gl_entries SET debit = '800.00' WHERE debit = '1200.00'",
        "UPDATE gl_entries SET credit = '800.00' WHERE credit = '1200.00'",
        "UPDATE payments SET payment_amount = '800.00'"
        " WHERE payment_amount = '1200.00'",
        f"UPDATE payments SET reference_number = '{ref}'"
        f" WHERE payment_id = '{big_pay['record']['record_id']}'",
    )
    return p, snap2


def test_one_debit_cannot_settle_two_payments(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    packet, snap = packets["split_payment"]
    p, snap2 = share_one_debit(packet, snap, tmp_path)
    result = check(p, snap2)
    assert result["audit_ok"], "the arithmetic itself is exact"
    assert not result["complete"] and "payments_own_debits" in result["missing"]
    assert result["missing"] == ["payments_own_debits"], "nothing else is wrong"
    assert score("split_payment", p, snap2)["primary"] == 0


def test_a_real_split_payment_still_passes(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["split_payment"]
    assert check(packet, snap)["complete"]
    assert score("split_payment", packet, snap)["primary"] == 1


def test_one_payment_covering_several_invoices_still_passes(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["batch_payment"]
    assert score("batch_payment", packet, snap)["primary"] == 1


def test_a_double_payment_over_one_shared_reference_is_not_supported(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    packet, snap = packets["f06_double_payment"]
    assert check(packet, snap)["complete"]
    rows = snap.q("SELECT payment_id, reference_number FROM payments")
    first, second = rows[0], rows[1]
    snap2 = mutated(
        snap,
        tmp_path,
        f"UPDATE payments SET reference_number = '{first['reference_number']}'"
        f" WHERE payment_id = '{second['payment_id']}'",
    )
    result = check(packet, snap2)
    assert "committed_payments_own_debits" in result["missing"]
    assert score("f06_double_payment", packet, snap2)["primary"] == 0
