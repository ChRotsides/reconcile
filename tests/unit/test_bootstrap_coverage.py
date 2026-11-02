"""Coverage simulation (evaluation/bootstrap_coverage.py): determinism and sanity."""

import importlib
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
bc: Any = importlib.import_module("bootstrap_coverage")


def test_true_difference_matches_targets() -> None:
    _, _, d = bc.model()
    assert abs(d - (bc.TARGET_A - bc.TARGET_B)) < 1e-4


def test_deterministic_for_seed() -> None:
    a = bc.run({"s": [2, 3]}, [8], sims=6, reps=100, seed=7)
    b = bc.run({"s": [2, 3]}, [8], sims=6, reps=100, seed=7)
    c = bc.run({"s": [2, 3]}, [8], sims=6, reps=100, seed=8)
    assert a == b
    assert a != c


def test_coverage_high_on_easy_configuration() -> None:
    # 60 equal clusters: the nominal 95% interval should cover most of the time.
    r = bc.run({"s": [3]}, [60], sims=40, reps=300, seed=1)
    assert r["rows"][0]["coverage"] >= 0.85


def test_alpha_is_recorded_and_widens_interval() -> None:
    wide = bc.run({"s": [3]}, [20], sims=20, reps=200, seed=3, alpha=0.01)
    narrow = bc.run({"s": [3]}, [20], sims=20, reps=200, seed=3, alpha=0.2)
    assert wide["alpha"] == 0.01
    assert wide["rows"][0]["mean_width"] > narrow["rows"][0]["mean_width"]
