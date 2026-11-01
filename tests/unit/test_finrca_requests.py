"""Neutral request preparation on tiny invented fixtures (never upstream rows)."""

import importlib.util
import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reconcile.adapters.finrca import (
    _load_key,
    _Tokens,
    create_key_file,
    prepare_finrca_snapshot,
)
from reconcile.adapters.finrca_requests import (
    ENTITY_TYPES,
    prepare_finrca_requests,
)
from reconcile.domain.contracts import InvestigationRequest

CUTOFF = date(2026, 6, 30)
STYLES = ("detect", "locate", "explain", "cite", "fix")
# (case id, entity type, raw entity id): raw ids exist in the adapter fixture
ENTITIES = [
    ("payment", "PAY_0000001"),
    ("payment", "PAY_0000002"),
    ("invoice", "INV_1"),
    ("bank_transaction", "BT_9000002"),
    ("gl_journal", "JE_1"),
]
SECRETS = ("SENTINEL_EXPECTED", "SENTINEL_WORDING", "SENTINEL_FAMILY", "hard")


def _load_make_source() -> Any:
    path = Path(__file__).parent / "test_finrca_adapter.py"
    spec = importlib.util.spec_from_file_location("adapter_fixture", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.make_source


def write_questions(bench: Path, split: str, n_cases: int = 30) -> list[str]:
    d = bench / split
    d.mkdir(parents=True)
    lines, case_ids = [], []
    for i in range(n_cases):
        case_id = f"CASEX_{1000 + i:04d}"
        case_ids.append(case_id)
        etype, raw = ENTITIES[i % len(ENTITIES)]
        for style in STYLES:
            lines.append(
                json.dumps(
                    {
                        "case_id": case_id,
                        "split": split,
                        "question_id": f"{case_id}_{style}",
                        "question_style": style,
                        "difficulty": "hard",
                        "question": f"SENTINEL_WORDING about {case_id}",
                        "expected_answer": {"root_cause": "SENTINEL_EXPECTED"},
                        "failure_type": "SENTINEL_FAMILY",
                        "primary_entity": {"type": etype, "id": raw},
                    }
                )
            )
    (d / "benchmark_questions.jsonl").write_text("\n".join(lines) + "\n")
    return case_ids


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Path]:
    src = tmp_path / "src"
    _load_make_source()(src)
    key = tmp_path / "private" / "key"
    key.parent.mkdir()
    create_key_file(key)
    prepare_finrca_snapshot(
        src, tmp_path / "snap", key_path=key, source_revision="r", cutoff=CUTOFF
    )
    bench = tmp_path / "bench"
    write_questions(bench, "train")
    return {
        "bench": bench,
        "snap": tmp_path / "snap",
        "key": key,
        "inf": tmp_path / "inference",
        "ev": tmp_path / "evaluator",
        "tmp": tmp_path,
    }


def run(env: dict[str, Path], split: str = "train", **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "snapshot_dir": env["snap"],
        "key_path": env["key"],
        "inference_dir": env["inf"],
        "evaluator_dir": env["ev"],
        "policy_version": "p1",
    }
    args.update(kw)
    return prepare_finrca_requests(env["bench"], split, **args)


