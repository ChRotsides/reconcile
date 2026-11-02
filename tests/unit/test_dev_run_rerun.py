"""The rerun check is only valid on the run's own image."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
D = importlib.import_module("dev_run")


def test_same_image_has_no_problem() -> None:
    assert D.image_problem("sha256:aaa", "sha256:aaa") is None


def test_different_image_is_refused() -> None:
    reason = D.image_problem("sha256:aaa", "sha256:bbb")
    assert reason is not None and "differs" in reason


def test_missing_image_is_refused() -> None:
    reason = D.image_problem("sha256:aaa", None)
    assert reason is not None and "no longer present" in reason
