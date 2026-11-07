"""The run record: what a run read, produced and when, written last.

`run-manifest.json` lists the sha256 and size of every file in the run folder
and says `complete: true`. It is written atomically after everything else, so
an interrupted run has no manifest and `verify_run` refuses it. It holds no
credentials and no record contents: only names, digests, counts and versions.
Resume is M6.
"""

import hashlib
import json
import os
import platform
import tempfile
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

MANIFEST_FILE = "run-manifest.json"
SNAPSHOT_MANIFEST_COPY = "snapshot-manifest.json"
SCHEMA_VERSION = 1


class RunRecordError(ValueError):
    """The run folder is not a complete, untampered run."""


def make_private_dir(path: Path) -> Path:
    """Create the new folder `path` readable by its owner only. Reports and runs
    hold real financial data (D164), so the mode is set explicitly."""
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"{path} already exists; choose a new folder")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkdir(path, 0o700)
    os.chmod(path, 0o700)  # the umask may only remove bits, but be explicit
    return path


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def app_version() -> str:
    try:
        return metadata.version("reconcile")
    except metadata.PackageNotFoundError:
        return "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_private_file(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def read_snapshot_manifest(snapshot_dir: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((snapshot_dir / "manifest.json").read_text())
        manifest["snapshot"]["content_sha256"]
        manifest["cutoff"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise RunRecordError(
            f"{snapshot_dir} is not a prepared snapshot "
            f"(manifest.json unreadable: {type(e).__name__})"
        ) from e
    if not (snapshot_dir / "snapshot.sqlite").is_file():
        raise RunRecordError(f"{snapshot_dir} has no snapshot.sqlite")
    return manifest  # type: ignore[no-any-return]


def write_run_manifest(
    run_dir: Path,
    *,
    command: str,
    started: str,
    snapshot_dir: Path,
    request_source_sha256: str,
    outcomes: dict[str, int],
) -> dict[str, Any]:
    """Copy the snapshot manifest into `run_dir`, hash every file there and write
    `run-manifest.json` last. Call it only when the run's outputs are final."""
    manifest = read_snapshot_manifest(snapshot_dir)
    write_private_file(
        run_dir / SNAPSHOT_MANIFEST_COPY, (snapshot_dir / "manifest.json").read_bytes()
    )
    files = {
        p.name: {"sha256": sha256_file(p), "size": p.stat().st_size}
        for p in sorted(run_dir.iterdir())
        if p.is_file() and p.name != MANIFEST_FILE
    }
    record = {
        "schema_version": SCHEMA_VERSION,
        "application_version": app_version(),
        "python_version": platform.python_version(),
        "command": command,
        "started_utc": started,
        "finished_utc": now_utc(),
        "snapshot_content_sha256": manifest["snapshot"]["content_sha256"],
        "request_source_sha256": request_source_sha256,
        "outcomes": dict(sorted(outcomes.items())),
        "files": files,
        "complete": True,
    }
    fd, tmp = tempfile.mkstemp(prefix=f".{MANIFEST_FILE}-", dir=run_dir)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, run_dir / MANIFEST_FILE)
    except BaseException:
        os.unlink(tmp)
        raise
    return record


def verify_run(run_dir: Path) -> dict[str, Any]:
    """Return the run manifest, or raise `RunRecordError` when it is missing,
    incomplete, unreadable, does not list the snapshot manifest copy or any
    listed file differs from its recorded hash. Readers must open only files
    this lists: an unlisted file was never verified."""
    path = run_dir / MANIFEST_FILE
    if not path.is_file():
        raise RunRecordError(
            f"{run_dir} has no {MANIFEST_FILE}: the run did not finish, or this "
            "is not a run folder"
        )
    try:
        record = json.loads(path.read_text())
        files = record["files"]
        ok = record["complete"] is True and isinstance(files, dict)
    except (ValueError, KeyError, TypeError) as e:
        raise RunRecordError(f"{path} is unreadable: {type(e).__name__}") from e
    if not ok:
        raise RunRecordError(f"{path} does not say the run is complete")
    if SNAPSHOT_MANIFEST_COPY not in files:
        raise RunRecordError(f"{path} does not list {SNAPSHOT_MANIFEST_COPY}")
    for name, entry in files.items():
        if name != Path(name).name or name.startswith("."):
            raise RunRecordError(f"{path} lists an unsafe file name")
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("sha256"), str)
            and isinstance(entry.get("size"), int)
            and not isinstance(entry["size"], bool)
        ):
            raise RunRecordError(f"{path} has a malformed entry for {name}")
        target = run_dir / name
        if target.is_symlink() or not target.is_file():
            raise RunRecordError(f"{name} is missing from {run_dir}")
        if (
            target.stat().st_size != entry["size"]
            or sha256_file(target) != entry["sha256"]
        ):
            raise RunRecordError(f"{name} differs from the hash in {MANIFEST_FILE}")
    return record  # type: ignore[no-any-return]
