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
        "disposition": outcome if outcome in ("abstained", "failed") else "answered",
        "category": "F06" if kind == "false_alarm" or not legit else "legitimate_x",
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
        "category": "F06" if kind == "false_alarm" or not legit else "legitimate_x",
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
    o = compare.outcomes(a)
    assert set(o) == {"c1", "c2", "c3", "c4", "c5", "c6"}
    assert [o[k]["success"] for k in sorted(o)] == [
        True,
        False,
        False,
        False,
        False,
        True,
    ]
    assert [o[k]["false_alarm"] for k in sorted(o)] == [False] * 3 + [
        True,
        False,
        False,
    ]
    b = by_id(
        [
            base("c1", "v1", True, True),
            base("c2", "v1", True, False),
            base("c3", "v1", False, False, kind="wrong_failure_class"),
            base("c4", "v2", False, False, legit=True, kind="false_alarm"),
            base("c5", "v2", True, True, legit=True),
        ]
    )
    o = compare.outcomes(b)
    assert [o[k]["success"] for k in sorted(o)] == [True, False, False, False, True]
    assert [o[k]["false_alarm"] for k in sorted(o)] == [False] * 3 + [True, False]
    # a false_alarm kind on a failure-labelled case is not a false alarm
    odd = by_id([base("x", "v", False, False, kind="false_alarm")])
    assert not compare.outcomes(odd)["x"]["false_alarm"]


