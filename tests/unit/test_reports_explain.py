"""explain(): fixed templates only, checked against every public fixture."""

import ast
import csv
import json
import re
import sqlite3
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
from reconcile.diagnosis.policy import SCOPE_LIMITATION
from reconcile.domain.contracts import (
    Diagnosis,
    Disposition,
    EvidencePacket,
    Finding,
    FindingState,
    InvestigationRequest,
    RecordRef,
)
from reconcile.reports.explain import (
    CATEGORIES,
    DISPOSITION_TITLES,
    REASONS,
    TITLES,
    explain,
    summary,
    title,
)
from reconcile.retrieval.snapshot import Limits
from reconcile.runs import batch

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "reconcile"
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
DIGEST = "a" * 64


@pytest.fixture(scope="module")
def packets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, EvidencePacket]:
    root = tmp_path_factory.mktemp("rep")
    key = tmp_path_factory.mktemp("key") / "key"
    create_key_file(key)
    out: dict[str, EvidencePacket] = {}
    for name, exp in EXPECTED.items():
        m = prepare_finrca_snapshot(
            FIXTURES / name,
            root / name,
            key_path=key,
            source_revision=f"fixture:{name}",
            cutoff=CUTOFF,
        )
        digest = m["snapshot"]["content_sha256"]
        snap = root / name / m["snapshot"]["file"]
        tokens = _Tokens(_load_key(key))

        def request(table: str, raw: str) -> InvestigationRequest:
            tok = tokens.token(NAMESPACE[table], raw)  # noqa: B023
            return InvestigationRequest(
                "req_test",
                digest,  # noqa: B023
                RecordRef(digest, table, tok),  # noqa: B023
                "Check whether there is a reconciliation issue.",
                CUTOFF,
                "policy_test",
            )

        e = exp["primary_entity"]
        out[name] = investigate(snap, request(e["table"], e["id"]))
        if name == "f06_double_payment":
            out["row_limit"] = investigate(
                snap, request(e["table"], e["id"]), Limits(max_rows=1)
            )
        if name == "f12_payment_missing_from_bank":
            out["journal_out_of_scope"] = investigate(
                snap, request("gl_entries", "J_PAY_1")
            )
            out["entity_missing"] = investigate(
                snap, request("payments", "NO_SUCH_PAYMENT")
            )
        if name == "accounted_bank_fee":
            out["bank_row_out_of_scope"] = investigate(
                snap, request("bank_transactions", "BT_1")
            )
    first = next(iter(out.values()))
    out["failed"] = batch._failed(first.request, RuntimeError("boom"))
    return out


def _request() -> InvestigationRequest:
    return InvestigationRequest(
        "req_x",
        DIGEST,
        RecordRef(DIGEST, "payments", "tok_1"),
        "symptom",
        CUTOFF,
        "policy_test",
    )


# ------------------------------------------------ codes the code can emit

_CODE = re.compile(r"[a-z]+(_[a-z0-9]+)+")
# call name -> positions of the reason argument
_REASON_ARG = {
    "abstain": (0,),
    "out_of_scope": (0,),
    "_abstain": (2,),
    "_Failure": (0,),
    "_failed": (2,),
    "_packet": (3,),
    "RetrievalIncomplete": (0,),
}


def _modules() -> list[ast.Module]:
    paths = [*(SRC / "diagnosis").glob("*.py"), SRC / "runs" / "batch.py"]
    paths.append(SRC / "retrieval" / "snapshot.py")
    return [ast.parse(p.read_text()) for p in paths]


def _constants(trees: list[ast.Module]) -> dict[str, str]:
    out: dict[str, str] = {}
    for tree in trees:
        for n in tree.body:
            if (
                isinstance(n, ast.Assign)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            ):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        out[t.id] = n.value.value
    return out


def _strings(node: ast.AST, consts: dict[str, str]) -> set[str]:
    found: set[str] = set()
    for x in ast.walk(node):
        if isinstance(x, ast.Constant) and isinstance(x.value, str):
            found.add(x.value)
        elif isinstance(x, ast.Name) and x.id in consts:
            found.add(consts[x.id])
    return {s for s in found if _CODE.fullmatch(s)}


