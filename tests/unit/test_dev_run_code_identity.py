"""code_commit identifies the scorer and container, not just src/."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
D = importlib.import_module("dev_run")


def _tree(root: Path) -> None:
    for rel, text in {
        "src/reconcile/a.py": "a",
        "evaluation/x.py": "x",
        "evaluation/sub/y.py": "y",
        "evaluation/protocol.toml": "p",
        "evaluation/verified_label_conflicts.json": "{}",
        "deploy/inference/Dockerfile": "d",
        "deploy/inference/run_inference.sh": "r",
    }.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)


def test_any_evaluator_file_changes_the_digest(tmp_path: Path) -> None:
    _tree(tmp_path)
    base = D.code_commit(tmp_path)
    for rel in (
        "evaluation/x.py",
        "evaluation/sub/y.py",
        "evaluation/protocol.toml",
        "evaluation/verified_label_conflicts.json",
    ):
        f = tmp_path / rel
        old = f.read_text()
        f.write_text(old + " ")
        changed = D.code_commit(tmp_path)
        assert changed["evaluation_sha256"] != base["evaluation_sha256"], rel
        assert changed["src_sha256"] == base["src_sha256"]
        assert changed["deploy_sha256"] == base["deploy_sha256"]
        f.write_text(old)
    assert D.code_commit(tmp_path) == base


def test_deploy_change_changes_only_deploy_digest(tmp_path: Path) -> None:
    _tree(tmp_path)
    base = D.code_commit(tmp_path)
    (tmp_path / "deploy/inference/Dockerfile").write_text("d2")
    changed = D.code_commit(tmp_path)
    assert changed["deploy_sha256"] != base["deploy_sha256"]
    assert changed["evaluation_sha256"] == base["evaluation_sha256"]


def test_digest_ignores_input_order(tmp_path: Path) -> None:
    _tree(tmp_path)
    files = list((tmp_path / "evaluation").rglob("*.py"))
    assert D.tree_sha256(tmp_path, files) == D.tree_sha256(tmp_path, files[::-1])
