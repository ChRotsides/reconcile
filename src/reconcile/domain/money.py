"""Exact money: a Decimal amount in an explicit currency, never a float.

Rounding policy
---------------
Nothing rounds, ever. Every operation is exact or raises: parsing,
construction, addition, subtraction, negation and absolute value all run in
a Decimal context that traps inexact results, and an amount with more decimal
places than the currency allows, or more than MAX_DIGITS digits, is an error.
Multiplication and allocation do not exist yet; add them with an explicit
rounding rule when a task needs them.

Currency scales come from an explicit table. An unlisted currency is an
error, never a guess.
"""

import re
from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, InvalidOperation

# Minor-unit digits (CcyMnrUnts) from ISO 4217 "List one - Current currency &
# funds", published by SIX, the ISO 4217 maintenance agency (list Pblshd
# 2026-09-17, retrieved 2026-11-03):
# https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml
# Every entry with a numeric CcyMnrUnts is here, once per code, except funds
# codes (IsFund) such as CLF and CHE. Entries the list gives "N.A." (XAU, XXX,
# XDR and similar non-currency codes) are absent. XOF, XAF, XCD, XPF and XCG
# are real currencies and are present. Regenerate from the list, never by hand.
CURRENCY_SCALE: dict[str, int] = {
    "AED": 2,
    "AFN": 2,
    "ALL": 2,
    "AMD": 2,
    "AOA": 2,
    "ARS": 2,
    "AUD": 2,
    "AWG": 2,
    "AZN": 2,
    "BAM": 2,
    "BBD": 2,
    "BDT": 2,
    "BHD": 3,
    "BIF": 0,
    "BMD": 2,
    "BND": 2,
    "BOB": 2,
    "BRL": 2,
    "BSD": 2,
    "BTN": 2,
    "BWP": 2,
    "BYN": 2,
    "BZD": 2,
    "CAD": 2,
    "CDF": 2,
    "CHF": 2,
    "CLP": 0,
    "CNY": 2,
    "COP": 2,
    "CRC": 2,
    "CUP": 2,
    "CVE": 2,
    "CZK": 2,
    "DJF": 0,
    "DKK": 2,
    "DOP": 2,
    "DZD": 2,
    "EGP": 2,
    "ERN": 2,
    "ETB": 2,
    "EUR": 2,
    "FJD": 2,
    "FKP": 2,
    "GBP": 2,
    "GEL": 2,
    "GHS": 2,
    "GIP": 2,
    "GMD": 2,
    "GNF": 0,
    "GTQ": 2,
    "GYD": 2,
    "HKD": 2,
    "HNL": 2,
    "HTG": 2,
    "HUF": 2,
    "IDR": 2,
    "ILS": 2,
    "INR": 2,
    "IQD": 3,
    "IRR": 2,
    "ISK": 0,
    "JMD": 2,
    "JOD": 3,
    "JPY": 0,
    "KES": 2,
    "KGS": 2,
    "KHR": 2,
    "KMF": 0,
    "KPW": 2,
    "KRW": 0,
    "KWD": 3,
    "KYD": 2,
    "KZT": 2,
    "LAK": 2,
    "LBP": 2,
    "LKR": 2,
    "LRD": 2,
    "LSL": 2,
    "LYD": 3,
    "MAD": 2,
    "MDL": 2,
    "MGA": 2,
    "MKD": 2,
    "MMK": 2,
    "MNT": 2,
    "MOP": 2,
    "MRU": 2,
    "MUR": 2,
    "MVR": 2,
    "MWK": 2,
    "MXN": 2,
    "MYR": 2,
    "MZN": 2,
    "NAD": 2,
    "NGN": 2,
    "NIO": 2,
    "NOK": 2,
    "NPR": 2,
    "NZD": 2,
    "OMR": 3,
    "PAB": 2,
    "PEN": 2,
    "PGK": 2,
    "PHP": 2,
    "PKR": 2,
    "PLN": 2,
    "PYG": 0,
    "QAR": 2,
    "RON": 2,
    "RSD": 2,
    "RUB": 2,
    "RWF": 0,
    "SAR": 2,
    "SBD": 2,
    "SCR": 2,
    "SDG": 2,
    "SEK": 2,
    "SGD": 2,
    "SHP": 2,
    "SLE": 2,
    "SOS": 2,
    "SRD": 2,
    "SSP": 2,
    "STN": 2,
    "SVC": 2,
    "SYP": 2,
    "SZL": 2,
    "THB": 2,
    "TJS": 2,
    "TMT": 2,
    "TND": 3,
    "TOP": 2,
    "TRY": 2,
    "TTD": 2,
    "TWD": 2,
    "TZS": 2,
    "UAH": 2,
    "UGX": 0,
    "USD": 2,
    "UYU": 2,
    "UZS": 2,
    "VED": 2,
    "VES": 2,
    "VND": 0,
    "VUV": 0,
    "WST": 2,
    "XAF": 0,
    "XCD": 2,
    "XCG": 2,
    "XOF": 0,
    "XPF": 0,
    "YER": 2,
    "ZAR": 2,
    "ZMW": 2,
    "ZWG": 2,
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
            quantized = self.amount.quantize(Decimal((0, (1,), -scale)), context=_EXACT)
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
