"""Paired comparison and vendor-cluster bootstrap (evaluation/paired.py)."""

import importlib
import json
import random
import sys
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
paired = importlib.import_module("paired")

Case = tuple[str, bool, bool]


def boot(cases: list[Case], seed: int = 1, reps: int = 400) -> dict[str, object]:
    return cast(dict[str, object], paired.cluster_bootstrap(cases, seed, reps, 0.05, 2))


def test_paired_counts() -> None:
    a = {"c1": True, "c2": True, "c3": False, "c4": False, "c5": True}
    b = {"c1": True, "c2": False, "c3": True, "c4": False, "c5": False}
    # both: c1; only_a: c2, c5; only_b: c3; neither: c4. n = 5.
    # successes a = 3, b = 2; difference = (2 - 1) / 5 = 0.2.
    r = paired.paired_counts(a, b)
    assert r == {
        "n": 5,
        "successes_a": 3,
        "successes_b": 2,
        "both": 1,
        "only_a": 2,
        "only_b": 1,
        "neither": 1,
        "difference": 0.2,
    }


def test_paired_counts_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="a has 2, b has 1, common 1"):
        paired.paired_counts({"x": True, "y": False}, {"x": True})
    with pytest.raises(ValueError):
        paired.paired_counts({"x": True}, {"z": True})  # same size, other ids


def test_point_difference_by_hand() -> None:
    # Cluster A: (T,F), (T,T); cluster B: (F,F).
    # sum_a = 2, sum_b = 1, n = 3 -> 2/3 - 1/3 = 1/3.
    cases: list[Case] = [("A", True, False), ("A", True, True), ("B", False, False)]
    r = boot(cases)
    assert r["difference"] == pytest.approx(1 / 3)
    assert r["n_cases"] == 3
    assert r["n_clusters"] == 2
    assert r["method"] == "cluster_percentile_bootstrap"
    assert r["too_few_clusters"] is False


def test_identical_clusters_collapse_interval() -> None:
    # Every cluster has 2 cases, (T,F) and (T,F): per cluster sum_a = 2,
    # sum_b = 0, n = 2. Any resample gives 2k/2k - 0 = 1.0.
    cases: list[Case] = []
    for v in "ABCD":
        cases += [(v, True, False), (v, True, False)]
    r = boot(cases)
    assert r["difference"] == pytest.approx(1.0)
    assert r["interval"] == [pytest.approx(1.0), pytest.approx(1.0)]


def test_single_cluster_is_too_few() -> None:
    r = boot([("A", True, False), ("A", False, False)])
    assert r["too_few_clusters"] is True
    assert r["interval"] is None
    assert r["difference"] == pytest.approx(0.5)  # 1/2 - 0/2
    assert r["n_clusters"] == 1


def test_clusters_are_resampled_not_cases() -> None:
    # One big cluster (20 cases, a right, b wrong) and four 1-case clusters
    # where both fail. Case resampling would keep the difference near
    # 20/24 with a lower bound far above 0. Cluster resampling omits the big
    # cluster in (4/5)^5 = 33% of replicates, where the difference is exactly
    # 0, so the 2.5th percentile is 0.
    cases: list[Case] = [("big", True, False)] * 20
    cases += [(f"s{i}", False, False) for i in range(4)]
    r = boot(cases, reps=1000)
    assert r["difference"] == pytest.approx(20 / 24)
    interval = r["interval"]
    assert isinstance(interval, list)
    assert interval[0] == 0.0
    assert 0.9 < interval[1] <= 1.0  # several draws of the big cluster


def test_input_order_invariant() -> None:
    cases: list[Case] = [
        ("A", True, False),
        ("A", False, False),
        ("B", True, True),
        ("C", False, True),
        ("C", True, False),
        ("D", False, False),
    ]
    shuffled = cases[:]
    random.Random(7).shuffle(shuffled)
    assert json.dumps(boot(cases)) == json.dumps(boot(shuffled))


def test_seed_reproducible_and_different_seed_differs() -> None:
    cases: list[Case] = [
        (f"v{i}", i % 2 == 0, i % 3 == 0) for i in range(12) for _ in range(2)
    ]
    assert json.dumps(boot(cases, seed=3)) == json.dumps(boot(cases, seed=3))
    assert boot(cases, seed=3)["interval"] != boot(cases, seed=4)["interval"]


def test_same_function_for_false_alarms() -> None:
    # Success = false alarm on a legitimate case. a raises 1 of 2 false
    # alarms, b raises 0: difference 1/2 - 0/2, no separate code path.
    cases: list[Case] = [("A", True, False), ("B", False, False)]
    assert boot(cases)["difference"] == pytest.approx(0.5)


def test_validation() -> None:
    cases: list[Case] = [("A", True, False), ("B", False, False)]
    with pytest.raises(ValueError, match="reps"):
        paired.cluster_bootstrap(cases, 1, 0, 0.05, 2)
    for alpha in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="alpha"):
            paired.cluster_bootstrap(cases, 1, 10, alpha, 2)
    with pytest.raises(ValueError):
        paired.cluster_bootstrap([], 1, 10, 0.05, 2)
