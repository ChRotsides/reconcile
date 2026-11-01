"""Business-day counts: the calendar and the DayCount contract."""

from datetime import date

import pytest

from reconcile.domain.calendar import business_days, spans_weekend
from reconcile.domain.contracts import (
    DayCount,
    Diagnosis,
    Disposition,
    EvidenceItem,
    EvidencePacket,
    Finding,
    FindingState,
    InvestigationRequest,
    RecordRef,
)

SNAP = "a" * 64


def test_business_days_half_open_weekdays_only() -> None:
    assert business_days(date(2026, 6, 5), date(2026, 6, 10)) == 3  # Fri to Wed
    assert business_days(date(2026, 6, 1), date(2026, 6, 15)) == 10  # Mon to Mon
    assert business_days(date(2026, 6, 6), date(2026, 6, 8)) == 1  # Sat to Mon
    assert business_days(date(2026, 6, 1), date(2026, 6, 1)) == 0
    with pytest.raises(ValueError):
        business_days(date(2026, 6, 2), date(2026, 6, 1))


def test_spans_weekend() -> None:
    assert spans_weekend(date(2026, 6, 5), date(2026, 6, 8))  # Fri to Mon
    assert not spans_weekend(date(2026, 6, 1), date(2026, 6, 5))  # Mon to Fri
    assert not spans_weekend(date(2026, 6, 6), date(2026, 6, 6))


def packet(count: DayCount) -> EvidencePacket:
    request = InvestigationRequest(
        "req_1",
        SNAP,
        RecordRef(SNAP, "payments", "p1"),
        "symptom",
        date(2026, 6, 30),
        "pol",
    )
    pay = EvidenceItem(
        "pay",
        RecordRef(SNAP, "payments", "p1"),
        None,
        "primary",
        timestamps=(("payment_date", "2026-06-05"),),
    )
    bank = EvidenceItem(
        "bank",
        RecordRef(SNAP, "bank_transactions", "b1"),
        None,
        "payment_reference",
        timestamps=(("posted_date", "2026-06-10"),),
    )
    finding = Finding("lag", FindingState.SUPPORTED, ("pay",), (count.calculation_id,))
    return EvidencePacket(
        request,
        Diagnosis(Disposition.ABSTAINED, None, (finding,), reason="x"),
        (pay, bank),
        day_counts=(count,),
    )


def count(result: int, end: bool = True) -> DayCount:
    return DayCount(
        "lag",
        "pay",
        "payment_date",
        "bank" if end else None,
        "posted_date" if end else None,
        result,
        3,
        "clearing_limit_ach",
    )


def test_day_count_is_recomputed_from_cited_dates() -> None:
    assert packet(count(3)).day_counts[0].result == 3
    with pytest.raises(ValueError, match="does not match"):
        packet(count(4))


def test_day_count_to_cutoff() -> None:
    # 2026-06-05 (Fri) to 2026-06-30 (Tue): 17 weekdays
    assert packet(count(17, end=False)).day_counts[0].end_evidence is None
    with pytest.raises(ValueError, match="does not match"):
        packet(count(16, end=False))


def test_day_count_round_trips_json() -> None:
    p = packet(count(3))
    assert EvidencePacket.from_json(p.to_json()) == p


def test_day_count_rejects_unknown_or_undated_evidence() -> None:
    bad = DayCount(
        "lag", "nope", "payment_date", None, None, 1, 3, "clearing_limit_ach"
    )
    with pytest.raises(ValueError, match="unknown record"):
        packet(bad)
    bad = DayCount("lag", "pay", "posted_date", None, None, 1, 3, "clearing_limit_ach")
    with pytest.raises(ValueError, match="no timestamp"):
        packet(bad)


def test_day_count_validation() -> None:
    with pytest.raises(ValueError):
        DayCount("lag", "pay", "payment_date", "bank", None, 1, 3, "clearing_limit_ach")
    with pytest.raises(ValueError):
        count(-1)
