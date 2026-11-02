"""The evaluation protocol (evaluation/protocol.toml) agrees with the code it
governs, so the two cannot drift silently."""

import ast
import importlib
import sys
import tomllib
from pathlib import Path
from typing import Any

from reconcile.diagnosis.pipeline import DEFAULT_LIMITS
from reconcile.diagnosis.policy import POLICY_VERSION
from reconcile.retrieval.snapshot import HOPS

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
cohort = importlib.import_module("cohort")
decide = importlib.import_module("decide")
paired = importlib.import_module("paired")

with (ROOT / "evaluation" / "protocol.toml").open("rb") as _f:
    P: dict[str, Any] = tomllib.load(_f)


def dev_run_constants() -> dict[str, Any]:
    # Parsed, not imported: dev_run pulls in the whole evaluation stack.
    tree = ast.parse((ROOT / "evaluation" / "dev_run.py").read_text(encoding="utf-8"))
    found: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "SOURCE_REVISION":
                found[name] = ast.literal_eval(node.value)
            if name == "CUTOFF":  # date(Y, M, D)
                assert isinstance(node.value, ast.Call)
                y, m, d = (ast.literal_eval(a) for a in node.value.args)
                found[name] = f"{y:04d}-{m:02d}-{d:02d}"
    return found


def test_cohort_matches_code() -> None:
    c = P["cohort"]
    assert c["families"] == list(cohort.FAMILY_KEYS)
    assert c["legitimate_variants"] == [
        k for k in cohort.VARIANT_CATEGORY if k != c["fx_slice"]
    ]
    assert cohort.VARIANT_CATEGORY[c["fx_slice"]] is None
    assert c["headline_view"] == "all_cases"
    assert c["headline_view"] in cohort.COHORT_VIEWS
    assert c["sensitivity_view"] in cohort.COHORT_VIEWS
    assert c["headline_view"] != c["sensitivity_view"]


def test_data_matches_code() -> None:
    d = P["data"]
    consts = dev_run_constants()
    assert d["policy_version"] == POLICY_VERSION
    assert d["cutoff"] == consts["CUTOFF"]
    assert d["source_revision"][:7] == consts["SOURCE_REVISION"]


def test_deterministic_budgets_match_code() -> None:
    b = P["budgets"]["deterministic"]
    assert b["max_rows_per_query"] == DEFAULT_LIMITS.max_rows
    assert b["max_seconds_per_case"] == DEFAULT_LIMITS.max_seconds
    assert b["max_hops_declared"] == HOPS


def test_statistics_well_formed() -> None:
    s = P["statistics"]
    assert s["method"] == paired.METHOD
    assert isinstance(s["seed"], int)
    assert isinstance(s["reps"], int)
    assert s["reps"] >= 1
    assert 0 < s["alpha"] < 1


def test_min_clusters_set() -> None:
    mc = P["statistics"]["min_clusters"]
    assert isinstance(mc, int)
    assert mc >= 1


def test_status_and_splits() -> None:
    assert P["protocol"]["status"] in {"draft", "frozen"}
    s = P["splits"]
    assert s["sealed"] == "challenge_test"
    assert s["sealed"] not in s["development"]
    assert s["reproduction"] not in s["development"]
    assert s["reproduction"] != s["sealed"]


def test_baseline_configurations_known_to_decide() -> None:
    configs = P["systems"]["baseline_configurations"]
    assert configs
    assert set(configs) <= set(decide.KNOWN_CONFIGURATIONS)


def test_release_hard_gates_match_decide() -> None:
    assert tuple(P["decision"]["release_hard_gates"]) == decide.HARD_GATES
