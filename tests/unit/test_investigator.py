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
from reconcile.diagnosis.policy import SCOPE_LIMITATION
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
    "gl_entries": "journal",
    "approval_events": "approval",
}
# expected.json name -> (our calculation id, sign): our difference operations
# compute "invoice total minus X", the contract's overpayment is "X minus total".
ALIASES = {
    "committed_overpayment": ("invoice_total_minus_committed", -1),
    "allocated_minus_invoice_total": ("invoice_total_minus_allocated", -1),
}
ALL_SCENARIOS = sorted(EXPECTED)
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


def test_every_fixture_scenario_is_run() -> None:
    assert len(TABLES) and ALL_SCENARIOS == sorted(
        p.name for p in FIXTURES.iterdir() if p.is_dir()
    )
    assert len(INVOICE_SCENARIOS) == 4


@pytest.mark.parametrize("name", ALL_SCENARIOS)
def test_scenario_matches_expected(prepared: dict[str, Prepared], name: str) -> None:
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

    by_id = {c.calculation_id: c for c in packet.calculations}
    counts = {c.calculation_id: c for c in packet.day_counts}
    for c in exp["calculations"]:
        name_ = c["name"]
        if c["unit"] == "days":  # a calendar-day gap is a finding, not a calculation
            assert name_ == "payment_date_minus_due_date"
            assert "payment_on_due_date" in {f.check_id for f in d.findings}
        elif c["unit"] == "business_days" and name_.startswith("clearing_limit_"):
            assert any(
                x.limit_policy == name_ and x.limit == int(c["value"])
                for x in counts.values()
            ), name_
        elif c["unit"] == "business_days":
            assert counts[name_].result == int(c["value"]), name_
        else:
            # Money: our difference operations are "total minus X"; a per-item
            # calculation is named "<name>:<token>".
            cid, sign = ALIASES.get(name_, (name_, 1))
            want = Money.parse(c["value"], c["unit"])
            got = [r.result for k, r in by_id.items() if k.split(":")[0] == cid]
            assert got, name_
            assert all((g if sign == 1 else -g) == want for g in got), name_


@pytest.mark.parametrize("name", ALL_SCENARIOS)
def test_calculations_are_backed_by_cited_evidence(
    prepared: dict[str, Prepared], name: str
) -> None:
    packet = run(prepared, name)
    items = {e.evidence_id: dict(e.values) for e in packet.evidence}
    for c in packet.calculations:
        for op in c.operands:
            assert items[op.evidence_id][op.field] == str(op.amount)


@pytest.mark.parametrize("name", ALL_SCENARIOS)
def test_packet_round_trips_json(prepared: dict[str, Prepared], name: str) -> None:
    packet = run(prepared, name)
    assert EvidencePacket.from_json(packet.to_json()) == packet


def test_missing_primary_entity_abstains(prepared: dict[str, Prepared]) -> None:
    name = "insufficient_primary_entity_missing"
    packet = run(prepared, name)
    assert packet.diagnosis.disposition is Disposition.ABSTAINED
    assert packet.diagnosis.reason == "primary_entity_not_in_snapshot"
    assert packet.evidence == () and packet.calculations == ()


def test_journal_primary_is_out_of_scope(prepared: dict[str, Prepared]) -> None:
    p = prepared["f12_payment_missing_from_bank"]
    packet = investigate(p.snapshot, p.request("gl_entries", "J_PAY_1"))
    assert packet.diagnosis.disposition is Disposition.OUT_OF_SCOPE
    assert packet.diagnosis.reason == "entity_type_not_supported_yet"
    assert packet.diagnosis.category is None and packet.evidence == ()


def test_non_fee_bank_row_primary_is_out_of_scope(
    prepared: dict[str, Prepared],
) -> None:
    p = prepared["accounted_bank_fee"]
    packet = investigate(p.snapshot, p.request("bank_transactions", "BT_1"))
    assert packet.diagnosis.disposition is Disposition.OUT_OF_SCOPE
    assert packet.diagnosis.reason == "bank_row_type_not_supported_yet"


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


# --- payment- and bank-row-primary rules -------------------------------------


def payment_packet(
    p: Prepared, tmp_path: Path, *sql: str, raw: str = "PAY_1", **kw: Any
) -> EvidencePacket:
    copy = clone(p, tmp_path, *sql)
    return investigate(copy, p.request("payments", raw), **kw)


