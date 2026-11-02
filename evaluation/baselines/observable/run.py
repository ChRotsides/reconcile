"""Classical-ML baseline on the investigator's observable inputs (PLAN.md M2).

    uv run --group eval python evaluation/baselines/observable/run.py TAG

Evaluator side. It prepares its own key, snapshot and requests (same adapters
as `evaluation/dev_run.py`), extracts features from the snapshot and requests
only (`features.py`), and opens labels only to train (train split) and to score
(validation split). Never names test or challenge_test. See
docs/baseline-observable.md.

Cohort, groups, label conflicts and category mapping come from the shared
`evaluation/cohort.py`, the same module `evaluation/dev_run.py` uses, so both
systems are scored on identical cohorts, in two views: the headline (all
cases) and a sensitivity view without the hand-verified label conflicts. The
metric definitions (outcome kinds, false-alarm and precision, all-clear risk)
are replicated from `dev_run.py` (`classify`, `cohort_metrics`,
`all_clear_risk`).
"""

import argparse
import json
import resource
import sqlite3
import sys
import time
import tomllib
import warnings
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score
from sklearn.model_selection import GroupKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cohort as C  # noqa: E402  (shared with evaluation/dev_run.py)
import evidence  # noqa: E402
import features as F  # noqa: E402
import metrics as M  # noqa: E402  (shared metric definitions, PLAN.md section 7)

from reconcile.adapters.finrca import create_key_file, prepare_finrca_snapshot  # noqa: E402
from reconcile.adapters.finrca_requests import prepare_finrca_requests  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
DEFAULT_UPSTREAM = Path(
    "/home/charalambos/Documents/fin-tech-projects/reconcile/data/upstream/FinRCA-AI-Bench"
)
SOURCE_REVISION = "76c93ec"
CUTOFF = date(2026, 6, 30)
SPLITS = ("train", "validation")  # the only splits this script ever names
SEED = 20261101
POLICY_VERSION = (
    "baseline-observable-0.1"  # recorded in the requests; not an investigator policy
)

is_no_failure = C.is_no_failure
is_failure = C.is_failure
VIEWS = C.COHORT_VIEWS


def fine_label(lab: dict[str, Any]) -> str:
    """Training target: the failure family, or the legitimate variant."""
    return (
        lab["scenario_variant"]
        if lab["failure_type"] == "NO_FAILURE"
        else lab["failure_type"]
    )


def category_of(label: str) -> str:
    """Predicted fine label -> the investigator's category name."""
    return C.FAMILY_KEYS.get(label, label)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ------------------------------------------------------------------ prepare


def prepare(tag: str, upstream: Path) -> tuple[Path, Path]:
    root = REPO / "artifacts/baselines" / tag
    private = REPO / "data/private" / tag
    if root.exists() or private.exists():
        sys.exit(f"tag {tag!r} already exists; choose a new tag")
    private.mkdir(parents=True)
    key = private / "key"
    create_key_file(key)
    snap_dir = root / "prepared/snapshot"
    prepare_finrca_snapshot(
        upstream / "data/benchmark/full",
        snap_dir,
        key_path=key,
        source_revision=SOURCE_REVISION,
        cutoff=CUTOFF,
    )
    for split in SPLITS:
        prepare_finrca_requests(
            upstream / "data/benchmark",
            split,
            snapshot_dir=snap_dir,
            key_path=key,
            inference_dir=root / "prepared/requests" / split,
            evaluator_dir=root / "evaluator" / split,
            policy_version=POLICY_VERSION,
        )
    return root, snap_dir


# ------------------------------------------------------------------ features