def requests_of(env: dict[str, Path]) -> list[dict[str, Any]]:
    text = (env["inf"] / "requests.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines()]


def test_split_refusal_includes_challenge_test(env: dict[str, Path]) -> None:
    for bad in ("challenge_test", "full", "", "../train", "Train"):
        with pytest.raises(ValueError, match="split must be one of"):
            run(env, bad)
    assert not env["inf"].exists() and not env["ev"].exists()


@pytest.mark.parametrize("split", ["train", "validation", "test"])
def test_allowed_splits_work(env: dict[str, Path], split: str) -> None:
    if split != "train":
        write_questions(env["bench"], split, 5)
    assert run(env, split)["split"] == split


def test_no_answer_fields_or_case_ids_are_written(env: dict[str, Path]) -> None:
    case_ids = write_questions(env["tmp"] / "other", "train", 1)  # id format only
    assert case_ids
    run(env)
    allowed = {
        "schema_version",
        "request_id",
        "snapshot",
        "primary_entity",
        "symptom",
        "cutoff",
        "policy_version",
    }
    entity_keys = {"snapshot", "table", "record_id"}
    for rec in requests_of(env):
        assert set(rec) == allowed
        assert set(rec["primary_entity"]) == entity_keys
    raw = (env["inf"] / "requests.jsonl").read_text()
    for needle in (*SECRETS, "CASEX_", "PAY_0", "INV_1", "BT_9", "JE_1", "RCA_"):
        assert needle not in raw
    for name in ("detect", "locate", "explain"):
        assert name not in raw
    assert list(p.name for p in env["inf"].iterdir()) == ["requests.jsonl"]


def test_symptom_depends_only_on_entity_type_and_names_no_class(
    env: dict[str, Path],
) -> None:
    run(env)
    by_type: dict[str, set[str]] = {}
    for rec in requests_of(env):
        table = rec["primary_entity"]["table"]
        by_type.setdefault(table, set()).add(rec["symptom"])
    assert set(by_type) == {t for t, _, _ in ENTITY_TYPES.values()}
    assert all(len(v) == 1 for v in by_type.values())
    assert len({next(iter(v)) for v in by_type.values()}) == len(by_type)
    for symptoms in by_type.values():
        text = next(iter(symptoms)).lower()
        for word in ("duplicate", "missing", "mismatch", "legitimate", "fee", "paid"):
            assert word not in text


def test_request_id_is_not_in_case_order(env: dict[str, Path]) -> None:
    case_ids = [f"CASEX_{1000 + i:04d}" for i in range(30)]
    run(env)
    links = [
        json.loads(line)
        for line in (env["ev"] / "links.jsonl").read_text().splitlines()
    ]
    in_request_order = [link["case_id"] for link in links]
    assert sorted(in_request_order) == case_ids
    assert in_request_order != case_ids  # 30 cases: a sorted match is ~1e-32
    assert in_request_order != case_ids[::-1]
    ids = [link["request_id"] for link in links]
    assert ids == sorted(ids)
    assert not any(cid[-4:] in rid for cid, rid in zip(case_ids, ids, strict=False))
    assert len(set(ids)) == 30


def test_one_request_per_case_not_per_question(env: dict[str, Path]) -> None:
    summary = run(env)
    assert summary["requests"] == 30  # 150 question rows
    assert len(requests_of(env)) == 30


def test_links_are_separate_and_complete(env: dict[str, Path]) -> None:
    run(env)
    assert not (env["inf"] / "links.jsonl").exists()
    assert not (env["ev"] / "requests.jsonl").exists()
    links = [
        json.loads(line)
        for line in (env["ev"] / "links.jsonl").read_text().splitlines()
    ]
    assert {r["request_id"] for r in requests_of(env)} == {
        link["request_id"] for link in links
    }
    assert {link["case_id"] for link in links} == {
        f"CASEX_{1000 + i:04d}" for i in range(30)
    }


def test_same_inputs_and_key_give_identical_files(env: dict[str, Path]) -> None:
    first = run(env)
    second = run(
        env,
        inference_dir=env["tmp"] / "inf2",
        evaluator_dir=env["tmp"] / "ev2",
    )
    assert first == second
    for a, b, name in (
        (env["inf"], env["tmp"] / "inf2", "requests.jsonl"),
        (env["ev"], env["tmp"] / "ev2", "links.jsonl"),
    ):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_a_different_key_gives_different_ids(env: dict[str, Path]) -> None:
    other = env["tmp"] / "private" / "key2"
    create_key_file(other)
    prepare_finrca_snapshot(
        env["tmp"] / "src",
        env["tmp"] / "snap2",
        key_path=other,
        source_revision="r",
        cutoff=CUTOFF,
    )
    run(env)
    run(
        env,
        snapshot_dir=env["tmp"] / "snap2",
        key_path=other,
        inference_dir=env["tmp"] / "inf2",
        evaluator_dir=env["tmp"] / "ev2",
    )
    assert (env["inf"] / "requests.jsonl").read_text() != (
        env["tmp"] / "inf2" / "requests.jsonl"
    ).read_text()


def test_key_must_belong_to_the_snapshot(env: dict[str, Path]) -> None:
    other = env["tmp"] / "private" / "key2"
    create_key_file(other)
    with pytest.raises(ValueError, match="does not belong"):
        run(env, key_path=other)


def test_key_inside_an_output_directory_is_refused(env: dict[str, Path]) -> None:
    for where in ("inf", "ev"):
        inside = env[where] / "sub" / "key"
        inside.parent.mkdir(parents=True)
        inside.write_text(env["key"].read_text())
        with pytest.raises(ValueError, match="key file must be outside"):
            run(env, key_path=inside)


def test_directories_must_be_separate_and_not_overwritten(env: dict[str, Path]) -> None:
    with pytest.raises(ValueError, match="separate"):
        run(env, evaluator_dir=env["inf"])
    with pytest.raises(ValueError, match="separate"):
        run(env, evaluator_dir=env["inf"] / "nested")
    run(env)
    with pytest.raises(FileExistsError):
        run(env)


def test_primary_entity_token_matches_the_snapshot_token(env: dict[str, Path]) -> None:
    run(env)
    key = _load_key(env["key"])
    tokens = _Tokens(key)
    con = sqlite3.connect(f"file:{env['snap'] / 'snapshot.sqlite'}?mode=ro", uri=True)
    id_column = {
        "payments": "payment_id",
        "invoices": "invoice_id",
        "bank_transactions": "bank_transaction_id",
        "gl_entries": "journal_id",
    }
    try:
        seen = 0
        for rec in requests_of(env):
            req = InvestigationRequest.from_obj(rec)
            table = req.primary_entity.table
            token = req.primary_entity.record_id
            found = con.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{id_column[table]}" = ?',
                (token,),
            ).fetchone()[0]
            assert found >= 1
            seen += 1
        assert seen == 30
        # and for each raw ID the token is exactly the adapter's token
        for etype, raw in ENTITIES:
            table, ns, _ = ENTITY_TYPES[etype]
            tok = tokens.token(ns, raw)
            assert (
                con.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE "{id_column[table]}" = ?',
                    (tok,),
                ).fetchone()[0]
                >= 1
            )
    finally:
        con.close()


