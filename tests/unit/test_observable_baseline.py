"""Pure helpers of the observable-input baseline (evaluation/baselines/observable)."""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

DIR = Path(__file__).resolve().parents[2] / "evaluation/baselines/observable"


def _load(name: str):  # type: ignore[no-untyped-def]
    sys.path.insert(0, str(DIR))
    try:
        spec = importlib.util.spec_from_file_location(name, DIR / f"{name}.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(DIR))


def test_business_days_is_the_investigators_calendar() -> None:
    """One definition, (start, end], from reconcile.domain.calendar; the
    baseline only returns 0 where the shared function raises (end < start)."""
    from reconcile.domain.calendar import business_days as shared

    f = _load("features")
    assert f.shared_business_days is shared
    mon = date(2026, 6, 29)
    assert f.business_days(mon, mon) == 0
    assert f.business_days(mon, date(2026, 7, 6)) == 5  # Mon to next Mon
    assert f.business_days(date(2026, 6, 26), date(2026, 6, 29)) == 1  # Fri to Mon
    assert f.business_days(date(2026, 6, 27), date(2026, 6, 29)) == 1  # Sat to Mon
    # the review's boundary case: Saturday payment, Tuesday cutoff
    assert f.business_days(date(2026, 6, 27), date(2026, 6, 30)) == 2
    assert f.business_days(mon, date(2026, 6, 1)) == 0  # reversed
    for start, end in [(date(2026, 6, 5), date(2026, 6, 10)), (mon, date(2026, 7, 6))]:
        assert f.business_days(start, end) == shared(start, end)


def test_baseline_defines_no_calendar_of_its_own() -> None:
    import ast

    tree = ast.parse((DIR / "features.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "business_days":
            body = ast.unparse(node)
            assert "weekday" not in body and "timedelta" not in body


def test_feature_groups_cover_the_declared_residuals() -> None:
    f = _load("features")
    names = [
        "e_pay",
        "i_status_paid",
        "i_n_approval",
        "i_age_days",
        "p_n_alloc",
        "p_amount_log10",
    ]
    assert f.group_columns(names, f.RESIDUAL_GROUPS["R3_entity_type"]) == ["e_pay"]
    assert f.group_columns(names, f.RESIDUAL_GROUPS["R4_invoice_age"]) == ["i_age_days"]
    assert f.group_columns(names, f.RESIDUAL_GROUPS["allocation_count"]) == [
        "p_n_alloc"
    ]
    assert f.group_columns(
        names, f.RESIDUAL_GROUPS["R1_invoice_status_and_approvals"]
    ) == [
        "i_status_paid",
        "i_n_approval",
    ]


def test_featurize_order_and_groups_do_not_depend_on_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two key-dependent token mappings of the same toy cases give the same
    samples, in the same (case_id) order, with the same group names. A1 and A3
    share a vendor; GL case A4 is its own group."""
    import json

    run = _load("run")
    cases = ["A1", "A2", "A3", "A4"]
    value = {"A1": 1.0, "A2": 2.0, "A3": 3.0, "A4": 4.0}
    vendor = {"A1": "v1", "A2": "v2", "A3": "v1"}

    def featurized(
        tokens: dict[str, str], split: str
    ) -> tuple[list[str], list[dict[str, float]], list[str]]:
        root = tmp_path / split
        (root / "evaluator" / split).mkdir(parents=True)
        (root / "prepared/requests" / split).mkdir(parents=True)
        by_tok = {tokens[c]: c for c in cases}
        reqs: list[dict[str, Any]] = [
            {
                "request_id": "req_" + tokens[c],
                "primary_entity": {
                    "table": "gl_entries" if c == "A4" else "payments",
                    "record_id": tokens[c],
                },
            }
            for c in cases
        ]
        links = [{"request_id": r["request_id"], "case_id": by_tok[r["request_id"][4:]]}
                 for r in reqs]  # fmt: skip
        reqs.sort(key=lambda r: r["request_id"])  # token order, as in the real files
        for name, rows in (("prepared/requests", reqs), ("evaluator", links)):
            file = "requests.jsonl" if name != "evaluator" else "links.jsonl"
            (root / name / split / file).write_text(
                "".join(json.dumps(r) + "\n" for r in rows)
            )
        monkeypatch.setattr(
            run.F, "extract", lambda s, t, rid: {"x": value[by_tok[rid]]}
        )
        monkeypatch.setattr(run.F, "resolve", lambda s, t, rid: by_tok[rid])
        monkeypatch.setattr(run.F, "vendor_of", lambda f: vendor.get(f, "none"))
        ids, ents, xs, groups = run.featurize(root, None, split)
        return ids, xs, groups

    a = featurized({"A1": "ff", "A2": "11", "A3": "55", "A4": "aa"}, "train")
    b = featurized({"A1": "01", "A2": "ee", "A3": "99", "A4": "20"}, "validation")
    assert a == b
    assert a[0] == cases
    assert a[1] == [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}, {"x": 4.0}]
    assert a[2] == ["A1", "A2", "A1", "A4"]


def test_build_rows_carry_split_and_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    """compare.py needs `split` and the evaluator-side `group_vendor_id`."""
    run = _load("run")
    monkeypatch.setattr(run.evidence, "retrieve", lambda snap, cat, table, rid: [])
    labels = {
        "A1": {
            "failure_type": "NO_FAILURE",
            "scenario_variant": "v",
            "group_vendor_id": "vend-1",
        }
    }
    conflicts = run.C.Conflicts(verified={}, candidates={}, review={})
    [row] = run.build_rows(
        ["A1"], [("payments", "p1")], ["NO_FAILURE"], [0.9], None, labels, conflicts
    )
    assert row["split"] == "validation"
    assert row["group_vendor_id"] == "vend-1"