def featurize(
    root: Path, snap: F.Snapshot, split: str
) -> tuple[list[str], list[tuple[str, str]], list[dict[str, float]], list[str]]:
    """Per request: (case_id, (table, record_id), features, vendor group), in
    case_id order. Reads the requests file and the snapshot; the evaluator-side
    links file only maps request_id to case_id (no label). Tokens, request ids
    and so their order depend on the HMAC key, so sample order and group names
    come from case ids, never from tokens: same inputs, same fit, any key."""
    links = {
        r["request_id"]: r["case_id"]
        for r in read_jsonl(root / "evaluator" / split / "links.jsonl")
    }
    reqs = sorted(
        read_jsonl(root / "prepared/requests" / split / "requests.jsonl"),
        key=lambda r: links[r["request_id"]],
    )
    ids, ents, xs, raw_groups = [], [], [], []
    for r in reqs:
        table, rid = r["primary_entity"]["table"], r["primary_entity"]["record_id"]
        ids.append(links[r["request_id"]])
        ents.append((table, rid))
        xs.append(F.extract(snap, table, rid))
        raw_groups.append(
            F.vendor_of(F.resolve(snap, table, rid)) if table != "gl_entries" else rid
        )
    # GroupKFold breaks ties by group name, so name each group by its first case
    first: dict[str, str] = {}
    groups = [first.setdefault(g, c) for g, c in zip(raw_groups, ids, strict=True)]
    return ids, ents, xs, groups


def matrix(xs: list[dict[str, float]], names: list[str]) -> np.ndarray:
    return np.array([[x.get(n, float("nan")) for n in names] for x in xs], dtype=float)


# ------------------------------------------------------------------ models


def candidates() -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for lr in (0.05, 0.1):
        for depth in (3, None):
            for l2 in (0.0, 1.0):
                out.append(
                    (
                        f"hgb lr={lr} depth={depth} l2={l2}",
                        HistGradientBoostingClassifier(
                            learning_rate=lr, max_depth=depth, l2_regularization=l2,
                            max_iter=150, early_stopping=False, random_state=SEED,
                        ),
                    )
                )  # fmt: skip
    for c in (0.1, 1.0, 10.0):
        out.append(
            (
                f"logreg C={c}",
                make_pipeline(
                    SimpleImputer(strategy="median", add_indicator=True),
                    StandardScaler(),
                    LogisticRegression(C=c, max_iter=3000, random_state=SEED),
                ),
            )
        )
    for depth in (None, 12):
        for leaf in (1, 3):
            out.append(
                (
                    f"random_forest depth={depth} min_leaf={leaf}",
                    make_pipeline(
                        SimpleImputer(strategy="median", add_indicator=True),
                        RandomForestClassifier(
                            n_estimators=300, max_depth=depth, min_samples_leaf=leaf,
                            n_jobs=1, random_state=SEED,
                        ),
                    ),
                )
            )  # fmt: skip
    return out


def clone_model(model: Any) -> Any:
    from sklearn.base import clone

    return clone(model)


# ------------------------------------------------------------------ scoring


def build_rows(
    case_ids: list[str],
    ents: list[tuple[str, str]],
    pred: list[str],
    conf: list[float],
    snap: F.Snapshot,
    labels: dict[str, dict[str, Any]],
    conflicts: C.Conflicts,
) -> list[dict[str, Any]]:
    rows = []
    for case_id, (table, rid), p, c in zip(case_ids, ents, pred, conf, strict=True):
        lab = labels[case_id]
        group = C.group_of(
            lab["failure_type"], lab["scenario_variant"], case_id, conflicts.verified
        )
        cat = category_of(p)
        reqs = evidence.retrieve(snap, cat, table, rid)
        row = {
            "split": "validation",
            "case_id": case_id,
            "failure_type": lab["failure_type"],
            "variant": lab["scenario_variant"],
            "difficulty": lab.get("difficulty"),
            "group_vendor_id": lab.get("group_vendor_id"),
            "group": group,
            "entity_table": table,
            "predicted": p,
            "category": cat,
            "confidence": round(float(c), 4),
            "expected_category": C.expected_category(
                lab["failure_type"], lab["scenario_variant"]
            ),
            "label_conflict_with": conflicts.verified.get(case_id, []),
            "conflict_candidate_with": conflicts.candidates.get(case_id, []),
            "review_candidate_with": conflicts.review.get(case_id, []),
            "contract_defined": bool(reqs),
            "complete": bool(reqs) and all(ok for _, ok in reqs),
            "missing": [n for n, ok in reqs if not ok],
        }
        label_fail = row["failure_type"] != "NO_FAILURE"
        row["correct"] = cat == row["expected_category"]
        if not label_fail and is_failure(cat):
            row["kind"] = "false_alarm"
        elif label_fail and is_no_failure(cat):
            row["kind"] = "failure_called_no_failure"
        elif not row["correct"]:
            row["kind"] = "wrong_failure_class" if label_fail else "wrong_legit_variant"
        rows.append(row)
    return rows


