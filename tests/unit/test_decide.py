"""The executable decision rule (evaluation/decide.py) on hand-built comparisons."""

import copy
import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
decide = importlib.import_module("decide")

PROTOCOL: dict[str, Any] = {
    "protocol": {"version": "1"},
    "splits": {"sealed": "challenge_test"},
    "cohort": {"headline_view": "all_cases"},
    "systems": {"baseline_configurations": ["answer_all", "threshold_0.85"]},
    "decision": {
        "min_primary_difference": 0.05,
        "primary_interval_must_exclude_zero": True,
        "max_false_alarm_difference_upper_bound": 0.0,
    },
    "statistics": {
        "method": "cluster_percentile_bootstrap",
        "seed": 1,
        "reps": 100,
        "alpha": 0.05,
        "min_clusters": 30,
    },
}


PSHA = "P" * 64
GATES = {g: True for g in decide.HARD_GATES}
PROV = {"tag": "t", "code": "c", "protocol_sha256": PSHA, "cases_sha256": "AAA"}


def comp(
    diff: float = 0.10,
    interval: list[float] | None = (0.02, 0.18),  # type: ignore[assignment]
    fa_diff: float = -0.1,
    fa_interval: list[float] | None = (-0.2, -0.01),  # type: ignore[assignment]
    name: str = "answer_all",
    split: str = "validation",
    a_sha: str = "AAA",
    b_sha: str | None = None,
    fa_counts: tuple[int, int] = (1, 3),
) -> dict[str, Any]:
    return {
        "configuration": name,
        "split": split,
        "a_sha256": a_sha,
        "b_sha256": b_sha or f"B-{name}",
        "investigator_provenance": dict(PROV),
        "investigator_hard_gates": dict(GATES),
        "view": "all_cases",
        "method": "cluster_percentile_bootstrap",
        "seed": 1,
        "reps": 100,
        "alpha": 0.05,
        "min_clusters": 30,
        "primary_success": {"bootstrap": {"difference": diff, "interval": interval}},
        "false_alarm": {
            "a": {"numerator": fa_counts[0], "denominator": 10},
            "b": {"numerator": fa_counts[1], "denominator": 10},
            "bootstrap": {"difference": fa_diff, "interval": fa_interval},
        },
    }


def both(**kw: Any) -> dict[str, dict[str, Any]]:
    return {
        "answer_all": comp(),
        "threshold_0.85": comp(name="threshold_0.85", **kw),
    }


def test_holds() -> None:
    r = decide.decide(PROTOCOL, both(), PSHA)
    assert r["verdict"] == "holds"
    assert r["reasons"] == []
    assert r["configurations"]["answer_all"]["primary"]["status"] == "pass"


def test_boundary_values_pass() -> None:
    r = decide.decide(PROTOCOL, both(diff=0.05, fa_interval=[-0.2, 0.0]), PSHA)
    assert r["verdict"] == "holds"


def test_fails_when_primary_below_threshold() -> None:
    r = decide.decide(PROTOCOL, both(diff=0.049), PSHA)
    assert r["verdict"] == "fails"
    assert r["configurations"]["threshold_0.85"]["primary"]["status"] == "fail"
    assert r["configurations"]["answer_all"]["primary"]["status"] == "pass"


def test_fails_when_interval_touches_zero() -> None:
    r = decide.decide(PROTOCOL, both(interval=[0.0, 0.2]), PSHA)
    assert r["verdict"] == "fails"
    assert "lower bound" in r["reasons"][0]


def test_fails_when_false_alarm_upper_bound_positive() -> None:
    r = decide.decide(PROTOCOL, both(fa_interval=[-0.2, 0.001]), PSHA)
    assert r["verdict"] == "fails"
    assert r["configurations"]["threshold_0.85"]["false_alarm"]["status"] == "fail"


def test_not_established_on_too_few_clusters() -> None:
    r = decide.decide(PROTOCOL, both(interval=None, fa_interval=None), PSHA)
    assert r["verdict"] == "not_established"
    assert len(r["reasons"]) == 2


def test_fail_outranks_not_established() -> None:
    r = decide.decide(PROTOCOL, both(diff=0.01, fa_interval=None), PSHA)
    assert r["verdict"] == "fails"


def test_point_difference_alone_never_passes_without_interval() -> None:
    r = decide.decide(PROTOCOL, both(diff=0.5, interval=None), PSHA)
    assert r["configurations"]["threshold_0.85"]["primary"]["status"] == (
        "not_established"
    )
    assert r["verdict"] == "not_established"


def test_refuses_missing_and_extra_configuration() -> None:
    with pytest.raises(ValueError, match="missing"):
        decide.decide(PROTOCOL, {"answer_all": comp()}, PSHA)
    with pytest.raises(ValueError, match="extra"):
        decide.decide(PROTOCOL, both() | {"other": comp(name="other")}, PSHA)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("alpha", 0.1),
        ("seed", 2),
        ("reps", 99),
        ("method", "other"),
        ("min_clusters", 1),
        ("view", "sensitivity_without_verified_conflicts"),
    ],
)
def test_refuses_mismatched_statistics_or_view(key: str, value: Any) -> None:
    comps = copy.deepcopy(both())
    comps["threshold_0.85"][key] = value
    with pytest.raises(ValueError, match=key):
        decide.decide(PROTOCOL, comps, PSHA)