def test_missing_cluster_refused() -> None:
    with pytest.raises(ValueError, match="c2: missing group_vendor_id"):
        compare.outcomes(
            by_id([inv("c1", "v", "correct", True), inv("c2", None, "correct", True)])
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
    data = ("\n".join(json.dumps(r) for r in toy()[0]) + "\n\n").encode()
    assert sorted(compare.load(data, "x")) == ["c1", "c2", "c3", "c4", "c5", "c6"]
    dup = (json.dumps(toy()[0][0]) + "\n" + json.dumps(toy()[0][0]) + "\n").encode()
    with pytest.raises(ValueError, match="duplicate"):
        compare.load(dup, "x")


def test_false_alarm_is_over_all_legitimate_cases_of_the_split() -> None:
    a, b = toy()
    # legitimate cases outside the view: an FX one (A false alarm, B right) and
    # an out-of-scope legitimate one (B false alarm, A right); a train one is ignored
    a.append(inv("f1", "v3", "wrong", True, legit=True, kind="false_alarm", group="fx"))
    b.append(base("f1", "v3", True, True, legit=True, group="fx"))
    a.append(inv("o1", "v4", "correct", True, legit=True, group="oos_legit"))
    b.append(
        base(
            "o1", "v4", False, False, legit=True, kind="false_alarm", group="oos_legit"
        )
    )
    extra = inv("t1", "v9", "wrong", True, legit=True, kind="false_alarm")
    extra["split"] = "train"
    a.append(extra)
    r = run(a, b)
    assert r["n_cases"] == 6  # primary stays over the view
    assert r["primary_success"]["a"]["denominator"] == 6
    assert r["n_legitimate_cases"] == 5
    f = r["false_alarm"]
    # A: c5, f1 = 2/5; B: c4 c5 o1 = 3/5; only_a f1, only_b c4 o1.
    assert f["a"] == {"numerator": 2, "denominator": 5}
    assert f["b"] == {"numerator": 3, "denominator": 5}
    assert (f["paired_counts"]["only_a"], f["paired_counts"]["only_b"]) == (1, 2)
    # the sensitivity view changes the primary cases, not the false-alarm set
    s = run(a, b, view="sensitivity_without_verified_conflicts")
    assert s["false_alarm"]["a"]["denominator"] == 5


def test_legitimate_sets_or_outside_view_clusters_must_agree() -> None:
    a, b = toy()
    a.append(inv("f1", "v3", "correct", True, legit=True, group="fx"))
    with pytest.raises(ValueError, match="legitimate case sets differ"):
        run(a, b)
    b.append(base("f1", "v3", True, True, legit=True, group="fx"))
    run(a, b)
    b[-1]["group_vendor_id"] = "other"
    with pytest.raises(ValueError, match="f1: cluster differs"):
        run(a, b)


def test_empty_primary_population_is_explicit() -> None:
    a, b = toy()
    # Only legitimate cases in the view's complement: use an out-of-view group.
    for r in a + b:
        r["group"] = "oos_failure" if r["failure_type"] != "NO_FAILURE" else "cohort"
    r = run(
        [x for x in a if x["failure_type"] == "NO_FAILURE"],
        [x for x in b if x["failure_type"] == "NO_FAILURE"],
        view="all_cases",
    )
    assert r["n_cases"] == 3  # legitimate cohort cases are in the view
    # An empty view: no case in any view group.
    for x in a + b:
        x["group"] = "oos_failure"
    r = run(a, b)
    p = r["primary_success"]
    assert p["a"]["denominator"] == 0
    assert p["bootstrap"]["difference"] is None
    assert p["bootstrap"]["interval"] is None
    assert r["false_alarm"]["a"]["denominator"] == 3  # legitimate, unaffected


def test_empty_legitimate_population_is_explicit() -> None:
    a, b = toy()
    a, b = a[:3], b[:3]
    r = run(a, b)
    fa = r["false_alarm"]
    assert fa["a"]["denominator"] == 0
    assert fa["bootstrap"]["difference"] is None
    assert fa["bootstrap"]["interval"] is None
    assert r["primary_success"]["a"]["denominator"] == 3


def test_false_alarm_is_group_independent() -> None:
    # a legitimate case answered with a failure category is a false alarm in
    # every group, whatever `kind` the scorer gave it
    for g in ("cohort", "label_conflict", "fx", "contradicted", "not_gradable"):
        r = inv("x", "v", "answered_other", None, legit=True, group=g)
        r["disposition"] = "answered"
        r["category"] = "F06"
        assert compare.outcomes({"x": r})["x"]["false_alarm"], g
    r = inv("y", "v", "oos", None, legit=True, group="oos_legit")
    r["disposition"], r["category"] = "answered", "F06"
    assert compare.outcomes({"y": r})["y"]["false_alarm"]
    # not a false alarm: abstained, legit category, failure label
    for disp, cat, legit in (
        ("abstained", "F06", True),
        ("answered", "legitimate_x", True),
        ("answered", "F06", False),
    ):
        r = inv("z", "v", "x", None, legit=legit, group="contradicted")
        r["disposition"], r["category"] = disp, cat
        assert not compare.outcomes({"z": r})["z"]["false_alarm"], (disp, cat, legit)
    # baseline answer-all row: no disposition, answered by construction
    b = base("w", "v", False, False, legit=True, group="fx")
    b["category"] = "F14"
    assert compare.outcomes({"w": b})["w"]["false_alarm"]


def _write(path: Path, text: str) -> str:
    path.write_text(text)
    return str(compare.sha256_bytes(path.read_bytes()))


def _check(a: Path, b: Path, metrics: Any, manifest: Any, cfg: str) -> Any:
    """check_inputs as main() calls it: bytes of each input."""
    return compare.check_inputs(
        a.read_bytes(),
        b.read_bytes(),
        b.name,
        json.dumps(metrics).encode(),
        json.dumps(manifest).encode(),
        cfg,
    )


def test_check_inputs_binds_files_to_recorded_digests(tmp_path: Path) -> None:
    a = tmp_path / "cases.jsonl"
    b = tmp_path / "cases_threshold_0.85.jsonl"
    a_sha, b_sha = _write(a, "a\n"), _write(b, "b\n")
    prov = {"protocol_sha256": "P", "cases_sha256": a_sha}
    gates = {"absence_check_passed": True}
    metrics = {"provenance": prov, "hard_gates": gates}
    manifest = {
        "system": "sys",
        "model": "m",
        "configurations": {
            "threshold_0.85": {
                "cases_file": b.name,
                "cases_sha256": b_sha,
                "abstention_threshold": 0.85,
            }
        },
    }
    got = _check(a, b, metrics, manifest, "threshold_0.85")
    assert got == {
        "configuration": "threshold_0.85",
        "a_sha256": a_sha,
        "b_sha256": b_sha,
        "investigator_metrics_sha256": compare.sha256_bytes(
            json.dumps(metrics).encode()
        ),
        "baseline_manifest_sha256": compare.sha256_bytes(json.dumps(manifest).encode()),
        "investigator_provenance": prov,
        "investigator_hard_gates": gates,
        "baseline_system": "sys",
        "baseline_model": "m",
        "abstention_threshold": 0.85,
    }
    with pytest.raises(ValueError, match="investigator cases file"):
        _check(
            a,
            b,
            {"provenance": prov | {"cases_sha256": "x"}},
            manifest,
            "threshold_0.85",
        )
    with pytest.raises(ValueError, match="no provenance"):
        _check(a, b, {}, manifest, "threshold_0.85")
    bad = json.loads(json.dumps(manifest))
    bad["configurations"]["threshold_0.85"]["cases_sha256"] = "x"
    with pytest.raises(ValueError, match="baseline cases file"):
        _check(a, b, metrics, bad, "threshold_0.85")
    bad = json.loads(json.dumps(manifest))
    bad["configurations"]["threshold_0.85"]["cases_file"] = "cases.jsonl"
    with pytest.raises(ValueError, match="manifest says"):
        _check(a, b, metrics, bad, "threshold_0.85")
    with pytest.raises(ValueError, match="no configuration"):
        _check(a, b, metrics, manifest, "answer_all")


def _to_bytes(rows: list[Row]) -> bytes:
    return ("\n".join(json.dumps(r) for r in rows) + "\n").encode()


def test_main_scores_the_verified_bytes_and_reads_each_input_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good_a, good_b = toy()
    a_bytes, b_bytes = _to_bytes(good_a), _to_bytes(good_b)
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_bytes(a_bytes)
    b.write_bytes(b_bytes)
    metrics = tmp_path / "metrics.json"
    manifest = tmp_path / "run-manifest.json"
    metrics.write_text(
        json.dumps({"provenance": {"cases_sha256": compare.sha256_bytes(a_bytes)}})
    )
    manifest.write_text(
        json.dumps(
            {
                "configurations": {
                    "c": {
                        "cases_file": "b.jsonl",
                        "cases_sha256": compare.sha256_bytes(b_bytes),
                    }
                }
            }
        )
    )
    reads: list[str] = []
    real = Path.read_bytes

    def read_once_then_swap(self: Path) -> bytes:
        reads.append(self.name)
        data = real(self)
        if self.suffix == ".jsonl":  # a concurrent writer replaces the file
            self.write_bytes(_to_bytes(good_a[:1]))
        return data

    monkeypatch.setattr(Path, "read_bytes", read_once_then_swap)
    out = tmp_path / "out.json"
    argv = [
        "compare",
        str(a),
        str(b),
        "--split=validation",
        "--configuration=c",
        f"--investigator-metrics={metrics}",
        f"--baseline-manifest={manifest}",
        "--view=all_cases",
        "--seed=1",
        "--reps=10",
        "--alpha=0.05",
        "--min-clusters=2",
        f"--out={out}",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    compare.main()
    assert sorted(reads) == sorted(
        ["a.jsonl", "b.jsonl", "metrics.json", "run-manifest.json"]
    )
    res = json.loads(out.read_text())
    assert res["n_cases"] == len(good_a)  # the verified rows, not the replacement
    assert res["investigator_metrics_sha256"] == compare.sha256_bytes(
        metrics.read_bytes()
    )
    assert res["baseline_manifest_sha256"] == compare.sha256_bytes(
        manifest.read_bytes()
    )
    # Digest mismatch on the (now replaced) file still refuses.
    monkeypatch.setattr(Path, "read_bytes", real)
    with pytest.raises(SystemExit, match="does not match"):
        compare.main()


def test_sha256_bytes() -> None:
    assert compare.sha256_bytes(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
