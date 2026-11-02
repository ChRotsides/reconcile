"""Round-2 evaluator hardening (review findings 15 to 21, 1 and 9): packet
structure, settlement semantics of batch/split/due-date, journal enumeration,
tolerance currency, recognised searches only, and the protocol order of the
run (same-image rerun before labels, verified snapshot copy)."""

from __future__ import annotations

import copy
import importlib
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_dev_run_audit import as_sum, calc, contract_fails, line, score
from test_dev_run_completeness import Packet, build_packets, check, mutated
from test_dev_run_efficiency import make_run, sha
from test_dev_run_semantics import evidence_of, restate

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
D = importlib.import_module("dev_run")
M = importlib.import_module("metrics")


@pytest.fixture(scope="module")
def packets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Packet, Any]]:
    return build_packets(tmp_path_factory)


# ------------------------------------------------------ 15: packet structure


def test_a_duplicate_calculation_id_with_a_wrong_result_is_not_a_success(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    assert score("weekend_clearing", packet, snap)["primary"] == 1
    p = copy.deepcopy(packet)
    bad = copy.deepcopy(p["calculations"][0])
    bad["result"] = "999.00 " + bad["result"].split()[1]  # an invalid emission
    p["calculations"].insert(0, bad)  # the valid one stays later and wins a dict
    row = score("weekend_clearing", p, snap)
    assert row["primary"] == 0 and not row["complete"] and not row["well_formed"]
    assert "malformed_packet" in row["missing"] and row["malformed_reason"]


def test_a_duplicate_evidence_id_is_not_a_success(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    p = copy.deepcopy(packet)
    dup = copy.deepcopy(p["evidence"][0])
    dup["values"] = {"amount": "1.00 EUR"}  # would be hidden behind the valid one
    p["evidence"].insert(0, dup)
    row = score("weekend_clearing", p, snap)
    assert row["primary"] == 0 and not row["well_formed"]


def test_a_calculation_and_a_day_count_may_not_share_an_id(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    p = copy.deepcopy(packet)
    p["day_counts"][0]["calculation_id"] = p["calculations"][0]["calculation_id"]
    assert score("weekend_clearing", p, snap)["primary"] == 0
    assert "share" in D.packet_structure_problem(p) or "both" in (
        D.packet_structure_problem(p) or ""
    )


def test_a_calculation_without_operands_is_unsuccessful_not_a_crash(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["f06_double_payment"]
    p = copy.deepcopy(packet)
    calc(p, "invoice_total_minus_committed")["operands"] = []
    row = score("f06_double_payment", p, snap)
    assert row["primary"] == 0 and not row["well_formed"]
    # the semantic check is also safe on its own
    p2 = copy.deepcopy(packet)
    calc(p2, "invoice_total_minus_committed")["operands"] = []
    v = D.View(p2)
    inv = snap.invoice(packet["request"]["primary_entity"]["record_id"])
    assert (
        D.committed_overpayment_ok(
            snap, v, inv, snap.allocs_of_invoice(inv["invoice_id"])
        )
        is False
    )


def test_a_missing_packet_field_is_unsuccessful_not_a_crash(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    p = copy.deepcopy(packet)
    del p["calculations"]
    assert not score("weekend_clearing", p, snap)["well_formed"]


def test_hard_gates_fail_for_a_malformed_answered_packet(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    good = score("weekend_clearing", packet, snap)
    p = copy.deepcopy(packet)
    p["calculations"].append(copy.deepcopy(p["calculations"][0]))
    bad = score("weekend_clearing", p, snap)
    manifest = {
        "absence_check": {"passed": True},
        "image_id": "i",
        "rerun": {
            "ok": True,
            "image_id": "i",
            "image_id_matches_manifest": True,
            "splits": {s: {"identical": True} for s in D.SPLITS},
        },
    }
    assert all(D.hard_gates([good], manifest).values())
    gates = D.hard_gates([good, bad], manifest)
    assert not gates["all_answered_packets_well_formed"]
    assert not gates["all_calculations_exact"]
    assert set(gates) == {
        "absence_check_passed",
        "rerun_identical_same_image",
        "all_answered_packets_well_formed",
        "all_citations_resolve",
        "all_calculations_exact",
        "all_day_counts_exact",
    }


# ------------------------------------------- 16: settlement semantics (bank)


def lower_amount(
    packet: Packet, snap: Any, tmp_path: Path, table: str, key: str, by: str
) -> tuple[Packet, Any]:
    """The bank debit lowered in the snapshot and followed by the packet:
    every calculation stays exact, but the debit no longer equals the payment."""
    e = evidence_of(packet, table)
    old = Decimal(e["values"]["amount"].split()[0])
    cur = e["values"]["amount"].split()[1]
    new = f"{old - Decimal(by)}"
    rid = e["record"]["record_id"]
    snap2 = mutated(
        snap, tmp_path, f"UPDATE {table} SET amount = '{new}' WHERE {key} = '{rid}'"
    )
    p = copy.deepcopy(packet)
    for ev in p["evidence"]:
        if ev["evidence_id"] == e["evidence_id"]:
            ev["values"]["amount"] = f"{new} {cur}"
    for c in p["calculations"]:
        for o in c["operands"]:
            if o["evidence_id"] == e["evidence_id"]:
                o["amount"] = f"{new} {cur}"
                if c["operation"] == "difference":
                    restate(c)
    return p, snap2


def test_batch_with_a_bank_debit_below_the_payment_is_incomplete(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    packet, snap = packets["batch_payment"]
    assert check(packet, snap)["complete"]
    p, snap2 = lower_amount(
        packet, snap, tmp_path, "bank_transactions", "bank_transaction_id", "50.00"
    )
    result = check(p, snap2)
    assert result["audit_ok"], "the arithmetic itself is exact"
    assert "amount_equality_calculation" in result["missing"]


@pytest.mark.parametrize(
    "cid,req",
    [
        ("delta", "amount_equality_calculation"),
        ("payment_minus_allocated", "allocation_sum_calculation"),
    ],
)
def test_batch_relations_must_be_differences(
    packets: dict[str, tuple[Packet, Any]], cid: str, req: str
) -> None:
    packet, snap = packets["batch_payment"]
    p = copy.deepcopy(packet)
    as_sum(calc(p, cid))
    contract_fails("batch_payment", packets, p, snap, req)


def test_batch_allocations_short_of_the_payment_are_incomplete(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    """One allocation dropped from the operands, result recomputed: exact, but
    the allocations no longer equal the payment."""
    packet, snap = packets["batch_payment"]
    p = copy.deepcopy(packet)
    c = calc(p, "payment_minus_allocated")
    c["operands"].pop()
    restate(c)
    contract_fails("batch_payment", packets, p, snap, "allocation_sum_calculation")


@pytest.mark.parametrize("name", ["split_payment", "due_date_payment"])
@pytest.mark.parametrize(
    "cid,req",
    [
        ("invoice_total_minus_allocated", "allocation_total_calculation"),
        ("debit_minus_payment:", "debit_equality_calculations"),
    ],
)
def test_invoice_settlement_relations_must_be_differences(
    packets: dict[str, tuple[Packet, Any]], name: str, cid: str, req: str
) -> None:
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    for c in p["calculations"]:
        if c["calculation_id"].startswith(cid):
            as_sum(c)
    contract_fails(name, packets, p, snap, req)


@pytest.mark.parametrize("name", ["split_payment", "due_date_payment"])
def test_a_debit_below_its_payment_is_incomplete_for_invoice_variants(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path, name: str
) -> None:
    packet, snap = packets[name]
    assert check(packet, snap)["complete"]
    p, snap2 = lower_amount(
        packet, snap, tmp_path, "bank_transactions", "bank_transaction_id", "5.00"
    )
    result = check(p, snap2)
    assert result["audit_ok"]
    assert "debit_equality_calculations" in result["missing"]


def test_a_due_date_answer_needs_a_payment_on_the_due_date(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    packet, snap = packets["due_date_payment"]
    inv = evidence_of(packet, "invoices")
    snap2 = mutated(
        snap,
        tmp_path,
        "UPDATE invoices SET due_date = '2020-01-02' WHERE invoice_id ="
        f" '{inv['record']['record_id']}'",
    )
    p = copy.deepcopy(packet)
    for ev in p["evidence"]:
        if ev["evidence_id"] == inv["evidence_id"]:
            ev["values"]["due_date"] = "2020-01-02"
    result = check(p, snap2)
    assert result["audit_ok"]
    assert "payment_on_due_date" in result["missing"]


def test_split_needs_two_allocations(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    """The same checks on the split answer relabelled due-date are not enough;
    and a one-leg invoice cannot be called a split."""
    packet, snap = packets["due_date_payment"]
    p = copy.deepcopy(packet)
    p["diagnosis"]["category"] = "legitimate_split_payment"
    assert "two_or_more_allocations" in check(p, snap)["missing"]


# ------------------------------------------------- 17: all journals relevant


def test_an_extra_unbalanced_payment_journal_blocks_the_all_clear(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    packet, snap = packets["weekend_clearing"]
    pay = evidence_of(packet, "payments")["record"]["record_id"]
    assert check(packet, snap)["complete"]
    snap2 = mutated(snap, tmp_path, line("J2_EXTRA", pay, "payment", "20.00", "0.00"))
    result = check(packet, snap2)  # J1 is still cited and valid
    assert not result["complete"]
    assert "payment_journal" in result["missing"]
    assert "payment_journal_calculation" in result["missing"]


# --------------------------------------------------- 18: tolerance as money


@pytest.mark.parametrize("tolerance", ["0.01 JPY", "0.01", "abc", "0.01 EUR EUR", ""])
def test_a_calculation_tolerance_must_be_money_in_the_calculations_currency(
    packets: dict[str, tuple[Packet, Any]], tolerance: str
) -> None:
    packet, snap = packets["net_fee_accounting"]
    assert check(packet, snap)["complete"]
    p = copy.deepcopy(packet)
    calc(p, "unexplained")["tolerance"] = tolerance
    result = check(p, snap)
    assert not result["complete"]
    assert "unexplained_calculation" in result["missing"] or not result["audit_ok"]


def test_a_foreign_currency_tolerance_fails_the_audit_and_the_requirement(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["net_fee_accounting"]
    p = copy.deepcopy(packet)
    calc(p, "unexplained")["tolerance"] = "0.01 JPY"
    result = check(p, snap)
    assert not result["audit_ok"]
    assert "unexplained_calculation" in result["missing"]
    assert score("net_fee_accounting", p, snap)["primary"] == 0


# ------------------------------------------ 21: only recognised searches run


def hang_predicate() -> str:
    return (
        "1 = 1 AND EXISTS (WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL"
        " SELECT n + 1 FROM r) SELECT 1 FROM r WHERE n < 0)"
    )


def search_item(table: str, predicate: str, count: int) -> Packet:
    return {
        "evidence_id": "search_extra",
        "record": None,
        "relationship": "x",
        "search": {"table": table, "predicate": predicate, "observed_count": count},
        "timestamps": {},
        "values": {},
    }


def test_an_unrecognised_supplemental_search_is_never_executed(
    packets: dict[str, tuple[Packet, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    packet, snap = packets["f12_payment_missing_from_bank"]
    seen: list[str] = []
    real = snap.bounded_count

    def spy(table: str, predicate: str) -> int:
        seen.append(predicate)
        if "RECURSIVE" in predicate:
            raise AssertionError("an unrecognised predicate was executed")
        return int(real(table, predicate))

    monkeypatch.setattr(snap, "bounded_count", spy)
    p = copy.deepcopy(packet)
    p["evidence"].append(search_item("bank_transactions", hang_predicate(), 0))
    result = check(p, snap)
    assert result["searches_mismatch"] == ["search_extra:unrecognised"]
    assert not result["complete"] and "searches_reproduce" in result["missing"]
    assert seen, "the recognised searches still run"


def test_the_evaluators_own_search_forms_are_all_recognised(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    for name, (packet, _snap) in packets.items():
        for e in packet["evidence"]:
            s = e["search"]
            if s:
                assert D.search_recognised(s["table"], s["predicate"]), (name, s)


@pytest.mark.parametrize(
    "table,predicate",
    [
        ("payments", "reference_number = 'x' OR 1 = 1"),
        ("payments", "reference_number = 'x'; DELETE FROM payments"),
        ("payments", "reference_number = 'x' AND 1=0"),
        ("bank_transactions", "reference_number = 'x'"),
    ],
)
def test_search_forms_are_matched_in_full(table: str, predicate: str) -> None:
    assert not D.search_recognised(table, predicate)


def test_a_runaway_query_is_aborted_by_the_progress_budget(
    packets: dict[str, tuple[Packet, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, snap = packets["f12_payment_missing_from_bank"]
    monkeypatch.setattr(D, "SEARCH_BUDGET_TICKS", 5)
    monkeypatch.setattr(D, "TICK_INSTRUCTIONS", 100)
    with pytest.raises(sqlite3.Error):
        snap.bounded_count("payments", hang_predicate())
    assert snap.bounded_count("payments", "1 = 1") >= 0  # handler is cleared


# ------------------------------------- 20, 1, 9: protocol order and scoring


def test_rerun_refuses_a_changed_requests_file_even_if_inference_is_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = make_run(tmp_path)
    m |= {"tag": "t", "image_id": "sha256:i"}
    ran: list[Any] = []
    monkeypatch.setattr(D, "run_cmd", lambda *a, **k: ran.append(a))
    (tmp_path / "prepared/requests/train/requests.jsonl").write_text("{}\n\n")
    with pytest.raises(SystemExit, match="requests.jsonl differs"):
        D.rerun_check(tmp_path, m)
    assert ran == []  # nothing was re-run on unverified inputs


def test_rerun_refuses_swapped_links(tmp_path: Path) -> None:
    m = make_run(tmp_path)
    m |= {"tag": "t", "image_id": "sha256:i"}
    (tmp_path / "evaluator/validation/links.jsonl").write_text('{"x": 1}\n')
    with pytest.raises(SystemExit, match="links.jsonl differs"):
        D.rerun_check(tmp_path, m)


def rerun_ok(image: str = "i") -> dict[str, Any]:
    return {
        "ok": True,
        "image_id": image,
        "image_id_matches_manifest": True,
        "splits": {s: {"identical": True} for s in D.SPLITS},
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.pop("rerun"),
        lambda m: m["rerun"].update(ok=False),
        lambda m: m["rerun"].update(image_id="other"),
        lambda m: m["rerun"].update(image_id_matches_manifest=False),
        lambda m: m["rerun"]["splits"]["train"].update(identical=False),
        lambda m: m["rerun"]["splits"].pop("validation"),
    ],
)
def test_evaluate_refuses_without_an_ok_same_image_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: Any
) -> None:
    m = make_run(tmp_path) | {"image_id": "i", "rerun": rerun_ok()}
    assert D.rerun_problem(m) is None
    mutate(m)
    assert D.rerun_problem(m)
    root = tmp_path / "artifacts/dev-runs/t"
    root.mkdir(parents=True)
    (root / "run-manifest.json").write_text(json.dumps(m))
    monkeypatch.setattr(D, "REPO", tmp_path)
    monkeypatch.setattr(
        D, "read_split_inputs", lambda *a: pytest.fail("inputs read before the rerun")
    )
    with pytest.raises(SystemExit, match="identical same-image rerun"):
        D.evaluate("t", tmp_path)


def test_the_scored_snapshot_is_a_private_copy_of_the_verified_bytes(
    tmp_path: Path,
) -> None:
    m = make_run(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    copy_ = D.verified_snapshot_copy(tmp_path, m, work)
    original = tmp_path / "prepared/snapshot" / D.SNAPSHOT_FILE
    assert copy_ != original and sha(copy_) == m["snapshot_file_sha256"]
    original.write_bytes(b"swapped after verification")
    assert sha(copy_) == m["snapshot_file_sha256"]  # the scored copy is unaffected
    assert not (copy_.stat().st_mode & 0o222)  # read-only


def test_a_swapped_snapshot_is_refused_before_a_copy_is_made(tmp_path: Path) -> None:
    m = make_run(tmp_path)
    (tmp_path / "prepared/snapshot" / D.SNAPSHOT_FILE).write_bytes(b"swapped")
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SystemExit, match="snapshot.sqlite differs"):
        D.verified_snapshot_copy(tmp_path, m, work)
    assert list(work.iterdir()) == []


# --------------------------------- shared false-alarm definition (metrics.py)


@pytest.mark.parametrize("group", ["contradicted", "not_gradable", "cohort", "fx"])
def test_false_alarm_counts_use_the_group_independent_definition(
    packets: dict[str, tuple[Packet, Any]], group: str
) -> None:
    packet, snap = packets["f12_payment_missing_from_bank"]
    label = {"failure_type": "NO_FAILURE", "scenario_variant": "weekend_clearing"}
    row = D.case_row(
        "train", "c1", label, packet, packet["request"]["primary_entity"], snap,
        importlib.import_module("types").SimpleNamespace(
            verified={}, candidates={}, review={}
        ),
    )  # fmt: skip
    row["group"] = group
    row["kind"] = "not_false_alarm_kind"
    assert M.is_false_alarm(row)
    fa = D.all_metrics([row])["false_alarms_all_legitimate"]
    assert fa["any_legitimate"]["n"] == 1


def test_a_malformed_non_answer_packet_does_not_crash_scoring(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    p = copy.deepcopy(packet)
    p["diagnosis"]["disposition"] = "abstained"
    del p["evidence"][0]["search"]
    del p["evidence"][0]["record"]
    row = D.case_row(
        "train",
        "c1",
        {"failure_type": "F06_INVOICE_PAID_TWICE", "scenario_variant": "x"},
        p,
        p["request"]["primary_entity"],
        snap,
        importlib.import_module("types").SimpleNamespace(
            verified={}, candidates={}, review={}
        ),
    )
    assert row["well_formed"] is False and row["searches_mismatch"] == []
