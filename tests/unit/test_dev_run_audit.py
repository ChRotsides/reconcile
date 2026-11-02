"""The packet audit and the semantic contract checks of evaluation/dev_run.py,
exercised through the whole scoring path (case_row -> metrics.cohort_metrics).

Every test here is a mutation of a packet the investigator produced for a
public fixture. Each one would have passed the previous evaluator (which only
checked that required records and calculations were present, and kept the audit
apart from `complete`), and must now not count as correct and evidence-complete.
"""

from __future__ import annotations

import copy
import importlib
import sqlite3
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_dev_run_completeness import (
    EXPECTED,
    Packet,
    build_packets,
    check,
    mutated,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
D = importlib.import_module("dev_run")
M = importlib.import_module("metrics")
CONFLICTS = SimpleNamespace(verified={}, candidates={}, review={})
FAMILY = {
    "F06": "F06_INVOICE_PAID_TWICE",
    "F12": "F12_ERP_PAYMENT_MISSING_FROM_BANK",
    "F14": "F14_BANK_ERP_AMOUNT_MISMATCH",
}
SCORED = [
    "f06_double_payment",
    "f12_payment_missing_from_bank",
    "f14_amount_shortfall",
    "weekend_clearing",
    "check_clearing",
    "pending_settlement",
    "split_payment",
    "net_fee_accounting",
]


@pytest.fixture(scope="module")
def packets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Packet, Any]]:
    return build_packets(tmp_path_factory)


def score(name: str, packet: Packet, snap: Any) -> dict[str, Any]:
    """The scored row of the case, built exactly as `load_split` builds it, and
    the primary metric over that single case."""
    category = EXPECTED[name]["category"]
    failure = FAMILY.get(category)
    label = {
        "failure_type": failure or "NO_FAILURE",
        "scenario_variant": "x" if failure else category,
    }
    row = D.case_row(
        "train",
        "c1",
        label,
        packet,
        packet["request"]["primary_entity"],
        snap,
        CONFLICTS,
    )
    row["primary"] = M.cohort_metrics([row])["correct_and_evidence_complete"]["n"]
    return dict(row)


def calc(packet: Packet, prefix: str) -> Packet:
    (c,) = [c for c in packet["calculations"] if c["calculation_id"] == prefix]
    found: Packet = c
    return found


@pytest.mark.parametrize("name", SCORED)
def test_unmodified_packets_score_as_correct_and_complete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    row = score(name, packet, snap)
    assert row["outcome"] == "correct" and row["audit_ok"] and row["primary"] == 1


# ------------------------------------------------------------------ F1 gate


def money_plus(text: str, cents: str = "0.01") -> str:
    amount, currency = text.split()
    return f"{Decimal(amount) + Decimal(cents)} {currency}"