def as_scored(rows: list[dict[str, Any]], answered_fn: Any) -> list[dict[str, Any]]:
    """Rows in the shape the shared metrics (evaluation/metrics.py) read: the
    classifier answers a case or, by `answered_fn`, abstains from it."""
    out = []
    for r in rows:
        a = bool(answered_fn(r))
        out.append(
            r
            | {
                "disposition": "answered" if a else "abstained",
                "reason": None,
                "searches_mismatch": [],
                "outcome": ("correct" if r["correct"] else "wrong")
                if a
                else "abstained",
                "kind": r.get("kind") if a else None,
            }
        )
    return out


def protocol_threshold(protocol_path: Path) -> float:
    """The abstention threshold the protocol froze, parsed from the name of its
    `threshold_<x>` baseline configuration so the number has one source."""
    names = tomllib.loads(protocol_path.read_text())["systems"][
        "baseline_configurations"
    ]
    found = [
        float(n.removeprefix("threshold_")) for n in names if n.startswith("threshold_")
    ]
    if len(found) != 1:
        raise SystemExit(
            f"{protocol_path}: expected one threshold_<x> configuration, got {names}"
        )
    return found[0]


def check_threshold(chosen: float, frozen: float) -> None:
    """The protocol froze `frozen` because the train-CV rule chose it; if the rule
    now chooses something else the frozen comparison no longer follows the rule."""
    if chosen != frozen:
        raise SystemExit(
            f"CV-chosen abstention threshold {chosen} differs from the protocol's "
            f"frozen {frozen}; the protocol needs a new version, not a silent change"
        )


def baseline_metrics(
    rows: list[dict[str, Any]],
    answered_fn: Any = lambda r: True,
    groups: tuple[str, ...] = VIEWS["all_cases"],
) -> dict[str, Any]:
    """The shared definitions of evaluation/metrics.py applied to the baseline's
    rows, with `answered_fn` deciding abstention (default: the classifier always
    answers) and `groups` the view."""
    sr = as_scored(rows, answered_fn)
    cm = M.cohort_metrics(sr, groups)
    c = [r for r in sr if r["group"] in groups]
    legit = [r for r in c if r["failure_type"] == "NO_FAILURE"]
    legit_all = [r for r in sr if r["failure_type"] == "NO_FAILURE"]
    ac = M.all_clear_risk(sr)

    def fa(r: dict[str, Any]) -> bool:
        return r["disposition"] == "answered" and r.get("kind") == "false_alarm"

    def fa_narrow(r: dict[str, Any]) -> bool:
        return r["disposition"] == "answered" and r["category"] in (
            "F06",
            "F12",
            "F14",
        )

    return {
        "cohort_cases": cm["cases"],
        "answered": cm["answered"],
        "answer_precision_exact": cm["answer_precision"],
        "failure_vs_no_failure_accuracy_answered": cm[
            "failure_vs_no_failure_accuracy_answered"
        ],
        "evidence_complete_among_answered": cm["evidence_complete_among_answered"],
        "evidence_complete_among_correct": cm["evidence_complete_among_correct"],
        "correct_and_evidence_complete_over_cohort": cm[
            "correct_and_evidence_complete"
        ],
        "correct_over_cohort": M.frac(
            sum(r["outcome"] == "correct" for r in c), len(c)
        ),
        "false_alarms_cohort_legit": cm["false_alarms"],
        "false_alarms_cohort_legit_F06_F12_F14_only": M.frac(
            sum(map(fa_narrow, legit)), len(legit)
        ),
        "false_alarms_all_legit": M.frac(
            sum(
                fa(r)
                for r in legit_all
                if r["group"] in (*C.COHORT_GROUPS, "fx", "contradicted", "oos_legit")
            ),
            len(legit_all),
        ),
        "false_alarms_ap_side_legit": M.frac(
            sum(fa(r) for r in legit_all if r["group"] == "oos_legit"),
            sum(r["group"] == "oos_legit" for r in legit_all),
        ),
        "all_clear_on_out_of_scope_failures": ac["overall"],
        "all_clear_per_family": ac["per_family"],
        "wrong_by_kind": cm["wrong_by_kind"],
    }