def emitted_reasons() -> set[str]:
    """Every reason string the code passes to an abstain, out-of-scope, failed or
    retrieval-incomplete call, a `reason=` argument or variable, or returns from a
    function annotated as returning a string."""
    trees = _modules()
    consts = _constants(trees)
    reasons: set[str] = set()
    for tree in trees:
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn = n.func
                name = (
                    fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                )
                for i in _REASON_ARG.get(name, ()):
                    if i < len(n.args):
                        reasons |= _strings(n.args[i], consts)
                for kw in n.keywords:
                    if kw.arg == "reason":
                        reasons |= _strings(kw.value, consts)
            elif isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "reason" for t in n.targets
            ):
                reasons |= _strings(n.value, consts)
            elif (
                isinstance(n, ast.FunctionDef)
                and n.returns is not None
                and "str" in ast.unparse(n.returns)
            ):
                for r in ast.walk(n):
                    if isinstance(r, ast.Return) and r.value is not None:
                        if isinstance(r.value, ast.Constant):
                            reasons |= _strings(r.value, consts)
    # The two retrieval limits are module constants passed by name.
    return reasons


def emitted_categories() -> set[str]:
    consts = _constants([t for t in _modules()])
    return {
        v
        for k, v in consts.items()
        if k.isupper() and re.fullmatch(r"F\d\d|legitimate_\w+|no_exception", v)
    }


def test_collector_finds_known_codes() -> None:
    reasons = emitted_reasons()
    # A few that come from different mechanisms (call argument, constant name,
    # kwarg, assignment, annotated return, retrieval limit).
    for known in (
        "unsupported_fx",
        "payment_journal_missing",
        "no_in_scope_pattern",
        "fee_reference_unresolved",
        "invoice_over_allocated",
        "retrieval_row_limit",
        "payment_without_invoice_allocation",
        "unexpected_exception",
        "snapshot_mismatch",
        "required_field_missing",
        "counterparty_unavailable",
    ):
        assert known in reasons, known
    assert len(reasons) >= 40


def test_every_emitted_reason_has_a_template() -> None:
    missing = emitted_reasons() - set(REASONS)
    assert not missing, f"add wording to REASONS for {sorted(missing)}"


def test_no_template_for_a_reason_the_code_never_emits() -> None:
    assert set(REASONS) - emitted_reasons() == set()


def test_every_emitted_category_has_a_template() -> None:
    cats = emitted_categories()
    assert {"F06", "F12", "F14", "no_exception"} <= cats
    assert cats == set(CATEGORIES)


# ------------------------------------------------------------ the fixtures


def _numbers_and_dates(text: str) -> set[str]:
    return {t.rstrip(".,;") for t in re.findall(r"\d[\d.,:-]*\d|\d", text)}


def test_every_expected_scenario_has_a_packet(
    packets: dict[str, EvidencePacket],
) -> None:
    assert set(EXPECTED) <= set(packets)
    dispositions = {p.diagnosis.disposition for p in packets.values()}
    assert dispositions == set(Disposition)


def test_numbers_and_dates_come_from_the_packet(
    packets: dict[str, EvidencePacket],
) -> None:
    for name, packet in packets.items():
        serialized = packet.to_json()
        for para in explain(packet):
            for token in _numbers_and_dates(para):
                assert token in serialized, (name, token, para)


def test_explanation_is_deterministic(packets: dict[str, EvidencePacket]) -> None:
    for packet in packets.values():
        assert explain(packet) == explain(packet)


def test_no_all_clear_without_scope(packets: dict[str, EvidencePacket]) -> None:
    seen = 0
    for name, packet in packets.items():
        text = " ".join(explain(packet)).lower()
        assert "no action" not in text, name
        assert "nothing to do" not in text, name
        d = packet.diagnosis
        all_clear = d.disposition is Disposition.ANSWERED and (
            (d.category or "").startswith("legitimate_") or d.category == "no_exception"
        )
        if all_clear:
            seen += 1
            assert SCOPE_LIMITATION in packet.limitations
            assert "covers only the checks" in text, name
            assert "not a statement that nothing else is wrong" in text, name
            assert SCOPE_LIMITATION.lower() in text, name
    assert seen >= 10


