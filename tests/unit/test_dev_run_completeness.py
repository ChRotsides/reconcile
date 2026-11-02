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
import shutil
import sqlite3
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
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


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_deleting_the_invoice_totals_finding_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    result = check(without_findings(packet, dev_run.TOTALS_FINDING), snap)
    assert not result["complete"] and "invoice_totals_finding" in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_deleting_the_invoice_totals_calculations_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    gone = [
        c["calculation_id"]
        for c in p["calculations"]
        if c["calculation_id"].startswith("invoice_open_balance:")
    ]
    if not gone:  # a pending or reversed payment has no effective allocation
        pytest.skip("no invoice balance in this packet")
    p["calculations"] = [
        c for c in p["calculations"] if c["calculation_id"] not in gone
    ]
    result = check(p, snap)
    assert not result["complete"]
    assert "invoice_totals_calculation" in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_deleting_the_payment_allocation_finding_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    result = check(without_findings(packet, dev_run.PAYMENT_ALLOCATION_FINDING), snap)
    assert not result["complete"]
    assert "payment_allocation_finding" in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_deleting_the_payment_allocation_calculation_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    p["calculations"] = [
        c
        for c in p["calculations"]
        if not c["calculation_id"].startswith("payment_minus_allocations:")
    ]
    result = check(p, snap)
    assert not result["complete"]
    assert "payment_allocation_calculation" in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_a_missing_or_altered_scope_statement_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    assert dev_run.SCOPE_LIMITATION in packet["limitations"]
    for limitations in ([], ["Scope of this all-clear. Checked: everything."]):
        p = copy.deepcopy(packet)
        p["limitations"] = limitations
        result = check(p, snap)
        assert not result["complete"] and result["missing"] == ["scope_statement"]


# ---- substitutions: the right kind of item is present but is the wrong one


def with_calc(packet: Packet, prefix: str, change: Callable[[Packet], None]) -> Packet:
    p = copy.deepcopy(packet)
    hit = [c for c in p["calculations"] if c["calculation_id"].startswith(prefix)]
    assert hit, prefix
    for c in hit:
        change(c)
    return p


def wrong_operation(c: Packet) -> None:
    """A valid sum over the same operands stands in for the difference."""
    c["operation"] = "sum"
    c["result"] = f"{sum(Decimal(o['amount'].split()[0]) for o in c['operands'])} USD"


def result_beyond_tolerance(c: Packet) -> None:
    c["result"] = "5.00 USD"


def operand_dropped(c: Packet) -> None:
    c["operands"] = c["operands"][:-1]


JOURNAL = "payment_minus_payment_journals:"
SUBSTITUTIONS = [wrong_operation, result_beyond_tolerance, operand_dropped]


@pytest.mark.parametrize("change", SUBSTITUTIONS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("name", ALL_CLEAR)
def test_a_wrong_journal_calculation_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str, change: Callable[[Packet], None]
) -> None:
    packet, snap = packets[name]
    result = check(with_calc(packet, JOURNAL, change), snap)
    assert not result["complete"]
    assert "payment_journal_calculation" in result["missing"]


@pytest.mark.parametrize("change", SUBSTITUTIONS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("name", ALL_CLEAR)
def test_a_wrong_invoice_balance_calculation_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str, change: Callable[[Packet], None]
) -> None:
    packet, snap = packets[name]
    if not any(
        c["calculation_id"].startswith("invoice_open_balance:")
        for c in packet["calculations"]
    ):
        pytest.skip("no invoice balance in this packet")
    result = check(with_calc(packet, "invoice_open_balance:", change), snap)
    assert not result["complete"]
    assert "invoice_totals_calculation" in result["missing"]


@pytest.mark.parametrize("change", SUBSTITUTIONS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("name", ALL_CLEAR)
def test_a_wrong_payment_allocation_calculation_makes_an_all_clear_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str, change: Callable[[Packet], None]
) -> None:
    packet, snap = packets[name]
    result = check(with_calc(packet, "payment_minus_allocations:", change), snap)
    assert not result["complete"]
    assert "payment_allocation_calculation" in result["missing"]


def zero_first_allocation(c: Packet) -> None:
    """The first allocation operand is claimed as 0.00 and the result is
    recomputed to match, so the packet's own arithmetic is consistent."""
    cur = c["operands"][1]["amount"].split()[1]
    c["operands"][1]["amount"] = f"0.00 {cur}"
    amounts = [Decimal(o["amount"].split()[0]) for o in c["operands"]]
    c["result"] = f"{amounts[0] - sum(amounts[1:], Decimal(0))} {cur}"


