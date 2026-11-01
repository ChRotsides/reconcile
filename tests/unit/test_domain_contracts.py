"""Domain contracts: strict validation, exact JSON round trip, versioning."""

import json
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from reconcile.domain.contracts import (
    LEGITIMATE,
    SCHEMA_VERSION,
    AbsenceSearch,
    Calculation,
    Diagnosis,
    Disposition,
    EvidenceItem,
    EvidencePacket,
    Finding,
    FindingState,
    InvestigationRequest,
    Operand,
    Recommendation,
    RecordRef,
    UnsupportedSchemaVersionError,
)
from reconcile.domain.money import CurrencyMismatchError, Money

SNAP = "a" * 64
CUTOFF = date(2026, 6, 30)


def eur(text: str) -> Money:
    return Money.parse(text, "EUR")


def request(**kw: Any) -> InvestigationRequest:
    base: dict[str, Any] = {
        "request_id": "req_1",
        "snapshot": SNAP,
        "primary_entity": RecordRef(SNAP, "payments", "tok1"),
        "symptom": "Check something.",
        "cutoff": CUTOFF,
        "policy_version": "p1",
    }
    return InvestigationRequest(**{**base, **kw})


def packet() -> EvidencePacket:
    pay = EvidenceItem(
        "e1",
        RecordRef(SNAP, "payments", "tok1"),
        None,
        "primary",
        values=(("payment_amount", "100.00 EUR"),),
        timestamps=(("payment_date", "2026-05-01"),),
    )
    bank = EvidenceItem(
        "e2",
        RecordRef(SNAP, "bank_transactions", "tok2"),
        None,
        "payment_ref",
        values=(("amount", "75.00 EUR"),),
    )
    gl = EvidenceItem(
        "e3",
        None,
        AbsenceSearch(
            SNAP, "gl_entries", "source = tok2 AND type = bank_fee", CUTOFF, 0
        ),
        "absence",
    )
    calc = Calculation(
        "c1",
        "difference",
        (
            Operand("e1", "payment_amount", eur("100.00")),
            Operand("e2", "amount", eur("75.00")),
        ),
        eur("25.00"),
        tolerance=eur("0.01"),
    )
    finding = Finding("shortfall", FindingState.SUPPORTED, ("e1", "e2", "e3"), ("c1",))
    diag = Diagnosis(Disposition.ANSWERED, "unexplained_shortfall", (finding,))
    rec = Recommendation(
        "review_bank_statement", ("shortfall",), ("confirm fee terms",)
    )
    return EvidencePacket(
        request(),
        diag,
        (pay, bank, gl),
        (calc,),
        (rec,),
        ("snapshot completeness is assumed",),
        (("code", "abc"),),
    )


def test_round_trip_is_exact_and_amounts_are_strings() -> None:
    p = packet()
    text = p.to_json()
    again = EvidencePacket.from_json(text)
    assert again == p
    assert again.to_json() == text
    obj = json.loads(text)
    assert obj["calculations"][0]["result"] == "25.00 EUR"
    assert obj["calculations"][0]["operands"][0]["amount"] == "100.00 EUR"


def test_no_float_anywhere_in_persisted_json() -> None:
    def walk(x: object) -> None:
        assert not isinstance(x, float)
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(json.loads(packet().to_json()))


def test_request_round_trip() -> None:
    r = request()
    assert InvestigationRequest.from_json(r.to_json()) == r


@pytest.mark.parametrize("cls", [InvestigationRequest, EvidencePacket])
def test_unsupported_schema_version_is_rejected_clearly(cls: Any) -> None:
    obj = (request() if cls is InvestigationRequest else packet()).to_obj()
    obj["schema_version"] = "999"
    with pytest.raises(UnsupportedSchemaVersionError, match="999"):
        cls.from_json(json.dumps(obj))
    del obj["schema_version"]
    with pytest.raises(ValueError, match="schema_version"):
        cls.from_json(json.dumps(obj))
    assert SCHEMA_VERSION == "1"


