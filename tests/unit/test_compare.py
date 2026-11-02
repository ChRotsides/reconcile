"""Paired comparison of two scored runs (evaluation/compare.py)."""

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
compare = importlib.import_module("compare")

Row = dict[str, Any]


def inv(
    cid: str,
    cluster: str | None,
    outcome: str,
    complete: bool | None,
    legit: bool = False,
    kind: str | None = None,
    group: str = "cohort",
) -> Row:
    r: Row = {
        "case_id": cid,
        "split": "validation",
        "group": group,
        "outcome": outcome,
        "complete": complete,
        "kind": kind,
        "failure_type": "NO_FAILURE" if legit else "F06_INVOICE_PAID_TWICE",
    }
    if cluster is not None:
        r["group_vendor_id"] = cluster
    return r


def base(
    cid: str,
    cluster: str | None,
    correct: bool,
    complete: bool,
    legit: bool = False,
    kind: str | None = None,
    group: str = "cohort",
) -> Row:
    r: Row = {
        "case_id": cid,
        "split": "validation",
        "group": group,
        "correct": correct,
        "complete": complete,
        "kind": kind,
        "failure_type": "NO_FAILURE" if legit else "F06_INVOICE_PAID_TWICE",
    }
    if cluster is not None:
        r["group_vendor_id"] = cluster
    return r


def by_id(rows: list[Row]) -> dict[str, Row]:
    return {r["case_id"]: r for r in rows}


def test_success_and_false_alarm_both_shapes() -> None:
    a = by_id(
        [
            inv("c1", "v1", "correct", True),
            inv("c2", "v1", "correct", False),  # correct but evidence incomplete
            inv("c3", "v1", "abstained", None),
            inv("c4", "v2", "wrong", True, legit=True, kind="false_alarm"),
            inv("c5", "v2", "wrong", True, legit=True, kind="wrong_legit_variant"),
            inv("c6", "v2", "correct", True, group="oos_failure"),  # outside view
        ]
    )
    o = compare.outcomes(a, ("cohort",))
    assert set(o) == {"c1", "c2", "c3", "c4", "c5"}
    assert [o[k]["success"] for k in sorted(o)] == [True, False, False, False, False]
    assert [o[k]["false_alarm"] for k in sorted(o)] == [False] * 3 + [True, False]
    b = by_id(
        [
            base("c1", "v1", True, True),
            base("c2", "v1", True, False),
            base("c3", "v1", False, False, kind="wrong_failure_class"),
            base("c4", "v2", False, False, legit=True, kind="false_alarm"),
            base("c5", "v2", True, True, legit=True),
        ]
    )
    o = compare.outcomes(b, ("cohort",))
    assert [o[k]["success"] for k in sorted(o)] == [True, False, False, False, True]
    assert [o[k]["false_alarm"] for k in sorted(o)] == [False] * 3 + [True, False]
    # a false_alarm kind on a failure-labelled case is not a false alarm
    odd = by_id([base("x", "v", False, False, kind="false_alarm")])
    assert not compare.outcomes(odd, ("cohort",))["x"]["false_alarm"]


def test_missing_cluster_refused() -> None:
    with pytest.raises(ValueError, match="c2: missing group_vendor_id"):
        compare.outcomes(
            by_id([inv("c1", "v", "correct", True), inv("c2", None, "correct", True)]),
            ("cohort",),
        )


def test_unknown_row_shape_refused() -> None:
    with pytest.raises(ValueError, match="neither"):
        compare.is_success({"case_id": "c1"})


def run(a: list[Row], b: list[Row], **kw: Any) -> dict[str, Any]:
    args = {
        "split": "validation",
        "view": "all_cases",
        "seed": 1,
        "reps": 200,
        "alpha": 0.05,
        "min_clusters": 2,
    } | kw
    r: dict[str, Any] = compare.compare(by_id(a), by_id(b), **args)
    return r


