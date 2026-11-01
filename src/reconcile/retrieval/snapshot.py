"""Bounded, read-only access to a prepared snapshot.

Limits (PLAN.md section 4: truncation and budget exhaustion are incomplete
evidence, never a silent partial answer):

* hops: at most `HOPS` relationship steps away from the primary entity
  (invoice -> allocations -> payments -> bank rows; payment -> bank rows,
  journals, allocations -> invoices -> their other allocations; bank row ->
  payment -> journals);
* rows: no query may return more than `Limits.max_rows` rows;
* time: the whole investigation may use `Limits.max_seconds` of wall time.

Exceeding the row or time limit raises `RetrievalIncomplete`; callers turn it
into an abstention.
"""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

Row = dict[str, str | None]

HOPS = 3
ROW_LIMIT = "retrieval_row_limit"
TIME_LIMIT = "retrieval_time_limit"


@dataclass(frozen=True)
class Limits:
    max_rows: int = 200
    max_seconds: float = 10.0
    clock: Callable[[], float] = time.monotonic  # injectable for tests


class RetrievalIncomplete(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Reader:
    """A read-only connection (`mode=ro`) that enforces `Limits`."""

    def __init__(self, path: Path, limits: Limits) -> None:
        self._max_rows = limits.max_rows
        self._clock = limits.clock
        self._deadline = self._clock() + limits.max_seconds
        self._con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA query_only = ON")
        # Stops a single runaway query; the check in rows() covers the rest.
        self._con.set_progress_handler(self._expired, 1000)

    def _expired(self) -> int:
        return int(self._clock() > self._deadline)

    def check_time(self) -> None:
        """Raise if the whole-investigation budget is spent (call before answering)."""
        if self._expired():
            raise RetrievalIncomplete(TIME_LIMIT)

    def __enter__(self) -> "Reader":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._con.close()

    def rows(self, sql: str, params: tuple[str, ...] = ()) -> list[Row]:
        """Run a SELECT built from constants; `params` carry every value."""
        self.check_time()
        try:
            got = self._con.execute(
                f"{sql} LIMIT {self._max_rows + 1}", params
            ).fetchall()
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                raise RetrievalIncomplete(TIME_LIMIT) from e
            raise
        if len(got) > self._max_rows:
            raise RetrievalIncomplete(ROW_LIMIT)
        return [dict(r) for r in got]

    def exists(self, table: str, key_column: str, token: str) -> bool:
        return bool(
            self.rows(f'SELECT 1 FROM "{table}" WHERE "{key_column}" = ?', (token,))
        )