def test_constructing_with_a_wrong_version_is_rejected() -> None:
    with pytest.raises(UnsupportedSchemaVersionError):
        request(schema_version="2")


def test_parser_rejects_floats_unknown_keys_and_duplicate_keys() -> None:
    obj = request().to_obj()
    with pytest.raises(ValueError, match="unexpected keys"):
        InvestigationRequest.from_json(json.dumps({**obj, "case_id": "RCA_1"}))
    with pytest.raises(ValueError, match="missing keys"):
        InvestigationRequest.from_json(
            json.dumps({k: v for k, v in obj.items() if k != "symptom"})
        )
    with pytest.raises(ValueError, match="floats"):
        InvestigationRequest.from_json(json.dumps({**obj, "extra": 1.5}))
    with pytest.raises(ValueError, match="duplicate"):
        InvestigationRequest.from_json('{"a": 1, "a": 2}')
    with pytest.raises(ValueError, match="NaN"):
        InvestigationRequest.from_json('{"schema_version": NaN}')


def test_calculation_amount_floats_are_rejected() -> None:
    obj = packet().to_obj()
    obj["calculations"][0]["result"] = 25.0
    with pytest.raises((TypeError, ValueError)):
        EvidencePacket.from_obj(obj)


def test_frozen() -> None:
    with pytest.raises(AttributeError):
        request().request_id = "x"  # type: ignore[misc]


def test_record_ref_is_qualified_by_snapshot_and_table() -> None:
    a = RecordRef(SNAP, "payments", "t")
    assert a != RecordRef(SNAP, "invoices", "t")
    assert a != RecordRef("b" * 64, "payments", "t")
    with pytest.raises(ValueError):
        RecordRef("short", "payments", "t")
    with pytest.raises(ValueError):
        RecordRef(SNAP, "bad table", "t")


def test_request_entity_must_be_in_the_same_snapshot() -> None:
    with pytest.raises(ValueError, match="different snapshot"):
        request(primary_entity=RecordRef("b" * 64, "payments", "t"))


def test_request_cutoff_must_be_a_date() -> None:
    with pytest.raises(TypeError):
        request(cutoff="2026-06-30")


# ------------------------------------------------------------------ diagnosis


def supported(check: str = "k") -> Finding:
    return Finding(check, FindingState.SUPPORTED, ("e1",))


def test_legitimate_requires_answered() -> None:
    for d in (Disposition.ABSTAINED, Disposition.OUT_OF_SCOPE, Disposition.FAILED):
        with pytest.raises(ValueError, match="no category"):
            Diagnosis(d, LEGITIMATE, (supported(),), reason="x")


def test_missing_evidence_cannot_produce_legitimate() -> None:
    with pytest.raises(ValueError, match="supported finding"):
        Diagnosis(Disposition.ANSWERED, LEGITIMATE, ())
    unknown = Finding("k2", FindingState.UNKNOWN)
    with pytest.raises(ValueError, match="legitimate needs"):
        Diagnosis(Disposition.ANSWERED, LEGITIMATE, (supported(), unknown))
    with pytest.raises(ValueError, match="legitimate needs"):
        Diagnosis(
            Disposition.ANSWERED,
            LEGITIMATE,
            (supported(),),
            missing_information=("bank statement",),
        )
    ok = Diagnosis(Disposition.ANSWERED, LEGITIMATE, (supported(),))
    assert ok.category == LEGITIMATE


def test_non_answered_needs_a_reason_and_no_category() -> None:
    with pytest.raises(ValueError, match="reason"):
        Diagnosis(Disposition.ABSTAINED, None)
    d = Diagnosis(Disposition.ABSTAINED, None, reason="unsupported_fx")
    assert Diagnosis.from_obj(d.to_obj()) == d
    with pytest.raises(ValueError, match="needs a category"):
        Diagnosis(Disposition.ANSWERED, None, (supported(),))
    with pytest.raises(ValueError, match="no reason"):
        Diagnosis(Disposition.ANSWERED, "x", (supported(),), reason="r")


