"""The draft validator on real fixture packets: faithful drafts pass, each kind of
fabrication is rejected."""

import copy
import json
import re
import tempfile
from dataclasses import replace
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from reconcile.adapters.finrca import (
    _load_key,
    _Tokens,
    create_key_file,
    prepare_finrca_snapshot,
)
from reconcile.diagnosis.pipeline import investigate
from reconcile.domain.contracts import (
    Disposition,
    EvidencePacket,
    Finding,
    FindingState,
    InvestigationRequest,
    RecordRef,
)
from reconcile.models.validate import validate

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "public"
NAMESPACE = {
    "invoices": "invoice",
    "payments": "payment",
    "bank_transactions": "bank_txn",
    "gl_entries": "journal",
    "approval_events": "approval",
}
NAMES = (
    "f06_double_payment",
    "f12_payment_missing_from_bank",
    "f14_amount_shortfall",
    "pending_settlement",
    "real_partial_payment",
    "real_approval_missing",
    "insufficient_primary_entity_missing",
)


@cache
def load_packets() -> dict[str, EvidencePacket]:
    """Real packets from the public fixtures (investigator output, not hand-made)."""
    expected = json.loads((FIXTURES / "expected.json").read_text())
    out: dict[str, EvidencePacket] = {}
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        key = root / "key"
        create_key_file(key)
        for name in NAMES:
            m = prepare_finrca_snapshot(
                FIXTURES / name,
                root / name,
                key_path=key,
                source_revision=f"fixture:{name}",
                cutoff=date(2026, 6, 30),
            )
            digest = m["snapshot"]["content_sha256"]
            e = expected[name]["primary_entity"]
            token = _Tokens(_load_key(key)).token(NAMESPACE[e["table"]], e["id"])
            request = InvestigationRequest(
                f"req_{name}",
                digest,
                RecordRef(digest, e["table"], token),
                "Check whether there is a reconciliation issue.",
                date(2026, 6, 30),
                "policy_test",
            )
            out[name] = investigate(root / name / m["snapshot"]["file"], request)
    return out


def faithful(packet: EvidencePacket) -> dict[str, Any]:
    """A draft that only restates the packet's own findings and recommendation."""
    d = packet.diagnosis
    claims = [
        {
            "finding": f.check_id,
            "text": f"The check {f.check_id} is supported by the cited records.",
            "evidence_ids": list(f.evidence_ids),
            "calculation_ids": list(f.calculation_ids),
        }
        for f in d.findings
        if f.state is FindingState.SUPPORTED
    ]
    if d.disposition is Disposition.ANSWERED:
        summary = f"The investigation reached the diagnosis {d.category}."
    else:
        summary = f"The investigation abstained: {d.reason}."
    action = packet.recommendations[0].action_type if packet.recommendations else "none"
    return {
        "summary": summary,
        "claims": claims,
        "next_step": {"action": action, "text": "Follow the recommended review step."},
    }


@pytest.mark.parametrize("name", NAMES)
def test_faithful_draft_is_accepted(name: str) -> None:
    packet = load_packets()[name]
    assert validate(faithful(packet), packet) == []


def test_hand_written_f06_draft_with_amounts_is_accepted() -> None:
    packet = load_packets()["f06_double_payment"]
    draft = faithful(packet)
    draft["summary"] = (
        "Invoice inv was paid twice: 1000.00 EUR on 2026-06-01 and 1000.00 EUR "
        "on 2026-06-08, so 2000.00 EUR is committed against a 1000.00 EUR invoice."
    )
    assert validate(draft, packet) == []


def test_abstention_with_no_findings_needs_no_claims() -> None:
    packet = load_packets()["insufficient_primary_entity_missing"]
    assert packet.diagnosis.findings == ()
    draft = faithful(packet)
    assert draft["claims"] == []
    assert validate(draft, packet) == []


def has(problems: list[str], text: str) -> bool:
    return any(text in p for p in problems)


def check(name: str, change: Any, expect: str) -> None:
    packet = load_packets()[name]
    draft = copy.deepcopy(faithful(packet))
    change(draft, packet)
    problems = validate(draft, packet)
    assert has(problems, expect), problems


def test_unknown_finding_is_rejected() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["claims"][0]["finding"] = "payment_was_approved"

    check("f06_double_payment", change, "unknown finding")