def test_abstentions_say_no_conclusion_and_what_is_missing(
    packets: dict[str, EvidencePacket],
) -> None:
    seen = 0
    for name, packet in packets.items():
        d = packet.diagnosis
        if d.disposition is not Disposition.ABSTAINED:
            continue
        seen += 1
        text = " ".join(explain(packet))
        assert "No conclusion was reached" in text, name
        assert "not an all-clear" in text, name
        assert f"Reason code: {d.reason}" in text, name
        for m in d.missing_information:
            assert m in text, name
        assert "legitimate" not in text.lower(), name
    assert seen >= 10


def test_failed_says_it_did_not_complete(packets: dict[str, EvidencePacket]) -> None:
    text = " ".join(explain(packets["failed"]))
    assert "The investigation did not complete" in text
    assert "unexpected_exception" in text
    assert "not an all-clear" in text


def test_out_of_scope_is_not_a_clean_bill(packets: dict[str, EvidencePacket]) -> None:
    for name in ("journal_out_of_scope", "bank_row_out_of_scope"):
        p = packets[name]
        assert p.diagnosis.disposition is Disposition.OUT_OF_SCOPE
        text = " ".join(explain(p))
        assert "did not assess" in text
        assert "not a finding that the record is correct" in text


def test_next_steps_are_only_the_packets_recommendations(
    packets: dict[str, EvidencePacket],
) -> None:
    f06 = packets["f06_double_payment"]
    assert f06.recommendations
    text = " ".join(explain(f06))
    for r in f06.recommendations:
        assert r.action_type in text
        for item in (*r.prerequisites, *r.unresolved_assumptions):
            assert item in text
    # No recommendation: the text says so and names no action.
    clear = packets["split_payment"]
    assert not clear.recommendations
    assert "No next step is proposed." in explain(clear)
    assert "Proposed next step" not in " ".join(explain(clear))


def test_calculations_and_day_counts_are_quoted(
    packets: dict[str, EvidencePacket],
) -> None:
    p = packets["f12_payment_missing_from_bank"]
    text = " ".join(explain(p))
    assert p.day_counts
    for dc in p.day_counts:
        assert f"{dc.result} business days" in text
        assert dc.limit_policy in text
    f14 = packets["f14_amount_shortfall"]
    ftext = " ".join(explain(f14))
    for c in f14.calculations:
        assert str(c.result) in ftext


# ------------------------------------------------------- unknown codes


def test_unknown_category_names_the_code_and_claims_nothing() -> None:
    d = Diagnosis(
        Disposition.ANSWERED,
        "brand_new_category",
        (Finding("some_check", FindingState.SUPPORTED, ("e1",)),),
    )
    from reconcile.domain.contracts import AbsenceSearch, EvidenceItem

    ev = EvidenceItem(
        "e1",
        None,
        AbsenceSearch(DIGEST, "payments", "x = 1", CUTOFF, 0),
        "a1",
    )
    packet = EvidencePacket(_request(), d, (ev,))
    text = " ".join(explain(packet))
    assert "'brand_new_category'" in text
    assert "no wording for it" in text


@pytest.mark.parametrize(
    "disposition",
    [Disposition.ABSTAINED, Disposition.OUT_OF_SCOPE, Disposition.FAILED],
)
def test_unknown_reason_names_the_code_and_claims_nothing(
    disposition: Disposition,
) -> None:
    packet = EvidencePacket(
        _request(), Diagnosis(disposition, None, reason="brand_new_reason")
    )
    text = " ".join(explain(packet))
    assert "'brand_new_reason'" in text
    assert "no wording for it" in text
    assert "all-clear" not in text.replace("not an all-clear", "")


# ------------------------------------------------- titles and the summary


def test_every_category_has_a_title() -> None:
    assert set(TITLES) == set(CATEGORIES)


def test_all_clear_titles_do_not_overclaim() -> None:
    banned = re.compile(r"\b(ok|clean|no issues?|nothing wrong|no action)\b", re.I)
    for category, text in TITLES.items():
        assert not banned.search(text), text
        if category.startswith("legitimate_"):
            assert text.startswith("Consistent with"), text
        if category == "no_exception":
            assert "found by these checks" in text


