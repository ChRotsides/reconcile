"""Invoice-primary investigator on the public fixtures (expected.json is the label)."""

import ast
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reconcile.adapters.finrca import (
    TABLES,
    _load_key,
    _Tokens,
    create_key_file,
    prepare_finrca_snapshot,
)
from reconcile.diagnosis import pipeline
from reconcile.diagnosis.invoice import diagnose_invoice
from reconcile.diagnosis.pipeline import investigate
from reconcile.domain.contracts import (
    Disposition,
    EvidencePacket,
    InvestigationRequest,
    RecordRef,
)
from reconcile.domain.money import Money
from reconcile.retrieval.snapshot import Limits

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "public"
EXPECTED: dict[str, Any] = json.loads((FIXTURES / "expected.json").read_text())
CUTOFF = date(2026, 6, 30)
NAMESPACE = {
    "invoices": "invoice",
    "payments": "payment",
    "bank_transactions": "bank_txn",
}
# expected.json name -> (our calculation id, sign): our difference operations
# compute "invoice total minus X", the contract's overpayment is "X minus total".
ALIASES = {
    "committed_overpayment": ("invoice_total_minus_committed", -1),
    "allocated_minus_invoice_total": ("invoice_total_minus_allocated", -1),
}
INVOICE_SCENARIOS = [
    n for n, e in EXPECTED.items() if e["primary_entity"]["table"] == "invoices"
]


@dataclass
class Prepared:
    snapshot: Path
    digest: str
    tokens: _Tokens

    def token(self, table: str, raw: str) -> str:
        if "|" in raw:  # allocation: payment|invoice
            p, i = raw.split("|")
            return f"{self.token('payments', p)}|{self.token('invoices', i)}"
        return self.tokens.token(NAMESPACE[table], raw)

    def request(self, table: str, raw: str) -> InvestigationRequest:
        return InvestigationRequest(
            "req_test",
            self.digest,
            RecordRef(self.digest, table, self.token(table, raw)),
            "Check whether there is a reconciliation issue.",
            CUTOFF,
            "policy_test",
        )


@pytest.fixture(scope="module")
def prepared(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Prepared]:
    root = tmp_path_factory.mktemp("inv")
    key = tmp_path_factory.mktemp("key") / "key"
    create_key_file(key)
    out: dict[str, Prepared] = {}
    for name in EXPECTED:
        m = prepare_finrca_snapshot(
            FIXTURES / name,
            root / name,
            key_path=key,
            source_revision=f"fixture:{name}",
            cutoff=CUTOFF,
        )
        out[name] = Prepared(
            root / name / m["snapshot"]["file"],
            m["snapshot"]["content_sha256"],
            _Tokens(_load_key(key)),
        )
    return out


def clone(p: Prepared, tmp_path: Path, *sql: str) -> Path:
    """Copy a snapshot and its manifest into tmp_path, then apply hand-written SQL."""
    out = tmp_path / "snap"
    out.mkdir(exist_ok=True)
    shutil.copy(p.snapshot.parent / "manifest.json", out / "manifest.json")
    copy = out / "snapshot.sqlite"
    shutil.copy(p.snapshot, copy)
    copy.chmod(0o644)
    con = sqlite3.connect(copy)
    for statement in sql:
        con.execute(statement)
    con.commit()
    con.close()
    return copy


def run(prepared: dict[str, Prepared], name: str, **kw: Any) -> EvidencePacket:
    p = prepared[name]
    e = EXPECTED[name]["primary_entity"]
    return investigate(p.snapshot, p.request(e["table"], e["id"]), **kw)


def test_scenario_lists_cover_expected() -> None:
    assert len(TABLES) and sorted(INVOICE_SCENARIOS) == [
        "due_date_payment",
        "f06_double_payment",
        "f06_second_debit_post_cutoff",
        "split_payment",
    ]


