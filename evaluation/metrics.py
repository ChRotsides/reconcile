"""Shared metric definitions (PLAN.md section 7) for both scorers:
evaluation/dev_run.py (investigator) and evaluation/baselines/observable/run.py.

Scored rows go in, plain dicts come out. Nothing here reads a snapshot, a packet
or a label file; the scorers put the per-case facts they computed on the row.

Empty-denominator convention: `frac(n, d)` has rate None when d == 0. A rate is
never replaced by 0 or 1 when nothing could be measured. The same holds for
every metric below that is a `frac`.

Row fields used (all optional unless a function says so):
  group, failure_type, variant, difficulty, disposition, reason, category,
  expected_category, outcome, kind, complete,
  req_total/req_met        evidence-contract items required / satisfied,
  cited_total/cited_resolved, claims/claims_supported,
  calcs/calcs_exact        (answered cases only).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import cohort as C

is_failure = C.is_failure
is_no_failure = C.is_no_failure


def frac(n: int, d: int) -> dict[str, Any]:
    return {"n": n, "d": d, "rate": (round(n / d, 4) if d else None)}


def classify(row: dict) -> None:
    """Fill row['outcome'] (and 'kind' for wrong ones)."""
    disp, cat, grp = row["disposition"], row["category"], row["group"]
    label_fail = row["failure_type"] != "NO_FAILURE"
    expected = row["expected_category"]
    if grp in ("cohort", "label_conflict"):
        if disp != "answered":
            row["outcome"] = disp  # abstained | out_of_scope | failed
        elif cat == expected:
            row["outcome"] = "correct"
        else:
            row["outcome"] = "wrong"
            if not label_fail and is_failure(cat):
                row["kind"] = "false_alarm"
            elif label_fail and is_no_failure(cat):
                row["kind"] = "failure_called_no_failure"
            elif label_fail:
                row["kind"] = "wrong_failure_class"
            elif cat == "no_exception":
                row["kind"] = "legit_variant_called_no_exception"
            else:
                row["kind"] = "wrong_legit_variant"
    elif grp == "fx":
        if disp == "abstained" and row["reason"] == "unsupported_fx":
            row["outcome"] = "correct_abstention"
        elif disp == "answered":
            row["outcome"] = "wrong"
            row["kind"] = "false_alarm" if is_failure(cat) else "answered_fx_pair"
        else:
            row["outcome"] = disp
            row["kind"] = "abstained_other_reason"
    elif grp == "not_gradable":
        row["outcome"] = "answered_not_gradable" if disp == "answered" else disp
    elif grp == "contradicted":
        row["outcome"] = disp
        if disp == "answered":
            row["kind"] = (
                "answered_label_category" if cat == expected else "answered_other"
            )
    elif grp == "oos_failure":
        if disp == "answered":
            row["outcome"] = "answered_oos"
            row["kind"] = (
                "failure_class_on_other_family"
                if is_failure(cat)
                else "failure_called_no_failure"
            )
        else:
            row["outcome"] = disp
    else:  # oos_legit
        if disp == "answered":
            row["outcome"] = "answered_oos"
            if is_failure(cat):
                row["kind"] = "false_alarm"
            elif cat == "no_exception":
                row["kind"] = "no_exception_on_ap_side_legit"
            else:
                row["kind"] = "payment_variant_on_ap_side_legit"
        else:
            row["outcome"] = disp


def cohort_metrics(
    rows: list[dict],
    groups: tuple[str, ...] = C.COHORT_VIEWS["all_cases"],
) -> dict:
    """Metrics over the gradable in-scope cohort. Default: the headline view,
    every such case. C.COHORT_VIEWS["sensitivity_without_verified_conflicts"]
    leaves out the hand-verified label conflicts; the baseline reports the same
    two views from the same module."""
    c = [r for r in rows if r["group"] in groups]
    answered = [r for r in c if r["disposition"] == "answered"]
    correct = [r for r in answered if r["outcome"] == "correct"]
    complete = [r for r in correct if r.get("complete")]
    legit = [r for r in c if r["failure_type"] == "NO_FAILURE"]
    coarse_ok = [
        r
        for r in answered
        if is_failure(r["category"]) == (r["failure_type"] != "NO_FAILURE")
    ]
    return {
        "cases": len(c),
        "dispositions": dict(Counter(r["disposition"] for r in c)),
        "answered": frac(len(answered), len(c)),
        "abstained": frac(sum(r["disposition"] == "abstained" for r in c), len(c)),
        "out_of_scope": frac(
            sum(r["disposition"] == "out_of_scope" for r in c), len(c)
        ),
        "failed": frac(sum(r["disposition"] == "failed" for r in c), len(c)),
        "answer_precision": frac(len(correct), len(answered)),
        "category_accuracy_answered": frac(len(correct), len(answered)),
        "failure_vs_no_failure_accuracy_answered": frac(len(coarse_ok), len(answered)),
        "correct_and_evidence_complete": frac(len(complete), len(c)),
        "evidence_complete_among_correct": frac(len(complete), len(correct)),
        "evidence_complete_among_answered": frac(
            sum(bool(r.get("complete")) for r in answered), len(answered)
        ),
        "false_alarms": frac(
            sum(r.get("kind") == "false_alarm" for r in legit), len(legit)
        ),
        "search_counts_reproduce_among_answered": frac(
            sum(not r["searches_mismatch"] for r in answered), len(answered)
        ),
        "wrong_by_kind": dict(Counter(r["kind"] for r in c if r["outcome"] == "wrong")),
        "abstain_or_nonanswer_reasons": dict(
            Counter(
                f"{r['disposition']}:{r['reason']}"
                for r in c
                if r["disposition"] != "answered"
            )
        ),
    }


def label_key(r: dict) -> str:
    return r["failure_type"] if r["failure_type"] != "NO_FAILURE" else r["variant"]


def per_label(rows: list[dict], groups: tuple[str, ...]) -> dict:
    out: dict[str, dict] = {}
    for k in sorted({label_key(r) for r in rows if r["group"] in groups}):
        sub = [r for r in rows if r["group"] in groups and label_key(r) == k]
        ans = [r for r in sub if r["disposition"] == "answered"]
        cor = [r for r in ans if r["outcome"] == "correct"]
        out[k] = {
            "cases": len(sub),
            "answered": len(ans),
            "correct": len(cor),
            "complete_correct": sum(bool(r.get("complete")) for r in cor),
            "abstained": sum(r["disposition"] == "abstained" for r in sub),
            "out_of_scope": sum(r["disposition"] == "out_of_scope" for r in sub),
            "failed": sum(r["disposition"] == "failed" for r in sub),
            "answered_categories": dict(Counter(r["category"] for r in ans)),
        }
    return out


def confusion(rows: list[dict], groups: tuple[str, ...]) -> dict:
    """label -> {answered category or non-answer disposition: count}."""
    out: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r["group"] in groups:
            out[label_key(r)][
                r["category"] if r["disposition"] == "answered" else r["disposition"]
            ] += 1
    return {k: dict(v) for k, v in sorted(out.items())}


def all_clear_risk(rows: list[dict]) -> dict:
    """Headline risk: failures outside the cohort that the tool answered
    legitimate_* or no_exception, i.e. a false all-clear. Per upstream family."""
    oos = [r for r in rows if r["group"] == "oos_failure"]

    def given(r: dict) -> bool:
        return r["disposition"] == "answered" and is_no_failure(r["category"])

    return {
        "overall": frac(sum(map(given, oos)), len(oos)),
        "per_family": {
            f: frac(
                sum(given(r) for r in oos if r["failure_type"] == f),
                sum(r["failure_type"] == f for r in oos),
            )
            for f in sorted({r["failure_type"] for r in oos})
        },
    }


# ------------------------------------------------------- section 7 additions


def root_cause_accuracy_by(
    rows: list[dict],
    field: str,
    groups: tuple[str, ...] = C.COHORT_VIEWS["all_cases"],
) -> dict:
    """Root-cause accuracy (exact category) per value of `field` ("failure_type"
    for family, "difficulty"), over cohort rows. `correct_over_cases` counts
    non-answers as misses; `correct_over_answered` is answer precision. A row
    without the field is grouped under "unknown". Empty groups do not appear;
    each rate follows `frac`."""
    out: dict[str, dict] = {}
    c = [r for r in rows if r["group"] in groups]
    for k in sorted({str(r.get(field) or "unknown") for r in c}):
        sub = [r for r in c if str(r.get(field) or "unknown") == k]
        ans = [r for r in sub if r["disposition"] == "answered"]
        cor = [r for r in ans if r["outcome"] == "correct"]
        out[k] = {
            "cases": len(sub),
            "answered": frac(len(ans), len(sub)),
            "correct_over_answered": frac(len(cor), len(ans)),
            "correct_over_cases": frac(len(cor), len(sub)),
        }
    return out


def _answered(rows: list[dict], groups: tuple[str, ...]) -> list[dict]:
    return [r for r in rows if r["group"] in groups and r["disposition"] == "answered"]


def evidence_recall(
    rows: list[dict], groups: tuple[str, ...] = C.COHORT_VIEWS["all_cases"]
) -> dict:
    """Required evidence-contract items satisfied / required items, micro-averaged
    over answered cohort cases. Cases whose category has no contract contribute
    no items (0/0); if no case has any, the rate is None."""
    a = _answered(rows, groups)
    return frac(
        sum(r.get("req_met", 0) for r in a), sum(r.get("req_total", 0) for r in a)
    )


def citation_resolution(
    rows: list[dict], groups: tuple[str, ...] = C.COHORT_VIEWS["all_cases"]
) -> dict:
    """Cited evidence items whose (table, record_id) exists in the snapshot and
    whose recorded values equal the snapshot values / cited evidence items, over
    answered cohort cases. This replaces PLAN's record-set evidence precision
    (D122): a cited record that the contract does not require is not an error,
    an unresolvable or altered one is. Search-only items cite no record and are
    outside this count."""
    a = _answered(rows, groups)
    return frac(
        sum(r.get("cited_resolved", 0) for r in a),
        sum(r.get("cited_total", 0) for r in a),
    )


def claim_support(
    rows: list[dict], groups: tuple[str, ...] = C.COHORT_VIEWS["all_cases"]
) -> dict:
    """Claims are the structured findings of answered packets. A finding is
    supported when all its evidence ids are in the packet, each resolves, and each
    calculation id it names exists and validates. Reports unsupported / emitted
    (rate None when no claims were emitted)."""
    a = _answered(rows, groups)
    emitted = sum(r.get("claims", 0) for r in a)
    supported = sum(r.get("claims_supported", 0) for r in a)
    return {
        "unsupported_over_emitted": frac(emitted - supported, emitted),
        "supported_over_emitted": frac(supported, emitted),
    }


def monetary_correctness(
    rows: list[dict], groups: tuple[str, ...] = C.COHORT_VIEWS["all_cases"]
) -> dict:
    """Calculations that recompute exactly from snapshot-bound operand values /
    calculations reported, and calculation coverage = answered cases with at
    least one calculation / answered cases (answered cohort cases)."""
    a = _answered(rows, groups)
    return {
        "exact_over_reported": frac(
            sum(r.get("calcs_exact", 0) for r in a), sum(r.get("calcs", 0) for r in a)
        ),
        "calculation_coverage": frac(sum(r.get("calcs", 0) > 0 for r in a), len(a)),
    }


def execution_reliability(rows: list[dict]) -> dict:
    """Over all attempted cases: failed; abstained because retrieval hit the time
    limit; abstained because it hit the row limit."""
    n = len(rows)

    def abstained_for(reason: str) -> int:
        return sum(
            r["disposition"] == "abstained" and r["reason"] == reason for r in rows
        )

    return {
        "failed": frac(sum(r["disposition"] == "failed" for r in rows), n),
        "abstained_retrieval_time_limit": frac(
            abstained_for("retrieval_time_limit"), n
        ),
        "abstained_retrieval_row_limit": frac(abstained_for("retrieval_row_limit"), n),
    }


def false_alarms_by(rows: list[dict]) -> dict:
    """False alarms among legitimate cases in `rows`, by legitimate variant
    (denominator: legitimate cases of that variant) and by predicted family
    (denominator: all legitimate cases)."""
    legit = [r for r in rows if r["failure_type"] == "NO_FAILURE"]
    fa = [r for r in legit if r.get("kind") == "false_alarm"]
    return {
        "by_variant": {
            v: frac(
                sum(r["variant"] == v for r in fa),
                sum(r["variant"] == v for r in legit),
            )
            for v in sorted({r["variant"] for r in legit})
        },
        "by_predicted_family": {
            c: frac(sum(r["category"] == c for r in fa), len(legit))
            for c in sorted({r["category"] for r in fa})
        },
    }


def nearest_rank(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile: the value at 1-based rank ceil(p/100 * n) of the
    sorted values. None for no values."""
    if not values:
        return None
    s = sorted(values)
    return s[max(math.ceil(p / 100 * len(s)), 1) - 1]


