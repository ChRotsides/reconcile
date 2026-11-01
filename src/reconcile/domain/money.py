"""Exact money: a Decimal amount in an explicit currency, never a float.

Rounding policy
---------------
Nothing rounds, ever. Every operation is exact or raises: parsing,
construction, addition, subtraction, negation and absolute value all run in
a Decimal context that traps inexact results, and an amount with more decimal
places than the currency allows, or more than MAX_DIGITS digits, is an error.
Multiplication and allocation do not exist yet; add them with an explicit
rounding rule when a task needs them.

Currency scales come from a small explicit table. An unlisted currency is an
error, never a guess.
"""

import re
from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, InvalidOperation

# Minor-unit digits (CcyMnrUnts) from ISO 4217 "List one - Current currency &
# funds", published by SIX, the ISO 4217 maintenance agency (list dated
# 2026-09-17, retrieved 2026-11-01):
# https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml
# Extend only by copying further entries from that list. Which currencies the
# FinRCA data uses is unverified (assumption to check against the M0 audit).
CURRENCY_SCALE: dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CHF": 2,
    "CAD": 2,
    "AUD": 2,
    "JPY": 0,
    "KRW": 0,
    "KWD": 3,
    "BHD": 3,
}

# Bounding digits keeps every sum or difference of two valid amounts exact
# inside _EXACT, which has more precision than 2 * MAX_DIGITS needs.
MAX_DIGITS = 30
_EXACT = Context(prec=2 * MAX_DIGITS + 5, traps=[Inexact, InvalidOperation])
_PLAIN_AMOUNT = re.compile(r"[+-]?[0-9]+(\.[0-9]+)?", re.ASCII)


class CurrencyMismatchError(ValueError):
    """Raised when amounts in different currencies are combined (no FX in v0.1)."""


def _scale(currency: str) -> int:
    if not isinstance(currency, str) or currency not in CURRENCY_SCALE:
        raise ValueError(f"unknown or unsupported currency: {currency!r}")
    return CURRENCY_SCALE[currency]


@dataclass(frozen=True, slots=True)
class Money:
    """An amount at exactly the currency's scale. Negative values are allowed."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"amount must be Decimal, not {type(self.amount).__name__}")
        scale = _scale(self.currency)
        if not self.amount.is_finite():
            raise ValueError(f"amount must be finite: {self.amount}")
        exponent = self.amount.as_tuple().exponent
        assert isinstance(exponent, int)  # finite, so not "n"/"N"/"F"
        if exponent < -scale:
            raise ValueError(
                f"{self.amount} has more than {scale} decimal places for "
                f"{self.currency}; refusing to round"
            )
        try:
            quantized = self.amount.quantize(Decimal(1).scaleb(-scale), context=_EXACT)
        except InvalidOperation:
            raise ValueError(f"amount too large: {self.amount}") from None
        if len(quantized.as_tuple().digits) > MAX_DIGITS:
            raise ValueError(f"amount exceeds {MAX_DIGITS} digits: {self.amount}")
        if quantized.is_zero():
            quantized = _EXACT.abs(quantized)  # one representation of zero, not -0.00
        object.__setattr__(self, "amount", quantized)

    @property
    def scale(self) -> int:
        return _scale(self.currency)

    @classmethod
    def parse(cls, amount: str, currency: str) -> "Money":
        """Parse a plain decimal string such as "-1250.50" exactly.

        Rejects floats and other non-strings, whitespace, exponents, thousands
        separators, NaN/Infinity, and more decimal places than the currency has.
        """
        if not isinstance(amount, str):
            raise TypeError(f"amount must be str, not {type(amount).__name__}")
        if not _PLAIN_AMOUNT.fullmatch(amount):
            raise ValueError(f"malformed amount: {amount!r}")
        return cls(Decimal(amount), currency)

    @classmethod
    def from_str(cls, text: str) -> "Money":
        """Inverse of str(): "<amount> <currency>", e.g. "12.50 EUR"."""
        if not isinstance(text, str):
            raise TypeError(f"expected str, not {type(text).__name__}")
        parts = text.split(" ")
        if len(parts) != 2:
            raise ValueError(f"expected '<amount> <currency>': {text!r}")
        return cls.parse(parts[0], parts[1])

    def __str__(self) -> str:
        return f"{self.amount:f} {self.currency}"

    def _same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"cannot combine {self.currency} with {other.currency}"
            )

    def __add__(self, other: object) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(_EXACT.add(self.amount, other.amount), self.currency)

    def __sub__(self, other: object) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(_EXACT.subtract(self.amount, other.amount), self.currency)

    def __neg__(self) -> "Money":
        return Money(_EXACT.minus(self.amount), self.currency)

    def __abs__(self) -> "Money":
        return Money(_EXACT.abs(self.amount), self.currency)
