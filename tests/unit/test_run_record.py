"""Run record: written last, hashes every file, refuses tampering."""

import json
import stat
from pathlib import Path

import pytest

from reconcile.runs import record

DIGEST = "c" * 64


def snapshot(tmp_path: Path) -> Path:
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "snapshot.sqlite").write_bytes(b"x")
    (snap / "manifest.json").write_text(
        json.dumps({"cutoff": "2026-06-30", "snapshot": {"content_sha256": DIGEST}})
    )
    return snap


def run(tmp_path: Path) -> Path:
    out = record.make_private_dir(tmp_path / "run")
    (out / "packets.jsonl").write_text("one\n")
    record.write_run_manifest(
        out,
        command="batch",
        started=record.now_utc(),
        snapshot_dir=snapshot(tmp_path),
        request_source_sha256="d" * 64,
        outcomes={"answered": 1},
    )
    return out


def test_private_dir_is_mode_700_and_new_only(tmp_path: Path) -> None:
    d = record.make_private_dir(tmp_path / "a" / "b")
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    with pytest.raises(FileExistsError):
        record.make_private_dir(d)


def test_manifest_lists_every_file_with_hash_and_size(tmp_path: Path) -> None:
    out = run(tmp_path)
    m = record.verify_run(out)
    assert m["complete"] is True
    assert m["snapshot_content_sha256"] == DIGEST
    assert set(m["files"]) == {"packets.jsonl", "snapshot-manifest.json"}
    assert m["files"]["packets.jsonl"]["size"] == 4
    assert m["outcomes"] == {"answered": 1}
    assert not [p for p in out.iterdir() if p.name.startswith(".")]


def test_run_without_manifest_is_refused(tmp_path: Path) -> None:
    out = record.make_private_dir(tmp_path / "run")
    (out / "packets.jsonl").write_text("one\n")
    with pytest.raises(record.RunRecordError, match="did not finish"):
        record.verify_run(out)


def test_tampered_file_is_refused(tmp_path: Path) -> None:
    out = run(tmp_path)
    (out / "packets.jsonl").write_text("two\n")  # same size, other bytes
    with pytest.raises(record.RunRecordError, match="differs"):
        record.verify_run(out)


def test_missing_file_and_incomplete_flag_are_refused(tmp_path: Path) -> None:
    out = run(tmp_path)
    manifest = json.loads((out / record.MANIFEST_FILE).read_text())
    (out / "packets.jsonl").unlink()
    with pytest.raises(record.RunRecordError, match="missing"):
        record.verify_run(out)
    manifest["complete"] = False
    (out / record.MANIFEST_FILE).write_text(json.dumps(manifest))
    with pytest.raises(record.RunRecordError, match="complete"):
        record.verify_run(out)


def test_manifest_naming_a_path_outside_the_run_is_refused(tmp_path: Path) -> None:
    out = run(tmp_path)
    manifest = json.loads((out / record.MANIFEST_FILE).read_text())
    manifest["files"]["../snap/manifest.json"] = {"sha256": "0", "size": 0}
    (out / record.MANIFEST_FILE).write_text(json.dumps(manifest))
    with pytest.raises(record.RunRecordError, match="unsafe"):
        record.verify_run(out)


def test_snapshot_without_manifest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(record.RunRecordError, match="not a prepared snapshot"):
        record.read_snapshot_manifest(tmp_path)


def _rewrite(out: Path, **changes: object) -> None:
    manifest = json.loads((out / record.MANIFEST_FILE).read_text())
    manifest.update(changes)
    (out / record.MANIFEST_FILE).write_text(json.dumps(manifest))


def test_manifest_with_no_files_is_not_a_verified_run(tmp_path: Path) -> None:
    out = run(tmp_path)
    _rewrite(out, files={})
    with pytest.raises(record.RunRecordError, match="snapshot-manifest.json"):
        record.verify_run(out)


@pytest.mark.parametrize(
    "entry",
    ["text", None, [], {}, {"size": 1}, {"sha256": "x"}, {"size": "4", "sha256": 1}],
)
def test_malformed_file_entries_are_run_record_errors(
    tmp_path: Path, entry: object
) -> None:
    out = run(tmp_path)
    manifest = json.loads((out / record.MANIFEST_FILE).read_text())
    manifest["files"]["packets.jsonl"] = entry
    (out / record.MANIFEST_FILE).write_text(json.dumps(manifest))
    with pytest.raises(record.RunRecordError, match="packets.jsonl"):
        record.verify_run(out)


def test_symlinked_snapshot_manifest_copy_is_refused(tmp_path: Path) -> None:
    out = run(tmp_path)
    copy = out / record.SNAPSHOT_MANIFEST_COPY
    target = tmp_path / "elsewhere.json"
    target.write_bytes(copy.read_bytes())
    copy.unlink()
    copy.symlink_to(target)
    with pytest.raises(record.RunRecordError, match="missing"):
        record.verify_run(out)
