# 0002. Money representation

**Status:** accepted, 2026-11-02 (decided in M1).

## Context
`PLAN.md` section 4 requires `Decimal` with explicit currency, no floats, and deterministic recomputation of every reported amount. Python's default `Decimal` context has 28 digits and rounds silently.

## Decision
- `Money` is a frozen dataclass of `Decimal` amount and currency code, quantised to the currency's scale, at most 30 digits (`src/reconcile/domain/money.py`).
- Operations are parse, construct, add, subtract, negate and absolute value. They run in a context that raises instead of rounding. There is no multiplication, division, percentage or allocation, so nothing rounds.
- The currency table lists 10 ISO 4217 currencies, including 0-, 2- and 3-decimal ones; any other code raises.
- Persisted form is the string `"<amount> <CCY>"`.
- Amounts are equal if they differ by at most one minor unit of the currency (JPY 1, others 0.01). There is no percentage tolerance (`docs/evidence-contract.md` section 6).
- Comparing across currencies is not attempted (see 0005, FX).

## Alternatives considered
1. A bare `Decimal` plus a separate currency string (the simplest option): rejected. Currency checks would be repeated at every call site.
2. Integer minor units: rejected. Sources supply decimal strings, so every boundary would need conversion.
3. Rounding helpers (`multiply`, default rounding mode): rejected for now. No task needs them, and a default mode would choose a financial policy silently.

## Evidence
`tests/unit/test_money.py`, including `test_floats_are_rejected_everywhere`, `test_constructor_never_rounds`, `test_digit_limit_is_enforced_not_rounded`, `test_negation_at_digit_limit_is_exact`. The negation test is a regression test: a first version returned a wrong value for a 30-digit amount because negation used the default context. Currency scales: comment above `CURRENCY_SCALE` in `money.py` (SIX ISO 4217 list). Provenance: D017, D018, D019, D055.

## Consequences and limits
- Amounts above 30 digits and currencies outside the table are rejected, not handled (A005, A006: unverified against real exports).
- A bank format that pads extra decimal places would be rejected.
- Any future percentage, fee or split calculation needs a new decision with an explicit rounding rule.

## Revisit if
A real export needs other currencies or more digits, or a check needs multiplication or allocation.
