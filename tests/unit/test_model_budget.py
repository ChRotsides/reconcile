import json
from decimal import Decimal
from pathlib import Path

import pytest

from reconcile.models.budget import SpendLedger, cost_usd, estimate_usd
from reconcile.models.client import ModelError


def test_cost_exact_haiku() -> None:
    # 1000*0.10/1e6 + 200*0.50/1e6 = 0.0001 + 0.0001
    assert cost_usd("claude-haiku-5-5", 1000, 200) == Decimal("0.0002")


def test_cost_exact_sonnet() -> None:
    # 10000*2/1e6 + 1000*10/1e6 = 0.02 + 0.01
    assert cost_usd("claude-sonnet-5-5", 10_000, 1_000) == Decimal("0.03")


def test_cost_unknown_model() -> None:
    with pytest.raises(ValueError):
        cost_usd("claude-unknown", 1, 1)


def test_estimate_is_conservative() -> None:
    # 3001 chars -> 1001 tokens (rounded up); output at the cap.
    est = estimate_usd("claude-haiku-5-5", 3001, 500)
    assert est == cost_usd("claude-haiku-5-5", 1001, 500)


def test_ledger_persists_and_sums(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    ledger = SpendLedger(path, Decimal(25))
    ledger.record("k1", "claude-sonnet-5-5", 10_000, 1_000, "ok")
    ledger.record("k2", "claude-haiku-5-5", 1000, 200, "refusal")
    again = SpendLedger(path, Decimal(25))
    assert again.total_usd() == Decimal("0.0302")
    entries = json.loads(path.read_text())["entries"]
    assert [e["outcome"] for e in entries] == ["ok", "refusal"]
    assert not list(tmp_path.glob("*.tmp"))


def test_ledger_refuses_over_cap(tmp_path: Path) -> None:
    ledger = SpendLedger(tmp_path / "l.json", Decimal("0.03"))
    ledger.record("k", "claude-sonnet-5-5", 10_000, 1_000, "ok")  # exactly 0.03
    ledger.check(Decimal(0))  # equal to cap is allowed
    with pytest.raises(ModelError) as exc:
        ledger.check(Decimal("0.0001"))
    assert exc.value.reason == "budget"


def test_empty_ledger_total_is_zero(tmp_path: Path) -> None:
    assert SpendLedger(tmp_path / "none.json", Decimal(1)).total_usd() == Decimal(0)