@pytest.mark.parametrize(
    "prefix", ["payment_minus_allocations:", "invoice_open_balance:"]
)
@pytest.mark.parametrize("name", ALL_CLEAR)
def test_a_wrong_allocation_operand_with_a_consistent_result_is_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str, prefix: str
) -> None:
    """The operand is not the snapshot's allocation amount, but the result was
    recomputed from it: only a comparison with the snapshot catches this."""
    packet, snap = packets[name]
    if not any(c["calculation_id"].startswith(prefix) for c in packet["calculations"]):
        pytest.skip("no such calculation in this packet")
    result = check(with_calc(packet, prefix, zero_first_allocation), snap)
    assert not result["complete"]
    assert {
        "payment_minus_allocations:": "payment_allocation_calculation",
        "invoice_open_balance:": "invoice_totals_calculation",
    }[prefix] in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_a_journal_operand_above_the_attributable_total_is_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str, tmp_path: Path
) -> None:
    """The packet says the payment journal is the payment amount (result 0); the
    snapshot's payment-attributable total is half of that."""
    packet, snap = packets[name]
    halved = mutated(
        snap,
        tmp_path,
        "UPDATE gl_entries SET debit = printf('%.2f', CAST(debit AS REAL) / 2),"
        " credit = printf('%.2f', CAST(credit AS REAL) / 2)"
        " WHERE transaction_type = 'payment'",
    )
    result = check(packet, halved)
    assert not result["complete"]
    assert "payment_journal_calculation" in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_a_journal_item_attributed_to_another_source_is_incomplete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    """Where a journal item records its source and GL type, they must be this
    payment and `payment`."""
    packet, snap = packets[name]
    for field, wrong in (("source", "SOMETHING_ELSE"), ("gl_type", "bank_fee")):
        p = copy.deepcopy(packet)
        hit = [
            e
            for e in p["evidence"]
            if e["record"] and e["record"]["table"] == "gl_entries"
        ]
        for e in hit:
            e["values"][field] = wrong
        result = check(p, snap)
        assert not result["complete"]
        assert "payment_journal_calculation" in result["missing"]


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_journals_that_are_not_operands_of_the_payment_calculation_are_not_checked_here(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    """A split cites the other payment's journal, a fee case a bank-fee journal,
    a reversal its reversal journal: their attribution is not this check's
    business, so re-attributing them leaves the packet complete."""
    packet, snap = packets[name]
    operands = {
        o["evidence_id"]
        for c in packet["calculations"]
        if c["calculation_id"].startswith("payment_minus_payment_journals:")
        for o in c["operands"]
    }
    p = copy.deepcopy(packet)
    for e in p["evidence"]:
        if e["record"] and e["record"]["table"] == "gl_entries":
            if e["evidence_id"] not in operands:
                e["values"]["source"] = "SOMETHING_ELSE"
                e["values"]["gl_type"] = "bank_fee"
    assert check(p, snap)["complete"]


def mutated(snap: Any, tmp_path: Path, *sql: str) -> Any:
    """A copy of the snapshot with hand-written SQL applied, as a Snap."""
    source = Path(snap.con.execute("PRAGMA database_list").fetchone()[2])
    copy_ = tmp_path / "mutated.sqlite"
    shutil.copy(source, copy_)
    copy_.chmod(0o644)
    con = sqlite3.connect(copy_)
    for statement in sql:
        con.execute(statement)
    con.commit()
    con.close()
    return dev_run.Snap(copy_)


def cited_approval(packet: Packet, snap: Any) -> dict[str, Any]:
    ids = [
        e["record"]["record_id"]
        for e in packet["evidence"]
        if e["record"] and e["record"]["table"] == "approval_events"
    ]
    assert ids
    return dict(
        snap.one("SELECT * FROM approval_events WHERE approval_event_id = ?", ids[0])
    )


def next_second(timestamp: str) -> str:
    moved = datetime.fromisoformat(timestamp) + timedelta(seconds=1)
    assert moved.date() == datetime.fromisoformat(timestamp).date()
    return moved.isoformat()


def add_event(e: dict[str, Any], action: str, status: str) -> str:
    return (
        "INSERT INTO approval_events VALUES ('x_new', "
        f"'{e['invoice_id']}', '{e['approval_level']}', 'manager', '{action}', "
        f"'{next_second(e['event_timestamp'])}', 'approved', '{status}')"
    )


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_an_approval_dated_after_the_payment_does_not_complete_an_all_clear(
    packets: dict[str, tuple[Packet, Any]], name: str, tmp_path: Path
) -> None:
    packet, snap = packets[name]
    e = cited_approval(packet, snap)
    late = mutated(
        snap,
        tmp_path,
        "UPDATE approval_events SET event_timestamp = '2099-01-01T00:00:00'"
        f" WHERE approval_event_id = '{e['approval_event_id']}'",
    )
    result = check(packet, late)
    assert not result["complete"]
    assert {"approval_event", "approval_finding"} <= set(result["missing"])


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_a_revoked_approval_does_not_complete_an_all_clear(
    packets: dict[str, tuple[Packet, Any]], name: str, tmp_path: Path
) -> None:
    packet, snap = packets[name]
    e = cited_approval(packet, snap)
    revoked = mutated(snap, tmp_path, add_event(e, "rejected", "rejected"))
    result = check(packet, revoked)
    assert not result["complete"]
    assert {"approval_event", "approval_finding"} <= set(result["missing"])


@pytest.mark.parametrize("name", ALL_CLEAR)
def test_a_superseded_approval_does_not_complete_an_all_clear(
    packets: dict[str, tuple[Packet, Any]], name: str, tmp_path: Path
) -> None:
    """A later re-approval exists: the packet cites the older event, which is no
    longer the latest, so it does not show the approval in force."""
    packet, snap = packets[name]
    e = cited_approval(packet, snap)
    newer = mutated(snap, tmp_path, add_event(e, "approved", "approved"))
    result = check(packet, newer)
    assert not result["complete"] and "approval_event" in result["missing"]


def test_deleting_unrequired_content_keeps_a_complete_packet_complete(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    """Control for the controls: removing things the contract does not require
    (recommendations, provenance) changes nothing, so the failures above come
    from the deleted or substituted requirement."""
    packet, snap = packets["weekend_clearing"]
    p = copy.deepcopy(packet)
    p["recommendations"] = []
    p["provenance"] = {}
    assert check(p, snap)["complete"]