def reason(packet: EvidencePacket) -> tuple[str, str | None]:
    return packet.diagnosis.disposition.value, (
        packet.diagnosis.category or packet.diagnosis.reason
    )


@pytest.mark.parametrize(
    ("payment_date", "outcome"),
    [
        ("2026-06-25", ("answered", "legitimate_pending_settlement")),  # age 3 = limit
        ("2026-06-24", ("answered", "F12")),  # age 4 > limit
    ],
)
def test_f12_age_versus_limit_boundary(
    prepared: dict[str, Prepared], tmp_path: Path, payment_date: str, outcome: Any
) -> None:
    p = prepared["f12_payment_missing_from_bank"]
    packet = payment_packet(
        p, tmp_path, f"UPDATE payments SET payment_date = '{payment_date}'"
    )
    assert reason(packet) == outcome
    (count,) = packet.day_counts
    assert count.limit == 3 and count.limit_policy == "clearing_limit_ach"


def test_a3_candidate_blocks_f12(prepared: dict[str, Prepared], tmp_path: Path) -> None:
    p = prepared["f12_payment_missing_from_bank"]
    pay = p.token("payments", "PAY_1")
    acct = "(SELECT bank_account_id FROM payments WHERE payment_id = '" + pay + "')"
    packet = payment_packet(
        p,
        tmp_path,
        "INSERT INTO bank_transactions VALUES ('b9', " + acct + ", 'other_ref',"
        " 'ACC', '2026-05-06', '2026-05-06', 'ACH debit', '500.00', 'EUR',"
        " 'debit', 'posted')",
    )
    assert reason(packet) == ("abstained", "reference_unmatched_candidate_bank_row")
    a3 = [e.search for e in packet.evidence if e.relationship == "a3"]
    assert [x.observed_count for x in a3 if x] == [1]


@pytest.mark.parametrize(
    ("fee_lines", "outcome"),
    [
        ([("10.00", "10.00")], ("abstained", "fee_journal_does_not_match_difference")),
        ([("30.00", "30.00")], ("abstained", "fee_journal_does_not_match_difference")),
        ([("25.00", "20.00")], ("abstained", "fee_journal_invalid")),
        ([("25.00", "25.00")], ("answered", "legitimate_net_fee_accounting")),
        ([("24.99", "24.99")], ("answered", "legitimate_net_fee_accounting")),
        ([("24.98", "24.98")], ("abstained", "fee_journal_does_not_match_difference")),
    ],
)
def test_f14_fee_journal_rules(
    prepared: dict[str, Prepared],
    tmp_path: Path,
    fee_lines: list[tuple[str, str]],
    outcome: Any,
) -> None:
    """Delta is 25.00. Partial or oversized journals are not gradable; so is an
    unbalanced one; only a journal within one minor unit explains the shortfall."""
    p = prepared["f14_amount_shortfall"]
    bt = p.token("bank_transactions", "BT_1")
    sql = []
    for n, (debit, credit) in enumerate(fee_lines):
        sql.append(
            f"INSERT INTO gl_entries VALUES ('jf{n}', 'jf{n}a', '{bt}', 'bank_fee',"
            f" '2026-06-03', '2026-06', '650000', '{debit}', '0.00', 'EUR')"
        )
        sql.append(
            f"INSERT INTO gl_entries VALUES ('jf{n}', 'jf{n}b', '{bt}', 'bank_fee',"
            f" '2026-06-03', '2026-06', '100000', '0.00', '{credit}', 'EUR')"
        )
    assert reason(payment_packet(p, tmp_path, *sql)) == outcome


