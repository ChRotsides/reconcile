"""Pure helpers of the observable-input baseline (evaluation/baselines/observable)."""

import importlib.util
import sys
from datetime import date
from pathlib import Path

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