def test_evidence_of_another_finding_is_rejected() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        other = p.diagnosis.findings[1]
        own = p.diagnosis.findings[0]
        extra = next(i for i in other.evidence_ids if i not in own.evidence_ids)
        d["claims"][0]["evidence_ids"].append(extra)

    check("pending_settlement", change, "is not in finding")


def test_calculation_of_another_finding_is_rejected() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["claims"][0]["calculation_ids"] = [p.calculations[0].calculation_id]

    check("pending_settlement", change, "calculation")


def test_empty_evidence_is_rejected() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["claims"][0]["evidence_ids"] = []

    check("f06_double_payment", change, "no evidence cited")


def test_unknown_evidence_id_is_rejected() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["claims"][0]["evidence_ids"].append("bank:" + "0" * 32)

    check("f06_double_payment", change, "is not in finding")


def test_missing_claim_for_supported_finding_is_rejected() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["claims"].pop()

    check("pending_settlement", change, "has no claim")


@pytest.mark.parametrize(
    "text",
    [
        "A third payment of 1500.00 EUR was also made.",
        "The overpayment is 999.99.",
        "The invoice total is 1000.00 USD.",
        "About 1,234.50 EUR is at risk.",
        "The shortfall is -42.10 EUR.",
        "Posted on 2026-01-01.",
        "The debit cleared in 9 business days.",
    ],
)
def test_amount_date_or_count_not_in_packet_is_rejected(text: str) -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["summary"] += " " + text

    check("f06_double_payment", change, "is not in the packet")


def test_packet_amount_in_another_currency_is_rejected() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["claims"][0]["text"] = "Committed 2000.00 USD."

    check("f06_double_payment", change, "2000.00 USD")


@pytest.mark.parametrize(
    "text",
    [
        "This is F12, a payment missing from the bank.",
        "Looks like legitimate_batch_payment.",
        "There is no_exception here.",
        "This was a legitimate payment.",
    ],
)
def test_wrong_category_is_rejected(text: str) -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["summary"] += " " + text

    check("f06_double_payment", change, "diagnosis")


def test_own_category_may_be_named() -> None:
    packet = load_packets()["f06_double_payment"]
    draft = faithful(packet)
    draft["summary"] = "Diagnosis F06 stands."
    assert validate(draft, packet) == []


def test_abstention_cannot_name_any_category() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["summary"] += " The payment is legitimate."

    check("real_approval_missing", change, "legitimate")


def test_abstention_cannot_name_a_category_code() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["summary"] += " Probably F06."

    check("real_approval_missing", change, "F06")


def test_unknown_action_is_rejected() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["next_step"]["action"] = "release_payment"

    check("f06_double_payment", change, "next_step.action")


def test_none_action_with_a_recommendation_is_rejected() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["next_step"]["action"] = "none"

    check("f06_double_payment", change, "next_step.action")


def test_action_for_a_packet_without_recommendations_must_be_none() -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["next_step"]["action"] = "review_possible_duplicate_payment"

    check("pending_settlement", change, "next_step.action")


@pytest.mark.parametrize("word", ["Pay", "Transfer", "approve", "delete", "write off"])
def test_instruction_to_act_outside_recommendations_is_rejected(word: str) -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["next_step"]["text"] = f"{word} the vendor now."

    check("f06_double_payment", change, "action word")


def test_ordinary_words_are_not_action_words() -> None:
    packet = load_packets()["f06_double_payment"]
    draft = faithful(packet)
    draft["next_step"]["text"] = "Review the payment, payments and the approval."
    assert validate(draft, packet) == []


@pytest.mark.parametrize(
    "token",
    ["f" * 32, "pay:" + "a" * 32, "journal:" + "b" * 32 + ":" + "c" * 32 + ":payment"],
)
def test_unknown_id_in_text_is_rejected(token: str) -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        d["claims"][0]["text"] = f"See {token}."

    check("f06_double_payment", change, "unknown")


def test_known_id_in_text_is_accepted() -> None:
    packet = load_packets()["f06_double_payment"]
    draft = faithful(packet)
    known = packet.diagnosis.findings[0].evidence_ids[1]
    draft["claims"][0]["text"] = f"The second debit is recorded in {known}."
    assert validate(draft, packet) == []


