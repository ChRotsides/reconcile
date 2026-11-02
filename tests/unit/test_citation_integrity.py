"""Citation integrity over every public fixture, checked independently of the
packet constructor (which already validates most of this) and against the
snapshot file itself. The negative tests mutate a valid packet past the
constructor to show each assertion can fail."""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reconcile.adapters.finrca import (
    KEYS,
    _load_key,
    _Tokens,
    create_key_file,
    prepare_finrca_snapshot,
)
from reconcile.diagnosis.pipeline import investigate
from reconcile.domain.contracts import (
    EvidencePacket,
    InvestigationRequest,
    Recommendation,
    RecordRef,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "public"
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected.json").read_text())
CUTOFF = date(2026, 6, 30)
NAMESPACE = {
    "invoices": "invoice",
    "payments": "payment",
    "bank_transactions": "bank_txn",
    "gl_entries": "journal",
    "approval_events": "approval",
}
Packets = dict[str, tuple[EvidencePacket, Path]]
# A journal is cited by its journal id (a journal has several lines).
CITED_BY = {**KEYS, "gl_entries": ("journal_id",)}


def _token(tokens: _Tokens, table: str, raw: str) -> str:
    if "|" in raw:  # allocation: payment|invoice
        p, i = raw.split("|")
        return f"{_token(tokens, 'payments', p)}|{_token(tokens, 'invoices', i)}"
    return tokens.token(NAMESPACE[table], raw)


@pytest.fixture(scope="module")
def packets(tmp_path_factory: pytest.TempPathFactory) -> Packets:
    root = tmp_path_factory.mktemp("cite")
    key = root / "key"
    create_key_file(key)
    tokens = _Tokens(_load_key(key))
    out: Packets = {}
    for name, exp in EXPECTED.items():
        m = prepare_finrca_snapshot(
            FIXTURES / name,
            root / name,
            key_path=key,
            source_revision=f"fixture:{name}",
            cutoff=CUTOFF,
        )
        snap = root / name / m["snapshot"]["file"]
        digest = m["snapshot"]["content_sha256"]
        e = exp["primary_entity"]
        request = InvestigationRequest(
            "req_test",
            digest,
            RecordRef(digest, e["table"], _token(tokens, e["table"], e["id"])),
            "Check whether there is a reconciliation issue.",
            CUTOFF,
            "policy_test",
        )
        out[name] = (investigate(snap, request), snap)
    return out


def check_citations(packet: EvidencePacket, snapshot: Path) -> None:
    evidence = {e.evidence_id: e for e in packet.evidence}
    calc_ids = {c.calculation_id for c in packet.calculations} | {
        c.calculation_id for c in packet.day_counts
    }
    for f in packet.diagnosis.findings:
        for i in f.evidence_ids:
            assert i in evidence, f"{f.check_id}: finding cites unknown evidence {i}"
        for i in f.calculation_ids:
            assert i in calc_ids, f"{f.check_id}: finding names unknown calculation {i}"
    for c in packet.calculations:
        for op in c.operands:
            assert op.evidence_id in evidence, f"{c.calculation_id}: unknown operand"
            recorded = dict(evidence[op.evidence_id].values).get(op.field)
            assert recorded == str(op.amount), f"{c.calculation_id}: operand differs"
    checks = {f.check_id for f in packet.diagnosis.findings}
    for r in packet.recommendations:
        for i in r.supporting_findings:
            assert i in checks, f"{r.action_type}: unknown finding {i}"
    con = sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for e in packet.evidence:
            if e.record is None:
                continue
            cols = CITED_BY[e.record.table]
            parts = e.record.record_id.split("|")
            assert len(parts) == len(cols), f"{e.evidence_id}: malformed record id"
            where = " AND ".join(f'"{c}" = ?' for c in cols)
            found = con.execute(
                f'SELECT 1 FROM "{e.record.table}" WHERE {where}', tuple(parts)
            ).fetchone()
            assert found, f"{e.evidence_id}: record not in snapshot"
    finally:
        con.close()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_citation_resolves(packets: Packets, name: str) -> None:
    packet, snap = packets[name]
    check_citations(packet, snap)


def test_the_fixtures_exercise_every_kind_of_citation(packets: Packets) -> None:
    every = [p for p, _ in packets.values()]
    assert any(f.evidence_ids for p in every for f in p.diagnosis.findings)
    assert any(f.calculation_ids for p in every for f in p.diagnosis.findings)
    assert any(c.operands for p in every for c in p.calculations)
    assert any(p.recommendations for p in every)
    assert any(e.record for p in every for e in p.evidence)


def _force(packet: EvidencePacket, **fields: object) -> EvidencePacket:
    """Bypass the constructor's own validation, which would reject these."""
    broken = replace(packet)
    for k, v in fields.items():
        object.__setattr__(broken, k, v)
    return broken


def _with_finding(packet: EvidencePacket, **fields: Any) -> EvidencePacket:
    f = next(f for f in packet.diagnosis.findings if f.evidence_ids)
    d = packet.diagnosis
    diag = replace(d)
    object.__setattr__(
        diag,
        "findings",
        tuple(replace(f, **fields) if x is f else x for x in d.findings),
    )
    return _force(packet, diagnosis=diag)


def _first_with(
    packets: Packets, pick: Callable[[EvidencePacket], bool]
) -> tuple[EvidencePacket, Path]:
    return next(pair for pair in packets.values() if pick(pair[0]))


def _has_cited_finding(p: EvidencePacket) -> bool:
    return any(f.evidence_ids for f in p.diagnosis.findings)


def _has_operand(p: EvidencePacket) -> bool:
    return any(c.operands for c in p.calculations)


def test_checker_rejects_a_finding_citing_unknown_evidence(packets: Packets) -> None:
    packet, snap = _first_with(packets, _has_cited_finding)
    check_citations(packet, snap)
    with pytest.raises(AssertionError, match="unknown evidence"):
        check_citations(_with_finding(packet, evidence_ids=("e_nope",)), snap)


def test_checker_rejects_a_finding_naming_unknown_calculation(
    packets: Packets,
) -> None:
    packet, snap = _first_with(packets, _has_cited_finding)
    with pytest.raises(AssertionError, match="unknown calculation"):
        check_citations(_with_finding(packet, calculation_ids=("c_nope",)), snap)


def test_checker_rejects_an_operand_without_its_evidence(packets: Packets) -> None:
    packet, snap = _first_with(packets, _has_operand)
    op_id = next(c.operands[0].evidence_id for c in packet.calculations if c.operands)
    kept = tuple(e for e in packet.evidence if e.evidence_id != op_id)
    broken = _force(packet, evidence=kept)
    with pytest.raises(AssertionError):
        check_citations(broken, snap)


def test_checker_rejects_an_operand_that_differs_from_the_record(
    packets: Packets,
) -> None:
    packet, snap = _first_with(packets, _has_operand)
    op = next(c.operands[0] for c in packet.calculations if c.operands)
    item = next(e for e in packet.evidence if e.evidence_id == op.evidence_id)
    changed = tuple((k, "0.01" if k == op.field else v) for k, v in item.values)
    swapped = tuple(
        replace(e, values=changed) if e is item else e for e in packet.evidence
    )
    with pytest.raises(AssertionError, match="operand differs"):
        check_citations(_force(packet, evidence=swapped), snap)


def test_checker_rejects_a_recommendation_for_an_unknown_finding(
    packets: Packets,
) -> None:
    packet, snap = next(iter(packets.values()))
    bad = Recommendation("review", ("no_such_check",))
    with pytest.raises(AssertionError, match="unknown finding"):
        check_citations(_force(packet, recommendations=(bad,)), snap)


def test_checker_rejects_a_record_missing_from_the_snapshot(packets: Packets) -> None:
    packet, snap = _first_with(packets, lambda p: any(e.record for e in p.evidence))
    item = next(e for e in packet.evidence if e.record)
    assert item.record is not None
    ghost_id = "|".join("ghost" for _ in CITED_BY[item.record.table])
    ghost = replace(
        item, record=RecordRef(item.record.snapshot, item.record.table, ghost_id)
    )
    broken = _force(
        packet, evidence=tuple(ghost if e is item else e for e in packet.evidence)
    )
    with pytest.raises(AssertionError, match="not in snapshot"):
        check_citations(broken, snap)
