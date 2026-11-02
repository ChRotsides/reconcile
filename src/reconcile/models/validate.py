"""Deterministic checks of a model-written draft against its evidence packet.

The draft can only explain the packet: it cannot change the diagnosis. A draft
is accepted only if every structured reference resolves inside the packet and
every amount, identifier, date, count, category and action named in its prose
appears in the packet. A claim bound to a supported finding inherits that
finding's deterministic check; the surrounding prose is not semantically
verified, and callers must label it "unvalidated" (PLAN.md section 7).

Known limits (documented, not hidden): prose can still misdescribe a real
record in words, and numbers written as words or as plain integers outside the
patterns below are not caught.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from reconcile.domain.contracts import (
    Disposition,
    EvidencePacket,
    FindingState,
)
from reconcile.domain.money import CURRENCY_SCALE

MAX_SUMMARY = 600
MAX_TEXT = 300
MAX_CLAIMS = 20
NO_ACTION = "none"

# Verbs that would send an analyst to change money or records. They are
# allowed only when the packet's own text (recommendations, assumptions,
# limitations) already uses the word.
FORBIDDEN_ACTIONS = (
    "pay",
    "repay",
    "transfer",
    "approve",
    "delete",
    "release",
    "refund",
    "reverse",
    "void",
    "cancel",
    "reissue",
    "recall",
    "write-off",
    "write off",
)

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_HEX = re.compile(r"\b[0-9a-f]{32}\b")
_PREFIXED = re.compile(
    r"\b(?:pay|alloc|bank|journal|inv|approval|search)(?::|_(?=\d))[\w.:|-]*"
)
_AMOUNT = re.compile(
    r"(?<![\w.:])(?P<num>\d[\d,]*\.\d+|\d[\d,]*(?=\s[A-Z]{3}\b))(?:\s(?P<cur>[A-Z]{3})\b)?"
)
_DAYS = re.compile(r"\b(\d+)\s+(?:business\s+|calendar\s+)?days?\b", re.I)
_CATEGORY = re.compile(r"\b(?:F\d{2}|legitimate_[a-z_]+|no_exception)\b", re.I)
_LEGITIMATE = re.compile(r"\blegitimate\b", re.I)
_ACTION_WORD = re.compile(
    r"\b(?:" + "|".join(a.replace(" ", r"\s+") for a in FORBIDDEN_ACTIONS) + r")\b",
    re.I,
)


def _strip_ids(text: str) -> str:
    return _PREFIXED.sub(" ", _HEX.sub(" ", text))


def _amounts(
    text: str, currencies: frozenset[str] | None = None
) -> set[tuple[Decimal, str | None]]:
    """(absolute value, currency or None) for each decimal or currency-tagged number.

    With `currencies`, a 3-letter token after a number is a currency only if it
    is in that set ("7590.62 ACH payment" is the bare amount 7590.62); a bare
    integer is then not an amount.
    """
    found: set[tuple[Decimal, str | None]] = set()
    for m in _AMOUNT.finditer(_DATE.sub(" ", _strip_ids(text))):
        cur = m["cur"]
        if currencies is not None and cur not in currencies:
            cur = None
            if "." not in m["num"]:
                continue
        try:
            value = abs(Decimal(m["num"].replace(",", "")))
        except InvalidOperation:  # a stray "1,,2" is not an amount
            continue
        found.add((value, cur))
    return found


def _packet_currencies(packet_json: str) -> frozenset[str]:
    """Codes the packet's amount strings carry ("12.50 USD"), plus known ISO codes
    so a real wrong currency in prose is still checked against the packet."""
    return frozenset(re.findall(r"\d ([A-Z]{3})\"", packet_json)) | frozenset(
        CURRENCY_SCALE
    )


def _prefixed_ids(text: str) -> set[str]:
    return {m.group().rstrip(".:|-") for m in _PREFIXED.finditer(text)}


def _packet_day_numbers(packet: EvidencePacket) -> set[int]:
    nums = {0}
    for d in packet.day_counts:
        nums.update((d.result, d.limit))
    for e in packet.evidence:
        if e.search is not None:
            nums.add(e.search.observed_count)
    return nums


def _snip(s: str) -> str:
    return s[:60]


def _is_text(v: object, limit: int) -> bool:
    return isinstance(v, str) and bool(v.strip()) and len(v) <= limit


def _id_list(v: object) -> list[str] | None:
    if isinstance(v, list) and all(isinstance(i, str) for i in v):
        return v
    return None


def _check_shape(draft: Any) -> list[str]:
    if not isinstance(draft, dict):
        return ["draft is not a JSON object"]
    problems: list[str] = []
    if set(draft) != {"summary", "claims", "next_step"}:
        problems.append("draft keys must be exactly summary, claims, next_step")
        return problems
    if not _is_text(draft["summary"], MAX_SUMMARY):
        problems.append(f"summary must be a non-empty string of at most {MAX_SUMMARY}")
    claims = draft["claims"]
    if not isinstance(claims, list) or len(claims) > MAX_CLAIMS:
        problems.append(f"claims must be a list of at most {MAX_CLAIMS}")
    else:
        for n, c in enumerate(claims):
            if not isinstance(c, dict) or set(c) != {
                "finding",
                "text",
                "evidence_ids",
                "calculation_ids",
            }:
                problems.append(f"claim {n}: wrong keys")
                continue
            if not isinstance(c["finding"], str):
                problems.append(f"claim {n}: finding must be a string")
            if not _is_text(c["text"], MAX_TEXT):
                problems.append(
                    f"claim {n}: text must be non-empty, at most {MAX_TEXT} characters"
                )
            for k in ("evidence_ids", "calculation_ids"):
                ids = _id_list(c[k])
                if ids is None:
                    problems.append(f"claim {n}: {k} must be a list of strings")
                elif len(set(ids)) != len(ids):
                    problems.append(f"claim {n}: duplicate {k}")
    ns = draft["next_step"]
    if not isinstance(ns, dict) or set(ns) != {"action", "text"}:
        problems.append("next_step must have exactly action and text")
    else:
        if not isinstance(ns["action"], str):
            problems.append("next_step.action must be a string")
        if not _is_text(ns["text"], MAX_TEXT):
            problems.append(f"next_step.text must be non-empty, at most {MAX_TEXT}")
    return problems


def _check_claims(draft: dict[str, Any], packet: EvidencePacket) -> list[str]:
    problems: list[str] = []
    findings = {f.check_id: f for f in packet.diagnosis.findings}
    answered = packet.diagnosis.disposition is Disposition.ANSWERED
    covered: set[str] = set()
    for n, c in enumerate(draft["claims"]):
        f = findings.get(c["finding"])
        if f is None:
            problems.append(f"claim {n}: unknown finding {_snip(c['finding'])!r}")
            continue
        if answered and f.state is not FindingState.SUPPORTED:
            problems.append(f"claim {n}: finding {f.check_id} is not supported")
        covered.add(f.check_id)
        if not c["evidence_ids"]:
            problems.append(f"claim {n}: no evidence cited")
        for i in c["evidence_ids"]:
            if i not in f.evidence_ids:
                problems.append(
                    f"claim {n}: evidence {_snip(i)!r} is not in finding {f.check_id}"
                )
        for i in c["calculation_ids"]:
            if i not in f.calculation_ids:
                problems.append(
                    f"claim {n}: calculation {_snip(i)!r} not in finding {f.check_id}"
                )
    if answered:
        # Every supported finding is part of why the category was reached, so
        # leaving one out would let the prose understate the basis.
        for f in packet.diagnosis.findings:
            if f.state is FindingState.SUPPORTED and f.check_id not in covered:
                problems.append(f"finding {f.check_id} has no claim")
    return problems


def _check_next_step(draft: dict[str, Any], packet: EvidencePacket) -> list[str]:
    allowed = {r.action_type for r in packet.recommendations} or {NO_ACTION}
    action = draft["next_step"]["action"]
    if action not in allowed:
        return [f"next_step.action {_snip(action)!r} is not one of {sorted(allowed)}"]
    return []


def _check_text(text: str, packet: EvidencePacket, packet_json: str) -> list[str]:
    problems: list[str] = []
    packet_ids = {e.evidence_id for e in packet.evidence}
    for tok in _HEX.findall(text):
        if tok not in packet_json:
            problems.append(f"unknown token {tok[:12]}...")
    for i in _prefixed_ids(text):
        if i not in packet_ids:
            problems.append(f"unknown id {_snip(i)!r}")
    for d in _DATE.findall(text):
        if d not in packet_json:
            problems.append(f"date {d} is not in the packet")
    packet_amounts = _amounts(packet_json)
    packet_values = {v for v, _ in packet_amounts}
    for value, cur in _amounts(text, _packet_currencies(packet_json)):
        ok = (value, cur) in packet_amounts if cur else value in packet_values
        if not ok:
            problems.append(
                f"amount {value}{' ' + cur if cur else ''} is not in the packet"
            )
    allowed_days = _packet_day_numbers(packet)
    for m in _DAYS.findall(text):
        if int(m) not in allowed_days:
            problems.append(f"day count {m} is not in the packet")
    category = packet.diagnosis.category
    for tok in _CATEGORY.findall(text):
        if category is None or tok.lower() != category.lower():
            problems.append(f"category {tok!r} is not this packet's diagnosis")
    if _LEGITIMATE.search(text) and not (category or "").startswith("legitimate"):
        problems.append("calls the case legitimate without a legitimate diagnosis")
    own = set(
        re.findall(
            r"[a-z]+",
            " ".join(
                r.action_type
                + " "
                + " ".join(r.prerequisites + r.unresolved_assumptions)
                for r in packet.recommendations
            ).lower()
            + " "
            + " ".join(packet.limitations).lower(),
        )
    )
    for w in {
        re.sub(r"\s+", " ", m.group().lower())
        for m in _ACTION_WORD.finditer(_strip_ids(text))
    }:
        if not set(re.findall(r"[a-z]+", w)) <= own:
            problems.append(f"action word {w!r} does not appear in the packet")
    return problems


def validate(draft: Any, packet: EvidencePacket) -> list[str]:
    """Problems with `draft` against `packet`; an empty list means accepted."""
    problems = _check_shape(draft)
    if problems:
        return problems
    problems = _check_claims(draft, packet) + _check_next_step(draft, packet)
    packet_json = json.dumps(packet.to_obj(), ensure_ascii=False)
    texts = [draft["summary"], draft["next_step"]["text"]]
    texts += [c["text"] for c in draft["claims"]]
    for t in texts:
        problems += _check_text(t, packet, packet_json)
    return list(dict.fromkeys(problems))


def claims_bound_to_findings(draft: dict[str, Any]) -> int:
    """Claims whose support is the cited deterministic finding. All other
    prose (the summary, wording of claims, next-step text) stays unvalidated."""
    return len(draft["claims"])
