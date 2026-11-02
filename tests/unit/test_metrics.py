"""The shared metrics module (evaluation/metrics.py, PLAN.md section 7): hand-
calculated examples, empty denominators, failed-case accounting, nearest-rank
percentiles, and the packet audit that feeds citation/claim/monetary metrics."""

import ast
import importlib
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
M = importlib.import_module("metrics")
D = importlib.import_module("dev_run")

SCORERS = [
    ROOT / "evaluation" / "dev_run.py",
    ROOT / "evaluation" / "baselines" / "observable" / "run.py",
]
METRIC_FUNCTIONS = {
    node.name
    for node in ast.parse((ROOT / "evaluation" / "metrics.py").read_text()).body
    if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
}


@pytest.mark.parametrize("path", SCORERS, ids=lambda p: p.name)
def test_scorers_use_the_shared_metrics_instead_of_defining_them(path: Path) -> None:
    tree = ast.parse(path.read_text())
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"frac", "classify", "cohort_metrics", "per_label"} <= METRIC_FUNCTIONS
    assert not defined & METRIC_FUNCTIONS
    imported = {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert "metrics" in imported


def row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "group": "cohort",
        "failure_type": "F12",
        "variant": "x",
        "difficulty": "easy",
        "disposition": "answered",
        "reason": None,
        "category": "payment_missing_from_bank",
        "expected_category": "payment_missing_from_bank",
        "searches_mismatch": [],
    }
    r = base | kw
    M.classify(r)
    return r


def test_frac_empty_denominator_has_no_rate() -> None:
    assert M.frac(0, 0) == {"n": 0, "d": 0, "rate": None}
    assert M.frac(1, 3) == {"n": 1, "d": 3, "rate": 0.3333}


def test_classify_outcomes() -> None:
    assert row()["outcome"] == "correct"
    wrong = row(category="legitimate_batch_payment")
    assert (wrong["outcome"], wrong["kind"]) == ("wrong", "failure_called_no_failure")
    fa = row(
        failure_type="NO_FAILURE",
        expected_category="legitimate_batch_payment",
    )
    assert (fa["outcome"], fa["kind"]) == ("wrong", "false_alarm")
    assert row(disposition="failed", category=None)["outcome"] == "failed"
    fx = row(group="fx", disposition="abstained", reason="unsupported_fx")
    assert fx["outcome"] == "correct_abstention"


# Six cohort cases used by several tests (expected values computed by hand).
# Packet counts are req met/total, cited resolved/total, findings with valid
# references/total, calcs exact/total:
#   A F12 answered correct, complete       easy
#      req 4/4, cited 5/5, findings 3/3, calcs 2/2
#   B F12 answered legit category (wrong)  hard
#      req 2/3, cited 3/4, findings 1/2, calcs 1/3
#   C legit v1 answered a failure          easy    (no packet counts)
#   D legit v2 answered correct, incomplete medium all packet counts 0/0
#   E F12 failed
#   F F12 abstained (retrieval_row_limit)
def cases() -> list[dict[str, Any]]:
    legit1 = {"failure_type": "NO_FAILURE", "expected_category": "legitimate_a"}
    return [
        row(
            complete=True,
            req_total=4,
            req_met=4,
            cited_total=5,
            cited_resolved=5,
            findings=3,
            findings_reference_ok=3,
            calcs=2,
            calcs_exact=2,
        ),
        row(
            category="legitimate_a",
            difficulty="hard",
            req_total=3,
            req_met=2,
            cited_total=4,
            cited_resolved=3,
            findings=2,
            findings_reference_ok=1,
            calcs=3,
            calcs_exact=1,
        ),
        row(**legit1, variant="v1", category="payment_missing_from_bank"),
        row(
            **(legit1 | {"expected_category": "legitimate_b"}),
            variant="v2",
            category="legitimate_b",
            difficulty="medium",
            complete=False,
            req_total=0,
            req_met=0,
            cited_total=0,
            cited_resolved=0,
            findings=0,
            findings_reference_ok=0,
            calcs=0,
            calcs_exact=0,
        ),
        row(disposition="failed", category=None),
        row(
            disposition="abstained",
            reason="retrieval_row_limit",
            category=None,
            req_total=9,
            req_met=0,
        ),
    ]


