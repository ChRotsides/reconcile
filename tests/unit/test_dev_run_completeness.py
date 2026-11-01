"""Negative controls for the evidence-completeness check of evaluation/dev_run.py.

Each test takes a packet the investigator produced for a public fixture, checks
that it is complete, deletes one class of required evidence and requires the
packet to become incomplete (docs/evidence-contract.md section 5, contract 0.3,
and principle 3 for the absence searches). A check that still passes after the
deletion would let an unsupported all-clear count as "correct and
evidence-complete".
"""

import copy
import importlib
import json
import sys
from collections.abc import Callable
from datetime import date
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
from reconcile.domain.contracts import InvestigationRequest, RecordRef

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "public"
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected.json").read_text())
CUTOFF = date(2026, 6, 30)
NAMESPACE = {
    "invoices": "invoice",
    "payments": "payment",
    "bank_transactions": "bank_txn",
    "gl_entries": "journal",
    "approval_events": "approval",
}

sys.path.insert(0, str(ROOT / "evaluation"))
dev_run = importlib.import_module("dev_run")

Packet = dict[str, Any]
ALL_CLEAR = sorted(
    n
    for n, e in EXPECTED.items()
    if e["disposition"] == "answered"
    and (e["category"].startswith("legitimate_") or e["category"] == "no_exception")
)
ABSENCE = {  # fixture -> number of absence searches its category requires
    "f12_payment_missing_from_bank": 3,
    "pending_settlement": 3,
    "f14_amount_shortfall": 1,
    "real_netted_fee_no_journal": 1,
}


@pytest.fixture(scope="module")
def packets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Packet, Any]]:
    """fixture name -> (investigator packet as a dict, Snap of its snapshot)."""
    root = tmp_path_factory.mktemp("completeness")
    key = tmp_path_factory.mktemp("key") / "key"
    create_key_file(key)
    tokens = _Tokens(_load_key(key))
    out: dict[str, tuple[Packet, Any]] = {}
    for name in sorted({*ALL_CLEAR, *ABSENCE}):
        m = prepare_finrca_snapshot(
            FIXTURES / name,
            root / name,
            key_path=key,
            source_revision=f"fixture:{name}",
            cutoff=CUTOFF,
        )
        path = root / name / m["snapshot"]["file"]
        digest = m["snapshot"]["content_sha256"]
        e = EXPECTED[name]["primary_entity"]
        request = InvestigationRequest(
            "req_test",
            digest,
            RecordRef(digest, e["table"], tokens.token(NAMESPACE[e["table"]], e["id"])),
            "Check whether there is a reconciliation issue.",
            CUTOFF,
            "policy_test",
        )
        packet = json.loads(investigate(path, request).to_json())
        out[name] = (packet, dev_run.Snap(path))
    return out


def check(packet: Packet, snap: Any) -> dict[str, Any]:
    ent = packet["request"]["primary_entity"]
    result: dict[str, Any] = dev_run.completeness(
        packet, (ent["table"], ent["record_id"]), snap
    )
    return result


def without_evidence(packet: Packet, drop: Callable[[Packet], bool]) -> Packet:
    p = copy.deepcopy(packet)
    p["evidence"] = [e for e in p["evidence"] if not drop(e)]
    return p


def without_evidence_id(packet: Packet, evidence_id: str) -> Packet:
    return without_evidence(packet, lambda e: bool(e["evidence_id"] == evidence_id))


def without_findings(packet: Packet, check_id: str) -> Packet:
    p = copy.deepcopy(packet)
    p["diagnosis"]["findings"] = [
        f for f in p["diagnosis"]["findings"] if f["check_id"] != check_id
    ]
    return p


def table(e: Packet) -> str | None:
    return e["record"]["table"] if e["record"] else None


@pytest.mark.parametrize("name", sorted({*ALL_CLEAR, *ABSENCE}))
def test_unmodified_packet_is_complete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    result = check(packet, snap)
    assert result["contract_defined"]
    assert result["complete"], result["missing"]
    assert result["searches_mismatch"] == []


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_deleting_the_approval_event_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    result = check(
        without_evidence(packet, lambda e: table(e) == "approval_events"), snap
    )
    assert not result["complete"] and "approval_event" in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_deleting_the_approval_finding_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    result = check(without_findings(packet, dev_run.APPROVAL_FINDING), snap)
    assert not result["complete"] and "approval_finding" in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_deleting_the_payment_journal_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    result = check(
        without_evidence(
            packet,
            lambda e: (
                table(e) == "gl_entries" and e["relationship"] == "payment_journal"
            ),
        ),
        snap,
    )
    assert not result["complete"] and "payment_journal" in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_deleting_the_journal_calculation_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    p["calculations"] = [
        c
        for c in p["calculations"]
        if not c["calculation_id"].startswith("payment_minus_payment_journals")
    ]
    result = check(p, snap)
    assert not result["complete"]
    assert "payment_journal_calculation" in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_deleting_the_journal_finding_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    result = check(without_findings(packet, dev_run.JOURNAL_FINDING), snap)
    assert not result["complete"] and "payment_journal_finding" in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_deleting_the_allocations_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    result = check(
        without_evidence(packet, lambda e: table(e) == "payment_allocations"), snap
    )
    assert not result["complete"] and "guard_allocations" in result["missing"]


@pytest.mark.parametrize("name", sorted(ABSENCE))
def test_deleting_any_required_absence_search_makes_the_packet_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    searches = [e["evidence_id"] for e in packet["evidence"] if e["search"]]
    assert len(searches) >= ABSENCE[name]
    dropped = 0
    for sid in searches:
        result = check(without_evidence_id(packet, sid), snap)
        dropped += not result["complete"]
    assert dropped == ABSENCE[name]  # exactly the required ones matter


@pytest.mark.parametrize("name", ["weekend_clearing", "f12_payment_missing_from_bank"])
def test_a_false_absence_claim_makes_the_packet_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    """A count-0 search over rows that exist: the contract's own requirements
    still hold, but the claim does not reproduce on the snapshot."""
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    p["evidence"].append(
        {
            "evidence_id": "search:false_absence",
            "record": None,
            "relationship": "absence_search",
            "search": {
                "table": "payments",
                "predicate": "payment_id IS NOT NULL",
                "observed_count": 0,
            },
            "timestamps": {},
            "values": {},
        }
    )
    result = check(p, snap)
    assert not result["complete"]
    assert result["missing"] == ["searches_reproduce"]
    assert result["searches_mismatch"]


def test_deleting_unrequired_text_keeps_a_complete_packet_complete(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    """Control for the controls: removing something the contract does not
    require (the limitations text) changes nothing, so the failures above come
    from the deleted requirement."""
    packet, snap = packets["weekend_clearing"]
    p = copy.deepcopy(packet)
    p["limitations"] = []
    assert check(p, snap)["complete"]