@pytest.mark.parametrize("name", INVOICE_SCENARIOS)
def test_invoice_scenario_matches_expected(
    prepared: dict[str, Prepared], name: str
) -> None:
    exp, p = EXPECTED[name], prepared[name]
    packet = run(prepared, name)
    d = packet.diagnosis
    assert d.disposition.value == exp["disposition"]
    assert d.category == exp["category"]
    assert d.reason == exp.get("abstention_reason")

    # Cited records: exactly the required ones (nothing extra exists in these fixtures).
    cited = {(e.record.table, e.record.record_id) for e in packet.evidence if e.record}
    required = {(e["table"], p.token(e["table"], e["id"])) for e in exp["evidence"]}
    assert cited == required

    # Absence searches: table and observed count.
    searches = sorted(
        (e.search.table, e.search.observed_count) for e in packet.evidence if e.search
    )
    assert searches == sorted((s["table"], s["count"]) for s in exp["absence_searches"])

    # Calculations: every expected value is present, exact, in our sign convention.
    by_id = {c.calculation_id: c for c in packet.calculations}
    for c in exp["calculations"]:
        if c["unit"] == "days":  # business/calendar days are not a Calculation yet
            assert c["name"] == "payment_date_minus_due_date"
            assert "payment_on_due_date" in {f.check_id for f in d.findings}
            continue
        cid, sign = ALIASES.get(c["name"], (c["name"], 1))
        want = Money.parse(c["value"], c["unit"])
        # per-payment calculations are named "<name>:<payment token>"
        got = [r.result for k, r in by_id.items() if k.split(":")[0] == cid]
        assert got, c["name"]
        assert all((g if sign == 1 else -g) == want for g in got), c["name"]


@pytest.mark.parametrize("name", INVOICE_SCENARIOS)
def test_calculations_are_backed_by_cited_evidence(
    prepared: dict[str, Prepared], name: str
) -> None:
    packet = run(prepared, name)
    items = {e.evidence_id: dict(e.values) for e in packet.evidence}
    assert packet.calculations
    for c in packet.calculations:
        for op in c.operands:
            assert items[op.evidence_id][op.field] == str(op.amount)


@pytest.mark.parametrize("name", INVOICE_SCENARIOS)
def test_packet_round_trips_json(prepared: dict[str, Prepared], name: str) -> None:
    packet = run(prepared, name)
    assert EvidencePacket.from_json(packet.to_json()) == packet


def test_missing_primary_entity_abstains(prepared: dict[str, Prepared]) -> None:
    name = "insufficient_primary_entity_missing"
    packet = run(prepared, name)
    assert packet.diagnosis.disposition is Disposition.ABSTAINED
    assert packet.diagnosis.reason == "primary_entity_not_in_snapshot"
    assert packet.evidence == () and packet.calculations == ()


@pytest.mark.parametrize(
    "name",
    [
        n
        for n, e in EXPECTED.items()
        if e["primary_entity"]["table"] != "invoices"
        and e.get("abstention_reason") != "primary_entity_not_in_snapshot"
    ],
)
def test_non_invoice_primary_is_out_of_scope(
    prepared: dict[str, Prepared], name: str
) -> None:
    packet = run(prepared, name)
    assert packet.diagnosis.disposition is Disposition.OUT_OF_SCOPE
    assert packet.diagnosis.reason == "entity_type_not_supported_yet"
    assert packet.diagnosis.category is None and packet.evidence == ()


def test_partial_payment_on_invoice_is_out_of_scope(
    prepared: dict[str, Prepared],
) -> None:
    # real_partial_payment is payment-primary in expected.json; ask about its invoice.
    p = prepared["real_partial_payment"]
    packet = investigate(p.snapshot, p.request("invoices", "INV_1"))
    assert packet.diagnosis.disposition is Disposition.OUT_OF_SCOPE
    assert packet.diagnosis.reason == "no_in_scope_pattern"
    assert packet.diagnosis.category is None