def test_cohort_metrics_hand_calculated() -> None:
    m = M.cohort_metrics(cases())
    assert m["cases"] == 6
    assert m["answered"] == M.frac(4, 6)
    assert m["failed"] == M.frac(1, 6)  # a failure is not an answer and not a success
    assert m["abstained"] == M.frac(1, 6)
    assert m["answer_precision"] == M.frac(2, 4)  # A and D
    assert m["failure_vs_no_failure_accuracy_answered"] == M.frac(2, 4)  # A, D
    assert m["correct_and_evidence_complete"] == M.frac(1, 6)  # only A
    assert m["evidence_complete_among_correct"] == M.frac(1, 2)
    assert m["false_alarms"] == M.frac(1, 2)  # C of legit C, D
    assert m["wrong_by_kind"] == {"failure_called_no_failure": 1, "false_alarm": 1}


def test_cohort_metrics_without_answers_has_no_rates() -> None:
    m = M.cohort_metrics([row(disposition="failed", category=None)])
    assert m["answer_precision"]["rate"] is None
    assert m["correct_and_evidence_complete"] == M.frac(0, 1)  # failure = no success
    assert M.cohort_metrics([])["answered"]["rate"] is None


def test_per_label_confusion_all_clear() -> None:
    rows = cases()
    pl = M.per_label(rows, ("cohort",))
    assert pl["F12"]["cases"] == 4 and pl["F12"]["failed"] == 1
    assert pl["F12"]["answered_categories"] == {
        "payment_missing_from_bank": 1,
        "legitimate_a": 1,
    }
    assert M.confusion(rows, ("cohort",))["F12"] == {
        "payment_missing_from_bank": 1,
        "legitimate_a": 1,
        "failed": 1,
        "abstained": 1,
    }
    oos = [
        row(group="oos_failure", failure_type="F01", category="legitimate_a"),
        row(group="oos_failure", failure_type="F01", disposition="abstained"),
        row(group="oos_failure", failure_type="F02", category="other_failure"),
    ]
    risk = M.all_clear_risk(oos)
    assert risk["overall"] == M.frac(1, 3)
    assert risk["per_family"] == {"F01": M.frac(1, 2), "F02": M.frac(0, 1)}


def test_root_cause_accuracy_by_family_and_difficulty() -> None:
    rows = cases()
    fam = M.root_cause_accuracy_by(rows, "failure_type")
    # F12: A, B answered, E failed, F abstained -> 4 cases, 1 correct
    assert fam["F12"]["correct_over_cases"] == M.frac(1, 4)
    assert fam["F12"]["correct_over_answered"] == M.frac(1, 2)
    assert fam["NO_FAILURE"]["correct_over_cases"] == M.frac(1, 2)
    diff = M.root_cause_accuracy_by(rows, "difficulty")
    # easy: A, C, E, F -> correct A only; hard: B; medium: D
    assert diff["easy"]["correct_over_cases"] == M.frac(1, 4)
    assert diff["hard"]["correct_over_answered"] == M.frac(0, 1)
    assert diff["medium"]["correct_over_cases"] == M.frac(1, 1)
    # a missing field and an empty group are not invented
    assert "unknown" in M.root_cause_accuracy_by([row(difficulty=None)], "difficulty")
    assert M.root_cause_accuracy_by([], "difficulty") == {}


def test_packet_metrics_hand_calculated() -> None:
    rows = cases()  # only answered rows count; the abstained row's req is ignored
    assert M.evidence_recall(rows) == M.frac(6, 7)  # (4+2+0) / (4+3+0)
    assert M.citation_resolution(rows) == M.frac(8, 9)  # (5+3+0) / (5+4+0)
    ri = M.reference_integrity(rows)
    assert ri["invalid_references_over_findings"] == M.frac(1, 5)  # 3+2 findings, 4 ok
    assert ri["valid_references_over_findings"] == M.frac(4, 5)
    assert "claim_support" not in dir(M)  # the measure does not support claims
    mc = M.monetary_correctness(rows)
    assert mc["exact_over_reported"] == M.frac(3, 5)  # (2+1) / (2+3)
    # coverage: answered with >= 1 calculation: A, B of A, B, C, D (C has none)
    assert mc["calculation_coverage"] == M.frac(2, 4)