def test_refuses_unknown_protocol_configuration() -> None:
    proto = copy.deepcopy(PROTOCOL)
    proto["systems"]["baseline_configurations"] = ["answer_all", "threshold_0.9"]
    with pytest.raises(ValueError, match="unknown"):
        decide.decide(proto, both(), PSHA)


def test_empty_populations_are_not_established() -> None:
    nones = {"difference": None, "interval": None}
    for field in ("primary_success", "false_alarm"):
        comps = both()
        comps["threshold_0.85"][field]["bootstrap"] = nones
        r = decide.decide(PROTOCOL, comps, PSHA)
        check = r["configurations"]["threshold_0.85"][
            "primary" if field == "primary_success" else "false_alarm"
        ]
        assert check["status"] == "not_established"
        assert "empty" in check["reason"]
        assert r["verdict"] == "not_established"


def test_refuses_wrong_configuration_name() -> None:
    comps = both()
    comps["threshold_0.85"]["configuration"] = "answer_all"
    with pytest.raises(ValueError, match="configuration"):
        decide.decide(PROTOCOL, comps, PSHA)


def test_refuses_different_investigator_runs() -> None:
    comps = both()
    comps["threshold_0.85"]["a_sha256"] = "OTHER"
    with pytest.raises(ValueError, match="investigator"):
        decide.decide(PROTOCOL, comps, PSHA)


def test_refuses_same_baseline_for_two_configurations() -> None:
    comps = both()
    comps["threshold_0.85"]["b_sha256"] = comps["answer_all"]["b_sha256"]
    with pytest.raises(ValueError, match="share one baseline"):
        decide.decide(PROTOCOL, comps, PSHA)


def test_refuses_different_splits_and_missing_provenance() -> None:
    comps = both()
    comps["threshold_0.85"]["split"] = "train"
    with pytest.raises(ValueError, match="different splits"):
        decide.decide(PROTOCOL, comps, PSHA)
    comps = both()
    del comps["answer_all"]["a_sha256"]
    with pytest.raises(ValueError, match="no a_sha256"):
        decide.decide(PROTOCOL, comps, PSHA)


def test_mode_and_provenance_recorded() -> None:
    r = decide.decide(PROTOCOL, both(), PSHA)
    assert r["mode"] == "rehearsal"
    assert r["split"] == "validation"
    assert r["digests"]["answer_all"] == {"a_sha256": "AAA", "b_sha256": "B-answer_all"}
    sealed = {n: c | {"split": "challenge_test"} for n, c in both().items()}
    assert decide.decide(PROTOCOL, sealed, PSHA)["mode"] == "release"


def test_false_alarm_criterion_wording_and_zero_note() -> None:
    r = decide.decide(PROTOCOL, both(), PSHA)
    fa = r["configurations"]["threshold_0.85"]["false_alarm"]
    assert fa["criterion"] == "no observed increase (descriptive interval)"
    assert "note" not in fa
    r = decide.decide(PROTOCOL, both(fa_counts=(0, 0)), PSHA)
    note = r["configurations"]["threshold_0.85"]["false_alarm"]["note"]
    assert note == (
        "neither system raised a false alarm; no statistical assurance about rates"
    )


def sealed(gates: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    comps = {n: c | {"split": "challenge_test"} for n, c in both().items()}
    if gates is not None:
        for c in comps.values():
            c["investigator_hard_gates"] = gates
    return comps


def test_release_holds_with_all_gates_true() -> None:
    r = decide.decide(PROTOCOL, sealed(), PSHA)
    assert r["verdict"] == "holds"
    assert r["hard_gates"] == GATES


@pytest.mark.parametrize("gate", decide.HARD_GATES)
def test_release_fails_on_any_false_gate(gate: str) -> None:
    r = decide.decide(PROTOCOL, sealed(GATES | {gate: False}), PSHA)
    assert r["verdict"] == "fails"
    assert any(gate in x and "false" in x for x in r["reasons"])


def test_release_fails_on_missing_gate() -> None:
    gates = dict(GATES)
    del gates["all_citations_resolve"]
    r = decide.decide(PROTOCOL, sealed(gates), PSHA)
    assert r["verdict"] == "fails"
    assert any("all_citations_resolve: missing" in x for x in r["reasons"])
    comps = sealed()
    del comps["answer_all"]["investigator_hard_gates"]
    assert decide.decide(PROTOCOL, comps, PSHA)["verdict"] == "fails"


def test_rehearsal_reports_gates_without_changing_verdict() -> None:
    comps = both()
    for c in comps.values():
        c["investigator_hard_gates"] = GATES | {"absence_check_passed": False}
    r = decide.decide(PROTOCOL, comps, PSHA)
    assert r["mode"] == "rehearsal"
    assert r["verdict"] == "holds"
    assert r["hard_gates"]["absence_check_passed"] is False


def test_refuses_provenance_mismatch() -> None:
    comps = both()
    comps["threshold_0.85"]["investigator_provenance"] = PROV | {"code": "other"}
    with pytest.raises(ValueError, match="different investigator_provenance"):
        decide.decide(PROTOCOL, comps, PSHA)


def test_refuses_protocol_hash_mismatch_and_missing_provenance() -> None:
    with pytest.raises(ValueError, match="protocol sha256"):
        decide.decide(PROTOCOL, both(), "Q" * 64)
    comps = both()
    del comps["answer_all"]["investigator_provenance"]
    with pytest.raises(ValueError, match="no investigator_provenance"):
        decide.decide(PROTOCOL, comps, PSHA)
