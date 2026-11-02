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
        ids, ents, xs, groups = run.featurize(
            root, None, split, run.read_links(root, split)
        )
        return ids, xs, groups

    a = featurized({"A1": "ff", "A2": "11", "A3": "55", "A4": "aa"}, "train")
    b = featurized({"A1": "01", "A2": "ee", "A3": "99", "A4": "20"}, "validation")
    assert a == b
    assert a[0] == cases
    assert a[1] == [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}, {"x": 4.0}]
    assert a[2] == ["A1", "A2", "A1", "A4"]


def test_prediction_featurize_needs_no_evaluator_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prediction featurization opens only requests and snapshot: it runs with
    the evaluator directory absent, and joining case ids afterwards gives the
    same per-case inputs as the training-style (links-first) path."""
    import json

    run = _load("run")
    value = {"r1": 3.0, "r2": 1.0, "r3": 2.0}
    case = {"r1": "C3", "r2": "C1", "r3": "C2"}
    root = tmp_path / "root"
    (root / "prepared/requests/validation").mkdir(parents=True)
    (root / "prepared/requests/validation/requests.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "request_id": q,
                    "primary_entity": {"table": "payments", "record_id": q},
                }
            )
            + "\n"
            for q in case
        )
    )
    monkeypatch.setattr(run.F, "extract", lambda s, t, rid: {"x": value[rid]})
    monkeypatch.setattr(run.F, "resolve", lambda s, t, rid: rid)
    monkeypatch.setattr(run.F, "vendor_of", lambda f: f)
    assert not (root / "evaluator").exists()
    pred = run.featurize(root, None, "validation")
    assert pred[0] == ["r1", "r2", "r3"]  # opaque request ids, file order
    ids, ents, xs = run.join_cases(pred, case)
    assert ids == ["C1", "C2", "C3"]
    assert [e[1] for e in ents] == ["r2", "r3", "r1"]
    assert xs == [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}]


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
        ["A1"],
        [("payments", "p1")],
        ["NO_FAILURE"],
        [0.9],
        [False],
        None,
        labels,
        conflicts,
    )
    assert row["split"] == "validation"
    assert row["group_vendor_id"] == "vend-1"


def test_threshold_is_parsed_from_the_protocol_and_drift_is_loud(
    tmp_path: Path,
) -> None:
    run = _load("run")
    proto = tmp_path / "protocol.toml"
    proto.write_text(
        '[systems]\nbaseline_configurations = ["answer_all", "threshold_0.85"]\n'
    )
    assert run.protocol_threshold(proto) == 0.85
    run.check_threshold(0.85, 0.85)
    with pytest.raises(SystemExit, match="differs from the protocol"):
        run.check_threshold(0.8, 0.85)


def test_threshold_rows_abstain_below_and_count_neither_success_nor_false_alarm() -> (
    None
):
    import importlib

    sys.path.insert(0, str(DIR.parents[1]))
    try:
        compare = importlib.import_module("compare")
    finally:
        sys.path.remove(str(DIR.parents[1]))
    run = _load("run")

    def row(cid: str, conf: float, ft: str) -> dict[str, Any]:
        return {
            "case_id": cid, "confidence": conf, "correct": True, "complete": True,
            "kind": "false_alarm" if ft == "NO_FAILURE" else "correct",
            "failure_type": ft, "group": "g", "group_vendor_id": "v",
            "split": "validation",
        }  # fmt: skip

    rows = [
        row("a", 0.9, "X"),
        row("b", 0.85, "X"),
        row("c", 0.84, "X"),
        row("d", 0.5, "NO_FAILURE"),
    ]
    out = {
        r["case_id"]: r for r in run.as_scored(rows, lambda r: r["confidence"] >= 0.85)
    }
    assert [out[k]["disposition"] for k in "abcd"] == [
        "answered",
        "answered",
        "abstained",
        "abstained",
    ]
    assert out["c"]["outcome"] == "abstained" and out["c"]["kind"] is None
    o = compare.outcomes(out)
    assert [o[k]["success"] for k in "abcd"] == [True, True, False, False]
    assert not o["d"]["false_alarm"]


class _ToyModel:
    classes_ = ["A", "B"]

    def predict_proba(self, X: Any) -> Any:
        import numpy as np

        return np.array([[0.9, 0.1] if x[0] < 2 else [0.3, 0.7] for x in X])


def test_predict_split_runs_without_evaluator_or_label_files(tmp_path: Path) -> None:
    """The prediction step takes the fitted model, threshold and operational
    inputs only: no evaluator/ directory, no label file exists, and the file
    comes out sorted by request_id with the abstention flag at the threshold."""
    import json

    run = _load("run")
    inputs = (
        ["r2", "r1"],
        [("p", "x"), ("p", "y")],
        [{"x": 2.0}, {"x": 1.0}],
        ["g", "h"],
    )
    out = tmp_path / "predictions_validation.jsonl"
    sha = run.predict_split(_ToyModel(), ["x"], 0.8, inputs, out)
    assert sorted(p.name for p in tmp_path.iterdir()) == [out.name]
    assert sha == run.sha256_file(out)
    recs = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["request_id"] for r in recs] == ["r1", "r2"]
    assert [r["predicted"] for r in recs] == ["A", "B"]
    assert [r["abstained"] for r in recs] == [False, True]  # 0.9 >= 0.8 > 0.7


def test_scoring_refuses_a_predictions_file_with_a_different_hash(
    tmp_path: Path,
) -> None:
    run = _load("run")
    inputs = (["r1", "r2"], [("p", "x")] * 2, [{"x": 1.0}, {"x": 2.0}], ["g", "h"])
    out = tmp_path / "predictions_validation.jsonl"
    sha = run.predict_split(_ToyModel(), ["x"], 0.5, inputs, out)
    case_of = {"r1": "C2", "r2": "C1"}
    assert run.scored_predictions(out, sha, case_of, ["C1", "C2"], 0.5) == (
        ["B", "A"],
        [0.7, 0.9],
        [False, False],
    )
    out.write_text(out.read_text().replace('"A"', '"B"'))
    with pytest.raises(SystemExit, match="changed since it was written"):
        run.scored_predictions(out, sha, case_of, ["C1", "C2"], 0.5)


def test_scoring_parses_the_bytes_it_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file is replaced after the hash is computed: scoring must use the
    verified bytes (one read), not re-open the path."""
    run = _load("run")
    inputs = (["r1"], [("p", "x")], [{"x": 1.0}], ["g"])
    out = tmp_path / "predictions_validation.jsonl"
    sha = run.predict_split(_ToyModel(), ["x"], 0.5, inputs, out)
    verified = out.read_bytes()
    reads = []
    real = Path.read_bytes

    def read_then_replace(self: Path) -> bytes:
        data = real(self)
        if self == out:
            reads.append(1)
            out.write_bytes(verified.replace(b'"A"', b'"B"'))
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_replace)
    pred, _, _ = run.scored_predictions(out, sha, {"r1": "C1"}, ["C1"], 0.5)
    assert pred == ["A"] and len(reads) == 1