def test_packet_metrics_empty_denominators() -> None:
    nothing = [row(disposition="abstained", category=None)]
    assert M.evidence_recall(nothing)["rate"] is None
    assert M.citation_resolution(nothing)["rate"] is None
    assert (
        M.reference_integrity(nothing)["invalid_references_over_findings"]["rate"]
        is None
    )
    assert M.monetary_correctness(nothing)["calculation_coverage"]["rate"] is None
    assert M.monetary_correctness(nothing)["exact_over_reported"]["rate"] is None
    assert M.execution_reliability([])["failed"]["rate"] is None


def test_execution_reliability_counts_attempted_cases() -> None:
    rows = [
        row(),
        row(disposition="failed", category=None),
        row(disposition="abstained", reason="retrieval_time_limit", category=None),
        row(disposition="abstained", reason="retrieval_row_limit", category=None),
        row(disposition="abstained", reason="retrieval_row_limit", category=None),
        row(disposition="abstained", reason="unsupported_fx", category=None),
    ]
    e = M.execution_reliability(rows)
    assert e["failed"] == M.frac(1, 6)
    assert e["abstained_retrieval_time_limit"] == M.frac(1, 6)
    assert e["abstained_retrieval_row_limit"] == M.frac(2, 6)


def test_false_alarms_by_variant_and_predicted_family() -> None:
    def legit(variant: str, category: str) -> dict[str, Any]:
        return row(
            failure_type="NO_FAILURE",
            variant=variant,
            expected_category="legitimate_z",
            category=category,
        )

    rows = [
        legit("v1", "fam_a"),
        legit("v1", "legitimate_z"),
        legit("v2", "fam_b"),
        legit("v3", "legitimate_z"),
        row(),  # a real failure case is not a legitimate case
    ]
    fa = M.false_alarms_by(rows)
    assert fa["by_variant"] == {
        "v1": M.frac(1, 2),
        "v2": M.frac(1, 1),
        "v3": M.frac(0, 1),
    }
    assert fa["by_predicted_family"] == {"fam_a": M.frac(1, 4), "fam_b": M.frac(1, 4)}
    assert M.false_alarms_by([]) == {"by_variant": {}, "by_predicted_family": {}}


def test_false_alarm_is_group_independent() -> None:
    groups = ("cohort", "label_conflict", "fx", "contradicted", "not_gradable")
    rows = [
        row(
            failure_type="NO_FAILURE",
            group=g,
            variant="v1",
            expected_category="legitimate_z",
            category="F06",
            reason=None,
        )
        for g in (*groups, "oos_legit")
    ]
    assert all(M.is_false_alarm(r) for r in rows)
    # kind is not what decides it: contradicted/not_gradable never get one
    assert [r.get("kind") for r in rows if r["group"] in groups[3:]] != [
        "false_alarm"
    ] * 2
    m = M.cohort_metrics(rows, groups)
    assert m["false_alarms"] == M.frac(len(groups), len(groups))
    assert M.false_alarms_by(rows)["by_variant"] == {"v1": M.frac(6, 6)}
    assert M.false_alarms_by(rows)["by_predicted_family"] == {"F06": M.frac(6, 6)}
    # not false alarms: abstained, legitimate category, failure label,
    # baseline answer-all row without disposition counts as answered
    ok = dict(rows[0])
    assert not M.is_false_alarm(ok | {"disposition": "abstained"})
    assert not M.is_false_alarm(ok | {"category": "legitimate_z"})
    assert not M.is_false_alarm(ok | {"failure_type": "F06_INVOICE_PAID_TWICE"})
    del ok["disposition"]
    assert M.is_false_alarm(ok)


