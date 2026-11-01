"""Write a prepared, read-only SQLite snapshot and compute its content digest.

Every value is stored as TEXT (or NULL). Amounts arrive as canonical decimal
strings, so SQLite never converts them to binary floats.
"""

import hashlib
import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

Row = tuple[str | None, ...]
# table name -> (column names, rows already in their final order)
Tables = Mapping[str, tuple[Sequence[str], Sequence[Row]]]


def write_snapshot(path: Path, tables: Tables) -> tuple[str, str]:
    """Write the snapshot atomically and read-only; return (content, file) SHA-256.

    The content digest is computed by reading the finished database back, in
    table-name order and row order, so it covers exactly what a reader sees.
    It does not depend on SQLite's page layout; the file digest does.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        for name, (columns, rows) in sorted(tables.items()):
            cols = ", ".join(f'"{c}" TEXT' for c in columns)
            con.execute(f'CREATE TABLE "{name}" ({cols})')
            marks = ", ".join("?" for _ in columns)
            con.executemany(f'INSERT INTO "{name}" VALUES ({marks})', rows)
        con.commit()
        content = content_digest(con)
    finally:
        con.close()
    os.chmod(tmp, 0o444)
    os.replace(tmp, path)
    return content, hashlib.sha256(path.read_bytes()).hexdigest()


def content_digest(con: sqlite3.Connection) -> str:
    h = hashlib.sha256()
    names = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    for name in names:
        cur = con.execute(f'SELECT * FROM "{name}" ORDER BY rowid')
        columns = [d[0] for d in cur.description]
        h.update(_line([name, columns]))
        for row in cur:
            h.update(_line(list(row)))
    return h.hexdigest()


def _line(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