def test_titles_by_disposition_and_unknown_category(
    packets: dict[str, EvidencePacket],
) -> None:
    for packet in packets.values():
        d = packet.diagnosis
        if d.disposition is Disposition.ANSWERED:
            assert title(packet) == TITLES[d.category or ""]
        else:
            assert title(packet) == DISPOSITION_TITLES[d.disposition]
    assert title(packets["failed"]) == "Investigation did not complete"
    d = Diagnosis(
        Disposition.ANSWERED,
        "brand_new_category",
        (Finding("c", FindingState.SUPPORTED, ("e1",)),),
    )
    from reconcile.domain.contracts import AbsenceSearch, EvidenceItem

    ev = EvidenceItem(
        "e1", None, AbsenceSearch(DIGEST, "payments", "x = 1", CUTOFF, 0), "a1"
    )
    assert "brand_new_category" in title(EvidencePacket(_request(), d, (ev,)))


def test_summary_holds_every_limitation_and_not_the_figures(
    packets: dict[str, EvidencePacket],
) -> None:
    for name, packet in packets.items():
        s = summary(packet)
        assert explain(packet)[: len(s)] == s
        for item in packet.limitations:
            assert f"Limitation: {item}" in s, name
        text = " ".join(s)
        assert "Calculation " not in text and "Day count" not in text, name
        assert "Proposed next step" not in text, name


def test_explanation_avoids_the_word_packet(
    packets: dict[str, EvidencePacket],
) -> None:
    for name, packet in packets.items():
        for text in (*explain(packet), title(packet)):
            assert "packet" not in text.lower(), (name, text)


# ------------------------------------------- wording says what the code checks


def _text(packet: EvidencePacket) -> str:
    return " ".join(explain(packet))


def _bank_references(scenario: str) -> list[str]:
    path = FIXTURES / scenario / "bank_transactions.csv"
    with path.open(newline="") as f:
        return [r["payment_reference"] for r in csv.DictReader(f)]


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("real_truncated_bank_reference", "reference_unmatched_candidate_bank_row"),
        ("real_bank_batch_debit", "bank_batch_grouping_unmodelled"),
        ("real_truncated_reference_netted_fee", "reference_unmatched_nearby_debit"),
    ],
)
def test_a_bank_row_with_an_unmatched_reference_is_not_called_unreferenced(
    packets: dict[str, EvidencePacket], scenario: str, reason: str
) -> None:
    refs = _bank_references(scenario)
    assert refs and all(r.strip() for r in refs)  # a reference is present
    packet = packets[scenario]
    assert packet.diagnosis.reason == reason
    text = _text(packet)
    assert "without a payment reference" not in text
    assert "payment reference matches no payment" in text


def test_the_bounded_subset_search_is_described_as_bounded(
    packets: dict[str, EvidencePacket],
) -> None:
    text = _text(packets["real_truncated_reference_netted_fee"])
    assert "same counterparty account" in text
    assert "some of the other unbanked payments" in text
    assert "among the combinations this tool tests" in text
    assert "equals no combination of payments" not in text


def _mutated(
    tmp_path: Path, scenario: str, *sql: str
) -> tuple[Path, InvestigationRequest]:
    key = tmp_path / "key"
    create_key_file(key)
    m = prepare_finrca_snapshot(
        FIXTURES / scenario,
        tmp_path / "prep",
        key_path=key,
        source_revision=f"fixture:{scenario}",
        cutoff=CUTOFF,
    )
    snap = tmp_path / "prep" / m["snapshot"]["file"]
    snap.chmod(0o644)
    con = sqlite3.connect(snap)
    for statement in sql:
        assert con.execute(statement).rowcount != 0, statement
    con.commit()
    con.close()
    digest = m["snapshot"]["content_sha256"]
    e = EXPECTED[scenario]["primary_entity"]
    token = _Tokens(_load_key(key)).token(NAMESPACE[e["table"]], e["id"])
    return snap, InvestigationRequest(
        "req_test",
        digest,
        RecordRef(digest, e["table"], token),
        "Check whether there is a reconciliation issue.",
        CUTOFF,
        "policy_test",
    )


