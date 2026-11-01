"""Declared, versioned policy values (docs/evidence-contract.md section 6).

Nothing here is inferred from data or labels. Change a value only with a new
POLICY_VERSION and a decision entry; every packet records the version applied.
"""

from decimal import Decimal

from reconcile.domain.money import Money

POLICY_VERSION = "evidence-contract-0.4"

# Stated in every answered legitimate_* and no_exception packet, so an all-clear
# cannot be read as a general clean bill (docs/evidence-contract.md section 5.1).
# Changing the wording or the lists needs a new POLICY_VERSION.
SCOPE_LIMITATION = (
    "Scope of this all-clear. Checked: settlement and bank match, amounts, "
    "allocations and invoice totals, duplicates, cutoff, invoice approval "
    "effective on the payment date, payment journal equal to the payment. Not checked: "
    "approval authority limits and segregation of duties, other GL postings "
    "(accounting period, accounts), vendor master data, PO matching."
)


# P1: clearing limit in business days, keyed by payments.payment_method.
# P2: no grace; a failure needs age > limit, a clearing variant lag <= limit.
CLEARING_LIMITS: dict[str, int] = {"ACH": 3, "wire": 1, "check": 10, "virtual_card": 2}


def clearing_policy(method: str) -> str | None:
    """Name of the declared limit for a payment method, None if undeclared."""
    return f"clearing_limit_{method.lower()}" if method in CLEARING_LIMITS else None


def quantum(currency: str) -> Money:
    """P3/P4: amount equality and fee tolerance are one minor unit, no percentage."""
    return Money(Decimal((0, (1,), -Money.parse("0", currency).scale)), currency)


def declared() -> tuple[tuple[str, str], ...]:
    limits = tuple(
        (f"clearing_limit_{m.lower()}", str(n)) for m, n in CLEARING_LIMITS.items()
    )
    return (
        ("policy_version", POLICY_VERSION),
        *limits,
        ("tolerance", "one_minor_unit"),
    )
