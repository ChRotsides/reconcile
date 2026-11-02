"""The evaluator checks the meaning of the calculations of the legitimate
net-fee, accounted-bank-fee and payment-reversal variants (contract section 5),
not only that some exact calculation is present. Each mutation below is
arithmetically exact (the audit passes) yet must fail its requirement."""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_dev_run_audit import as_sum, calc, contract_fails, score
from test_dev_run_completeness import Packet, build_packets, check, mutated

NET = "net_fee_accounting"
FEE = "accounted_bank_fee"
REV = "payment_reversal"
FEE_REQ = {NET: "unexplained_calculation", FEE: "fee_calculation"}
FEE_CALC = {NET: "unexplained", FEE: "fee_row_minus_journals"}
REV_CALCS = ["debit_minus_credit", "credit_minus_journal", "journal_minus_payment"]


@pytest.fixture(scope="module")
def packets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Packet, Any]]:
    return build_packets(tmp_path_factory)


def amount(text: str) -> Decimal:
    return Decimal(text.split()[0])


def restate(c: Packet) -> None:
    """Recompute a difference result from its (possibly edited) operands."""
    first, *rest = (amount(o["amount"]) for o in c["operands"])
    c["result"] = f"{first - sum(rest, Decimal(0))} {c['result'].split()[1]}"


def evidence_of(packet: Packet, table: str, gl_type: str | None = None) -> Packet:
    found: Packet = next(
        e
        for e in packet["evidence"]
        if e["record"]
        and e["record"]["table"] == table
        and (gl_type is None or e["values"].get("gl_type") == gl_type)
    )
    return found