def test_bank_amount_above_payment_abstains(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["f14_amount_shortfall"]
    packet = payment_packet(
        p, tmp_path, "UPDATE bank_transactions SET amount = '1010.00'"
    )
    assert reason(packet) == ("abstained", "bank_amount_above_payment")


def test_returned_row_is_not_a_posted_debit(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["f14_amount_shortfall"]
    packet = payment_packet(
        p, tmp_path, "UPDATE bank_transactions SET status = 'returned'"
    )
    assert reason(packet) == ("abstained", "bank_row_not_a_posted_debit")


def test_two_posted_debits_are_ambiguous(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["weekend_clearing"]
    packet = payment_packet(
        p,
        tmp_path,
        "INSERT INTO bank_transactions SELECT 'b2', counterparty_token,"
        " payment_reference, bank_account_id, transaction_date, posted_date,"
        " transaction_type, amount, currency, direction, status FROM bank_transactions",
    )
    assert reason(packet) == ("abstained", "ambiguous_reference")


@pytest.mark.parametrize(
    ("posted", "outcome"),
    [
        ("2026-06-10", ("answered", "legitimate_weekend_clearing")),  # lag 3
        ("2026-06-11", ("abstained", "clearing_lag_exceeds_limit")),  # lag 4
    ],
)
def test_clearing_lag_boundary(
    prepared: dict[str, Prepared], tmp_path: Path, posted: str, outcome: Any
) -> None:
    p = prepared["weekend_clearing"]
    packet = payment_packet(
        p, tmp_path, f"UPDATE bank_transactions SET posted_date = '{posted}'"
    )
    assert reason(packet) == outcome


def test_same_day_ach_is_no_exception_not_a_named_variant(
    prepared: dict[str, Prepared],
) -> None:
    packet = run(prepared, "real_partial_payment")
    assert reason(packet) == ("answered", "no_exception")
    assert packet.limitations and "not a general clean bill" in packet.limitations[0]


def test_twin_on_the_same_invoice_is_not_a_clean_bill(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    """Equal twins stay distinct; two on one invoice over-allocate it."""
    p = prepared["real_identical_distinct_payments"]
    inv1 = p.token("invoices", "INV_1")
    packet = payment_packet(
        p,
        tmp_path,
        f"UPDATE payment_allocations SET invoice_id = '{inv1}'",
    )
    assert reason(packet) == ("abstained", "invoice_over_allocated")


def test_batch_with_unequal_allocations_abstains(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["batch_payment"]
    packet = payment_packet(
        p,
        tmp_path,
        "UPDATE payment_allocations SET allocated_amount = '1.00'"
        " WHERE allocated_amount = '1000.00'",
    )
    assert reason(packet) == ("abstained", "allocations_differ_from_payment")


def test_reversed_invoice_still_paid_without_repayment_abstains(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["payment_reversal"]
    packet = payment_packet(p, tmp_path, "UPDATE invoices SET status = 'paid'")
    assert reason(packet) == ("abstained", "reversal_invoice_still_paid")


def test_reversal_credit_before_debit_abstains(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["payment_reversal"]
    packet = payment_packet(
        p,
        tmp_path,
        "UPDATE bank_transactions SET posted_date = '2026-06-01'"
        " WHERE transaction_type = 'payment reversal'",
    )
    assert reason(packet) == ("abstained", "reversal_credit_before_debit")


def test_fee_journal_amount_mismatch_abstains(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["accounted_bank_fee"]
    copy = clone(
        p,
        tmp_path,
        "UPDATE gl_entries SET debit = '14.00' WHERE debit = '15.00'",
        "UPDATE gl_entries SET credit = '14.00' WHERE credit = '15.00'",
    )
    packet = investigate(copy, p.request("bank_transactions", "BT_2"))
    assert reason(packet) == ("abstained", "fee_journal_amount_differs")


def test_fee_without_journal_abstains(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["accounted_bank_fee"]
    copy = clone(p, tmp_path, "DELETE FROM gl_entries")
    packet = investigate(copy, p.request("bank_transactions", "BT_2"))
    assert reason(packet) == ("abstained", "accounted_fee_journal_missing")


def test_non_effective_unknown_status_is_out_of_scope(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["f12_payment_missing_from_bank"]
    packet = payment_packet(
        p, tmp_path, "UPDATE payments SET payment_status = 'failed'"
    )
    assert reason(packet) == ("out_of_scope", "no_in_scope_pattern")


def test_old_pending_payment_abstains(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["f12_payment_missing_from_bank"]
    packet = payment_packet(
        p, tmp_path, "UPDATE payments SET settlement_status = 'pending'"
    )
    assert reason(packet) == ("abstained", "pending_payment_beyond_clearing_limit")


def test_batch_search_over_limit_abstains(
    prepared: dict[str, Prepared], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reconcile.diagnosis import payment as payment_rules

    monkeypatch.setattr(payment_rules, "MAX_BATCH_CANDIDATES", 1)
    packet = run_clone(prepared["real_bank_batch_debit"], tmp_path)
    assert reason(packet) == ("abstained", "batch_search_limit_exceeded")


def run_clone(p: Prepared, tmp_path: Path) -> EvidencePacket:
    return payment_packet(p, tmp_path)


def test_policy_is_declared_in_every_packet(prepared: dict[str, Prepared]) -> None:
    prov = dict(run(prepared, "weekend_clearing").provenance)
    assert prov["policy_version"] == "evidence-contract-0.3"
    assert [
        prov[f"clearing_limit_{m}"] for m in ("ach", "wire", "check", "virtual_card")
    ] == [
        "3",
        "1",
        "10",
        "2",
    ]
    assert prov["tolerance"] == "one_minor_unit"
    assert prov["requested_policy_version"] == "policy_test"


def test_payment_investigation_ignores_invoice_dates(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    """Residual R4 for payment-primary requests: invoice dates change nothing."""
    p = prepared["real_partial_payment"]
    copy = clone(
        p,
        tmp_path,
        "UPDATE invoices SET invoice_date = '2001-01-01', received_date = '2001-01-02'",
    )
    request = p.request("payments", "PAY_1")
    assert investigate(copy, request) == investigate(p.snapshot, request)


def test_every_select_avoids_invoice_dates() -> None:
    modules = [
        "retrieval/invoice.py",
        "retrieval/payment.py",
        "diagnosis/invoice.py",
        "diagnosis/payment.py",
        "diagnosis/settled.py",
    ]
    sql = [
        node.value
        for module in modules
        for node in ast.walk(ast.parse((ROOT / "src/reconcile" / module).read_text()))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "SELECT" in node.value
    ]
    assert len(sql) >= 15
    assert not any("invoice_date" in q or "received_date" in q for q in sql)


# --- no invoice, no all-clear; scope statement; variant overlap --------------

ALL_CLEAR_PRIMARIES = [
    ("weekend_clearing", "payments", "PAY_1"),
    ("check_clearing", "payments", "PAY_1"),
    ("net_fee_accounting", "payments", "PAY_1"),
    ("accounted_bank_fee", "bank_transactions", "BT_2"),
    ("real_partial_payment", "payments", "PAY_1"),  # no_exception
]


@pytest.mark.parametrize(("name", "table", "raw"), ALL_CLEAR_PRIMARIES)
def test_payment_without_allocation_is_never_an_all_clear(
    prepared: dict[str, Prepared], tmp_path: Path, name: str, table: str, raw: str
) -> None:
    p = prepared[name]
    copy = clone(p, tmp_path, "DELETE FROM payment_allocations")
    packet = investigate(copy, p.request(table, raw))
    assert reason(packet) == ("abstained", "payment_without_invoice_allocation")
    (finding,) = packet.diagnosis.findings
    assert finding.check_id == "payment_without_invoice_allocation"
    assert finding.state.value == "supported"
    cited = {e.evidence_id: e for e in packet.evidence}
    searches = [
        (s.table, s.observed_count)
        for i in finding.evidence_ids
        if (s := cited[i].search) is not None
    ]
    assert searches == [("payment_allocations", 0)]
    assert "allocation" in packet.diagnosis.missing_information[0]


def test_allocation_to_an_invoice_in_another_currency_is_not_an_all_clear(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    p = prepared["weekend_clearing"]
    copy = clone(p, tmp_path, "UPDATE invoices SET currency = 'USD'")
    packet = investigate(copy, p.request("payments", "PAY_1"))
    assert reason(packet) == ("abstained", "unsupported_fx")


@pytest.mark.parametrize("name", ALL_SCENARIOS)
def test_every_all_clear_states_its_scope(
    prepared: dict[str, Prepared], name: str
) -> None:
    packet = run(prepared, name)
    category = packet.diagnosis.category or ""
    all_clear = category.startswith("legitimate_") or category == "no_exception"
    assert (SCOPE_LIMITATION in packet.limitations) == all_clear


@pytest.mark.parametrize(
    ("sql", "alternative"),
    [
        ("UPDATE payments SET payment_method = 'check'", "legitimate_check_clearing"),
        (
            "UPDATE payments SET payment_date = '2026-06-05'",
            "legitimate_weekend_clearing",
        ),
    ],
)
def test_batch_lists_the_clearing_variant_that_also_holds(
    prepared: dict[str, Prepared], tmp_path: Path, sql: str, alternative: str
) -> None:
    p = prepared["batch_payment"]
    posted = "UPDATE bank_transactions SET posted_date = '2026-06-08'"
    packet = payment_packet(p, tmp_path, sql, posted)
    assert reason(packet) == ("answered", "legitimate_batch_payment")
    assert packet.diagnosis.alternatives == (alternative,)


def test_batch_without_clearing_overlap_lists_no_alternative(
    prepared: dict[str, Prepared],
) -> None:
    assert run(prepared, "batch_payment").diagnosis.alternatives == ()


# --- all-clear prerequisites: final approval and payment journal -------------

ALL_CLEAR_FIXTURES = [
    n
    for n, e in EXPECTED.items()
    if e["disposition"] == "answered"
    and (e["category"].startswith("legitimate_") or e["category"] == "no_exception")
]


def primary_packet(
    prepared: dict[str, Prepared], tmp_path: Path, name: str, *sql: str
) -> EvidencePacket:
    p = prepared[name]
    e = EXPECTED[name]["primary_entity"]
    return investigate(clone(p, tmp_path, *sql), p.request(e["table"], e["id"]))


def test_the_guard_covers_every_all_clear_fixture() -> None:
    assert len(ALL_CLEAR_FIXTURES) == 11


@pytest.mark.parametrize("name", ALL_CLEAR_FIXTURES)
def test_no_completed_final_approval_is_never_an_all_clear(
    prepared: dict[str, Prepared], tmp_path: Path, name: str
) -> None:
    packet = primary_packet(prepared, tmp_path, name, "DELETE FROM approval_events")
    assert reason(packet) == ("abstained", "invoice_approval_not_completed")
    (finding,) = packet.diagnosis.findings
    assert finding.check_id == "invoice_approval_not_completed"
    assert finding.state.value == "supported"


@pytest.mark.parametrize("name", ALL_CLEAR_FIXTURES)
def test_missing_payment_journal_is_never_an_all_clear(
    prepared: dict[str, Prepared], tmp_path: Path, name: str
) -> None:
    packet = primary_packet(
        prepared,
        tmp_path,
        name,
        "DELETE FROM gl_entries WHERE transaction_type = 'payment'",
    )
    # Pending settlement already needed its journal before the guard existed.
    want = "required_records_missing" if name == "pending_settlement" else None
    assert reason(packet) == ("abstained", want or "payment_journal_missing")


def test_payment_before_final_approval_abstains(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    packet = primary_packet(
        prepared,
        tmp_path,
        "weekend_clearing",
        "UPDATE approval_events SET event_timestamp = '2026-06-06T10:00:00'"
        " WHERE new_status = 'approved'",
    )
    assert reason(packet) == ("abstained", "payment_before_approval")
    assert packet.diagnosis.findings[0].check_id == "payment_before_approval"


def test_approval_on_the_payment_date_is_enough(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    packet = primary_packet(
        prepared,
        tmp_path,
        "weekend_clearing",
        "UPDATE approval_events SET event_timestamp = '2026-06-05T17:00:00'"
        " WHERE new_status = 'approved'",
    )
    assert reason(packet) == ("answered", "legitimate_weekend_clearing")


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "UPDATE gl_entries SET credit = '700.00' WHERE credit = '800.00'",
            "payment_journal_unbalanced",
        ),
        ("UPDATE gl_entries SET currency = 'USD'", "payment_journal_currency_differs"),
        (
            "UPDATE gl_entries SET debit = '800.02' WHERE debit = '800.00'",
            "payment_journal_unbalanced",
        ),
    ],
)
def test_payment_journal_defects_abstain(
    prepared: dict[str, Prepared], tmp_path: Path, sql: str, expected: str
) -> None:
    packet = primary_packet(prepared, tmp_path, "weekend_clearing", sql)
    assert reason(packet) == ("abstained", expected)


def test_journal_within_one_minor_unit_is_enough(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    packet = primary_packet(
        prepared,
        tmp_path,
        "weekend_clearing",
        "UPDATE gl_entries SET debit = '800.01' WHERE debit = '800.00'",
        "UPDATE gl_entries SET credit = '800.01' WHERE credit = '800.00'",
    )
    assert reason(packet) == ("answered", "legitimate_weekend_clearing")


def test_journal_two_minor_units_off_abstains(
    prepared: dict[str, Prepared], tmp_path: Path
) -> None:
    packet = primary_packet(
        prepared,
        tmp_path,
        "weekend_clearing",
        "UPDATE gl_entries SET debit = '800.02' WHERE debit = '800.00'",
        "UPDATE gl_entries SET credit = '800.02' WHERE credit = '800.00'",
    )
    assert reason(packet) == ("abstained", "payment_journal_amount_differs")
