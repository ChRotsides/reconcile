"""Paired comparison of two systems scored on the same cases, with a vendor
cluster bootstrap (PLAN.md section 7).

Evaluator side: takes booleans only, never labels or investigation code. One
function serves every metric; the caller chooses what "success" means (correct
with complete evidence for the primary metric, a false alarm on a legitimate
case for the false-alarm rate).

Method "cluster_percentile_bootstrap": clusters (vendor groups) are resampled
with replacement, because cases of one vendor are correlated and resampling
rows would understate uncertainty. Each replicate recomputes
sum_a/sum_n - sum_b/sum_n over the resampled clusters (a ratio of totals, so
larger clusters weigh more, as they do in the point estimate).

Percentile interval: replicates sorted ascending as r[0..reps-1];
lower = r[floor(alpha/2 * (reps-1))], upper = r[ceil((1 - alpha/2) * (reps-1))].
No interpolation, so the bounds are always actual replicate values.
"""

import math
import random

METHOD = "cluster_percentile_bootstrap"


def paired_counts(a: dict[str, bool], b: dict[str, bool]) -> dict[str, int | float]:
    if a.keys() != b.keys():
        raise ValueError(
            f"case id sets differ: a has {len(a)}, b has {len(b)}, "
            f"common {len(a.keys() & b.keys())}"
        )
    n = len(a)
    if n == 0:
        raise ValueError("no cases")
    both = sum(1 for k in a if a[k] and b[k])
    only_a = sum(1 for k in a if a[k] and not b[k])
    only_b = sum(1 for k in a if b[k] and not a[k])
    return {
        "n": n,
        "successes_a": both + only_a,
        "successes_b": both + only_b,
        "both": both,
        "only_a": only_a,
        "only_b": only_b,
        "neither": n - both - only_a - only_b,
        "difference": (only_a - only_b) / n,
    }


def cluster_bootstrap(
    cases: list[tuple[str, bool, bool]],
    seed: int,
    reps: int,
    alpha: float,
    min_clusters: int,
) -> dict[str, object]:
    if reps < 1:
        raise ValueError(f"reps must be >= 1, got {reps}")
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if not cases:
        raise ValueError("no cases")
    totals: dict[str, list[int]] = {}
    for cluster, sa, sb in cases:
        t = totals.setdefault(cluster, [0, 0, 0])
        t[0] += sa
        t[1] += sb
        t[2] += 1
    clusters = [totals[k] for k in sorted(totals)]  # order-independent
    n_cases = len(cases)
    point = (sum(c[0] for c in clusters) - sum(c[1] for c in clusters)) / n_cases
    result: dict[str, object] = {
        "difference": point,
        "interval": None,
        "alpha": alpha,
        "n_cases": n_cases,
        "n_clusters": len(clusters),
        "seed": seed,
        "reps": reps,
        "method": METHOD,
        "too_few_clusters": len(clusters) < min_clusters,
    }
    if result["too_few_clusters"]:
        return result
    rng = random.Random(seed)
    k = len(clusters)
    diffs: list[float] = []
    for _ in range(reps):
        sa = sb = n = 0
        for _ in range(k):
            c = clusters[rng.randrange(k)]
            sa += c[0]
            sb += c[1]
            n += c[2]
        diffs.append(sa / n - sb / n)
    diffs.sort()
    lo = diffs[math.floor(alpha / 2 * (reps - 1))]
    hi = diffs[math.ceil((1 - alpha / 2) * (reps - 1))]
    result["interval"] = [lo, hi]
    return result