def toy() -> tuple[list[Row], list[Row]]:
    # clusters v1: c1 c2 c3, v2: c4 c5 c6 ; c4..c6 legitimate
    a = [
        inv("c1", "v1", "correct", True),
        inv("c2", "v1", "correct", True),
        inv("c3", "v1", "abstained", None),
        inv("c4", "v2", "correct", True, legit=True),
        inv("c5", "v2", "wrong", True, legit=True, kind="false_alarm"),
        inv("c6", "v2", "correct", True, legit=True),
    ]
    b = [
        base("c1", "v1", True, True),
        base("c2", "v1", False, False),
        base("c3", "v1", True, True),
        base("c4", "v2", False, False, legit=True, kind="false_alarm"),
        base("c5", "v2", False, False, legit=True, kind="false_alarm"),
        base("c6", "v2", True, True, legit=True),
    ]
    return a, b


def test_end_to_end_toy_counts() -> None:
    r = run(*toy())
    assert (r["n_cases"], r["n_clusters"], r["n_legitimate_cases"]) == (6, 2, 3)
    p = r["primary_success"]
    # A succeeds on c1 c2 c4 c6 = 4 (c5 is wrong); B on c1 c3 c6 = 3.
    # both c1 c6; only_a c2 c4; only_b c3; neither c5.
    assert p["a"] == {"numerator": 4, "denominator": 6}
    assert p["b"] == {"numerator": 3, "denominator": 6}
    assert (
        p["paired_counts"]["both"],
        p["paired_counts"]["only_a"],
        p["paired_counts"]["only_b"],
        p["paired_counts"]["neither"],
    ) == (2, 2, 1, 1)
    f = r["false_alarm"]
    # false alarms over c4 c5 c6: A c5 = 1/3; B c4 c5 = 2/3; only_a none, only_b c4.
    assert f["a"] == {"numerator": 1, "denominator": 3}
    assert f["b"] == {"numerator": 2, "denominator": 3}
    assert (f["paired_counts"]["only_a"], f["paired_counts"]["only_b"]) == (0, 1)
    assert p["bootstrap"]["difference"] == pytest.approx(1 / 6)
    assert f["bootstrap"]["difference"] == pytest.approx(-1 / 3)
    assert p["bootstrap"]["seed"] == 1 and p["bootstrap"]["reps"] == 200
    json.dumps(r)


def test_view_and_split_restrict_cases() -> None:
    a, b = toy()
    a.append(inv("c7", "v3", "correct", True, group="label_conflict"))
    b.append(base("c7", "v3", True, True, group="label_conflict"))
    a.append(inv("c8", "v3", "correct", True, group="oos_failure"))  # never in a view
    b.append(base("c8", "v3", True, True, group="oos_failure"))
    other = inv("c9", "v9", "correct", True)
    other["split"] = "train"  # only in A, but another split
    a.append(other)
    assert run(a, b)["n_cases"] == 7
    assert run(a, b, view="sensitivity_without_verified_conflicts")["n_cases"] == 6


def test_mismatched_case_sets_refused() -> None:
    a, b = toy()
    with pytest.raises(ValueError, match="case sets differ"):
        run(a, b[:-1])


def test_mismatched_group_or_cluster_refused() -> None:
    a, b = toy()
    b[0]["group"] = "label_conflict"
    with pytest.raises(ValueError, match="c1: group differs"):
        run(a, b)
    a, b = toy()
    b[0]["group_vendor_id"] = "other"
    with pytest.raises(ValueError, match="c1: cluster differs"):
        run(a, b)


def test_missing_split_refused() -> None:
    a, b = toy()
    del b[0]["split"]
    with pytest.raises(ValueError, match="B: c1 has no 'split'"):
        run(a, b)


def test_load_round_trip_and_duplicates(tmp_path: Path) -> None:
    p = tmp_path / "cases.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in toy()[0]) + "\n\n")
    assert sorted(compare.load(p)) == ["c1", "c2", "c3", "c4", "c5", "c6"]
    p.write_text(json.dumps(toy()[0][0]) + "\n" + json.dumps(toy()[0][0]) + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        compare.load(p)
