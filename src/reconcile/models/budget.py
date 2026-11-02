"""Model cost arithmetic and the persistent spend ledger (PLAN.md section 7).

Billed failures count toward the cap: a refused or truncated call still costs
tokens, so the ledger records every call that may have been billed.
"""

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from reconcile.models.client import ModelError

# USD per million tokens, (input, output). Source: claude-api skill reference
# (cached 2026-10-06), checked 2026-11-02. Prompt caching is not used, so cache
# reads and writes are not priced separately.
PRICES_USD_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
}

_MILLION = Decimal(1_000_000)
# Conservative bound on characters per token for the pre-call estimate.
_CHARS_PER_TOKEN = 3


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    if model not in PRICES_USD_PER_MTOK:
        raise ValueError(f"unknown model for pricing: {model!r}")
    price_in, price_out = PRICES_USD_PER_MTOK[model]
    return (input_tokens * price_in + output_tokens * price_out) / _MILLION


def estimate_usd(model: str, input_chars: int, max_output_tokens: int) -> Decimal:
    """Upper bound: input at 1 token per 3 chars (rounded up), output at the cap."""
    input_tokens = -(-input_chars // _CHARS_PER_TOKEN)
    return cost_usd(model, input_tokens, max_output_tokens)


class SpendLedger:
    """JSON file of every billed call, rewritten atomically on each record."""

    def __init__(self, path: Path, cap_usd: Decimal) -> None:
        self.path = path
        self.cap_usd = cap_usd

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        entries: list[dict[str, Any]] = data["entries"]
        return entries

    def total_usd(self) -> Decimal:
        return sum((Decimal(e["cost_usd"]) for e in self._load()), Decimal(0))

    def check(self, estimate: Decimal) -> None:
        total = self.total_usd()
        if total + estimate > self.cap_usd:
            raise ModelError(
                "budget",
                f"spent {total} + estimate {estimate} exceeds cap {self.cap_usd}",
            )

    def record(
        self,
        request_key: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        outcome: str,
    ) -> Decimal:
        cost = cost_usd(model, input_tokens, output_tokens)
        entries = self._load()
        entries.append(
            {
                "request_key": request_key,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": str(cost),
                "timestamp": datetime.now(UTC).isoformat(),
                "outcome": outcome,
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"entries": entries}, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        return cost
