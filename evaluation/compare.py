"""Paired comparison of two scored runs (PLAN.md section 7).

    uv run python evaluation/compare.py A_CASES B_CASES --split validation \
        --configuration answer_all --view all_cases --seed S --reps R \
        --alpha 0.05 --min-clusters M --out OUT.json

Evaluator side. Reads the per-case `cases.jsonl` of two scorers (the
investigator's `dev_run.py evaluate`, the classifier baseline) and feeds
`paired.py`. Two outcomes, both over the same cases:

- primary: success = correct with complete evidence, over every case of the
  view. Abstentions, out-of-scope and failures are not successes.
- false alarm: `kind == "false_alarm"` over ALL legitimate cases of the split
  (label NO_FAILURE, any group: cohort, label_conflict, fx, oos_legit, ...),
  as PLAN.md section 7 defines it, whatever the view. The two runs must
  agree on that set and on every compared case's cluster.

Row shapes: the investigator writes `outcome` ("correct" is a success);
the baseline writes `correct` (it always answers). Both write `complete`.
The cluster is the field `group_vendor_id`; a row without it stops the
comparison, there is no fallback.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import cohort as C
import paired

CLUSTER_FIELD = "group_vendor_id"

Row = dict[str, Any]


def load(path: str | Path) -> dict[str, Row]:
    rows: dict[str, Row] = {}
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row["case_id"] in rows:
                raise ValueError(f"{path}: duplicate case_id {row['case_id']}")
            rows[row["case_id"]] = row
    return rows


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_success(row: Row) -> bool:
    if "outcome" in row:  # investigator
        return bool(row["outcome"] == "correct" and row.get("complete"))
    if "correct" in row:  # baseline
        return bool(row["correct"] and row.get("complete"))
    raise ValueError(f"{row['case_id']}: row has neither 'outcome' nor 'correct'")


def outcomes(rows: dict[str, Row]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for case_id, r in rows.items():
        if r.get(CLUSTER_FIELD) is None:
            raise ValueError(f"{case_id}: missing {CLUSTER_FIELD}")
        legit = r["failure_type"] == "NO_FAILURE"
        out[case_id] = {
            "success": is_success(r),
            "legitimate": legit,
            "false_alarm": legit and r.get("kind") == "false_alarm",
            "group": r["group"],
            "cluster": r[CLUSTER_FIELD],
        }
    return out


def _in_split(rows: dict[str, Row], split: str, name: str) -> dict[str, Row]:
    for r in rows.values():
        if "split" not in r:
            raise ValueError(f"{name}: {r['case_id']} has no 'split'")
    return {k: r for k, r in rows.items() if r["split"] == split}


def _metric(
    a: dict[str, dict[str, Any]],
    b: dict[str, dict[str, Any]],
    field: str,
    ids: list[str],
    seed: int,
    reps: int,
    alpha: float,
    min_clusters: int,
) -> dict[str, Any]:
    pa = {k: bool(a[k][field]) for k in ids}
    pb = {k: bool(b[k][field]) for k in ids}
    counts = paired.paired_counts(pa, pb)
    boot = paired.cluster_bootstrap(
        [(a[k]["cluster"], pa[k], pb[k]) for k in ids], seed, reps, alpha, min_clusters
    )
    n = counts["n"]
    return {
        "a": {"numerator": counts["successes_a"], "denominator": n},
        "b": {"numerator": counts["successes_b"], "denominator": n},
        "paired_counts": counts,
        "bootstrap": boot,
    }


def compare(
    a_rows: dict[str, Row],
    b_rows: dict[str, Row],
    split: str,
    view: str,
    seed: int,
    reps: int,
    alpha: float,
    min_clusters: int,
) -> dict[str, Any]:
    groups = C.COHORT_VIEWS[view]
    a = outcomes(_in_split(a_rows, split, "A"))
    b = outcomes(_in_split(b_rows, split, "B"))
    ids = sorted(k for k in a if a[k]["group"] in groups)
    b_ids = sorted(k for k in b if b[k]["group"] in groups)
    if ids != b_ids:
        raise ValueError(
            f"case sets differ in split {split!r}, view {view!r}: A has {len(ids)},"
            f" B has {len(b_ids)}, common {len(set(ids) & set(b_ids))}"
        )
    legit = sorted(k for k in a if a[k]["legitimate"])
    b_legit = sorted(k for k in b if b[k]["legitimate"])
    if legit != b_legit:
        raise ValueError(
            f"legitimate case sets differ in split {split!r}: A has {len(legit)},"
            f" B has {len(b_legit)}, common {len(set(legit) & set(b_legit))}"
        )
    for k in sorted(set(ids) | set(legit)):
        for f in ("group", "cluster"):
            if a[k][f] != b[k][f]:
                raise ValueError(f"{k}: {f} differs: {a[k][f]!r} vs {b[k][f]!r}")
    args = (seed, reps, alpha, min_clusters)
    return {
        "split": split,
        "view": view,
        "groups": list(groups),
        "n_cases": len(ids),
        "n_clusters": len({a[k]["cluster"] for k in ids}),
        "n_legitimate_cases": len(legit),  # all of the split, not only the view
        "method": paired.METHOD,
        "seed": seed,
        "reps": reps,
        "alpha": alpha,
        "min_clusters": min_clusters,
        "primary_success": _metric(a, b, "success", ids, *args),
        "false_alarm": _metric(a, b, "false_alarm", legit, *args),
    }


def _fmt(m: dict[str, Any]) -> str:
    a, b, boot = m["a"], m["b"], m["bootstrap"]
    iv = boot["interval"]
    ci = (
        "no interval (empty or too few clusters)"
        if iv is None
        else f"[{iv[0]:+.4f}, {iv[1]:+.4f}]"
    )
    d = boot["difference"]
    diff = "undefined" if d is None else f"{d:+.4f}"
    return (
        f"A {a['numerator']}/{a['denominator']}  B {b['numerator']}/{b['denominator']}"
        f"  only_a {m['paired_counts']['only_a']} only_b {m['paired_counts']['only_b']}"
        f"  diff {diff}"
        f"  {ci}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("a_cases")
    p.add_argument("b_cases")
    p.add_argument("--split", required=True)
    p.add_argument("--configuration", required=True)
    p.add_argument("--view", required=True, choices=sorted(C.COHORT_VIEWS))
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--reps", type=int, required=True)
    p.add_argument("--alpha", type=float, required=True)
    p.add_argument("--min-clusters", type=int, required=True)
    p.add_argument("--out", required=True)
    ns = p.parse_args()
    try:
        result = compare(
            load(ns.a_cases),
            load(ns.b_cases),
            ns.split,
            ns.view,
            ns.seed,
            ns.reps,
            ns.alpha,
            ns.min_clusters,
        )
    except ValueError as e:
        sys.exit(f"compare: {e}")
    result |= {
        "configuration": ns.configuration,
        "a_sha256": sha256_file(ns.a_cases),
        "b_sha256": sha256_file(ns.b_cases),
    }
    Path(ns.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        f"{result['split']} / {result['view']}: {result['n_cases']} cases,"
        f" {result['n_clusters']} clusters, {result['n_legitimate_cases']} legitimate"
        f" ({result['method']}, seed {ns.seed}, reps {ns.reps}, alpha {ns.alpha})"
    )
    print("primary (correct with complete evidence):", _fmt(result["primary_success"]))
    print("false alarms (all legitimate cases):    ", _fmt(result["false_alarm"]))


if __name__ == "__main__":
    main()
