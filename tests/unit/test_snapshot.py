"""Snapshot writer: indexes must not change what the content digest covers."""

import sqlite3
from pathlib import Path

import pytest

from reconcile.store.snapshot import Tables, content_digest, write_snapshot

TABLES: Tables = {
    "a": (["id", "ref", "amount"], [("1", "r1", "1.50"), ("2", None, "2.00")]),
    "b": (["id", "a_id"], [("x", "1"), ("y", "2")]),
}


def indexes_of(path: Path) -> set[str]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
    finally:
        con.close()


def test_content_digest_is_independent_of_indexes(tmp_path: Path) -> None:
    plain, plain_file = write_snapshot(tmp_path / "plain.sqlite", TABLES)
    indexed, indexed_file = write_snapshot(
        tmp_path / "indexed.sqlite", TABLES, {"a": ("ref", "id"), "b": ("a_id",)}
    )
    assert plain == indexed
    assert plain_file != indexed_file  # the file digest does cover the indexes
    assert indexes_of(tmp_path / "plain.sqlite") == set()
    assert indexes_of(tmp_path / "indexed.sqlite") == {
        "ix_a_ref",
        "ix_a_id",
        "ix_b_a_id",
    }


def test_digest_recomputed_from_a_reopened_indexed_file(tmp_path: Path) -> None:
    path = tmp_path / "s.sqlite"
    content, _ = write_snapshot(path, TABLES, {"a": ("ref",)})
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        assert content_digest(con) == content
    finally:
        con.close()


def test_unknown_index_column_fails_and_leaves_no_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="a.nope"):
        write_snapshot(tmp_path / "s.sqlite", TABLES, {"a": ("nope",)})
    assert not (tmp_path / "s.sqlite").exists()
