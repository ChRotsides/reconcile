"""Classical-ML baseline on the investigator's observable inputs (PLAN.md M2).

    uv run --group eval python evaluation/baselines/observable/run.py TAG

Evaluator side. It prepares its own key, snapshot and requests (same adapters
as `evaluation/dev_run.py`), extracts features from the snapshot and requests
only (`features.py`), and opens labels only to train (train split) and to score
(validation split). Never names test or challenge_test. See
docs/baseline-observable.md.

Cohort, category mapping and metric definitions are replicated from
`evaluation/dev_run.py` (not imported): FAMILY_KEYS and VARIANT_CATEGORY lines
56-77, CONTRADICTED and NOT_GRADABLE 78-79, is_no_failure and is_failure
86-91, group_of 607-618, classify 621-680, all_clear_risk 862-879,
false-alarm definition in cohort_metrics 778-822.
"""

import argparse
import json
import sys
import time
import warnings
from collections import Counter
from datetime import date
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

import evidence  # noqa: E402
import features as F  # noqa: E402

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

# ---- replicated from evaluation/dev_run.py (lines 56-91)
FAMILY_KEYS = {
    "F06_INVOICE_PAID_TWICE": "F06",
    "F12_ERP_PAYMENT_MISSING_FROM_BANK": "F12",
    "F14_BANK_ERP_AMOUNT_MISMATCH": "F14",
}
VARIANT_CATEGORY = {
    "legitimate_pending_settlement": "legitimate_pending_settlement",
    "legitimate_weekend_clearing": "legitimate_weekend_clearing",
    "legitimate_check_clearing": "legitimate_check_clearing",
    "legitimate_due_date_payment": "legitimate_due_date_payment",
    "legitimate_split_payment": "legitimate_split_payment",
    "legitimate_batch_payment": "legitimate_batch_payment",
    "legitimate_payment_reversal": "legitimate_payment_reversal",
    "legitimate_net_fee_accounting": "legitimate_net_fee_accounting",
    "legitimate_accounted_bank_fee": "legitimate_accounted_bank_fee",
    "legitimate_fx_settlement": None,  # the investigator abstains here
}
CONTRADICTED = {"RCA_001631", "RCA_001791", "RCA_002223", "RCA_002127"}
NOT_GRADABLE = {"RCA_000588"}


def is_no_failure(category: str) -> bool:
    return category == "no_exception" or category.startswith("legitimate")


def is_failure(category: str) -> bool:
    return not is_no_failure(category)


def group_of(failure_type: str, variant: str, case_id: str) -> str:
    in_cohort = failure_type in FAMILY_KEYS or (
        failure_type == "NO_FAILURE" and variant in VARIANT_CATEGORY
    )
    if not in_cohort:
        return "oos_legit" if failure_type == "NO_FAILURE" else "oos_failure"
    if case_id in CONTRADICTED:
        return "contradicted"
    if case_id in NOT_GRADABLE:
        return "not_gradable"
    return "fx" if variant == "legitimate_fx_settlement" else "cohort"


def fine_label(lab: dict[str, Any]) -> str:
    """Training target: the failure family, or the legitimate variant."""
    return (
        lab["scenario_variant"]
        if lab["failure_type"] == "NO_FAILURE"
        else lab["failure_type"]
    )