def test_unknown_disposition_is_rejected_on_parse() -> None:
    obj = Diagnosis(Disposition.ABSTAINED, None, reason="r").to_obj()
    obj["disposition"] = "legitimate"
    with pytest.raises(ValueError):
        Diagnosis.from_obj(obj)


def test_finding_needs_evidence_unless_unknown() -> None:
    with pytest.raises(ValueError, match="must cite"):
        Finding("k", FindingState.CONTRADICTED)
    assert Finding("k", FindingState.UNKNOWN).evidence_ids == ()


def test_recommendation_needs_a_supporting_finding() -> None:
    with pytest.raises(ValueError):
        Recommendation("review", ())


# ---------------------------------------------------- calculations / evidence


def test_calculation_result_must_follow_from_operands() -> None:
    ops = (Operand("e1", "a", eur("1.10")), Operand("e2", "a", eur("2.20")))
    assert Calculation("c", "sum", ops, eur("3.30")).result == eur("3.30")
    with pytest.raises(ValueError, match="does not equal"):
        Calculation("c", "sum", ops, eur("3.31"))
    assert Calculation("c", "abs_difference", ops, eur("1.10")).result == eur("1.10")
    with pytest.raises(ValueError, match="operands"):
        Calculation("c", "difference", ops[:1], eur("1.10"))


def test_calculation_refuses_mixed_currencies_and_bad_tolerance() -> None:
    ops = (Operand("e1", "a", eur("1.00")), Operand("e2", "a", Money.parse("1", "JPY")))
    with pytest.raises(CurrencyMismatchError):
        Calculation("c", "sum", ops, eur("2.00"))
    one = (Operand("e1", "a", eur("1.00")),)
    with pytest.raises(ValueError, match="tolerance"):
        Calculation("c", "sum", one, eur("1.00"), tolerance=Money.parse("1", "JPY"))
    with pytest.raises(ValueError, match="negative"):
        Calculation("c", "sum", one, eur("1.00"), tolerance=eur("-0.01"))


def test_evidence_is_a_record_xor_a_search() -> None:
    ref = RecordRef(SNAP, "payments", "t")
    search = AbsenceSearch(SNAP, "gl_entries", "p", CUTOFF, 0)
    with pytest.raises(ValueError, match="exactly one"):
        EvidenceItem("e", None, None, "r")
    with pytest.raises(ValueError, match="exactly one"):
        EvidenceItem("e", ref, search, "r")
    with pytest.raises(ValueError):
        AbsenceSearch(SNAP, "gl_entries", "p", CUTOFF, -1)
    with pytest.raises(ValueError):
        AbsenceSearch(SNAP, "gl_entries", "p", CUTOFF, True)


def test_evidence_pairs_are_normalised_and_checked() -> None:
    a = EvidenceItem(
        "e", RecordRef(SNAP, "t", "x"), None, "r", (("b", "1"), ("a", "2"))
    )
    assert a.values == (("a", "2"), ("b", "1"))
    with pytest.raises(ValueError, match="duplicates"):
        EvidenceItem(
            "e", RecordRef(SNAP, "t", "x"), None, "r", (("a", "1"), ("a", "2"))
        )
    with pytest.raises(ValueError, match="ISO"):
        EvidenceItem("e", RecordRef(SNAP, "t", "x"), None, "r", (), (("d", "soon"),))


# --------------------------------------------------------------------- packet


