"""Cohort, groups, label conflicts and category mapping, shared by every system
that is scored on the FinRCA development splits (docs/evidence-contract.md
section 8). `evaluation/dev_run.py` (investigator) and
`evaluation/baselines/observable/run.py` (classifier) both import this module,
so the two can never score different cohorts.

Evaluator side: it reads labels passed in by the caller and a token key, so it
must never be imported by src/reconcile/. Standard library plus the token
helper of the preparation adapters.

Groups of a request, from its label:

    cohort          gradable, in scope, scored in both views
    label_conflict  a legitimate case refuted by a failure case (see below);
                    scored only in the view "with label conflicts"
    fx              legitimate FX settlement (correct outcome: abstention)
    contradicted    records refute the label (CONTRADICTED)
    not_gradable    evidence unusable (NOT_GRADABLE)
    oos_failure     failure family the investigator does not check
    oos_legit       legitimate variant outside the payment contract

Label conflicts. The headline view scores EVERY gradable in-scope case, with no
exclusion. A second, sensitivity view leaves out only the label conflicts that
were verified by hand and are recorded with their evidence in the tracked file
`evaluation/verified_label_conflicts.json` (5 pairs on train and validation). A
rule over the labels never excludes a case; it only proposes:

- *Conflict candidate*: a legitimate-labelled cohort case C and a failure case D
  of a checked family such that a record that is where D's defect is lies in C's
  own records. "Where D's defect is": D's primary entity or a payment or invoice
  named in D's upstream evidence IDs (whether that record is really defective
  has to be checked by hand). "C's own records": the payments and invoices C
  names (primary entity, affected entities, evidence IDs) plus, for each payment,
  the invoices it is allocated to and, for each invoice, its allocating
  payments.
- *Review candidate*: any other pair whose own-record sets intersect, i.e. a
  payment or invoice that both cases name or reach through allocations (on
  either or both sides: C names payment B, D names payment A, both allocated to
  invoice I). Review candidates are reported only.

`conflict_report` also checks that every verified pair is among the conflict
candidates, so the file cannot drift from the labels.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import NamedTuple

from reconcile.adapters.finrca import _Tokens

FAMILY_KEYS = {
    "F06_INVOICE_PAID_TWICE": "F06",
    "F12_ERP_PAYMENT_MISSING_FROM_BANK": "F12",
    "F14_BANK_ERP_AMOUNT_MISMATCH": "F14",
}
# Ten payment-related legitimate variants (docs/data-audit.md section 2) and the
# category each is scored against. FX has no answered category: the correct
# outcome is an abstention.
VARIANT_CATEGORY: dict[str, str | None] = {
    "legitimate_pending_settlement": "legitimate_pending_settlement",
    "legitimate_weekend_clearing": "legitimate_weekend_clearing",
    "legitimate_check_clearing": "legitimate_check_clearing",
    "legitimate_due_date_payment": "legitimate_due_date_payment",
    "legitimate_split_payment": "legitimate_split_payment",
    "legitimate_batch_payment": "legitimate_batch_payment",
    "legitimate_payment_reversal": "legitimate_payment_reversal",
    "legitimate_net_fee_accounting": "legitimate_net_fee_accounting",
    "legitimate_accounted_bank_fee": "legitimate_accounted_bank_fee",
    "legitimate_fx_settlement": None,
}
# docs/evidence-contract.md section 8: the 4 split cases whose records refute
# the label, and the F06 case whose duplicate debit posts after the cutoff.
CONTRADICTED = frozenset({"RCA_001631", "RCA_001791", "RCA_002223", "RCA_002127"})
NOT_GRADABLE = frozenset({"RCA_000588"})

# The two views of the cohort. Every system reports both; the first is the headline.
COHORT_VIEWS: dict[str, tuple[str, ...]] = {
    "all_cases": ("cohort", "label_conflict"),
    "sensitivity_without_verified_conflicts": ("cohort",),
}
COHORT_GROUPS = COHORT_VIEWS["all_cases"]  # every in-scope gradable case
VERIFIED_FILE = Path(__file__).with_name("verified_label_conflicts.json")

# Failure families whose defect the investigator checks (the "Checked" list of
# SCOPE_LIMITATION in src/reconcile/diagnosis/policy.py): F05 allocation to an
# invoice, F06 duplicates and invoice totals, F08 approval completion, F09
# payment journal equal to the payment, F12 and F14 settlement, bank match and
# amounts, F15 payment-bank match. F10 (accounting period) is listed there as
# not checked, so a shared F10 record hides no wrong answer.
CHECKED_FAMILIES = (
    "F05_PAYMENT_WITHOUT_VALID_INVOICE",
    "F06_INVOICE_PAID_TWICE",
    "F08_APPROVAL_WORKFLOW_FAILURE",
    "F09_GL_POSTING_MISMATCH",
    "F12_ERP_PAYMENT_MISSING_FROM_BANK",
    "F14_BANK_ERP_AMOUNT_MISMATCH",
    "F15_INCORRECT_PAYMENT_BANK_MATCH",
)


def is_no_failure(category: str) -> bool:
    return category == "no_exception" or category.startswith("legitimate")


def is_failure(category: str) -> bool:
    return not is_no_failure(category)


def expected_category(failure_type: str, variant: str) -> str | None:
    return FAMILY_KEYS.get(failure_type) or VARIANT_CATEGORY.get(variant)


def label_key(failure_type: str, variant: str) -> str:
    """Per-label reporting key: the failure family or the legitimate variant."""
    return failure_type if failure_type != "NO_FAILURE" else variant


def group_of(
    failure_type: str,
    variant: str,
    case_id: str,
    verified: frozenset[str] | set[str] | dict[str, object] = frozenset(),
) -> str:
    """The group of one case (module docstring). `verified` holds the case IDs of
    the hand-verified label conflicts; they move from "cohort" to "label_conflict"
    (still scored in the headline view)."""
    in_cohort = failure_type in FAMILY_KEYS or (
        failure_type == "NO_FAILURE" and variant in VARIANT_CATEGORY
    )
    if not in_cohort:
        return "oos_legit" if failure_type == "NO_FAILURE" else "oos_failure"
    if case_id in CONTRADICTED:
        return "contradicted"
    if case_id in NOT_GRADABLE:
        return "not_gradable"
    if variant == "legitimate_fx_settlement":
        return "fx"
    return "label_conflict" if case_id in verified else "cohort"


Entries = dict[str, list[dict[str, object]]]


class Conflicts(NamedTuple):
    """Each mapping is legitimate case -> list of `{failure_case, failure_type,
    shared, ...}`. `verified` (from the tracked file) leaves the sensitivity view;
    `candidates` and `review` are proposals and exclude nothing."""

    verified: Entries
    candidates: Entries
    review: Entries


def verified_conflicts(path: Path = VERIFIED_FILE) -> Entries:
    out: Entries = {}
    for v in json.loads(path.read_text())["verified"]:
        out.setdefault(v["legitimate_case"], []).append(
            {
                "failure_case": v["failure_case"],
                "failure_type": v["failure_type"],
                "shared": [v["shared_record"]],
                "defect": v["defect"],
            }
        )
    return out


def label_candidates(
    labels: dict[str, dict], con: sqlite3.Connection, key: Path
) -> tuple[Entries, Entries]:
    """(conflict candidates, review candidates) from the rule of the module
    docstring, over the pooled train and validation labels. `con` is the prepared
    snapshot (allocations, by token), `key` the run's token key."""
    tokens = _Tokens(bytes.fromhex(key.read_text().strip()))
    raw_of: dict[str, str] = {}

    def tok(raw: str) -> str:
        t = tokens.token("payment" if raw.startswith("PAY_") else "invoice", raw)
        raw_of[t] = raw
        return t

    def tokens_of(ids: set[str]) -> set[str]:
        return {tok(i) for i in ids if i.startswith(("PAY_", "INV_"))}

    def named(lab: dict) -> set[str]:
        ids = {lab["primary_entity"]["id"], *lab["evidence_ids"]}
        ids |= {e["id"] for e in lab["affected_entities"]}
        return tokens_of(ids)

    def defect(lab: dict) -> set[str]:
        return tokens_of({lab["primary_entity"]["id"], *lab["evidence_ids"]})

    def records(named_set: set[str]) -> set[str]:
        out = set(named_set)
        for t in named_set:
            for a in con.execute(
                "SELECT payment_id, invoice_id FROM payment_allocations"
                " WHERE payment_id = ? OR invoice_id = ?",
                (t, t),
            ):
                out |= {a[0], a[1]}
        return out

    for lab in labels.values():  # so that reached-only records print as raw IDs
        named(lab)
    recs = {c: records(named(lab)) for c, lab in labels.items()}
    defects = {c: defect(lab) for c, lab in labels.items()}

    def entry(d: str, dl: dict, shared: set[str]) -> dict[str, object]:
        return {
            "failure_case": d,
            "failure_type": dl["failure_type"],
            "shared": sorted(raw_of.get(t, t) for t in shared),
        }

    candidates: Entries = {}
    review: Entries = {}
    for c, lab in labels.items():
        if lab["failure_type"] != "NO_FAILURE":
            continue
        for d, dl in sorted(labels.items()):
            if dl["failure_type"] not in CHECKED_FAMILIES:
                continue
            hit = defects[d] & recs[c]
            if hit:
                candidates.setdefault(c, []).append(entry(d, dl, hit))
            elif both := recs[c] & recs[d]:
                review.setdefault(c, []).append(entry(d, dl, both))
    return candidates, review


def conflict_report(
    labels: dict[str, dict], con: sqlite3.Connection, key: Path
) -> Conflicts:
    """Verified conflicts plus the rule's proposals; fails if a verified pair is
    not among the candidates (the file and the labels disagree)."""
    candidates, review = label_candidates(labels, con, key)
    verified = verified_conflicts()
    for c, entries in verified.items():
        found = {e["failure_case"] for e in candidates.get(c, [])}
        wanted = {e["failure_case"] for e in entries}
        if c not in labels or not wanted <= found:
            raise ValueError(
                f"verified conflict {c} {sorted(wanted)} is not a candidate"
                f" ({sorted(found)}): {VERIFIED_FILE.name} and the labels disagree"
            )
    return Conflicts(verified, candidates, review)