def test_nearest_rank_percentiles() -> None:
    xs = [50.0, 15.0, 40.0, 20.0, 35.0]  # unsorted on purpose
    assert M.nearest_rank(xs, 50) == 35.0  # ceil(0.5*5) = rank 3
    assert M.nearest_rank(xs, 30) == 20.0  # ceil(1.5) = rank 2
    assert M.nearest_rank(xs, 95) == 50.0  # ceil(4.75) = rank 5
    assert M.nearest_rank(xs, 0) == 15.0  # rank clamps to 1
    hundred = [float(i) for i in range(1, 101)]
    assert M.nearest_rank(hundred, 95) == 95.0
    assert M.nearest_rank(hundred, 50) == 50.0
    assert M.nearest_rank([], 50) is None


def test_efficiency_hand_calculated() -> None:
    e = M.efficiency([1.0, 2.0, 3.0, 4.0], 2048, Decimal("0.05"), correct_complete=2)
    assert e["attempted"] == 4
    assert e["latency_p50_s"] == 2.0  # rank ceil(2.0) = 2
    assert e["latency_p95_s"] == 4.0  # rank ceil(3.8) = 4
    assert e["throughput_cases_per_s"] == pytest.approx(0.4)  # 4 cases / 10 s
    assert e["peak_rss_kib"] == 2048
    assert e["cost_per_attempted_case"] == Decimal("0.05")
    # total cost 0.20 over 2 correct complete packets
    assert e["cost_per_correct_complete_packet"] == Decimal("0.10")


def test_efficiency_empty_and_no_success() -> None:
    e = M.efficiency([], None, Decimal("0"))
    assert e["latency_p50_s"] is None and e["throughput_cases_per_s"] is None
    assert e["cost_per_correct_complete_packet"] is None
    assert e["peak_rss_kib"] is None
    none_ok = M.efficiency([1.0], 1, Decimal("0.01"))
    assert none_ok["cost_per_correct_complete_packet"] is None


def test_precision_coverage_curve() -> None:
    pairs = [(0.9, True), (0.8, False), (0.6, True), (0.5, True)]
    curve = M.precision_coverage_curve(pairs, [0.5, 0.7, 0.95])
    assert curve[0] == {
        "threshold": 0.5,
        "coverage": M.frac(4, 4),
        "precision": M.frac(3, 4),
    }
    assert curve[1]["coverage"] == M.frac(2, 4)  # 0.9 and 0.8
    assert curve[1]["precision"] == M.frac(1, 2)
    assert curve[2]["coverage"] == M.frac(0, 4)  # nothing kept: no precision
    assert curve[2]["precision"]["rate"] is None
    assert M.precision_coverage_curve([], [0.5])[0]["coverage"]["rate"] is None


# ------------------------------------------------------------- packet audit