def test_packet_references_must_resolve() -> None:
    p = packet()
    bad = Finding("shortfall", FindingState.SUPPORTED, ("e9",))
    with pytest.raises(ValueError, match="unknown evidence"):
        replace(p, diagnosis=Diagnosis(Disposition.ANSWERED, "x", (bad,)))
    badc = Finding("shortfall", FindingState.SUPPORTED, ("e1",), ("c9",))
    with pytest.raises(ValueError, match="unknown calculation"):
        replace(p, diagnosis=Diagnosis(Disposition.ANSWERED, "x", (badc,)))
    with pytest.raises(ValueError, match="unknown e1"):
        replace(p, evidence=p.evidence[1:])
    with pytest.raises(ValueError, match="unknown finding"):
        replace(p, recommendations=(Recommendation("a", ("nope",)),))


def test_packet_citations_must_respect_snapshot_and_cutoff() -> None:
    p = packet()
    other = EvidenceItem("e1", RecordRef("b" * 64, "payments", "t"), None, "r")
    with pytest.raises(ValueError, match="different snapshot"):
        replace(p, evidence=(other, *p.evidence[1:]), calculations=())
    late = EvidenceItem(
        "e1",
        RecordRef(SNAP, "bank_transactions", "t"),
        None,
        "r",
        timestamps=(("posted_date", "2026-07-01"),),
    )
    with pytest.raises(ValueError, match="after the cutoff"):
        replace(p, evidence=(late, *p.evidence[1:]))
    s = EvidenceItem(
        "e3", None, AbsenceSearch(SNAP, "gl_entries", "p", date(2026, 7, 1), 0), "a"
    )
    with pytest.raises(ValueError, match="another cutoff"):
        replace(p, evidence=(*p.evidence[:2], s))


def test_future_contractual_dates_are_citable_but_availability_dates_are_not() -> None:
    p = packet()
    inv = EvidenceItem(
        "e1",
        RecordRef(SNAP, "invoices", "t"),
        None,
        "r",
        values=p.evidence[0].values,
        timestamps=(("due_date", "2026-07-10"),),
    )
    replace(p, evidence=(inv, *p.evidence[1:]))  # does not raise
    for key, day in (
        ("created_at", "2026-07-10T08:00:00"),
        ("invoice_date", "2026-07-10"),
    ):
        late = replace(inv, timestamps=((key, day),))
        with pytest.raises(ValueError, match="after the cutoff"):
            replace(p, evidence=(late, *p.evidence[1:]))


def _with_operands(p: EvidencePacket, *ops: Operand) -> EvidencePacket:
    calc = replace(
        p.calculations[0], operands=ops, result=ops[0].amount - ops[1].amount
    )
    return replace(p, calculations=(calc,))


def test_operands_must_match_the_cited_evidence_value() -> None:
    p = packet()
    ok = (
        Operand("e1", "payment_amount", eur("100.00")),
        Operand("e2", "amount", eur("75.00")),
    )
    _with_operands(p, *ok)  # does not raise
    inflated = Operand("e1", "payment_amount", eur("1000.00"))
    with pytest.raises(ValueError, match="does not match"):
        _with_operands(p, inflated, ok[1])
    usd_bank = replace(p.evidence[1], values=(("amount", "75.00 USD"),))
    with pytest.raises(ValueError, match="does not match"):
        replace(
            _with_operands(p, *ok), evidence=(p.evidence[0], usd_bank, p.evidence[2])
        )
    with pytest.raises(ValueError, match="no field"):
        _with_operands(p, ok[0], Operand("e2", "fee", eur("75.00")))
    from_search = Operand("e3", "amount", eur("75.00"))
    with pytest.raises(ValueError, match="record"):
        _with_operands(p, ok[0], from_search)


def test_packet_ids_must_be_unique() -> None:
    p = packet()
    with pytest.raises(ValueError, match="duplicates"):
        replace(p, evidence=(p.evidence[0], p.evidence[0]))


def test_packet_represents_the_bounded_absence_search() -> None:
    obj = packet().to_obj()
    search = obj["evidence"][2]["search"]
    assert search["observed_count"] == 0
    assert search["cutoff"] == "2026-06-30"
    assert search["table"] == "gl_entries"
