from decimal import Context, Decimal, Inexact, InvalidOperation, Rounded, localcontext

import pytest

from reconcile.domain.money import CURRENCY_SCALE, CurrencyMismatchError, Money


def eur(text: str) -> Money:
    return Money.parse(text, "EUR")


# --- parsing -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "currency", "canonical"),
    [
        ("12.50", "EUR", "12.50 EUR"),
        ("12.5", "EUR", "12.50 EUR"),  # fewer places pad, never change value
        ("12", "EUR", "12.00 EUR"),
        ("-12.50", "EUR", "-12.50 EUR"),
        ("+12.50", "EUR", "12.50 EUR"),
        ("0", "EUR", "0.00 EUR"),
        ("1250", "JPY", "1250 JPY"),
        ("1.234", "KWD", "1.234 KWD"),
        ("0.01", "USD", "0.01 USD"),
    ],
)
def test_parse_canonical(text: str, currency: str, canonical: str) -> None:
    assert str(Money.parse(text, currency)) == canonical


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "abc",
        "1,250.50",
        "1 250.50",
        " 1.00",
        "1.00 ",
        "1.",
        ".5",
        "1e2",
        "1E+2",
        "--1",
        "1.2.3",
        "NaN",
        "-NaN",
        "sNaN",
        "Infinity",
        "-Infinity",
        "inf",
        "1_000",
        "１２",  # fullwidth digits
        "0x10",
        "1.00\n",
    ],
)
def test_parse_rejects_malformed(text: str) -> None:
    with pytest.raises(ValueError):
        eur(text)


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("1.001", "EUR"),
        ("1.000", "EUR"),  # extra places rejected even if the value is unchanged
        ("0.001", "USD"),
        ("1.5", "JPY"),
        ("1.0", "JPY"),
        ("1.2345", "KWD"),
    ],
)
def test_parse_rejects_excess_decimal_places(text: str, currency: str) -> None:
    with pytest.raises(ValueError, match="decimal places"):
        Money.parse(text, currency)


def test_parse_rejects_non_strings() -> None:
    for bad in (1.5, 12, Decimal("1.00"), None, b"1.00"):
        with pytest.raises(TypeError):
            Money.parse(bad, "EUR")  # type: ignore[arg-type]


def test_unknown_currency_is_an_error() -> None:
    for bad in ("XXX", "ZZZ", "eur", "EU", "", " EUR"):
        with pytest.raises(ValueError, match="currency"):
            Money.parse("1.00", bad)
    with pytest.raises(ValueError, match="currency"):
        Money(Decimal("1.00"), None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("code", "scale"),
    [("NZD", 2), ("JPY", 0), ("KWD", 3), ("TND", 3), ("XOF", 0), ("CLP", 0)],
)
def test_scales_match_the_iso_4217_list(code: str, scale: int) -> None:
    # Values read from SIX list-one.xml, Pblshd 2026-09-17.
    assert CURRENCY_SCALE[code] == scale
    assert Money.parse("1", code).scale == scale


def test_funds_and_non_currency_codes_are_not_listed() -> None:
    for code in ("CLF", "CHE", "UYI", "XAU", "XDR", "XXX"):
        assert code not in CURRENCY_SCALE


# --- construction --------------------------------------------------------


def test_floats_are_rejected_everywhere() -> None:
    with pytest.raises(TypeError):
        Money(1.5, "EUR")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money(1, "EUR")  # type: ignore[arg-type]
    assert eur("1.00").__add__(1.0) is NotImplemented
    with pytest.raises(TypeError):
        eur("1.00") + 1.0


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_constructor_rejects_non_finite(value: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        Money(Decimal(value), "EUR")


def test_constructor_never_rounds() -> None:
    with pytest.raises(ValueError, match="decimal places"):
        Money(Decimal("0.005"), "EUR")
    with pytest.raises(ValueError, match="decimal places"):
        Money(Decimal("2.50"), "JPY")


def test_constructor_normalises_scale_and_exponent_form() -> None:
    assert Money(Decimal("5"), "EUR").amount.as_tuple().exponent == -2
    assert Money(Decimal("5E+2"), "EUR") == eur("500.00")
    assert Money(Decimal("1E+3"), "JPY").amount == Decimal(1000)


def test_negative_zero_equals_zero() -> None:
    assert eur("-0.00") == eur("0.00")
    assert str(eur("-0")) == "0.00 EUR"
    assert str(-eur("0.00")) == "0.00 EUR"


def test_money_is_immutable() -> None:
    m = eur("1.00")
    with pytest.raises(AttributeError):
        m.amount = Decimal("2.00")  # type: ignore[misc]


def test_digit_limit_is_enforced_not_rounded() -> None:
    biggest = "9" * 28 + ".99"  # 30 digits at scale 2
    assert str(eur(biggest)) == f"{biggest} EUR"
    with pytest.raises(ValueError, match="digits|large"):
        eur("9" * 29 + ".99")
    with pytest.raises(ValueError, match="digits|large"):
        eur("1" + "0" * 40)


def test_result_over_digit_limit_raises_instead_of_rounding() -> None:
    biggest = eur("9" * 28 + ".99")
    with pytest.raises(ValueError, match="digits|large"):
        biggest + eur("0.01")


# --- serialisation -------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "0.00 EUR",
        "0.01 EUR",
        "-0.01 EUR",
        "1234567890123456789012345678.99 USD",
        "-1234567890123456789012345678.99 USD",
        "0 JPY",
        "-5 JPY",
        "9999999999999999999999999999 JPY",
        "0.001 KWD",
        "-123456.789 BHD",
    ],
)
def test_string_round_trip_is_lossless(text: str) -> None:
    money = Money.from_str(text)
    assert str(money) == text
    assert Money.from_str(str(money)) == money