@pytest.fixture
def snap(tmp_path: Path) -> Any:
    path = tmp_path / "snapshot.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE payments (payment_id TEXT, payment_date TEXT,
          payment_currency TEXT, payment_amount TEXT, payment_status TEXT);
        CREATE TABLE bank_transactions (bank_transaction_id TEXT, posted_date TEXT,
          amount TEXT, currency TEXT);
        CREATE TABLE payment_allocations (payment_id TEXT, invoice_id TEXT,
          allocated_amount TEXT, allocation_date TEXT);
        CREATE TABLE gl_entries (journal_id TEXT, journal_line_id TEXT,
          source_transaction_id TEXT, transaction_type TEXT, posting_date TEXT,
          debit TEXT, credit TEXT, currency TEXT);
        INSERT INTO payments VALUES ('P1', '2025-01-03', 'USD', '100.00', 'completed');
        INSERT INTO bank_transactions VALUES ('B1', '2025-01-08', '100.00', 'USD');
        INSERT INTO payment_allocations VALUES ('P1', 'I1', '60.00', '2025-01-03');
        INSERT INTO payment_allocations VALUES ('P1', 'I2', '40.00', '2025-01-03');
        INSERT INTO gl_entries VALUES ('J1', 'L1', 'P1', 'payment', '2025-01-03',
          '100.00', '0.00', 'USD');
        INSERT INTO gl_entries VALUES ('J1', 'L2', 'X9', 'other', '2025-01-03',
          '5.00', '0.00', 'USD');
        INSERT INTO gl_entries VALUES ('J1', 'L3', 'P1', 'payment', '2025-01-03',
          '0.00', '100.00', 'USD');
        INSERT INTO gl_entries VALUES ('J1', 'L4', 'X9', 'other', '2025-01-03',
          '0.00', '5.00', 'USD');
        """
    )
    con.commit()
    con.close()
    return D.Snap(path, "s")


def ev(eid: str, table: str, rid: str, values: dict[str, str]) -> dict[str, Any]:
    return {
        "evidence_id": eid,
        "record": {"table": table, "record_id": rid, "snapshot": "s"},
        "search": None,
        "values": values,
    }


PAY = ev(
    "pay",
    "payments",
    "P1",
    {"payment_amount": "100.00 USD", "payment_currency": "USD"},
)
A1 = ev("a1", "payment_allocations", "P1|I1", {"allocated_amount": "60.00 USD"})
A2 = ev("a2", "payment_allocations", "P1|I2", {"allocated_amount": "40.00 USD"})
BANK = ev(
    "bank", "bank_transactions", "B1", {"amount": "100.00 USD", "currency": "USD"}
)


def calc(
    cid: str, op: str, operands: list[tuple[str, str, str]], result: str
) -> dict[str, Any]:
    return {
        "calculation_id": cid,
        "operation": op,
        "operands": [
            {"evidence_id": e, "field": f, "amount": a} for e, f, a in operands
        ],
        "result": result,
        "tolerance": None,
    }


def test_evidence_resolves_against_the_snapshot(snap: Any) -> None:
    assert D.evidence_resolves(PAY, snap)
    assert D.evidence_resolves(A1, snap)
    # equal amount written with other trailing zeros is still the same Decimal
    assert D.evidence_resolves(
        ev("p", "payments", "P1", {"payment_amount": "100.0 USD"}), snap
    )
    assert not D.evidence_resolves(  # one cent off
        ev("p", "payments", "P1", {"payment_amount": "100.01 USD"}), snap
    )
    assert not D.evidence_resolves(  # wrong currency
        ev("p", "payments", "P1", {"payment_amount": "100.00 EUR"}), snap
    )
    assert not D.evidence_resolves(  # no such record
        ev("p", "payments", "P9", {"payment_amount": "100.00 USD"}), snap
    )
    assert not D.evidence_resolves(  # altered text value
        ev("p", "payments", "P1", {"payment_status": "pending"}), snap
    )
    assert not D.evidence_resolves(ev("v", "vendors", "V1", {}), snap)


def test_journal_evidence_resolves_with_attributable_amount(snap: Any) -> None:
    good = {
        "amount": "100.00 USD",
        "journal_total": "105.00 USD",
        "currency": "USD",
        "source": "P1",
        "gl_type": "payment",
    }
    assert D.evidence_resolves(ev("j", "gl_entries", "J1", good), snap)
    # the whole-journal total is not the attributable amount
    bad = good | {"amount": "105.00 USD"}
    assert not D.evidence_resolves(ev("j", "gl_entries", "J1", bad), snap)
    assert not D.evidence_resolves(
        ev("j", "gl_entries", "J1", good | {"source": "P2"}), snap
    )


def test_calculation_exact_recomputes_from_operands() -> None:
    by_id = {e["evidence_id"]: e for e in (PAY, A1, A2)}
    ok = {"pay": True, "a1": True, "a2": True}
    ops = [
        ("pay", "payment_amount", "100.00 USD"),
        ("a1", "allocated_amount", "60.00 USD"),
        ("a2", "allocated_amount", "40.00 USD"),
    ]
    diff = calc("d", "difference", ops, "0.00 USD")
    assert D.calculation_exact(diff, by_id, ok)
    assert not D.calculation_exact(calc("d", "difference", ops, "0.01 USD"), by_id, ok)
    assert not D.calculation_exact(  # an operand whose evidence did not resolve
        diff, by_id, ok | {"a2": False}
    )
    # an operand amount that differs from the cited item's recorded value
    forged = ops[:2] + [("a2", "allocated_amount", "41.00 USD")]
    assert not D.calculation_exact(
        calc("d", "difference", forged, "-1.00 USD"), by_id, ok
    )
    assert D.calculation_exact(calc("s", "sum", ops[1:], "100.00 USD"), by_id, ok)
    assert not D.calculation_exact(
        calc("s", "product", ops[1:], "100.00 USD"), by_id, ok
    )
    assert not D.calculation_exact(calc("s", "sum", [], "0.00 USD"), by_id, ok)
    assert not D.calculation_exact(calc("s", "sum", ops[1:], "100.00 EUR"), by_id, ok)


def test_day_count_exact_counts_weekdays(snap: Any) -> None:
    by_id = {"pay": PAY, "bank": BANK}
    ok = {"pay": True, "bank": True}
    cutoff = D.date(2025, 1, 31)

    def dc(result: int, end: str | None = "bank") -> dict[str, Any]:
        return {
            "calculation_id": "lag",
            "start_evidence": "pay",
            "start_field": "payment_date",
            "end_evidence": end,
            "end_field": "posted_date" if end else None,
            "result": result,
            "limit": 3,
        }

    # Friday 2025-01-03 to Wednesday 2025-01-08: Mon, Tue, Wed = 3 weekdays
    assert D.day_count_exact(dc(3), by_id, ok, snap, cutoff)
    assert not D.day_count_exact(dc(4), by_id, ok, snap, cutoff)
    assert not D.day_count_exact(
        dc(3), by_id, {"pay": True, "bank": False}, snap, cutoff
    )
    # an absent end runs to the cutoff: (Jan 3, Jan 31] has 20 weekdays
    assert D.day_count_exact(dc(20, None), by_id, ok, snap, cutoff)


def test_packet_audit_counts_invalid_references(snap: Any) -> None:
    ops = [
        ("pay", "payment_amount", "100.00 USD"),
        ("a1", "allocated_amount", "60.00 USD"),
    ]
    forged = ev("fp", "payments", "P1", {"payment_amount": "99.00 USD"})
    packet = {
        "request": {"cutoff": "2025-01-31"},
        "evidence": [PAY, A1, forged],
        "calculations": [
            calc("ok", "difference", ops, "40.00 USD"),
            calc("bad", "difference", ops, "41.00 USD"),
        ],
        "day_counts": [],
        "diagnosis": {
            "findings": [
                {"evidence_ids": ["pay", "a1"], "calculation_ids": ["ok"]},
                {"evidence_ids": ["pay"], "calculation_ids": ["bad"]},  # bad calc
                {"evidence_ids": ["pay", "fp"], "calculation_ids": []},  # forged
                {"evidence_ids": ["pay", "nope"], "calculation_ids": []},  # absent id
                {"evidence_ids": ["pay"], "calculation_ids": ["missing"]},  # no calc
                {"evidence_ids": [], "calculation_ids": []},  # cites nothing
            ]
        },
    }
    a = D.packet_audit(packet, snap)
    assert a == {
        "cited_total": 3,
        "cited_resolved": 2,  # the forged item does not resolve
        "findings": 6,
        "findings_reference_ok": 1,  # the vacuous finding establishes nothing
        "calcs": 2,
        "calcs_exact": 1,
        "day_counts": 0,
        "day_counts_exact": 0,
        "searches_mismatch": [],
        "audit_ok": False,
    }
    clean = {
        **packet,
        "evidence": [PAY, A1],
        "calculations": [calc("ok", "difference", ops, "40.00 USD")],
        "diagnosis": {
            "findings": [{"evidence_ids": ["pay", "a1"], "calculation_ids": ["ok"]}]
        },
    }
    assert D.packet_audit(clean, snap)["audit_ok"]
