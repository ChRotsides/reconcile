"""HTML reports: escaping, no external assets, resolving anchors, shown values."""

import json
import re
from datetime import date
from html import escape
from html.parser import HTMLParser
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
    AbsenceSearch,
    Diagnosis,
    Disposition,
    EvidenceItem,
    EvidencePacket,
    Finding,
    FindingState,
    InvestigationRequest,
    Recommendation,
    RecordRef,
)
from reconcile.reports.explain import title
from reconcile.reports.html import CSP, CSS, el, render_index, render_packet
from reconcile.retrieval.snapshot import Limits
from reconcile.runs import batch

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
DIGEST = "a" * 64
PAYLOADS = ("<script>alert(1)</script>", '"><img src=x onerror=1>', "&amp;")
EVIL = " ".join(PAYLOADS)


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
    first = next(iter(out.values()))
    out["failed"] = batch._failed(first.request, RuntimeError("boom"))
    return out


class Page(HTMLParser):
    """What a browser would see: tags, attributes, ids, links and text."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str, str | None]] = []
        self.ids: list[str] = []
        self.hrefs: list[str] = []
        self.text = ""
        self.h1 = ""
        self.prose = ""  # visible text outside <code>, <style> and <title>
        self._open: list[str] = []
        self.feed(text)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self._open.append(tag)
        for k, v in attrs:
            self.attrs.append((tag, k, v))
            if k == "id" and v is not None:
                self.ids.append(v)
            if k == "href" and v is not None:
                self.hrefs.append(v)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._open:
            while self._open.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        self.text += data + "\n"
        if "h1" in self._open:
            self.h1 += data
        if not {"code", "style", "title"} & set(self._open):
            self.prose += data + "\n"


ALLOWED_TAGS = {
    "html", "head", "meta", "title", "style", "body", "h1", "h2", "h3", "h4",
    "p", "nav", "ul", "li", "section", "div", "span", "a", "code", "table",
    "thead", "tbody", "tr", "th", "td", "dl", "dt", "dd",
}  # fmt: skip


def check_safe(html: str, *, plain_data: bool = True) -> Page:
    page = Page(html)
    assert set(page.tags) <= ALLOWED_TAGS, set(page.tags) - ALLOWED_TAGS
    assert not [a for a in page.attrs if a[1].startswith("on")]
    assert not [a for a in page.attrs if a[1] in ("src", "srcset", "action", "data")]
    assert not re.search(r"https?://|//\w", html, re.I)
    if plain_data:  # hostile test data legitimately contains "src=" as text
        assert not re.search(r"\bsrc\s*=", html, re.I)
    assert "url(" not in html.lower() and "@import" not in html.lower()
    assert "javascript:" not in html.lower()
    assert len(page.ids) == len(set(page.ids)), "duplicate id"
    for href in page.hrefs:
        assert href.startswith("#"), href
        assert href[1:] in page.ids, f"dangling anchor {href}"
    csp = [a for a in page.attrs if a[:2] == ("meta", "http-equiv")]
    assert csp and f'content="{CSP}"' in html
    return page


# -------------------------------------------------------------- fixtures


def test_every_scenario_renders_safely_with_resolving_anchors(
    packets: dict[str, EvidencePacket],
) -> None:
    assert set(EXPECTED) <= set(packets)
    assert {p.diagnosis.disposition for p in packets.values()} == set(Disposition)
    for name, packet in packets.items():
        page = check_safe(render_packet(packet))
        for f in packet.diagnosis.findings:
            assert f"chk-{f.check_id}" in page.ids, name
            for i in f.evidence_ids:
                assert f"ev-{i}" in page.ids and f"#ev-{i}" in page.hrefs, (name, i)
            for i in f.calculation_ids:
                assert f"calc-{i}" in page.ids and f"#calc-{i}" in page.hrefs, name
        for e in packet.evidence:
            assert f"ev-{e.evidence_id}" in page.ids, name
        for r in packet.recommendations:
            for c in r.supporting_findings:
                assert f"#chk-{c}" in page.hrefs, name


def test_sections_are_in_the_required_order(
    packets: dict[str, EvidencePacket],
) -> None:
    html = render_packet(packets["f06_double_payment"])
    marks = [
        'id="conclusion"',
        'id="next-steps"',
        'id="findings"',
        'id="calculations"',
        'id="sources"',
        'id="unknowns"',
        'id="run"',
    ]
    positions = [html.index(m) for m in marks]
    assert positions == sorted(positions)


def test_packet_values_are_shown_verbatim(packets: dict[str, EvidencePacket]) -> None:
    for name, p in packets.items():
        html = render_packet(p)
        text = check_safe(html).text
        for e in p.evidence:
            for k, v in (*e.values, *e.timestamps):
                assert k in text and v in text, (name, k, v)
            if e.search is not None:
                s = e.search
                assert s.predicate in text and s.table in text, name
                assert f"{s.observed_count} rows" in text, name
            if e.record is not None:
                assert e.record.record_id in text, name
        for c in p.calculations:
            assert str(c.result) in text, name
            assert c.operation in text, name
            for o in c.operands:
                assert str(o.amount) in text, (name, o.amount)
        for dc in p.day_counts:
            assert str(dc.result) in text and dc.limit_policy in text, name
        for text_item in (
            *p.limitations,
            *p.diagnosis.missing_information,
            *p.diagnosis.alternatives,
            p.request.symptom,
            p.request.snapshot,
            p.request.policy_version,
            p.request.cutoff.isoformat(),
        ):
            assert text_item in text, (name, text_item)
        for k, v in p.provenance:
            assert k in text and v in text, name
        for r in p.recommendations:
            for item in (r.action_type, *r.prerequisites, *r.unresolved_assumptions):
                assert item in text, name
        assert "covers only what was searched" in text or not any(
            e.search for e in p.evidence
        )


def test_trailing_zero_amounts_are_not_reformatted(
    packets: dict[str, EvidencePacket],
) -> None:
    p = packets["f06_double_payment"]
    amounts = {str(o.amount) for c in p.calculations for o in c.operands}
    assert any(a.startswith("1000.00") for a in amounts)
    text = check_safe(render_packet(p)).text
    for a in amounts:
        assert a in text
    assert not re.search(r"\b1000(\.0)?\b(?! ?\w)", text.replace("1000.00", ""))


def test_outcome_is_never_only_a_colour(packets: dict[str, EvidencePacket]) -> None:
    for name in ("failed", "row_limit", "journal_out_of_scope", "split_payment"):
        p = packets[name]
        page = check_safe(render_packet(p))
        assert title(p) in page.text, name
        assert page.h1 == title(p), name
        d = p.diagnosis
        assert (d.category or d.reason or "") in page.text, name


def test_no_next_steps_is_stated(packets: dict[str, EvidencePacket]) -> None:
    p = packets["split_payment"]
    assert not p.recommendations
    assert "No next steps were proposed" in check_safe(render_packet(p)).text


def test_unknowns_are_shown_in_full(packets: dict[str, EvidencePacket]) -> None:
    p = packets["split_payment"]
    text = check_safe(render_packet(p)).text
    for item in p.limitations:
        assert item in text
    p = packets["row_limit"]
    text = check_safe(render_packet(p)).text
    assert p.diagnosis.missing_information
    for item in (*p.diagnosis.missing_information, *p.limitations):
        assert item in text


def test_no_confidence_is_invented(packets: dict[str, EvidencePacket]) -> None:
    for p in packets.values():
        assert "confidence" not in check_safe(render_packet(p)).text.lower()


# ------------------------------------------------------------- manifest


def test_manifest_fields_are_shown_when_present_and_quiet_when_absent(
    packets: dict[str, EvidencePacket],
) -> None:
    p = packets["split_payment"]
    bare = check_safe(
        render_packet(p, manifest={"snapshot": {"content_sha256": "b" * 64}})
    )
    assert "b" * 64 in bare.text
    for label in ("Bank data through", "Tables not supplied", "Source format"):
        assert label not in bare.text
    full = check_safe(
        render_packet(
            p,
            manifest={
                "snapshot": {"content_sha256": "b" * 64},
                "bank_data_through": "2026-06-26",
                "tables_not_supplied": ["approval_events", "gl_entries"],
                "format": "csv_export_v1",
                "reference_quality": {"payment_ref": "free text"},
            },
        )
    )
    for want in (
        "2026-06-26",
        "declared by whoever prepared the data",
        "approval_events, gl_entries",
        "csv_export_v1",
        "payment_ref: free text",
    ):
        assert want in full.text, want
    none = check_safe(render_packet(p, manifest=None))
    assert "Bank data through" not in none.text


# ------------------------------------------------------------ injection


def _hostile_packets() -> list[EvidencePacket]:
    req = InvestigationRequest(
        "req_evil",
        DIGEST,
        RecordRef(DIGEST, "payments", EVIL),
        EVIL,
        CUTOFF,
        "policy_test",
    )
    search = AbsenceSearch(DIGEST, "payments", EVIL, CUTOFF, 0)
    ev = (
        EvidenceItem("e_search", None, search, "a1"),
        EvidenceItem(
            "e_rec",
            RecordRef(DIGEST, "payments", EVIL),
            None,
            "payment_ref",
            values=(("payment_amount", "10.00 EUR"), ("memo", EVIL)),
            timestamps=(("payment_date", "2026-06-01"),),
        ),
    )
    findings = (Finding("c1", FindingState.SUPPORTED, ("e_search", "e_rec")),)
    rec = Recommendation(
        "do_it",
        ("c1",),
        prerequisites=(EVIL,),
        unresolved_assumptions=(EVIL,),
    )
    prov = (("investigator", EVIL),)
    answered = EvidencePacket(
        req,
        Diagnosis(
            Disposition.ANSWERED,
            "evil_category",
            findings,
            missing_information=(EVIL,),
        ),
        ev,
        recommendations=(rec,),
        limitations=(EVIL,),
        provenance=prov,
    )
    out = [answered]
    for d in (Disposition.ABSTAINED, Disposition.OUT_OF_SCOPE, Disposition.FAILED):
        out.append(
            EvidencePacket(
                req,
                Diagnosis(d, None, findings, missing_information=(EVIL,), reason=EVIL),
                ev,
                recommendations=(rec,),
                limitations=(EVIL,),
                provenance=prov,
            )
        )
    return out


def test_hostile_text_never_becomes_markup() -> None:
    manifest = {
        "snapshot": {"content_sha256": EVIL},
        "bank_data_through": EVIL,
        "tables_not_supplied": [EVIL],
        "format": EVIL,
        "reference_quality": EVIL,
    }
    for packet in _hostile_packets():
        html = render_packet(packet, manifest=manifest)
        page = check_safe(html, plain_data=False)
        assert "<script" not in html and "<img" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        # &amp; in the data is escaped once more, so it displays as the same text.
        assert "&amp;amp;" in html
        for payload in PAYLOADS:
            assert payload in page.text
        assert "script" not in page.tags and "img" not in page.tags


def test_hostile_text_in_the_index() -> None:
    packets = _hostile_packets()
    html = render_index(packets, {"req_evil": "case.html"})
    page = Page(html)
    assert "script" not in page.tags and "img" not in page.tags
    assert not [a for a in page.attrs if a[1].startswith("on")]
    assert "<script" not in html and "<img" not in html
    assert EVIL in page.text


def test_element_builder_escapes_text_and_attributes() -> None:
    node = el("p", {"title": '"><img src=x onerror=1>', "class": "a"}, EVIL, None)
    page = Page(node.render())
    assert page.tags == ["p"]
    assert dict((k, v) for _, k, v in page.attrs)["title"] == '"><img src=x onerror=1>'
    assert EVIL in page.text


def test_element_builder_refuses_unsafe_names() -> None:
    for tag in ("script>", "P", "a b", ""):
        with pytest.raises(ValueError):
            el(tag)
    for name in ("onclick", "onerror", 'a" onclick="x', "A", ""):
        with pytest.raises(ValueError):
            el("p", {name: "x"})


def test_css_survives_escaping_unchanged() -> None:
    assert escape(CSS, quote=False) == CSS
    assert "prefers-color-scheme: dark" in CSS and "@media print" in CSS
    assert "overflow-wrap" in CSS


def test_index_links_must_be_relative() -> None:
    packets = _hostile_packets()[:1]
    for bad in ("javascript:alert(1)", "https://example.com/x", "//evil/x"):
        with pytest.raises(ValueError):
            render_index(packets, {"req_evil": bad})


# ------------------------------------------------------------------ index


def test_index_counts_links_and_makes_failures_visible(
    packets: dict[str, EvidencePacket],
) -> None:
    listed = list(packets.values())
    links = {p.request.request_id: f"{p.request.request_id}.html" for p in listed}
    html = render_index(listed, links)
    page = Page(html)
    assert not [a for a in page.attrs if a[1].startswith("on")]
    assert not re.search(r"https?://|\bsrc\s*=|url\(", html)
    for kind in Disposition:
        assert any(p.diagnosis.disposition is kind for p in listed)
    expected = {
        "Answered": sum(
            p.diagnosis.disposition is Disposition.ANSWERED for p in listed
        ),
        "No conclusion (abstained)": sum(
            p.diagnosis.disposition is Disposition.ABSTAINED for p in listed
        ),
        "Not assessed (out of scope)": sum(
            p.diagnosis.disposition is Disposition.OUT_OF_SCOPE for p in listed
        ),
        "Failed: the investigation did not complete": sum(
            p.diagnosis.disposition is Disposition.FAILED for p in listed
        ),
    }
    for label, n in expected.items():
        assert f"{label}: {n}" in page.text, label
    assert f"{len(listed)} cases." in page.text
    assert "req_test.html" in page.hrefs
    rows = html.count("<tr class=")
    assert rows == len(listed)
    for p in listed:
        diag = p.diagnosis
        assert (diag.category or diag.reason or "") in page.text
    for cls in ("answered", "abstained", "out_of_scope", "failed"):
        assert f'<tr class="{cls}">' in html


def test_index_without_a_link_shows_the_id_as_text(
    packets: dict[str, EvidencePacket],
) -> None:
    page = Page(render_index([packets["failed"]], {}))
    assert page.hrefs == []
    assert "req_test" in page.text


def test_visible_text_never_says_packet(packets: dict[str, EvidencePacket]) -> None:
    for name, p in packets.items():
        page = check_safe(render_packet(p, manifest={"format": "x"}))
        assert "packet" not in page.prose.lower(), name
    listed = list(packets.values())
    assert "packet" not in Page(render_index(listed, {})).prose.lower()


def test_conclusion_carries_the_limitations_and_titles_headline(
    packets: dict[str, EvidencePacket],
) -> None:
    for name, p in packets.items():
        html = render_packet(p)
        concl = html[html.index('id="conclusion"') : html.index('id="next-steps"')]
        text = Page(concl).text
        for item in p.limitations:
            assert item in text, name
        assert "Calculation " not in text and "Proposed next step" not in text, name
        assert f"<h1>{escape(title(p), quote=False)}</h1>" in html


def test_narrow_screen_css() -> None:
    assert "@media (max-width: 30rem){dl{grid-template-columns:1fr" in CSS
    assert "h1{" in CSS and "overflow-wrap:break-word" in CSS.split("h1{")[1][:120]


def test_index_outcome_column_uses_the_title(
    packets: dict[str, EvidencePacket],
) -> None:
    page = Page(render_index([packets["f12_payment_missing_from_bank"]], {}))
    assert "Payment possibly missing from the bank" in page.text