def test_round_trip_for_every_currency_in_table() -> None:
    for currency, scale in CURRENCY_SCALE.items():
        digits = "1234567890"[: scale + 3]
        text = f"{digits[:-scale] if scale else digits}"
        if scale:
            text += "." + digits[-scale:]
        money = Money.parse(text, currency)
        assert money.scale == scale
        assert Money.from_str(str(money)) == money
        assert str(money) == f"{text} {currency}"


@pytest.mark.parametrize("text", ["12.50", "12.50EUR", "12.50  EUR", "EUR 12.50", ""])
def test_from_str_rejects_malformed(text: str) -> None:
    with pytest.raises(ValueError):
        Money.from_str(text)


def test_no_float_in_value_path() -> None:
    # 0.1 + 0.2 != 0.3 in binary floats; money must be exact.
    assert eur("0.10") + eur("0.20") == eur("0.30")
    assert sum((eur("0.01") for _ in range(1000)), eur("0")) == eur("10.00")
    assert isinstance(eur("0.30").amount, Decimal)


# --- arithmetic ----------------------------------------------------------


def test_add_subtract_negate_abs() -> None:
    assert eur("10.25") + eur("0.75") == eur("11.00")
    assert eur("10.25") - eur("10.50") == eur("-0.25")
    assert -eur("3.10") == eur("-3.10")
    assert abs(eur("-3.10")) == eur("3.10")
    assert eur("5.00") + (-eur("5.00")) == eur("0")  # reversal nets to zero


def test_refund_reversal_pair_nets_exactly() -> None:
    payment = eur("1234.56")
    assert payment + (-payment) == eur("0.00")
    assert (payment - eur("234.56")) == eur("1000.00")


@pytest.mark.parametrize("op", ["add", "sub"])
def test_different_currencies_raise(op: str) -> None:
    a, b = eur("1.00"), Money.parse("1.00", "USD")
    with pytest.raises(CurrencyMismatchError):
        a + b if op == "add" else a - b


def test_equality_requires_same_currency() -> None:
    assert eur("1.00") != Money.parse("1.00", "USD")


def test_large_amount_arithmetic_is_exact() -> None:
    big = eur("1" + "0" * 20 + ".01")
    assert str(big + big) == "200000000000000000000.02 EUR"
    assert str(big - big) == "0.00 EUR"


def test_three_decimal_and_zero_decimal_currencies_add_exactly() -> None:
    assert str(Money.parse("0.001", "KWD") + Money.parse("0.009", "KWD")) == (
        "0.010 KWD"
    )
    assert str(Money.parse("1", "JPY") + Money.parse("2", "JPY")) == "3 JPY"


# --- regression: 30-digit boundary must not round in neg/abs/add/sub -------

_EDGE = "1234567890123456789012345678.91"  # 30 digits, USD


def test_negation_at_digit_limit_is_exact() -> None:
    m = Money.parse(_EDGE, "USD")
    assert str(-m) == f"-{_EDGE} USD"
    negated = -m
    assert -negated == m


def test_abs_at_digit_limit_is_exact() -> None:
    m = Money.parse(f"-{_EDGE}", "USD")
    assert str(abs(m)) == f"{_EDGE} USD"


def test_add_and_subtract_at_digit_limit_are_exact() -> None:
    m = Money.parse(_EDGE, "USD")
    zero = Money.parse("0", "USD")
    assert str(m + zero) == f"{_EDGE} USD"
    assert str(m - zero) == f"{_EDGE} USD"
    assert str(zero - m) == f"-{_EDGE} USD"
    assert m - m == zero


def test_operations_ignore_the_ambient_decimal_context() -> None:
    hostile = Context(
        prec=1, Emin=0, Emax=3, traps=[Inexact, Rounded, InvalidOperation]
    )
    with localcontext(hostile):
        a = Money.parse("12.34", "EUR")
        b = Money(Decimal("0.66"), "EUR")
        assert str(a) == "12.34 EUR"
        assert Money.from_str("12.34 EUR") == a
        assert str(a + b) == "13.00 EUR"
        assert str(a - b) == "11.68 EUR"
        assert str(-a) == "-12.34 EUR"
        assert str(abs(-a)) == "12.34 EUR"
        assert str(Money.parse("-0", "EUR")) == "0.00 EUR"
        assert str(Money.parse("1234567", "JPY")) == "1234567 JPY"