@pytest.mark.parametrize("field", ["summary", "claim", "step"])
def test_over_length_text_is_rejected(field: str) -> None:
    def change(d: dict[str, Any], p: EvidencePacket) -> None:
        if field == "summary":
            d["summary"] = "x" * 601
        elif field == "claim":
            d["claims"][0]["text"] = "x" * 301
        else:
            d["next_step"]["text"] = "x" * 301

    check("f06_double_payment", change, "at most")


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        "text",
        {},
        {
            "summary": "s",
            "claims": [],
            "next_step": {"action": "none", "text": "t"},
            "x": 1,
        },
        {
            "summary": "s",
            "claims": "none",
            "next_step": {"action": "none", "text": "t"},
        },
        {
            "summary": "s",
            "claims": [{"finding": "a"}],
            "next_step": {"action": "none", "text": "t"},
        },
        {"summary": "", "claims": [], "next_step": {"action": "none", "text": "t"}},
        {"summary": "s", "claims": [], "next_step": {"action": 3, "text": "t"}},
    ],
)
def test_malformed_draft_is_rejected(bad: Any) -> None:
    packet = load_packets()["f06_double_payment"]
    assert validate(bad, packet)


def test_claim_on_a_contradicted_finding_of_an_answered_packet_is_rejected() -> None:
    packet = load_packets()["f06_double_payment"]
    d = packet.diagnosis
    contradicted = Finding("not_a_reversal", FindingState.CONTRADICTED, ("inv",))
    packet = replace(packet, diagnosis=replace(d, findings=(*d.findings, contradicted)))
    draft = faithful(packet)
    draft["claims"].append(
        {
            "finding": "not_a_reversal",
            "text": "No reversal.",
            "evidence_ids": ["inv"],
            "calculation_ids": [],
        }
    )
    assert has(validate(draft, packet), "is not supported")


def test_problems_never_quote_long_model_text() -> None:
    packet = load_packets()["f06_double_payment"]
    draft = faithful(packet)
    draft["next_step"]["action"] = "q" * 5000
    assert all(len(p) < 300 for p in validate(draft, packet))


def test_faithful_restatement_of_packet_limitation_is_accepted() -> None:
    packet = load_packets()["f12_payment_missing_from_bank"]
    assert any("do not pay again" in x for x in packet.limitations)
    draft = faithful(packet)
    draft["next_step"]["text"] = (
        "A missing row is not proof; do not pay again unchecked."
    )
    assert validate(draft, packet) == []


def test_action_word_absent_from_the_whole_packet_is_rejected() -> None:
    packet = load_packets()["f06_double_payment"]
    texts = [*packet.limitations]
    for r in packet.recommendations:
        texts += [r.action_type, *r.prerequisites, *r.unresolved_assumptions]
    assert not re.search(r"\bpay\b", " ".join(texts), re.I)
    draft = faithful(packet)
    draft["next_step"]["text"] = "Pay the vendor again."
    assert has(validate(draft, packet), "action word")


def _packet_amount(packet: EvidencePacket) -> tuple[str, str]:
    m = re.search(r"(\d+\.\d\d) ([A-Z]{3})\"", json.dumps(packet.to_obj()))
    assert m
    return m.group(1), m.group(2)


def test_token_after_amount_that_is_not_a_currency_is_a_bare_amount() -> None:
    packet = load_packets()["f06_double_payment"]
    amount, cur = _packet_amount(packet)
    draft = faithful(packet)
    draft["summary"] = f"{cur} {amount} ACH payment was made."
    assert validate(draft, packet) == []


def test_bare_amount_before_non_currency_token_must_still_be_in_the_packet() -> None:
    packet = load_packets()["f06_double_payment"]
    draft = faithful(packet)
    draft["summary"] = "USD 7590.62 ACH payment was made."
    assert has(validate(draft, packet), "7590.62")


def test_real_wrong_currency_after_packet_amount_is_still_rejected() -> None:
    packet = load_packets()["f06_double_payment"]
    draft = faithful(packet)
    amount, cur = _packet_amount(packet)
    other = "USD" if cur != "USD" else "EUR"
    draft["summary"] = f"{amount} {other} was paid."
    assert has(validate(draft, packet), other)