@pytest.mark.parametrize(
    ("p_max", "abstains"), [(0.84996, True), (0.85, False), (0.85004, False)]
)
def test_threshold_uses_the_raw_probability(
    tmp_path: Path, p_max: float, abstains: bool
) -> None:
    """Frozen convention: abstain iff raw max probability < 0.85, no rounding
    (0.84996 would round to 0.85 and wrongly answer)."""
    import json

    import numpy as np

    class Model:
        classes_ = ["A", "B"]

        def predict_proba(self, X: Any) -> Any:
            return np.array([[p_max, 1 - p_max] for _ in X])

    run = _load("run")
    inputs = (["r1"], [("p", "x")], [{"x": 1.0}], ["g"])
    out = tmp_path / "p.jsonl"
    sha = run.predict_split(Model(), ["x"], 0.85, inputs, out)
    [rec] = [json.loads(line) for line in out.read_text().splitlines()]
    assert rec["abstained"] is abstains
    _, _, ab = run.scored_predictions(out, sha, {"r1": "C1"}, ["C1"], 0.85)
    assert ab == [abstains]
    # a recorded flag that disagrees with the threshold is refused
    out.write_text(json.dumps(rec | {"abstained": not abstains}) + "\n")
    bad = run.sha256_file(out)
    with pytest.raises(SystemExit, match="disagrees"):
        run.scored_predictions(out, bad, {"r1": "C1"}, ["C1"], 0.85)


def test_baseline_false_alarms_use_the_shared_definition() -> None:
    """A legitimate case in group 'contradicted' called a failure never gets
    kind == 'false_alarm' from build_rows, but is one by metrics.is_false_alarm
    and must be counted in the all-legit false-alarm rate."""
    run = _load("run")
    row = {
        "case_id": "C1",
        "failure_type": "NO_FAILURE",
        "group": "contradicted",
        "category": "F06",
        "correct": False,
        "confidence": 0.9,
        "kind": "wrong_legit_variant",
    }
    m = run.baseline_metrics([row], groups=run.C.COHORT_GROUPS)
    assert m["false_alarms_all_legit"]["n"] == 1
    m = run.baseline_metrics(
        [row | {"category": "no_exception"}], groups=run.C.COHORT_GROUPS
    )
    assert m["false_alarms_all_legit"]["n"] == 0


def test_all_legit_false_alarm_numerator_matches_its_denominator() -> None:
    """A not_gradable legitimate row answered with a failure category is in the
    denominator, so it must be in the numerator."""
    run = _load("run")
    row = {
        "case_id": "C1",
        "failure_type": "NO_FAILURE",
        "group": "not_gradable",
        "category": "F06",
        "correct": False,
        "confidence": 0.9,
        "abstained": False,
    }
    m = run.baseline_metrics([row], groups=run.C.COHORT_GROUPS)
    assert (m["false_alarms_all_legit"]["n"], m["false_alarms_all_legit"]["d"]) == (
        1,
        1,
    )


def test_selected_model_must_be_the_protocols_frozen_baseline() -> None:
    run = _load("run")
    proto = Path(__file__).resolve().parents[2] / "evaluation/protocol.toml"
    run.check_model("hgb lr=0.05 depth=3 l2=1.0", proto)
    for other in (
        "hgb lr=0.1 depth=3 l2=1.0",
        "rf n=300",
        "hgb lr=0.05 depth=3 l2=0.0",
    ):
        with pytest.raises(SystemExit, match="frozen baseline"):
            run.check_model(other, proto)


def test_baseline_manifest_records_the_same_code_identity() -> None:
    """The manifest's code entry comes from dev_run.code_commit, so a baseline
    and a dev run of the same tree carry identical digests."""
    src = (DIR / "run.py").read_text()
    assert '"code": dev_run.code_commit()' in src
    sys.path.insert(0, str(DIR.parents[1]))
    try:
        dev_run = importlib.import_module("dev_run")
    finally:
        sys.path.remove(str(DIR.parents[1]))
    code = dev_run.code_commit()
    assert {"commit", "src_sha256", "evaluation_sha256", "deploy_sha256"} <= set(code)