def test_legitimate_answers_have_all_findings_supported(
    prepared: dict[str, Prepared],
) -> None:
    for name in ("split_payment", "due_date_payment"):
        d = run(prepared, name).diagnosis
        assert d.category and d.category.startswith("legitimate")
        assert d.findings and all(f.state.value == "supported" for f in d.findings)
        assert not d.missing_information


def test_row_limit_is_incomplete_evidence(prepared: dict[str, Prepared]) -> None:
    packet = run(prepared, "f06_double_payment", limits=Limits(max_rows=1))
    assert packet.diagnosis.disposition is Disposition.ABSTAINED
    assert packet.diagnosis.reason == "retrieval_row_limit"
    assert "incomplete" in packet.limitations[0]


def test_time_limit_is_incomplete_evidence(prepared: dict[str, Prepared]) -> None:
    packet = run(prepared, "f06_double_payment", limits=Limits(max_seconds=0))
    assert packet.diagnosis.reason == "retrieval_time_limit"
    assert packet.diagnosis.disposition is Disposition.ABSTAINED


def test_snapshot_is_not_modified(prepared: dict[str, Prepared]) -> None:
    path = prepared["f06_double_payment"].snapshot
    before = path.read_bytes()
    run(prepared, "f06_double_payment")
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ["f06_double_payment", "split_payment"])
def test_invoice_age_is_not_evidence(
    prepared: dict[str, Prepared], tmp_path: Path, name: str
) -> None:
    """Residual R4: changing invoice_date and received_date changes nothing."""
    p = prepared[name]
    copy = clone(
        p,
        tmp_path,
        "UPDATE invoices SET invoice_date = '2001-01-01', received_date = '2001-01-02'",
    )
    request = p.request("invoices", "INV_1")
    assert investigate(copy, request) == investigate(p.snapshot, request)


def test_no_query_reads_invoice_dates() -> None:
    sql = [
        node.value
        for module in ("retrieval/invoice.py", "diagnosis/invoice.py")
        for node in ast.walk(ast.parse((ROOT / "src/reconcile" / module).read_text()))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "SELECT" in node.value
    ]
    assert sql
    assert not any("invoice_date" in q or "received_date" in q for q in sql)