def _extra_line(source: str, kind: str, debit: str, credit: str, like: str) -> str:
    return (
        "INSERT INTO gl_entries (journal_id, journal_line_id, source_transaction_id,"
        " transaction_type, posting_date, accounting_period, gl_account, debit,"
        f" credit, currency) SELECT journal_id, journal_line_id || 'x', '{source}',"
        f" '{kind}', posting_date, accounting_period, gl_account, '{debit}',"
        f" '{credit}', currency FROM gl_entries WHERE transaction_type = '{like}'"
        " LIMIT 1"
    )


def test_a_journal_that_balances_but_whose_attributed_lines_do_not(
    tmp_path: Path,
) -> None:
    # The whole journal balances (800 = 800); the lines attributed to the payment
    # do not (800 against 700), so "not balanced" alone would misdescribe it.
    snap, request = _mutated(
        tmp_path,
        "weekend_clearing",
        "UPDATE gl_entries SET credit = '700.00' WHERE credit = '800.00'",
        _extra_line("OTHER", "accrual", "0.00", "100.00", "payment"),
    )
    packet = investigate(snap, request)
    assert packet.diagnosis.reason == "payment_journal_unbalanced"
    text = _text(packet)
    assert "lines attributed to this payment" in text
    assert "balanced in one currency" in text


def test_a_fee_journal_with_an_invalid_attribution(tmp_path: Path) -> None:
    snap, request = _mutated(
        tmp_path,
        "net_fee_accounting",
        _extra_line("OTHER", "accrual", "5.00", "0.00", "bank_fee"),
    )
    packet = investigate(snap, request)
    assert packet.diagnosis.reason == "fee_journal_invalid"
    text = _text(packet)
    assert "lines attributed to this bank row" in text
    assert "not balanced or mixes currencies" not in text


def test_a_negative_calculated_balance_is_not_called_unassessed(
    packets: dict[str, EvidencePacket],
) -> None:
    packet = packets["real_check_double_payment"]
    assert packet.diagnosis.reason == "invoice_over_allocated"
    assert any(str(c.result).startswith("-") for c in packet.calculations)
    text = _text(packet)
    assert "negative" in text and "could not be assessed" not in text


def test_no_template_claims_a_search_for_an_explanation() -> None:
    for reason, text in REASONS.items():
        assert "no explanation was found" not in text, reason
    unexplained = {
        "bank_amount_above_payment",
        "bank_row_before_payment",
        "clearing_lag_exceeds_limit",
        "debit_differs_from_payment",
        "allocations_differ_from_payment",
        "fee_journal_amount_differs",
        "reversal_credit_before_debit",
        "payment_over_allocated",
        "payment_journal_amount_differs",
    }
    for reason in unexplained:
        assert "does not establish an explanation" in REASONS[reason], reason


def test_absence_conclusions_speak_of_principal_rows_in_the_stated_searches() -> None:
    for category in ("F12", "legitimate_pending_settlement"):
        text = CATEGORIES[category]
        assert "principal (non-fee) bank row" in text, category
        assert "searches shown" in text, category
    assert "no bank row was found" not in CATEGORIES["F12"]


def test_tolerance_is_stated_for_equal_to_the_invoice_total() -> None:
    for category in ("legitimate_split_payment", "legitimate_due_date_payment"):
        assert "within the stated tolerance" in CATEGORIES[category]


def test_templates_added_for_the_new_reason_codes() -> None:
    assert REASONS["cutoff_differs_from_snapshot"] == (
        "The request's cutoff differs from the cutoff the data was prepared "
        "with, so the investigation did not run. Prepare the data again with "
        "this cutoff."
    )
    assert REASONS["payment_before_bank_coverage"] == (
        "The payment is dated before the bank data starts, so a missing bank "
        "row says nothing. No conclusion was drawn."
    )
    assert (
        "was not declared to mark bank-fee journals"
        in REASONS["fee_journals_not_recognised"]
    )
    assert "pending or not final" in REASONS["pending_payment_beyond_clearing_limit"]
    assert "counterparty account" in REASONS["batch_search_limit_exceeded"]
    assert "could not be established" in REASONS["required_records_missing"]
