"""Invented demo data: a small company, six payment exceptions (docs/cli-guide.md).

Nothing here is real or taken from a benchmark. The files are plain data beside
this module so `reconcile demo` can read them from an installed package.
"""

from pathlib import Path

BUNDLE = Path(__file__).parent / "bundle"
MAPPING = BUNDLE / "mapping.toml"
RECORDS = BUNDLE / "records.csv"
CUTOFF = "2026-06-30"