def category_of(label: str) -> str:
    """Predicted fine label -> the investigator's category name."""
    return FAMILY_KEYS.get(label, label)


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
    """Per request: (request_id, (table, record_id), features, vendor group).
    Reads the requests file and the snapshot only."""
    reqs = sorted(
        read_jsonl(root / "prepared/requests" / split / "requests.jsonl"),
        key=lambda r: r["request_id"],
    )
    ids, ents, xs, groups = [], [], [], []
    for r in reqs:
        table, rid = r["primary_entity"]["table"], r["primary_entity"]["record_id"]
        ids.append(r["request_id"])
        ents.append((table, rid))
        xs.append(F.extract(snap, table, rid))
        groups.append(
            F.vendor_of(F.resolve(snap, table, rid)) if table != "gl_entries" else rid
        )
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
) -> list[dict[str, Any]]:
    rows = []
    for case_id, (table, rid), p, c in zip(case_ids, ents, pred, conf, strict=True):
        lab = labels[case_id]
        group = group_of(lab["failure_type"], lab["scenario_variant"], case_id)
        cat = category_of(p)
        reqs = evidence.retrieve(snap, cat, table, rid)
        row = {
            "case_id": case_id,
            "failure_type": lab["failure_type"],
            "variant": lab["scenario_variant"],
            "group": group,
            "entity_table": table,
            "predicted": p,
            "category": cat,
            "confidence": round(float(c), 4),
            "expected_category": FAMILY_KEYS.get(lab["failure_type"])
            or VARIANT_CATEGORY.get(lab["scenario_variant"]),
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


def frac(n: int, d: int) -> dict[str, Any]:
    return {"n": n, "d": d, "rate": round(n / d, 4) if d else None}


def metrics(
    rows: list[dict[str, Any]], answered_fn: Any = lambda r: True
) -> dict[str, Any]:
    """Same definitions as dev_run.cohort_metrics, with `answered_fn` deciding
    abstention (default: the classifier always answers)."""
    c = [r for r in rows if r["group"] == "cohort"]
    ans = [r for r in c if answered_fn(r)]
    cor = [r for r in ans if r["correct"]]
    comp = [r for r in cor if r["complete"]]
    legit = [r for r in c if r["failure_type"] == "NO_FAILURE"]
    legit_all = [r for r in rows if r["failure_type"] == "NO_FAILURE"]
    oos = [r for r in rows if r["group"] == "oos_failure"]
    coarse = [
        r
        for r in ans
        if is_failure(r["category"]) == (r["failure_type"] != "NO_FAILURE")
    ]

    def fa(r: dict[str, Any]) -> bool:
        return answered_fn(r) and r.get("kind") == "false_alarm"

    def fa_narrow(r: dict[str, Any]) -> bool:
        return answered_fn(r) and r["category"] in ("F06", "F12", "F14")

    return {
        "cohort_cases": len(c),
        "answered": frac(len(ans), len(c)),
        "answer_precision_exact": frac(len(cor), len(ans)),
        "failure_vs_no_failure_accuracy_answered": frac(len(coarse), len(ans)),
        "evidence_complete_among_answered": frac(
            sum(r["complete"] for r in ans), len(ans)
        ),
        "evidence_complete_among_correct": frac(len(comp), len(cor)),
        "correct_and_evidence_complete_over_cohort": frac(len(comp), len(c)),
        "correct_over_cohort": frac(len(cor), len(c)),
        "false_alarms_cohort_legit": frac(sum(map(fa, legit)), len(legit)),
        "false_alarms_cohort_legit_F06_F12_F14_only": frac(
            sum(map(fa_narrow, legit)), len(legit)
        ),
        "false_alarms_all_legit": frac(
            sum(
                fa(r)
                for r in legit_all
                if r["group"] in ("cohort", "fx", "contradicted", "oos_legit")
            ),
            len(legit_all),
        ),
        "false_alarms_ap_side_legit": frac(
            sum(fa(r) for r in legit_all if r["group"] == "oos_legit"),
            sum(r["group"] == "oos_legit" for r in legit_all),
        ),
        "all_clear_on_out_of_scope_failures": frac(
            sum(answered_fn(r) and is_no_failure(r["category"]) for r in oos), len(oos)
        ),
        "all_clear_per_family": {
            f: frac(
                sum(
                    answered_fn(r) and is_no_failure(r["category"])
                    for r in oos
                    if r["failure_type"] == f
                ),
                sum(r["failure_type"] == f for r in oos),
            )
            for f in sorted({r["failure_type"] for r in oos})
        },
        "wrong_by_kind": dict(
            Counter(
                r["kind"] for r in ans if not r["correct"] and r["group"] == "cohort"
            )
        ),
    }


def fx_slice(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fx = [r for r in rows if r["group"] == "fx"]
    return {
        "cases": len(fx),
        "predicted": dict(Counter(r["predicted"] for r in fx)),
        "called_failure": sum(is_failure(r["category"]) for r in fx),
    }


def per_label(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    cohort = [r for r in rows if r["group"] == "cohort"]
    for k in sorted(
        {
            r["failure_type"] if r["failure_type"] != "NO_FAILURE" else r["variant"]
            for r in cohort
        }
    ):
        sub = [
            r
            for r in cohort
            if (
                r["failure_type"] if r["failure_type"] != "NO_FAILURE" else r["variant"]
            )
            == k
        ]
        out[k] = {
            "cases": len(sub),
            "correct": sum(r["correct"] for r in sub),
            "complete_correct": sum(r["correct"] and r["complete"] for r in sub),
            "predicted": dict(Counter(r["category"] for r in sub)),
        }
    return out


def investigator_validation(tag: str) -> dict[str, Any]:
    """Recompute the same metrics from the investigator's evaluator-side
    cases.jsonl (validation rows only)."""
    rows = [
        r
        for r in read_jsonl(REPO / "artifacts/dev-runs" / tag / "cases.jsonl")
        if r["split"] == "validation"
    ]
    for r in rows:
        ans = r["disposition"] == "answered"
        r["answered"] = ans
        r["category"] = r["category"] or ""
        r["correct"] = ans and r["category"] == r["expected_category"]
        r["complete"] = bool(r.get("complete")) and ans
        r["kind"] = r.get("kind")
    return metrics(rows, lambda r: r["answered"])


# ------------------------------------------------------------------ main


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    ap.add_argument("--investigator-run", default="m2-dev-4")
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
        links = {
            r["request_id"]: r["case_id"]
            for r in read_jsonl(root / "evaluator" / split / "links.jsonl")
        }
        return [by_case[links[rid]] | {"case_id": links[rid]} for rid in data[split][0]]

    ltr, lva = labels_for("train"), labels_for("validation")
    ytr = np.array([fine_label(r) for r in ltr])
    yva = np.array([fine_label(r) for r in lva])
    lab_va = {r["case_id"]: r for r in lva}
    case_va = [r["case_id"] for r in lva]
    ent_va = data["validation"][1]

    def score_rows(model_pred: list[str], conf: list[float]) -> list[dict[str, Any]]:
        return build_rows(case_va, ent_va, model_pred, conf, snap, lab_va)

    def cohort_acc(pred: np.ndarray) -> tuple[int, int]:
        rows = score_rows(list(pred), [1.0] * len(pred))
        m = metrics(rows)
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
    model = clone_model(best_model).fit(Xtr, ytr)
    classes = list(model.classes_)
    proba = model.predict_proba(Xva)
    pred = [classes[i] for i in proba.argmax(1)]
    conf = list(proba.max(1))
    rows = score_rows(pred, conf)
    primary = metrics(rows)

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
        if group_of(r["failure_type"], r["scenario_variant"], r["case_id"]) == "cohort"
    ]
    thr_grid = [round(0.5 + 0.05 * k, 2) for k in range(10)]
    chosen = None
    cv_table = []
    for th in thr_grid:
        kept = [(a, b) for a, b, c in cohort_tr if c >= th]
        prec = (
            sum(category_of(a) == category_of(b) for a, b in kept) / len(kept)
            if kept
            else float("nan")
        )
        cv_table.append(
            {
                "threshold": th,
                "cv_cohort_answered": len(kept),
                "cv_cohort": len(cohort_tr),
                "cv_precision": round(prec, 4),
            }
        )
        if chosen is None and kept and prec >= args.cv_precision_target:
            chosen = th
    threshold = chosen if chosen is not None else thr_grid[-1]
    with_abstention = metrics(rows, lambda r: r["confidence"] >= threshold)

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
        "metrics": metrics(rows_nores),
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
        and group_of(r["failure_type"], r["scenario_variant"], r["case_id"]) == "cohort"
    ]
    inv_va = [
        i
        for i, r in enumerate(lva)
        if ent_va[i][0] == "invoices"
        and group_of(r["failure_type"], r["scenario_variant"], r["case_id"]) == "cohort"
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
        "per_label_no_abstention": per_label(rows),
        "permutation_importance_top20_exact_label_accuracy_drop": top,
        "feature_family_permutation_relative_cohort_drop": group_perm,
        "residual_ablation": ablation,
        "type_only_baseline": type_only,
        "invoice_age_only_baseline": age_only,
        "selected_model_invoice_stratum_balanced_accuracy": round(float(full_inv), 4),
        "investigator_validation": {
            "run": args.investigator_run,
            **investigator_validation(args.investigator_run),
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