def test_returned_row_still_gets_a_bounded_search(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    """A third payment whose only bank row is returned: absence is evidenced."""
    p = prepared["f06_double_payment"]
    pay2, ref3 = p.token("payments", "PAY_2"), "r3"
    inv = p.token("invoices", "INV_1")
    copy = clone(
        p,
        tmp_path,
        f"INSERT INTO payments SELECT 'p3', vendor_id, bank_account_id, '{ref3}',"
        f" payment_date, payment_method, payment_currency, payment_amount,"
        f" payment_status, settlement_status, created_at FROM payments"
        f" WHERE payment_id = '{pay2}'",
        f"INSERT INTO payment_allocations VALUES ('p3', '{inv}', '1000.00',"
        f" '2026-06-09')",
        f"INSERT INTO bank_transactions SELECT 'b3', counterparty_token, '{ref3}',"
        f" bank_account_id, transaction_date, posted_date, transaction_type, amount,"
        f" currency, direction, 'returned' FROM bank_transactions"
        f" WHERE payment_reference = (SELECT reference_number FROM payments"
        f" WHERE payment_id = '{pay2}')",
    )
    packet = investigate(copy, p.request("invoices", "INV_1"))
    assert packet.diagnosis.category == "F06"
    finding = next(
        f
        for f in packet.diagnosis.findings
        if f.check_id == "allocating_payment_without_posted_debit"
    )
    cited = {e.evidence_id: e for e in packet.evidence}
    searches = [s for i in finding.evidence_ids if (s := cited[i].search) is not None]
    assert len(searches) == 1 and searches[0].observed_count == 0
    assert "direction = 'debit'" in searches[0].predicate


def test_missing_counterpart_stays_visible_when_abstaining(
    prepared: dict[str, Prepared],
) -> None:
    packet = run(prepared, "f06_second_debit_post_cutoff")
    assert packet.diagnosis.disposition is Disposition.ABSTAINED
    assert [f.check_id for f in packet.diagnosis.findings] == [
        "allocating_payment_without_posted_debit"
    ]


def test_unreadable_snapshot_fails_explicitly(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["f06_double_payment"]
    copy = clone(p, tmp_path)
    copy.write_bytes(b"this is not a database" * 100)
    packet = investigate(copy, p.request("invoices", "INV_1"))
    assert packet.diagnosis.disposition is Disposition.FAILED
    assert packet.diagnosis.reason == "snapshot_unreadable"


def test_missing_table_fails_explicitly(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["f06_double_payment"]
    copy = clone(p, tmp_path, "DROP TABLE payment_allocations")
    packet = investigate(copy, p.request("invoices", "INV_1"))
    assert packet.diagnosis.disposition is Disposition.FAILED
    assert packet.diagnosis.reason == "snapshot_unreadable"


def test_duplicate_invoice_rows_fail_explicitly(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["f06_double_payment"]
    copy = clone(p, tmp_path, "INSERT INTO invoices SELECT * FROM invoices")
    packet = investigate(copy, p.request("invoices", "INV_1"))
    assert packet.diagnosis.disposition is Disposition.FAILED
    assert packet.diagnosis.reason == "malformed_record"


@pytest.mark.parametrize(
    ("debit", "category", "reason"),
    [
        ("1199.99", "legitimate_split_payment", None),
        ("1200.01", "legitimate_split_payment", None),
        ("1199.98", None, "debit_differs_from_payment"),
    ],
)
def test_debit_difference_uses_one_minor_unit_tolerance(
    prepared: dict[str, Prepared],
    tmp_path: Path,
    debit: str,
    category: str | None,
    reason: str | None,
) -> None:
    p = prepared["split_payment"]
    copy = clone(
        p,
        tmp_path,
        f"UPDATE bank_transactions SET amount = '{debit}' WHERE amount = '1200.00'",
    )
    d = investigate(copy, p.request("invoices", "INV_1")).diagnosis
    assert (d.category, d.reason) == (category, reason)


def test_partial_payment_on_due_date_is_not_legitimate(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["real_partial_payment"]
    copy = clone(p, tmp_path, "UPDATE invoices SET due_date = '2026-06-01'")
    d = investigate(copy, p.request("invoices", "INV_1")).diagnosis
    assert d.disposition is Disposition.OUT_OF_SCOPE


def test_time_budget_covers_diagnosis(
    prepared: dict[str, Prepared], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [0.0]
    original = diagnose_invoice

    def slow(*args: Any) -> EvidencePacket:
        packet = original(*args)
        now[0] = 100.0  # diagnosis ran past the deadline
        return packet

    monkeypatch.setattr(pipeline, "diagnose_invoice", slow)
    packet = run(prepared, "f06_double_payment", limits=Limits(clock=lambda: now[0]))
    assert packet.diagnosis.disposition is Disposition.ABSTAINED
    assert packet.diagnosis.reason == "retrieval_time_limit"


def test_snapshot_must_match_the_request(
    prepared: dict[str, Prepared],
) -> None:
    a, b = prepared["f06_double_payment"], prepared["split_payment"]
    packet = investigate(b.snapshot, a.request("invoices", "INV_1"))
    assert packet.diagnosis.disposition is Disposition.FAILED
    assert packet.diagnosis.reason == "snapshot_mismatch"


def test_missing_manifest_fails_explicitly(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["f06_double_payment"]
    copy = clone(p, tmp_path)
    (copy.parent / "manifest.json").unlink()
    packet = investigate(copy, p.request("invoices", "INV_1"))
    assert packet.diagnosis.reason == "snapshot_manifest_unreadable"
