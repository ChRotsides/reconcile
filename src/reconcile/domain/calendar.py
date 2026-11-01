"""Business-day counting (docs/evidence-contract.md section 3).

Monday to Friday only, no holiday calendar: the number of weekdays in the
half-open interval (start, end].
"""

from datetime import date, timedelta


def business_days(start: date, end: date) -> int:
    if end < start:
        raise ValueError("end is before start")
    days = (end - start).days
    return sum(
        1 for i in range(1, days + 1) if (start + timedelta(days=i)).weekday() < 5
    )


def spans_weekend(start: date, end: date) -> bool:
    """True if a Saturday or Sunday lies in (start, end]."""
    return any(
        (start + timedelta(days=i)).weekday() >= 5
        for i in range(1, (end - start).days + 1)
    )