@pytest.mark.parametrize(
    "name", [n for n in SCORED if n != "f12_payment_missing_from_bank"]
)  # the F12 packet has day counts but no money calculation
def test_one_altered_calculation_result_is_not_a_primary_success(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    """Whichever calculation is altered (required by the contract or not), the
    otherwise complete, correctly labelled packet must not count."""
    packet, snap = packets[name]
    assert packet["calculations"]
    for i in range(len(packet["calculations"])):
        p = copy.deepcopy(packet)
        c = p["calculations"][i]
        c["result"] = money_plus(c["result"])
        row = score(name, p, snap)
        assert row["primary"] == 0, c["calculation_id"]
        assert row["calcs_exact"] == row["calcs"] - 1


@pytest.mark.parametrize("name", SCORED)
def test_one_unresolvable_citation_is_not_a_primary_success(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    cited = [i for i, e in enumerate(packet["evidence"]) if e["record"]]
    for i in cited:
        p = copy.deepcopy(packet)
        p["evidence"][i]["record"]["record_id"] += "_NOPE"
        assert score(name, p, snap)["primary"] == 0, p["evidence"][i]["evidence_id"]


def test_an_altered_value_on_an_unrequired_citation_is_caught_by_the_audit(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    """The contract requirements look only at which records are cited; the value
    is the audit's business. The old evaluator counted this packet complete."""
    packet, snap = packets["f06_double_payment"]
    p = copy.deepcopy(packet)
    e = next(e for e in p["evidence"] if e["record"]["table"] == "payments")
    e["values"]["payment_status"] = "bogus"
    result = check(p, snap)
    assert result["contract_complete"] and not result["complete"]
    assert "packet_audit" in result["missing"]
    assert score("f06_double_payment", p, snap)["primary"] == 0


@pytest.mark.parametrize("name", ["f12_payment_missing_from_bank", "weekend_clearing"])
def test_an_altered_day_count_is_not_a_primary_success(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    p["day_counts"][0]["result"] += 1
    row = score(name, p, snap)
    assert row["primary"] == 0 and row["day_counts_exact"] == row["day_counts"] - 1


def test_a_finding_that_cites_nothing_is_not_valid_and_fails_the_audit(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["f14_amount_shortfall"]
    p = copy.deepcopy(packet)
    p["diagnosis"]["findings"].append(
        {"check_id": "vacuous", "state": "supported", "evidence_ids": [],
         "calculation_ids": []}
    )  # fmt: skip
    row = score("f14_amount_shortfall", p, snap)
    assert row["findings"] - row["findings_reference_ok"] == 1
    assert row["primary"] == 0


# ------------------------------------------------- F3: semantic calculations


def as_sum(c: Packet) -> None:
    """A sum of the same operands: arithmetically exact, the wrong relation."""
    c["operation"] = "sum"
    total = sum(Decimal(o["amount"].split()[0]) for o in c["operands"])
    c["result"] = f"{total} {c['result'].split()[1]}"


def contract_fails(
    name: str,
    packets: dict[str, tuple[Packet, Any]],
    p: Packet,
    snap: Any,
    requirement: str,
) -> None:
    result = check(p, snap)
    assert result["audit_ok"], "the arithmetic itself is exact"
    assert not result["complete"] and requirement in result["missing"]
    assert score(name, p, snap)["primary"] == 0


def test_f14_sum_of_payment_and_debit_is_not_the_difference(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    """EUR/USD 100 against 80: a correct sum of 180 shows nothing about the 20."""
    packet, snap = packets["f14_amount_shortfall"]
    p = copy.deepcopy(packet)
    as_sum(calc(p, "delta"))
    contract_fails("f14_amount_shortfall", packets, p, snap, "delta_calculation")


def test_f14_difference_the_wrong_way_round_is_not_the_shortfall(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["f14_amount_shortfall"]
    p = copy.deepcopy(packet)
    c = calc(p, "delta")
    c["operands"].reverse()
    first, second = (Decimal(o["amount"].split()[0]) for o in c["operands"])
    c["result"] = f"{first - second} {c['result'].split()[1]}"
    contract_fails("f14_amount_shortfall", packets, p, snap, "delta_calculation")


def test_f14_delta_within_the_quantum_is_not_a_shortfall(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    packet, snap = packets["f14_amount_shortfall"]
    pay = next(e for e in packet["evidence"] if e["record"]["table"] == "payments")
    bank = next(
        e for e in packet["evidence"] if e["record"]["table"] == "bank_transactions"
    )
    amount = snap.payment(pay["record"]["record_id"])["payment_amount"]
    cur = pay["values"]["payment_currency"]
    snap2 = mutated(
        snap,
        tmp_path,
        f"UPDATE bank_transactions SET amount = '{amount}'"
        f" WHERE bank_transaction_id = '{bank['record']['record_id']}'",
    )
    p = copy.deepcopy(packet)
    for e in p["evidence"]:  # the packet agrees with the mutated snapshot
        if e["record"] and e["record"]["table"] == "bank_transactions":
            e["values"]["amount"] = f"{amount} {cur}"
    c = calc(p, "delta")
    for o in c["operands"]:
        o["amount"] = f"{amount} {cur}"
    c["result"] = f"0.00 {cur}"
    result = check(p, snap2)
    assert result["audit_ok"]
    assert "delta_calculation" in result["missing"]  # delta is not above a quantum


def test_f06_committed_overpayment_must_be_the_committed_allocations(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["f06_double_payment"]
    name = "f06_double_payment"
    # a sum of the invoice total and the allocations: exact, irrelevant
    p = copy.deepcopy(packet)
    as_sum(calc(p, "invoice_total_minus_committed"))
    contract_fails(name, packets, p, snap, "overpayment_calculation")
    # a committed allocation left out, result recomputed over the rest
    p = copy.deepcopy(packet)
    c = calc(p, "invoice_total_minus_committed")
    assert len(c["operands"]) > 2
    c["operands"].pop()
    first, *rest = (Decimal(o["amount"].split()[0]) for o in c["operands"])
    c["result"] = f"{first - sum(rest)} {c['result'].split()[1]}"
    contract_fails(name, packets, p, snap, "overpayment_calculation")
    # the same calculation under another id (the all-allocations one) is not it
    p = copy.deepcopy(packet)
    calc(p, "invoice_total_minus_committed")["calculation_id"] = "something_else"
    for f in p["diagnosis"]["findings"]:
        f["calculation_ids"] = [
            "something_else" if i == "invoice_total_minus_committed" else i
            for i in f["calculation_ids"]
        ]
    contract_fails(name, packets, p, snap, "overpayment_calculation")


def test_f12_the_limit_is_the_policys_not_the_packets(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["f12_payment_missing_from_bank"]
    p = copy.deepcopy(packet)
    d = p["day_counts"][0]
    assert d["result"] > d["limit"]
    d["limit"] = 0  # a packet that states a smaller limit still has result > limit
    contract_fails(
        "f12_payment_missing_from_bank", packets, p, snap, "business_day_age"
    )
    p = copy.deepcopy(packet)
    p["day_counts"][0]["limit_policy"] = "clearing_limit_wire"
    contract_fails(
        "f12_payment_missing_from_bank", packets, p, snap, "business_day_age"
    )


def test_pending_the_limit_is_the_policys_not_the_packets(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["pending_settlement"]
    p = copy.deepcopy(packet)
    d = next(d for d in p["day_counts"] if d["calculation_id"] == "business_day_age")
    d["limit"] += 50  # an overdue payment could be made to look inside its window
    contract_fails("pending_settlement", packets, p, snap, "business_day_age")


@pytest.mark.parametrize("name", ["weekend_clearing", "check_clearing"])
def test_clearing_amount_equality_must_be_a_difference_of_the_two_amounts(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    as_sum(calc(p, "delta"))  # 100 + 100 = 200: exact, says nothing about equality
    contract_fails(name, packets, p, snap, "amount_equality_calculation")


@pytest.mark.parametrize("name", ["weekend_clearing", "check_clearing"])
def test_clearing_lag_must_end_at_the_debit_with_the_policy_limit(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    next(d for d in p["day_counts"] if d["calculation_id"] == "business_day_lag")[
        "limit"
    ] += 5
    contract_fails(name, packets, p, snap, "business_day_lag")


# ------------------------------------------------- F5: whole-journal balance


def line(
    journal: str, source: str, kind: str, debit: str, credit: str, cur: str = "EUR"
) -> str:
    return (
        f"INSERT INTO gl_entries VALUES ('{journal}', 'extra_{debit}_{credit}',"
        f" '{source}', '{kind}', '2026-01-01', '2026-01', '1000', '{debit}',"
        f" '{credit}', '{cur}')"
    )


def payment_journal(packet: Packet) -> str:
    return str(
        next(
            e["record"]["record_id"]
            for e in packet["evidence"]
            if e["record"]["table"] == "gl_entries"
            and e["values"]["gl_type"] == "payment"
        )
    )


@pytest.mark.parametrize("name", ["weekend_clearing", "net_fee_accounting"])
def test_an_unbalanced_unrelated_line_in_the_payment_journal_blocks_the_all_clear(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path, name: str
) -> None:
    packet, snap = packets[name]
    j = payment_journal(packet)
    snap2 = mutated(snap, tmp_path, line(j, "UNRELATED", "other", "20.00", "0.00"))
    result = check(packet, snap2)
    assert not result["complete"] and not result["contract_complete"]
    assert "payment_journal" in result["missing"]
    assert not D.evidence_resolves(
        next(e for e in packet["evidence"] if e["record"]["record_id"] == j), snap2
    )


def test_a_balanced_unrelated_pair_does_not_change_the_attributable_amount(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    packet, snap = packets["weekend_clearing"]
    j = payment_journal(packet)
    snap2 = mutated(
        snap,
        tmp_path,
        line(j, "UNRELATED", "other", "20.00", "0.00"),
        line(j, "UNRELATED", "other", "0.00", "20.00"),
    )
    assert check(packet, snap2)["contract_complete"]  # requirements unaffected
    # the packet's stated journal_total is now stale, which the audit sees
    assert not check(packet, snap2)["audit_ok"]


def jl(
    source: str, kind: str, debit: str, credit: str, cur: str = "USD"
) -> dict[str, str]:
    return {
        "source_transaction_id": source,
        "transaction_type": kind,
        "debit": debit,
        "credit": credit,
        "currency": cur,
    }


def test_journal_totals_conditions() -> None:
    pay = [jl("P", "payment", "100", "0"), jl("P", "payment", "0", "100")]
    assert D.journal_totals(pay, "P", "payment") == ("USD", Decimal(100), Decimal(100))
    # whole journal unbalanced although the payment lines balance
    assert not D.journal_totals([*pay, jl("X", "o", "20", "0")], "P", "payment")
    # whole journal balanced, payment lines not (the other side sits on another source)
    skew = [jl("P", "payment", "100", "0"), jl("X", "o", "0", "100")]
    assert not D.journal_totals(skew, "P", "payment")
    # mixed currency, negative amount, zero journal
    assert not D.journal_totals(
        [*pay, jl("P", "payment", "0", "0", "EUR")], "P", "payment"
    )
    assert not D.journal_totals(
        [jl("P", "payment", "-5", "0"), jl("P", "payment", "0", "-5")], "P", "payment"
    )
    assert not D.journal_totals([jl("P", "payment", "0", "0")], "P", "payment")
    assert not D.journal_totals(pay, "Q", "payment")  # nothing attributable


# ------------------------------------------------------ F6: exact searches


def search_item(table: str, predicate: str, count: int) -> Packet:
    return {
        "evidence_id": "search_extra",
        "record": None,
        "relationship": "x",
        "search": {"table": table, "predicate": predicate, "observed_count": count},
        "timestamps": {},
        "values": {},
    }


@pytest.mark.parametrize(
    "name", ["f12_payment_missing_from_bank", "pending_settlement"]
)
def test_a_search_with_an_extra_restriction_is_not_the_required_search(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    """`... AND 1=0` contains the required condition and reproduces count 0
    whatever the table holds, so the absence would be 'proved' by narrowing."""
    packet, snap = packets[name]
    for i, e in enumerate(packet["evidence"]):
        if not e["search"] or e["search"]["observed_count"] != 0:
            continue
        p = copy.deepcopy(packet)
        p["evidence"][i]["search"]["predicate"] += " AND 1=0"
        result = check(p, snap)
        assert result["searches_mismatch"] == []  # it reproduces
        assert not result["complete"], e["evidence_id"]


def test_f14_fee_search_with_an_extra_restriction_is_rejected(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["f14_amount_shortfall"]
    p = copy.deepcopy(packet)
    next(e for e in p["evidence"] if e["search"])["search"]["predicate"] += " AND 1=0"
    assert "search_no_fee_journal" in check(p, snap)["missing"]


@pytest.mark.parametrize(
    "table,predicate",
    [
        ("bank_transactions", "payment_reference = ("),
        ("bank_transactions", "no_such_column = 1"),
        ("no_such_table", "1 = 1"),
        ("bank_transactions; DROP TABLE payments", "1 = 1"),
        ("bank_transactions", "1 = 1; DELETE FROM payments"),
    ],
)
def test_a_malformed_search_is_invalid_evidence_not_a_crash(
    packets: dict[str, tuple[Packet, Any]], table: str, predicate: str
) -> None:
    packet, snap = packets["f12_payment_missing_from_bank"]
    p = copy.deepcopy(packet)
    p["evidence"].append(search_item(table, predicate, 0))
    assert D.searches_reproduce(p, snap) == ["search_extra:malformed"]
    result = check(p, snap)
    assert not result["complete"] and "searches_reproduce" in result["missing"]
    assert snap.q("SELECT COUNT(*) AS n FROM payments")[0]["n"] > 0


# --------------------------------------------------- F7: exact arithmetic


def test_exact_context_keeps_the_money_types_30_digit_amounts() -> None:
    big = "1000000000000000000000000000.01"  # 30 digits
    assert len(big.replace(".", "")) == 30
    ev = {
        "a": {
            "evidence_id": "a",
            "record": {"table": "x"},
            "values": {"f": f"{big} USD"},
        },
        "b": {
            "evidence_id": "b",
            "record": {"table": "x"},
            "values": {"f": "0.01 USD"},
        },
    }
    ok = {"a": True, "b": True}

    def c(op: str, operands: list[tuple[str, str]], result: str) -> Packet:
        return {
            "operation": op,
            "result": result,
            "operands": [
                {"evidence_id": e, "field": "f", "amount": a} for e, a in operands
            ],
        }

    a, b = ("a", f"{big} USD"), ("b", "0.01 USD")
    exact = "1000000000000000000000000000.02 USD"
    rounded = "1000000000000000000000000000.00 USD"  # what 28 digits would give
    assert D.calculation_exact(c("sum", [a, b], exact), ev, ok)
    assert not D.calculation_exact(c("sum", [a, b], rounded), ev, ok)
    assert D.calculation_exact(c("sum", [a], f"{big} USD"), ev, ok)  # 0 + a is a
    assert D.calculation_exact(c("difference", [a, a, b], "-0.01 USD"), ev, ok)
    assert not D.calculation_exact(c("difference", [a, a, b], "0.00 USD"), ev, ok)


def test_amounts_beyond_the_money_type_are_rejected_not_rounded() -> None:
    huge = "1" + "0" * 60 + ".01"
    ev = {
        "a": {
            "evidence_id": "a",
            "record": {"table": "x"},
            "values": {"f": f"{huge} USD"},
        },
        "b": {
            "evidence_id": "b",
            "record": {"table": "x"},
            "values": {"f": "1.00 USD"},
        },
    }
    ops = [
        {"evidence_id": "a", "field": "f", "amount": f"{huge} USD"},
        {"evidence_id": "b", "field": "f", "amount": "1.00 USD"},
    ]
    calc_ = {"operation": "sum", "operands": ops, "result": f"{huge} USD"}
    assert not D.calculation_exact(calc_, ev, {"a": True, "b": True})


def test_journal_totals_are_exact_at_the_digit_limit() -> None:
    big = "1000000000000000000000000000.01"
    lines = [jl("P", "payment", big, "0"), jl("P", "payment", "0", big)]
    assert D.journal_totals(lines, "P", "payment") == (
        "USD",
        Decimal(big),
        Decimal(big),
    )
    # a one-cent imbalance below the 28th digit is an imbalance, not a rounding
    off = [
        jl("P", "payment", big, "0"),
        jl("P", "payment", "0", "1000000000000000000000000000.00"),
    ]
    assert not D.journal_totals(off, "P", "payment")


# ------------------------------------------- F8: identity, fields, dates


def test_a_citation_of_another_snapshot_does_not_resolve(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    e = next(e for e in packet["evidence"] if e["record"]["table"] == "payments")
    assert D.evidence_resolves(e, snap)
    other = copy.deepcopy(e)
    other["record"]["snapshot"] = "0" * 64
    assert not D.evidence_resolves(other, snap)
    p = copy.deepcopy(packet)
    for x in p["evidence"]:
        if x["evidence_id"] == e["evidence_id"]:
            x["record"]["snapshot"] = "0" * 64
    assert score("weekend_clearing", p, snap)["primary"] == 0


def test_journal_citation_validates_every_supplied_field(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    e = next(e for e in packet["evidence"] if e["record"]["table"] == "gl_entries")
    assert D.evidence_resolves(e, snap)
    for part, key, value in (
        ("values", "invented", "1"),
        ("timestamps", "posting_date", "2020-01-01"),
        ("timestamps", "invented", "2026-01-01"),
        ("values", "currency", "USD"),
    ):
        bad = copy.deepcopy(e)
        bad[part][key] = value
        assert not D.evidence_resolves(bad, snap)


def test_record_timestamps_must_be_the_snapshots(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    e = next(e for e in packet["evidence"] if e["record"]["table"] == "payments")
    assert e["timestamps"]
    for key in e["timestamps"]:
        bad = copy.deepcopy(e)
        bad["timestamps"][key] = "2000-01-01"
        assert not D.evidence_resolves(bad, snap)
    bad = copy.deepcopy(e)
    bad["timestamps"]["not_a_column"] = "2026-01-01"
    assert not D.evidence_resolves(bad, snap)


def test_snapshot_day_honours_the_requested_field(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    journal = next(
        e for e in packet["evidence"] if e["record"]["table"] == "gl_entries"
    )
    pay = next(e for e in packet["evidence"] if e["record"]["table"] == "payments")
    assert isinstance(D._snapshot_day(journal, "posting_date", snap), date)
    assert D._snapshot_day(journal, "no_such_field", snap) is None
    assert D._snapshot_day(journal, "debit", snap) is None
    assert D._snapshot_day(pay, "payment_date", snap) is not None
    assert D._snapshot_day(pay, "payment_amount", snap) is None
    assert D._snapshot_day(pay, "no_such_field", snap) is None


def test_a_journal_day_count_with_an_invented_field_is_not_exact(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets["weekend_clearing"]
    by_id = {e["evidence_id"]: e for e in packet["evidence"]}
    journal = next(
        e for e in packet["evidence"] if e["record"]["table"] == "gl_entries"
    )
    day = D._snapshot_day(journal, "posting_date", snap)
    assert day is not None
    d = {
        "calculation_id": "x",
        "start_evidence": journal["evidence_id"],
        "start_field": "invented_date",
        "end_evidence": None,
        "end_field": None,
        "result": 0,
        "limit": 3,
    }
    ok = {i: True for i in by_id}
    assert not D.day_count_exact(d, by_id, ok, snap, day)
    d["start_field"] = "posting_date"
    assert D.day_count_exact(d, by_id, ok, snap, day)


def test_the_snapshot_is_the_runs_digest(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    packet, snap = packets["weekend_clearing"]
    other = D.Snap(Path(snap.con.execute("PRAGMA database_list").fetchone()[2]), "x")
    assert check(packet, snap)["complete"]
    assert not check(packet, other)["complete"]
    assert sqlite3.sqlite_version
