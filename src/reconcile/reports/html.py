"""Self-contained HTML reports for evidence packets (decision D157: no template
engine, no third-party code).

Markup is built from `Element` objects only. Text children and attribute values
are escaped when rendered, so data can never become markup; raw markup can only
come from another `Element`. The page has inline CSS, no script, no external
asset, and a Content-Security-Policy that forbids all three.
"""

import re
from collections.abc import Mapping
from html import escape
from typing import Any

from reconcile.domain.contracts import (
    Disposition,
    EvidenceItem,
    EvidencePacket,
    Finding,
    Recommendation,
)
from reconcile.reports.explain import summary, title

_TAG = re.compile(r"[a-z][a-z0-9]*")
_ATTR = re.compile(r"[a-z][a-z0-9-]*")
# Only the relative forms the report writer generates: `name.html` or
# `cases/name.html`. Anything else, including a leading space or a backslash that
# a browser may read as an absolute URL, is refused.
_RELATIVE_LINK = re.compile(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*")
_VOID = frozenset({"meta", "br"})


class Element:
    __slots__ = ("attrs", "children", "tag")

    def __init__(
        self,
        tag: str,
        attrs: Mapping[str, str | None] | None,
        children: tuple["str | Element | None", ...],
    ) -> None:
        if not _TAG.fullmatch(tag):
            raise ValueError(f"bad tag {tag!r}")
        clean: list[tuple[str, str]] = []
        for name, value in (attrs or {}).items():
            # Event-handler attributes are refused by name, whatever the value.
            if not _ATTR.fullmatch(name) or name.startswith("on"):
                raise ValueError(f"bad attribute {name!r}")
            if value is not None:
                clean.append((name, value))
        self.tag = tag
        self.attrs = tuple(clean)
        self.children = children

    def render(self) -> str:
        attrs = "".join(
            f' {k}="{escape(v, quote=False).replace(chr(34), "&quot;")}"'
            for k, v in self.attrs
        )
        if self.tag in _VOID:
            return f"<{self.tag}{attrs}>"
        inner = "".join(
            c.render() if isinstance(c, Element) else escape(c, quote=False)
            for c in self.children
            if c is not None
        )
        return f"<{self.tag}{attrs}>{inner}</{self.tag}>"


def el(
    tag: str,
    attrs: Mapping[str, str | None] | None = None,
    *children: "str | Element | None",
) -> Element:
    return Element(tag, attrs, children)


def _code(text: str) -> Element:
    return el("code", None, text)


def _p(*children: "str | Element | None") -> Element:
    return el("p", None, *children)


def _link(target: str, text: str) -> Element:
    return el("a", {"href": f"#{target}"}, text)


def _list(items: tuple[str, ...], empty: str) -> Element:
    if not items:
        return _p(empty)
    return el("ul", None, *[el("li", None, i) for i in items])


def _table(
    headers: tuple[str, ...], rows: list[list["str | Element | None"]]
) -> Element:
    head = el("tr", None, *[el("th", {"scope": "col"}, h) for h in headers])
    body = [el("tr", None, *[el("td", None, c) for c in r]) for r in rows]
    return el("table", None, el("thead", None, head), el("tbody", None, *body))


def _dl(pairs: list[tuple[str, "str | Element | None"]]) -> Element:
    parts: list[Element] = []
    for k, v in pairs:
        parts += [el("dt", None, k), el("dd", None, v)]
    return el("dl", None, *parts)


# ----------------------------------------------------------------- styling

# No ampersand, angle bracket or quote may appear here: the text is escaped
# like any other, and a test pins that it comes out unchanged.
CSS = """
:root{--bg:#fff;--fg:#1b1f24;--muted:#555d68;--line:#c3c9d1;--panel:#f4f6f8;--link:#0b4fa8;--ok:#1a6b3a;--warn:#8a5a00;--stop:#a11d33;color-scheme:light dark}
@media (prefers-color-scheme: dark){:root{--bg:#14171b;--fg:#e8ebef;--muted:#a3abb6;--line:#3b424b;--panel:#1d2227;--link:#8ab9ff;--ok:#6fd49a;--warn:#f0c060;--stop:#ff8fa0}}
*{box-sizing:border-box}
body{margin:0 auto;max-width:60rem;padding:1rem;background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;font-size:1rem;line-height:1.5;overflow-wrap:anywhere}
h1{font-size:1.6rem;margin:0 0 .25rem;overflow-wrap:break-word;word-break:normal}
h2{font-size:1.25rem;margin:2rem 0 .5rem;padding-bottom:.25rem;border-bottom:2px solid var(--line)}
h3{font-size:1.05rem;margin:1rem 0 .25rem}
a{color:var(--link)}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em;overflow-wrap:anywhere}
nav ul{list-style:none;padding:0;margin:.5rem 0;display:flex;flex-wrap:wrap;gap:.25rem 1rem}
table{width:100%;table-layout:fixed;border-collapse:collapse;margin:.5rem 0}
th,td{border:1px solid var(--line);padding:.35rem .5rem;text-align:left;vertical-align:top;overflow-wrap:anywhere;word-break:break-word}
th{background:var(--panel)}
dl{display:grid;grid-template-columns:minmax(7rem,12rem) 1fr;gap:.25rem .75rem;margin:.5rem 0}
dt{font-weight:600;color:var(--muted)}
dd{margin:0}
.badge{display:inline-block;padding:.2rem .6rem;border:3px solid var(--fg);font-weight:700}
.conclusion,.record,.calc,.rec{border:1px solid var(--line);background:var(--panel);padding:.5rem .75rem;margin:.75rem 0;break-inside:avoid}
.note{border-left:4px solid var(--muted);padding:.25rem .75rem;margin:.75rem 0}
.counts{display:flex;flex-wrap:wrap;gap:.5rem;margin:.75rem 0}
.counts span{padding:.2rem .6rem;border:3px solid var(--line);font-weight:600}
span.exception{border-style:solid;border-color:var(--stop)}
span.allclear{border-style:solid;border-color:var(--ok)}
span.abstained{border-style:dashed;border-color:var(--warn)}
span.out_of_scope{border-style:dotted;border-color:var(--warn)}
span.failed{border-style:double;border-color:var(--stop)}
tr.exception td:first-child{border-left:6px solid var(--stop)}
tr.allclear td:first-child{border-left:6px solid var(--ok)}
tr.abstained td:first-child{border-left:6px dashed var(--warn)}
tr.out_of_scope td:first-child{border-left:6px dotted var(--warn)}
tr.failed td:first-child{border-left:6px double var(--stop)}
@media (max-width: 30rem){dl{grid-template-columns:1fr;gap:0}dd{margin-bottom:.5rem}}
@media print{body{max-width:none;padding:0;font-size:10pt}a{color:inherit}nav{display:none}h2{break-after:avoid}tr{break-inside:avoid}}
"""  # noqa: E501

# What an outcome means to an analyst. An answered category that is not an
# all-clear, including an unknown one, is a possible exception.
OUTCOME_COUNT_LABELS = {
    "exception": "Possible exceptions",
    "allclear": "Consistent with the checks",
    "abstained": "No conclusion",
    "out_of_scope": "Not assessed",
    "failed": "Failed",
}


def outcome_kind(packet: EvidencePacket) -> str:
    d = packet.diagnosis
    if d.disposition is not Disposition.ANSWERED:
        return d.disposition.value
    category = d.category or ""
    if category.startswith("legitimate_") or category == "no_exception":
        return "allclear"
    return "exception"


CSP = "default-src 'none'; style-src 'unsafe-inline'"


def document(title: str, body: tuple[Element, ...]) -> str:
    head = el(
        "head",
        None,
        el("meta", {"charset": "utf-8"}),
        el("meta", {"http-equiv": "Content-Security-Policy", "content": CSP}),
        el(
            "meta",
            {"name": "viewport", "content": "width=device-width, initial-scale=1"},
        ),
        el("title", None, title),
        el("style", None, CSS),
    )
    page = el("html", {"lang": "en"}, head, el("body", None, *body))
    return "<!doctype html>\n" + page.render() + "\n"


# ------------------------------------------------------------------ packet


def _record_name(packet: EvidencePacket) -> str:
    e = packet.request.primary_entity
    return f"{e.table} {e.record_id}"


def _conclusion(packet: EvidencePacket) -> Element:
    d = packet.diagnosis
    pairs: list[tuple[str, str | Element | None]] = [
        (
            "Outcome",
            el(
                "span",
                {"class": f"badge {outcome_kind(packet)}"},
                title(packet),
            ),
        ),
        (
            "Primary record",
            _record_name(packet),
        ),
        ("Symptom in the request", packet.request.symptom),
    ]
    if d.category is not None:
        pairs.append(("Code", _code(d.category)))
    if d.reason is not None:
        pairs.append(("Code", _code(d.reason)))
    return el(
        "section",
        {"id": "conclusion"},
        el("h2", None, "Conclusion"),
        el(
            "div",
            {"class": "conclusion"},
            _dl(pairs),
            *[_p(t) for t in summary(packet)],
        ),
    )


def _recommendation(r: Recommendation) -> Element:
    rests = [_link(f"chk-{c}", c) for c in r.supporting_findings]
    spaced: list[str | Element | None] = []
    for i, link in enumerate(rests):
        if i:
            spaced.append(", ")
        spaced.append(link)
    return el(
        "div",
        {"class": "rec"},
        el("h3", None, r.action_type),
        _p("Rests on the findings: ", *spaced),
        el("h4", None, "Before acting"),
        _list(r.prerequisites, "None stated."),
        el("h4", None, "Assumptions not yet resolved"),
        _list(r.unresolved_assumptions, "None stated."),
    )


def _next_steps(packet: EvidencePacket) -> Element:
    recs = packet.recommendations
    body: list[Element] = (
        [_recommendation(r) for r in recs]
        if recs
        else [_p("No next steps were proposed.")]
    )
    return el("section", {"id": "next-steps"}, el("h2", None, "Next steps"), *body)


def _links(prefix: str, ids: tuple[str, ...]) -> Element | str:
    if not ids:
        return "none"
    parts: list[str | Element | None] = []
    for i, ident in enumerate(ids):
        if i:
            parts.append(", ")
        parts.append(_link(f"{prefix}-{ident}", ident))
    return el("span", None, *parts)


def _finding_row(f: Finding) -> list[str | Element | None]:
    return [
        el("span", {"id": f"chk-{f.check_id}"}, _code(f.check_id)),
        f.state.value,
        _links("ev", f.evidence_ids),
        _links("calc", f.calculation_ids),
    ]


def _findings(packet: EvidencePacket) -> Element:
    findings = packet.diagnosis.findings
    body: Element = (
        _table(
            ("Check", "State", "Evidence", "Calculations"),
            [_finding_row(f) for f in findings],
        )
        if findings
        else _p("No findings were recorded.")
    )
    return el(
        "section",
        {"id": "findings"},
        el("h2", None, "Findings"),
        _p("Follow the links to the records and calculations each check cites."),
        body,
    )


def _calculations(packet: EvidencePacket) -> Element:
    blocks: list[Element] = []
    for c in packet.calculations:
        rows: list[list[str | Element | None]] = [
            [_link(f"ev-{o.evidence_id}", o.evidence_id), _code(o.field), str(o.amount)]
            for o in c.operands
        ]
        pairs: list[tuple[str, str | Element | None]] = [
            ("Operation", c.operation),
            ("Result", str(c.result)),
        ]
        if c.tolerance is not None:
            pairs.append(("Tolerance", str(c.tolerance)))
        blocks.append(
            el(
                "div",
                {"class": "calc", "id": f"calc-{c.calculation_id}"},
                el("h3", None, c.calculation_id),
                _table(("Evidence", "Field", "Amount"), rows),
                _dl(pairs),
            )
        )
    for dc in packet.day_counts:
        end: str | Element = (
            f"the request cutoff, {packet.request.cutoff.isoformat()}"
            if dc.end_evidence is None
            else el(
                "span",
                None,
                _link(f"ev-{dc.end_evidence}", dc.end_evidence),
                " ",
                _code(dc.end_field or ""),
            )
        )
        blocks.append(
            el(
                "div",
                {"class": "calc", "id": f"calc-{dc.calculation_id}"},
                el("h3", None, dc.calculation_id),
                _dl(
                    [
                        (
                            "From",
                            el(
                                "span",
                                None,
                                _link(f"ev-{dc.start_evidence}", dc.start_evidence),
                                " ",
                                _code(dc.start_field),
                            ),
                        ),
                        ("To", end),
                        ("Business days", str(dc.result)),
                        ("Limit", f"{dc.limit} business days"),
                        ("Limit policy", _code(dc.limit_policy)),
                    ]
                ),
            )
        )
    if not blocks:
        blocks.append(_p("There are no calculations or day counts."))
    return el(
        "section",
        {"id": "calculations"},
        el("h2", None, "Exact calculations and day counts"),
        _p(
            "Amounts are exact decimal strings with their currency, as stored "
            "in the report data. A difference is the first amount minus the others."
        ),
        *blocks,
    )


def _record(e: EvidenceItem) -> Element:
    head = el("h3", None, e.evidence_id)
    pairs: list[tuple[str, str | Element | None]] = [
        ("Relationship", _code(e.relationship))
    ]
    body: list[Element] = []
    if e.record is not None:
        pairs += [
            ("Table", _code(e.record.table)),
            ("Record id", _code(e.record.record_id)),
        ]
    if e.search is not None:
        s = e.search
        pairs += [
            ("Kind", "Absence search"),
            ("Table searched", _code(s.table)),
            ("Condition", _code(s.predicate)),
            ("Cutoff", s.cutoff.isoformat()),
            ("Rows found", str(s.observed_count)),
        ]
        body.append(
            _p(
                f"This search covers only the table and condition above, up to "
                f"the cutoff. It found {s.observed_count} rows there. It says "
                "nothing about records outside that search."
            )
        )
    if e.values:
        body.append(el("h4", None, "Fields"))
        body.append(_dl([(k, v) for k, v in e.values]))
    if e.timestamps:
        body.append(el("h4", None, "Timestamps"))
        body.append(_dl([(k, v) for k, v in e.timestamps]))
    return el(
        "div",
        {"class": "record", "id": f"ev-{e.evidence_id}"},
        head,
        _dl(pairs),
        *body,
    )


def _sources(packet: EvidencePacket) -> Element:
    blocks = [_record(e) for e in packet.evidence] or [_p("No records are cited.")]
    return el(
        "section",
        {"id": "sources"},
        el("h2", None, "Source records"),
        el(
            "p",
            {"class": "note"},
            "An absence search covers only what was searched: the table and "
            "condition shown on it, up to the cutoff. It is not a statement "
            "about any other table, record or date.",
        ),
        *blocks,
    )


def _unknowns(packet: EvidencePacket) -> Element:
    d = packet.diagnosis
    none = "None recorded."
    return el(
        "section",
        {"id": "unknowns"},
        el("h2", None, "Other supported patterns, and unknowns"),
        el("h3", None, "Other patterns the records also support"),
        el(
            "p",
            {"class": "note"},
            "The checks for these patterns also hold. The records alone do not "
            "choose between them and the conclusion.",
        ),
        _list(d.alternatives, none),
        el("h3", None, "Missing information"),
        _list(d.missing_information, none),
        el("h3", None, "Limitations"),
        _list(packet.limitations, none),
    )


def _plain(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(_plain(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_plain(v)}" for k, v in value.items())
    return str(value)


def _manifest_pairs(manifest: dict[str, Any]) -> list[tuple[str, str | Element | None]]:
    pairs: list[tuple[str, str | Element | None]] = []
    snapshot = manifest.get("snapshot")
    if isinstance(snapshot, dict) and "content_sha256" in snapshot:
        pairs.append(
            ("Snapshot digest (manifest)", _code(_plain(snapshot["content_sha256"])))
        )
    if "bank_data_through" in manifest:
        pairs.append(
            (
                "Bank data through",
                f"{_plain(manifest['bank_data_through'])} "
                "(declared by whoever prepared the data)",
            )
        )
    for key, label in (
        ("bank_data_from", "Bank data from"),
        ("all_paying_accounts_covered", "All paying accounts covered"),
        ("fee_journals_recognised", "Bank-fee journals declared as typed"),
        ("reference_matching", "Reference matching"),
    ):
        if key in manifest:
            pairs.append(
                (
                    label,
                    f"{_plain(manifest[key])} (declared by whoever prepared the data)",
                )
            )
    for key, label in (
        ("tables_not_supplied", "Tables not supplied"),
        ("format", "Source format"),
        ("reference_quality", "Reference quality"),
    ):
        if key in manifest:
            pairs.append((label, _plain(manifest[key])))
    return pairs


def _run_details(packet: EvidencePacket, manifest: dict[str, Any] | None) -> Element:
    r = packet.request
    pairs: list[tuple[str, str | Element | None]] = [
        ("Request id", _code(r.request_id)),
        ("Snapshot digest", _code(r.snapshot)),
        ("Cutoff", r.cutoff.isoformat()),
        ("Policy version requested", _code(r.policy_version)),
    ]
    if manifest:
        pairs += _manifest_pairs(manifest)
    prov = (
        _table(("Key", "Value"), [[_code(k), v] for k, v in packet.provenance])
        if packet.provenance
        else _p("No provenance recorded.")
    )
    return el(
        "section",
        {"id": "run"},
        el("h2", None, "Run details"),
        _dl(pairs),
        el("h3", None, "Provenance"),
        prov,
    )


_NAV = (
    ("conclusion", "Conclusion"),
    ("next-steps", "Next steps"),
    ("findings", "Findings"),
    ("calculations", "Calculations"),
    ("sources", "Source records"),
    ("unknowns", "Unknowns"),
    ("run", "Run details"),
)


def render_packet(
    packet: EvidencePacket, *, manifest: dict[str, Any] | None = None
) -> str:
    nav = el(
        "nav",
        None,
        el("ul", None, *[el("li", None, _link(i, t)) for i, t in _NAV]),
    )
    body = (
        el("h1", None, title(packet)),
        _p("Case ", _code(packet.request.request_id)),
        nav,
        _conclusion(packet),
        _next_steps(packet),
        _findings(packet),
        _calculations(packet),
        _sources(packet),
        _unknowns(packet),
        _run_details(packet, manifest),
    )
    return document(f"{title(packet)} - {packet.request.request_id}", body)


# ------------------------------------------------------------------- index


def _href(value: str) -> str:
    if not _RELATIVE_LINK.fullmatch(value) or ".." in value.split("/"):
        raise ValueError(f"index links must be relative, got {value!r}")
    return value


def render_index(packets: list[EvidencePacket], links: dict[str, str]) -> str:
    counts = dict.fromkeys(OUTCOME_COUNT_LABELS, 0)
    rows: list[Element] = []
    for p in packets:
        d = p.diagnosis
        kind = outcome_kind(p)
        counts[kind] += 1
        rid = p.request.request_id
        href = links.get(rid)
        record = _record_name(p)
        record_cell: str | Element = (
            record if href is None else el("a", {"href": _href(href)}, record)
        )
        what = d.category if d.category is not None else d.reason
        rows.append(
            el(
                "tr",
                {"class": kind},
                el("td", None, record_cell),
                el("td", None, title(p)),
                el("td", None, _code(what or "")),
                el("td", None, el("small", None, _code(rid))),
            )
        )
    head = el(
        "tr",
        None,
        *[
            el("th", {"scope": "col"}, h)
            for h in ("Primary record", "Outcome", "Code", "Request id")
        ],
    )
    body = (
        el("h1", None, "Reconcile cases"),
        _p(f"{len(packets)} cases."),
        el(
            "div",
            {"class": "counts"},
            *[
                el("span", {"class": kind}, f"{OUTCOME_COUNT_LABELS[kind]}: {n}")
                for kind, n in counts.items()
            ],
        ),
        el("table", None, el("thead", None, head), el("tbody", None, *rows)),
    )
    return document("Reconcile cases", body)