def test_entity_differing_within_a_case_is_an_error(env: dict[str, Path]) -> None:
    path = env["bench"] / "train" / "benchmark_questions.jsonl"
    lines = path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["primary_entity"]["id"] = "PAY_0000002"
    lines[1] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="differs within a case"):
        run(env)


def test_unknown_entity_type_is_an_error(env: dict[str, Path]) -> None:
    path = env["bench"] / "train" / "benchmark_questions.jsonl"
    path.write_text(path.read_text().replace('"type": "invoice"', '"type": "widget"'))
    with pytest.raises(ValueError, match="unknown entity type"):
        run(env)


def test_evaluator_dir_must_not_overlap_the_snapshot_dir(env: dict[str, Path]) -> None:
    outer = env["tmp"] / "outer"
    snap = outer / "snap"
    shutil.copytree(env["snap"], snap)
    # equal to, inside, and containing the snapshot directory
    for evaluator in (snap, snap / "nested", outer):
        with pytest.raises(ValueError, match="snapshot directory"):
            run(env, snapshot_dir=snap, evaluator_dir=evaluator)
    assert not (snap / "links.jsonl").exists()
    assert not (snap / "nested").exists()
    assert not (outer / "links.jsonl").exists()
    assert not env["inf"].exists()


def test_inference_dir_must_not_hold_links(env: dict[str, Path]) -> None:
    # An inference directory equal to, inside or containing the evaluator
    # directory would hold links.jsonl.
    for inference in (env["ev"], env["ev"] / "nested", env["tmp"]):
        with pytest.raises(ValueError, match="separate"):
            run(env, inference_dir=inference)
    assert not (env["ev"] / "links.jsonl").exists()


def test_key_inside_the_snapshot_directory_is_refused(env: dict[str, Path]) -> None:
    for name in ("key.bin", "sub/key.bin"):
        inside = env["snap"] / name
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text(env["key"].read_text())
        with pytest.raises(ValueError, match="key file must be outside"):
            run(env, key_path=inside)
    assert not env["inf"].exists() and not env["ev"].exists()