def efficiency(
    seconds: list[float],
    peak_rss_kib: int | None,
    cost_per_case: Decimal,
    correct_complete: int = 0,
) -> dict[str, Any]:
    """Latency, throughput, memory and cost over attempted cases. `seconds` has
    one entry per attempted case (failures included). p50/p95 use nearest rank.
    Throughput = cases / total seconds. `cost_per_case` is the mean billed cost
    of an attempted case; cost per correct evidence-complete packet is total cost
    / `correct_complete`. None where the denominator is zero."""
    n = len(seconds)
    total = sum(seconds)
    cost_total = cost_per_case * n
    return {
        "attempted": n,
        "latency_p50_s": nearest_rank(seconds, 50),
        "latency_p95_s": nearest_rank(seconds, 95),
        "throughput_cases_per_s": (n / total) if total > 0 else None,
        "peak_rss_kib": peak_rss_kib,
        "cost_per_attempted_case": cost_per_case,
        "cost_per_correct_complete_packet": (
            cost_total / correct_complete if correct_complete else None
        ),
    }


def precision_coverage_curve(
    pairs: list[tuple[float, bool]], thresholds: Sequence[float]
) -> list[dict[str, Any]]:
    """For each threshold, keep the (confidence, correct) pairs with confidence
    >= threshold: coverage = kept / all, precision = correct kept / kept (rate
    None when nothing is kept)."""
    out = []
    for th in thresholds:
        kept = [ok for conf, ok in pairs if conf >= th]
        out.append(
            {
                "threshold": th,
                "coverage": frac(len(kept), len(pairs)),
                "precision": frac(sum(kept), len(kept)),
            }
        )
    return out