@pytest.mark.parametrize("name", [NET, FEE, REV])
def test_the_investigators_packets_are_correct_and_complete(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    result = check(packet, snap)
    assert result["complete"], result["missing"]
    assert score(name, packet, snap)["primary"] == 1


# ------------------------------------------------- net fee and accounted fee


@pytest.mark.parametrize("name", [NET, FEE])
def test_fee_calculation_must_be_a_difference(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    as_sum(calc(p, FEE_CALC[name]))
    contract_fails(name, packets, p, snap, FEE_REQ[name])


@pytest.mark.parametrize("name", [NET, FEE])
def test_fee_calculation_without_the_journal_is_not_the_accounting(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    """The journal operand dropped and the result recomputed: exact, but the
    remainder (the whole fee) is not within a quantum."""
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    c = calc(p, FEE_CALC[name])
    c["operands"].pop()
    restate(c)
    assert amount(c["result"]) != 0
    contract_fails(name, packets, p, snap, FEE_REQ[name])


@pytest.mark.parametrize("name", [NET, FEE])
def test_fee_calculation_with_an_extra_operand_fails(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    c = calc(p, FEE_CALC[name])
    journal = c["operands"][-1]
    c["operands"].append(copy.deepcopy(journal))  # the journal counted twice
    restate(c)
    contract_fails(name, packets, p, snap, FEE_REQ[name])


@pytest.mark.parametrize("name", [NET, FEE])
def test_fee_calculation_operand_from_the_wrong_record_fails(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    """The fee journal operand replaced by the payment's allocation: exact
    arithmetic, but not the journal booking the fee."""
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    c = calc(p, FEE_CALC[name])
    alloc = evidence_of(p, "payment_allocations")
    c["operands"][-1] = {
        "evidence_id": alloc["evidence_id"],
        "field": "allocated_amount",
        "amount": alloc["values"]["allocated_amount"],
    }
    restate(c)
    contract_fails(name, packets, p, snap, FEE_REQ[name])


def test_net_fee_operands_in_the_wrong_order_fail(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets[NET]
    p = copy.deepcopy(packet)
    c = calc(p, "unexplained")
    c["operands"][0], c["operands"][1] = c["operands"][1], c["operands"][0]
    restate(c)
    contract_fails(NET, packets, p, snap, "unexplained_calculation")


def test_net_fee_unexplained_outside_the_tolerance_fails(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    """Bank debit lowered by 10 in the snapshot (the packet follows it): the
    calculations are exact and say the fee leaves 10 unexplained."""
    packet, snap = packets[NET]
    bank = evidence_of(packet, "bank_transactions")
    snap2 = mutated(
        snap,
        tmp_path,
        "UPDATE bank_transactions SET amount = '970.00' WHERE bank_transaction_id"
        f" = '{bank['record']['record_id']}'",
    )
    p = copy.deepcopy(packet)
    for e in p["evidence"]:
        if e["evidence_id"] == bank["evidence_id"]:
            e["values"]["amount"] = "970.00 EUR"
    for c in p["calculations"]:
        for o in c["operands"]:
            if o["evidence_id"] == bank["evidence_id"]:
                o["amount"] = "970.00 EUR"
        if c["operation"] == "difference" and any(
            o["evidence_id"] == bank["evidence_id"] for o in c["operands"]
        ):
            restate(c)
    assert calc(p, "unexplained")["result"] == "10.00 EUR"
    result = check(p, snap2)
    assert result["audit_ok"]
    assert "unexplained_calculation" in result["missing"]


# ---------------------------------------------------------- payment reversal


@pytest.mark.parametrize("cid", REV_CALCS)
def test_reversal_equality_must_be_a_difference(
    packets: dict[str, tuple[Packet, Any]], cid: str
) -> None:
    packet, snap = packets[REV]
    p = copy.deepcopy(packet)
    as_sum(calc(p, cid))
    contract_fails(REV, packets, p, snap, "reversal_calculations")


@pytest.mark.parametrize("cid", REV_CALCS)
def test_reversal_equality_missing_fails(
    packets: dict[str, tuple[Packet, Any]], cid: str
) -> None:
    packet, snap = packets[REV]
    p = copy.deepcopy(packet)
    calc(p, cid)["calculation_id"] = f"{cid}_renamed"
    for f in p["diagnosis"]["findings"]:
        f["calculation_ids"] = [
            f"{cid}_renamed" if i == cid else i for i in f["calculation_ids"]
        ]
    contract_fails(REV, packets, p, snap, "reversal_calculations")


@pytest.mark.parametrize("cid", REV_CALCS)
def test_reversal_equality_with_an_extra_operand_fails(
    packets: dict[str, tuple[Packet, Any]], cid: str
) -> None:
    packet, snap = packets[REV]
    p = copy.deepcopy(packet)
    c = calc(p, cid)
    c["operands"].append(copy.deepcopy(c["operands"][1]))  # counted twice
    restate(c)
    contract_fails(REV, packets, p, snap, "reversal_calculations")


@pytest.mark.parametrize("cid", REV_CALCS)
def test_reversal_equality_operand_from_the_wrong_record_fails(
    packets: dict[str, tuple[Packet, Any]], cid: str
) -> None:
    """The second operand replaced by another record of the same amount in the
    packet (all four reversal amounts are equal): still exact, wrong relation."""
    packet, snap = packets[REV]
    p = copy.deepcopy(packet)
    c = calc(p, cid)
    used = {o["evidence_id"] for o in c["operands"]}
    other = next(
        e
        for e in p["evidence"]
        if e["evidence_id"] not in used
        and e["record"]["table"] in ("bank_transactions", "gl_entries", "payments")
        and any(k in e["values"] for k in ("amount", "payment_amount"))
        and e["values"].get("gl_type") != "payment"
    )
    field = "payment_amount" if "payment_amount" in other["values"] else "amount"
    c["operands"][1] = {
        "evidence_id": other["evidence_id"],
        "field": field,
        "amount": other["values"][field],
    }
    restate(c)
    assert amount(c["result"]) == 0
    result = check(p, snap)
    assert "reversal_calculations" in result["missing"]
    assert score(REV, p, snap)["primary"] == 0


def test_reversal_amounts_that_differ_beyond_the_tolerance_fail(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    """Credit raised by 5 in the snapshot, the packet following it: exact, and
    the calculations say debit and credit differ."""
    packet, snap = packets[REV]
    credit = next(
        e
        for e in packet["evidence"]
        if e["record"]["table"] == "bank_transactions"
        and e["values"]["direction"] == "credit"
    )
    old = amount(credit["values"]["amount"])
    new = f"{old + 5}"
    snap2 = mutated(
        snap,
        tmp_path,
        f"UPDATE bank_transactions SET amount = '{new}' WHERE bank_transaction_id"
        f" = '{credit['record']['record_id']}'",
    )
    p = copy.deepcopy(packet)
    for e in p["evidence"]:
        if e["evidence_id"] == credit["evidence_id"]:
            e["values"]["amount"] = f"{new} EUR"
    for c in p["calculations"]:
        hit = False
        for o in c["operands"]:
            if o["evidence_id"] == credit["evidence_id"]:
                o["amount"] = f"{new} EUR"
                hit = True
        if hit and c["operation"] == "difference":
            restate(c)
    result = check(p, snap2)
    assert result["audit_ok"]
    assert "reversal_calculations" in result["missing"]


# ------------------------------------------- each condition on its own


@pytest.mark.parametrize("name", [NET, FEE])
def test_fee_calculation_tolerance_must_be_one_quantum(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    packet, snap = packets[name]
    p = copy.deepcopy(packet)
    calc(p, FEE_CALC[name])["tolerance"] = "1.00 EUR"
    result = check(p, snap)
    assert FEE_REQ[name] in result["missing"]


@pytest.mark.parametrize("name", [NET, FEE, REV])
def test_a_stated_result_that_is_not_the_operands_difference_fails_on_its_own(
    packets: dict[str, tuple[Packet, Any]], name: str
) -> None:
    """Whatever the audit says, the requirement recomputes the result."""
    packet, snap = packets[name]
    cid = {NET: "unexplained", FEE: "fee_row_minus_journals", REV: REV_CALCS[0]}[name]
    p = copy.deepcopy(packet)
    calc(p, cid)["result"] = "0.01 EUR"
    req = {**FEE_REQ, REV: "reversal_calculations"}[name]
    assert req in check(p, snap)["missing"]


def test_net_fee_needs_the_delta_to_be_a_shortfall_difference(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    packet, snap = packets[NET]
    p = copy.deepcopy(packet)
    as_sum(calc(p, "delta"))
    assert "unexplained_calculation" in check(p, snap)["missing"]


def test_accounted_fee_journal_replaced_by_the_fee_row_itself_fails(
    packets: dict[str, tuple[Packet, Any]],
) -> None:
    """Fee row minus fee row is exactly zero but books nothing."""
    packet, snap = packets[FEE]
    p = copy.deepcopy(packet)
    c = calc(p, "fee_row_minus_journals")
    c["operands"][1] = copy.deepcopy(c["operands"][0])
    restate(c)
    assert amount(c["result"]) == 0
    contract_fails(FEE, packets, p, snap, "fee_calculation")


def test_reversal_credit_dated_before_the_debit_fails(
    packets: dict[str, tuple[Packet, Any]], tmp_path: Path
) -> None:
    """Amounts and citations are all valid; only the snapshot credit date moves
    before the debit's (the contract: credit not earlier than debit)."""
    packet, snap = packets[REV]
    credit = next(
        e
        for e in packet["evidence"]
        if e["record"]
        and e["record"]["table"] == "bank_transactions"
        and e["values"]["direction"] == "credit"
    )
    debit = next(
        e
        for e in packet["evidence"]
        if e["record"]
        and e["record"]["table"] == "bank_transactions"
        and e["values"]["direction"] == "debit"
    )
    cid, did = credit["record"]["record_id"], debit["record"]["record_id"]
    assert check(packet, snap)["complete"]
    snap2 = mutated(
        snap,
        tmp_path,
        "UPDATE bank_transactions SET posted_date = '1999-01-01'"
        f" WHERE bank_transaction_id = '{cid}'",
    )
    assert (
        snap2.one(
            "SELECT posted_date FROM bank_transactions WHERE bank_transaction_id = ?",
            did,
        )["posted_date"]
        > "1999-01-01"
    )
    assert "reversal_calculations" in check(packet, snap2)["missing"]