def both_views(
    rows: list[dict[str, Any]], answered_fn: Any = lambda r: True
) -> dict[str, Any]:
    """The metrics in both cohort views, from the shared definitions."""
    return {name: baseline_metrics(rows, answered_fn, g) for name, g in VIEWS.items()}


def fx_slice(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fx = [r for r in rows if r["group"] == "fx"]
    return {
        "cases": len(fx),
        "predicted": dict(Counter(r["predicted"] for r in fx)),
        "called_failure": sum(is_failure(r["category"]) for r in fx),
    }


def baseline_per_label(rows: list[dict[str, Any]]) -> dict[str, Any]:
    shared = M.per_label(as_scored(rows, lambda r: True), C.COHORT_GROUPS)
    return {
        k: {
            "cases": v["cases"],
            "correct": v["correct"],
            "complete_correct": v["complete_correct"],
            "predicted": v["answered_categories"],
        }
        for k, v in shared.items()
    }


def investigator_rows(tag: str, conflicts: C.Conflicts) -> list[dict[str, Any]]:
    """Validation rows of the investigator's evaluator-side cases.jsonl, with
    the group recomputed by the shared `cohort.group_of` and the same conflict
    set as the baseline's. Stops if the file was made with a different set."""
    rows = [
        r
        for r in read_jsonl(REPO / "artifacts/dev-runs" / tag / "cases.jsonl")
        if r["split"] == "validation"
    ]
    recorded = {r["case_id"] for r in rows if r["group"] == "label_conflict"}
    ours = {
        r["case_id"]
        for r in rows
        if r["group"] in C.COHORT_GROUPS and r["case_id"] in conflicts.verified
    }
    if recorded != ours:
        sys.exit(
            f"{tag}/cases.jsonl flags label conflicts {sorted(recorded)}, the shared"
            f" rule flags {sorted(ours)}: re-run `dev_run.py evaluate {tag}`"
        )
    for r in rows:
        r["group"] = C.group_of(
            r["failure_type"], r["variant"], r["case_id"], conflicts.verified
        )
        ans = r["disposition"] == "answered"
        r["answered"] = ans
        r["category"] = r["category"] or ""
        r["correct"] = ans and r["category"] == r["expected_category"]
        r["complete"] = bool(r.get("complete")) and ans
        r["kind"] = r.get("kind")
    return rows


def investigator_validation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return both_views(rows, lambda r: r["answered"])


# ------------------------------------------------------------------ main


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    ap.add_argument(
        "--investigator-run",
        required=True,
        help="tag of the investigator run to compare with (artifacts/dev-runs/TAG)",
    )
    ap.add_argument("--cv-precision-target", type=float, default=0.99)
    args = ap.parse_args()
    warnings.filterwarnings("ignore")
    t0 = time.monotonic()
    root, snap_dir = prepare(args.tag, args.upstream)
    snap = F.Snapshot(snap_dir / "snapshot.sqlite", CUTOFF)

    # 1. features: snapshot and requests only (no label file open yet)
    data = {s: featurize(root, snap, s) for s in SPLITS}
    names = F.feature_names(data["train"][2][0])
    Xtr, Xva = (matrix(data[s][2], names) for s in SPLITS)

    # 2. labels: opened only now, after every feature exists
    def labels_for(split: str) -> list[dict[str, Any]]:
        by_case = {
            r["case_id"]: r
            for r in read_jsonl(
                args.upstream / "data/benchmark" / split / "rca_ground_truth.jsonl"
            )
        }
        return [by_case[c] | {"case_id": c} for c in data[split][0]]

    ltr, lva = labels_for("train"), labels_for("validation")
    ytr = np.array([fine_label(r) for r in ltr])
    yva = np.array([fine_label(r) for r in lva])
    lab_va = {r["case_id"]: r for r in lva}
    con = sqlite3.connect(
        (snap_dir / "snapshot.sqlite").resolve().as_uri() + "?mode=ro", uri=True
    )
    conflicts = C.conflict_report(
        {r["case_id"]: r for r in (*ltr, *lva)},
        con,
        REPO / "data/private" / args.tag / "key",
    )
    con.close()
    case_va = [r["case_id"] for r in lva]
    ent_va = data["validation"][1]

    def score_rows(model_pred: list[str], conf: list[float]) -> list[dict[str, Any]]:
        return build_rows(case_va, ent_va, model_pred, conf, snap, lab_va, conflicts)

    def cohort_acc(pred: np.ndarray) -> tuple[int, int]:
        rows = score_rows(list(pred), [1.0] * len(pred))
        # model selection keeps the pre-conflict definition: all in-scope cases
        m = baseline_metrics(rows, groups=C.COHORT_GROUPS)
        return m["answer_precision_exact"]["n"], m["answer_precision_exact"]["d"]

    # 3. model selection on validation: exact cohort accuracy, first best wins
    grid = []
    best: tuple[int, str, Any] | None = None
    for name, model in candidates():
        m = clone_model(model).fit(Xtr, ytr)
        n, d = cohort_acc(m.predict(Xva))
        ba = balanced_accuracy_score(yva, m.predict(Xva))
        grid.append(
            {
                "model": name,
                "validation_cohort_correct": n,
                "cohort": d,
                "validation_all_balanced_accuracy": round(ba, 4),
            }
        )
        if best is None or n > best[0]:
            best = (n, name, model)
    assert best is not None
    _, best_name, best_model = best
    t_fit = time.monotonic()
    model = clone_model(best_model).fit(Xtr, ytr)
    fit_seconds = time.monotonic() - t_fit
    classes = list(model.classes_)
    t_pred = time.monotonic()
    proba = model.predict_proba(Xva)
    batch_predict_seconds = time.monotonic() - t_pred
    # per-case latency: one single-row predict_proba call per validation case
    # (results above come from the batch call; these timings only measure)
    per_case_seconds = []
    for i in range(len(Xva)):
        t = time.monotonic()
        model.predict_proba(Xva[i : i + 1])
        per_case_seconds.append(time.monotonic() - t)
    pred = [classes[i] for i in proba.argmax(1)]
    conf = list(proba.max(1))
    rows = score_rows(pred, conf)
    primary = both_views(rows)

    # 4. optional abstention threshold chosen on TRAIN by grouped cross-validation
    cv_pred_proba = cross_val_predict(
        clone_model(best_model), Xtr, ytr, groups=np.array(data["train"][3]),
        cv=GroupKFold(n_splits=5), method="predict_proba",
    )  # fmt: skip
    cv_classes = sorted(set(ytr))
    cvp = [cv_classes[i] for i in cv_pred_proba.argmax(1)]
    cvc = cv_pred_proba.max(1)
    cohort_tr = [
        (fine_label(r), p, c)
        for r, p, c in zip(ltr, cvp, cvc, strict=True)
        if C.group_of(
            r["failure_type"], r["scenario_variant"], r["case_id"], conflicts.verified
        )
        in C.COHORT_GROUPS
    ]
    thr_grid = [round(0.5 + 0.05 * k, 2) for k in range(10)]
    curve = M.precision_coverage_curve(
        [(c, category_of(a) == category_of(b)) for a, b, c in cohort_tr], thr_grid
    )
    chosen = None
    cv_table = []
    for pt in curve:
        kept, prec = pt["precision"]["d"], pt["precision"]["n"]
        cv_table.append(
            {
                "threshold": pt["threshold"],
                "cv_cohort_answered": kept,
                "cv_cohort": pt["coverage"]["d"],
                "cv_precision": round(prec / kept, 4) if kept else float("nan"),
            }
        )
        if chosen is None and kept and prec / kept >= args.cv_precision_target:
            chosen = pt["threshold"]
    threshold = chosen if chosen is not None else thr_grid[-1]
    frozen_threshold = protocol_threshold(REPO / "evaluation/protocol.toml")
    check_threshold(threshold, frozen_threshold)
    with_abstention = both_views(rows, lambda r: r["confidence"] >= threshold)

    # 5. reliance: permutation importance on validation, single and by family
    pi = permutation_importance(
        model, Xva, yva, n_repeats=10, random_state=SEED, scoring="accuracy"
    )
    order = np.argsort(-pi.importances_mean)
    top = [
        {
            "feature": names[i],
            "drop": round(float(pi.importances_mean[i]), 4),
            "sd": round(float(pi.importances_std[i]), 4),
        }
        for i in order[:20]
    ]

    def cohort_correct_with(Xv: np.ndarray, mdl: Any = model) -> int:
        return cohort_acc(mdl.predict(Xv))[0]

    base_correct = cohort_acc(model.predict(Xva))[0]
    rng = np.random.default_rng(SEED)

    def group_drop(cols: list[int], reps: int = 20) -> float:
        drops = []
        for _ in range(reps):
            Xp = Xva.copy()
            idx = rng.permutation(len(Xp))
            Xp[:, cols] = Xp[idx][:, cols]
            drops.append(base_correct - cohort_correct_with(Xp))
        return float(np.mean(drops))

    families = {**{k: v for k, v in F.RESIDUAL_GROUPS.items()}}
    families |= {
        "payment block (p_*)": ("p_",),
        "invoice block (i_*)": ("i_",),
        "bank-row block (b_*)": ("b_",),
        "GL block (g_*)": ("g_",),
    }
    fam_cols = {
        k: [names.index(n) for n in F.group_columns(names, v)]
        for k, v in families.items()
    }
    group_perm = {
        k: round(group_drop(c) / base_correct, 4) if c else None
        for k, c in fam_cols.items()
    }

    # 6. residual ablation: retrain the selected configuration without a residual group
    ablation = {}
    resid_all: list[str] = []
    for k, prefixes in F.RESIDUAL_GROUPS.items():
        drop = F.group_columns(names, prefixes)
        resid_all += drop
        keep = [i for i, n in enumerate(names) if n not in drop]
        m = clone_model(best_model).fit(Xtr[:, keep], ytr)
        ablation[f"without {k}"] = {
            "columns_removed": drop,
            "cohort_correct": cohort_acc(m.predict(Xva[:, keep]))[0],
        }
    keep = [i for i, n in enumerate(names) if n not in set(resid_all)]
    m_nores = clone_model(best_model).fit(Xtr[:, keep], ytr)
    pr_nores = list(m_nores.predict(Xva[:, keep]))
    pw = m_nores.predict_proba(Xva[:, keep])
    rows_nores = score_rows(pr_nores, list(pw.max(1)))
    ablation["without all five residual groups"] = {
        "cohort_correct": cohort_acc(np.array(pr_nores))[0],
        "metrics": both_views(rows_nores),
    }

    # type-only (R3) and invoice-age-only (R4) baselines
    et = [i for i, n in enumerate(names) if n.startswith("e_")]
    m_type = HistGradientBoostingClassifier(
        max_depth=3, max_iter=100, random_state=SEED
    ).fit(Xtr[:, et], ytr)
    type_only = {"cohort_correct": cohort_acc(m_type.predict(Xva[:, et]))[0]}
    ia = names.index("i_age_days")
    inv_tr = [
        i
        for i, r in enumerate(ltr)
        if data["train"][1][i][0] == "invoices"
        and C.group_of(
            r["failure_type"], r["scenario_variant"], r["case_id"], conflicts.verified
        )
        in C.COHORT_GROUPS
    ]
    inv_va = [
        i
        for i, r in enumerate(lva)
        if ent_va[i][0] == "invoices"
        and C.group_of(
            r["failure_type"], r["scenario_variant"], r["case_id"], conflicts.verified
        )
        in C.COHORT_GROUPS
    ]
    age_model = HistGradientBoostingClassifier(
        max_depth=2, max_iter=50, random_state=SEED
    ).fit(
        Xtr[inv_tr][:, [ia]],
        np.array([ytr[i] == "F06_INVOICE_PAID_TWICE" for i in inv_tr]),
    )
    yv = np.array([yva[i] == "F06_INVOICE_PAID_TWICE" for i in inv_va])
    age_pred = age_model.predict(Xva[inv_va][:, [ia]])
    age_only = {
        "invoice_primary_validation_cases": len(inv_va),
        "balanced_accuracy_F06_vs_legit": round(
            float(balanced_accuracy_score(yv, age_pred)), 4
        ),
    }
    full_inv = balanced_accuracy_score(
        yv, [pred[i] == "F06_INVOICE_PAID_TWICE" for i in inv_va]
    )

    inv_rows = investigator_rows(args.investigator_run, conflicts)
    for view, groups in VIEWS.items():  # both systems, identical cohorts
        ours = {r["case_id"] for r in rows if r["group"] in groups}
        theirs = {r["case_id"] for r in inv_rows if r["group"] in groups}
        if ours != theirs:
            sys.exit(f"cohort view {view!r} differs between baseline and investigator")

    out = {
        "tag": args.tag,
        "seed": SEED,
        "cutoff": CUTOFF.isoformat(),
        "n_features": len(names),
        "features": names,
        "train_requests": len(ytr),
        "validation_requests": len(yva),
        "model_grid_on_validation": grid,
        "selected": best_name,
        "primary_no_abstention": primary,
        "fx_slice": fx_slice(rows),
        "abstention_threshold": {
            "rule": (
                f"smallest grid threshold with train grouped-CV cohort precision >= "
                f"{args.cv_precision_target}; else the largest"
            ),
            "chosen": threshold,
            "cv_table": cv_table,
            "validation_with_abstention": with_abstention,
        },
        "per_label_no_abstention": baseline_per_label(rows),
        "root_cause_accuracy_by_family": M.root_cause_accuracy_by(
            as_scored(rows, lambda r: True), "failure_type"
        ),
        "root_cause_accuracy_by_difficulty": M.root_cause_accuracy_by(
            as_scored(rows, lambda r: True), "difficulty"
        ),
        "false_alarms_by": M.false_alarms_by(
            [
                r
                for r in as_scored(rows, lambda r: True)
                if r["failure_type"] == "NO_FAILURE"
            ]
        ),
        "validation_precision_coverage_curve": M.precision_coverage_curve(
            [
                (r["confidence"], r["correct"])
                for r in rows
                if r["group"] in C.COHORT_GROUPS
            ],
            thr_grid,
        ),
        "permutation_importance_top20_exact_label_accuracy_drop": top,
        "feature_family_permutation_relative_cohort_drop": group_perm,
        "residual_ablation": ablation,
        "type_only_baseline": type_only,
        "invoice_age_only_baseline": age_only,
        "selected_model_invoice_stratum_balanced_accuracy": round(float(full_inv), 4),
        "investigator_validation": {
            "run": args.investigator_run,
            **investigator_validation(inv_rows),
        },
        "identical_cohorts_checked": True,
        "verified_label_conflicts": {
            c: v for c, v in conflicts.verified.items() if c in lab_va
        },
        "conflict_candidates": {
            c: v for c, v in conflicts.candidates.items() if c in lab_va
        },
        "review_candidates": {c: v for c, v in conflicts.review.items() if c in lab_va},
        "evidence_complete_meaning": (
            "relaxed record-availability upper bound, not a packet check "
            "(evidence.py docstring, docs/baseline-observable.md section 2)"
        ),
        "efficiency": {
            **M.efficiency(
                per_case_seconds,
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,  # KiB on Linux
                Decimal("0"),  # no paid calls
                primary["all_cases"]["correct_and_evidence_complete_over_cohort"]["n"],
            ),
            "latency_basis": (
                "per validation case: one single-row predict_proba call on the "
                "selected fitted model, features already extracted"
            ),
            "train_fit_seconds_selected_model": round(fit_seconds, 3),
            "validation_batch_predict_seconds": round(batch_predict_seconds, 4),
            "peak_rss_note": "whole script process, including grid search",
        },
        "seconds": round(time.monotonic() - t0, 1),
        "command": (
            "uv run --group eval python evaluation/baselines/observable/run.py "
            + args.tag
        ),
    }
    (root / "metrics.json").write_text(
        json.dumps(out, indent=2, sort_keys=True, default=str) + "\n"
    )
    with (root / "cases.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    with (root / f"cases_threshold_{frozen_threshold}.jsonl").open("w") as f:
        for r in as_scored(rows, lambda r: r["confidence"] >= frozen_threshold):
            f.write(json.dumps(r, sort_keys=True) + "\n")
    print(
        json.dumps(
            {k: out[k] for k in ("selected", "primary_no_abstention", "fx_slice")},
            indent=1,
            default=str,
        )
    )
    print("written", root / "metrics.json")


if __name__ == "__main__":
    main()
