"""What the input readers share: containing paths, hashing files, and collecting
problems so one run shows several of them."""

import hashlib
from pathlib import Path, PurePosixPath

PROBLEM_CAP = 25


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inside(
    base: Path, name: object, where: str, *, owner: str, directory: bool = False
) -> Path:
    """The file (or folder) `name` names, which must lie inside `base`, the folder
    of the mapping or config file (`owner` says which, for the messages)."""

    def fail(message: str) -> ValueError:
        return ValueError(f"{owner} {where}: {message}")

    if not isinstance(name, str) or not name:
        raise fail("must be a path")
    posix = PurePosixPath(name.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts or Path(name).is_absolute():
        raise fail(f"{name!r} must be a relative path inside the {owner}'s folder")
    base = base.resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base):
        raise fail(f"{name!r} leaves the {owner}'s folder")
    if not (path.is_dir() if directory else path.is_file()):
        raise fail(f"{name!r} is not a {'folder' if directory else 'file'}")
    return path


class Problems:
    """Problems found while reading `source` (for example "CSV files"); `unit` is
    what is never dropped or guessed (for example "row")."""

    def __init__(self, source: str, unit: str) -> None:
        self.source = source
        self.unit = unit
        self.items: list[str] = []

    def add(self, message: str, where: str = "") -> None:
        self.items.append(f"{where}: {message}" if where else message)

    def raise_if_any(self) -> None:
        if not self.items:
            return
        shown = "\n  ".join(self.items[:PROBLEM_CAP])
        more = len(self.items) - PROBLEM_CAP
        tail = f"\n  ... and {more} more" if more > 0 else ""
        raise ValueError(
            f"{len(self.items)} problem(s) in the {self.source}; nothing was "
            f"prepared (no {self.unit} is dropped or guessed):\n  {shown}{tail}"
        )
