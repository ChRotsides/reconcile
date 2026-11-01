"""End-to-end preparation of the real FinRCA `full/` tables.

Skips when the pinned checkout is absent. Set FINRCA_FULL_DIR to its
`data/benchmark/full` directory, or place the checkout at the default path.
"""

import json
import os
import subprocess
from datetime import date
from pathlib import Path

import pytest

from reconcile.adapters.finrca import TABLES, create_key_file, prepare_finrca_snapshot

PINNED_REVISION = "76c93ec32536e29826e2ab9e68b58e0351410bc7"
DEFAULT_FULL = Path("data/upstream/FinRCA-AI-Bench/data/benchmark/full")
FULL_DIR = Path(os.environ.get("FINRCA_FULL_DIR", DEFAULT_FULL))

pytestmark = pytest.mark.skipif(
    not (FULL_DIR / "payments.csv").exists(), reason="FinRCA full/ tables not present"
)


def test_real_tables_end_to_end(tmp_path: Path) -> None:
    revision = subprocess.run(
        ["git", "-C", str(FULL_DIR), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert revision == PINNED_REVISION

    key = tmp_path / "private" / "key"
    key.parent.mkdir()
    create_key_file(key)
    runs = []
    for name in ("a", "b"):
        runs.append(
            prepare_finrca_snapshot(
                FULL_DIR,
                tmp_path / name,
                key_path=key,
                source_revision=revision,
                cutoff=date(2026, 6, 30),
            )
        )
    first, second = runs

    assert first == second  # same digests and counts on a second run
    assert (tmp_path / "a" / "snapshot.sqlite").read_bytes() == (
        tmp_path / "b" / "snapshot.sqlite"
    ).read_bytes()

    assert set(first["tables"]) == set(TABLES)
    for name, t in first["tables"].items():
        assert t["rows_before"] == t["rows_after"] + t["dropped_by_cutoff"], name
        print(
            name,
            t["rows_before"],
            t["rows_after"],
            t["dropped_by_cutoff"],
            t["exact_duplicates_removed"],
        )
    # docs/data-audit.md section 6: 2 late bank rows and 10 late GL lines
    assert first["tables"]["bank_transactions"]["dropped_by_cutoff"] == 2
    assert first["tables"]["gl_entries"]["dropped_by_cutoff"] == 10
    assert first["money"]["max_digits"] <= first["money"]["max_digits_allowed"]
    print(first["money"], first["snapshot"])
    quality = json.loads((tmp_path / "a" / "quality.json").read_text())
    assert quality == json.loads((tmp_path / "b" / "quality.json").read_text())
    for name, gap in quality["gaps"].items():
        print("quality", name, gap["count"])
        assert len(gap["sample_tokens"]) <= 5
