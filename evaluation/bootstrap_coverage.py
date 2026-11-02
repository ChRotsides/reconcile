"""Coverage of paired.cluster_bootstrap's nominal 95% interval vs. number of
clusters, on simulated data with a known true difference (PLAN.md section 7).

Used once to choose the frozen protocol's min_clusters from a measurement.
Evaluator side, stdlib only, no labels.

Outcome model (logit scale), case i in cluster c:
  p_a = expit(mu_a + u_c + z_i)          p_b = expit(mu_b + u_c + z_i + w_c)
  u_c ~ N(0, SD_CLUSTER^2)  shared by both systems (cluster correlation)
  z_i ~ N(0, SD_CASE^2)     shared by both systems (same case is easy/hard for both)
  w_c ~ N(0, SD_SYSTEM^2)   system B only (systems differ by vendor)
Given the effects, success_a and success_b are independent Bernoulli.
Intercepts mu_a, mu_b are calibrated so the population success rates are
TARGET_A and TARGET_B; the true difference is TARGET_A - TARGET_B up to
quadrature error (computed by midpoint quadrature over normal quantiles, not
Monte Carlo). Cluster sizes do not affect p, so the expected value of the
ratio-of-totals difference equals this population difference for any sizes.

Cluster sizes: each simulated dataset draws n_clusters sizes with replacement
from a stand-in empirical distribution (SIZE_SCENARIOS), consistent with the
validation summary of 123 cases in 55 clusters, largest 35. Replace the stand-ins
with the exact sizes via --sizes-json to rerun.
"""

import argparse
import json
import math
import random
import sys
import time
from functools import cache
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paired  # noqa: E402

TARGET_A = 0.95
TARGET_B = 0.88
SD_CLUSTER = 1.0
SD_CASE = 1.0
SD_SYSTEM = 0.5
QUAD_POINTS = 20000  # midpoint rule error ~1e-6 here

# Stand-ins, not the real sizes. 123 cases, 55 clusters, max 35.
SIZE_SCENARIOS: dict[str, list[int]] = {
    # one cluster of 35; 88 cases over 54 clusters: 34 of size 2, 20 of size 1.
    "skewed": [35] + [2] * 34 + [1] * 20,
    # as even as possible: 13 clusters of 3, 42 of 2.
    "uniform": [3] * 13 + [2] * 42,
}


def expit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@cache
def _quantiles() -> tuple[float, ...]:
    nd = NormalDist()
    return tuple(nd.inv_cdf((i + 0.5) / QUAD_POINTS) for i in range(QUAD_POINTS))


def mean_rate(mu: float, sd: float) -> float:
    """E[expit(mu + sd * Z)], Z standard normal, by midpoint quadrature."""
    return sum(expit(mu + sd * q) for q in _quantiles()) / QUAD_POINTS


def calibrate(target: float, sd: float) -> float:
    lo, hi = -10.0, 10.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if mean_rate(mid, sd) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def model(
    sd_cluster: float = SD_CLUSTER, sd_system: float = SD_SYSTEM
) -> tuple[float, float, float]:
    """Return (mu_a, mu_b, exact population difference)."""
    sd_a = math.hypot(sd_cluster, SD_CASE)
    sd_b = math.hypot(sd_cluster, SD_CASE, sd_system)
    mu_a = calibrate(TARGET_A, sd_a)
    mu_b = calibrate(TARGET_B, sd_b)
    true_diff = mean_rate(mu_a, sd_a) - mean_rate(mu_b, sd_b)
    return mu_a, mu_b, true_diff


def simulate_dataset(
    rng: random.Random,
    sizes: list[int],
    n_clusters: int,
    mu_a: float,
    mu_b: float,
    sd_cluster: float,
    sd_system: float,
) -> list[tuple[str, bool, bool]]:
    cases: list[tuple[str, bool, bool]] = []
    for c in range(n_clusters):
        size = sizes[rng.randrange(len(sizes))]
        u = rng.gauss(0, sd_cluster)
        w = rng.gauss(0, sd_system)
        for _ in range(size):
            z = rng.gauss(0, SD_CASE)
            sa = rng.random() < expit(mu_a + u + z)
            sb = rng.random() < expit(mu_b + u + z + w)
            cases.append((f"c{c}", sa, sb))
    return cases


def coverage(
    sizes: list[int],
    n_clusters: int,
    sims: int,
    reps: int,
    seed: int,
    label: str,
    alpha: float,
    mu_a: float,
    mu_b: float,
    true_diff: float,
    sd_cluster: float,
    sd_system: float,
) -> dict[str, object]:
    rng = random.Random(f"{seed}/{label}/{n_clusters}")
    hits = 0
    widths = 0.0
    for _ in range(sims):
        cases = simulate_dataset(
            rng, sizes, n_clusters, mu_a, mu_b, sd_cluster, sd_system
        )
        r = paired.cluster_bootstrap(cases, rng.randrange(2**31), reps, alpha, 1)
        lo, hi = r["interval"]  # type: ignore[misc]
        hits += lo <= true_diff <= hi
        widths += hi - lo
    p = hits / sims
    return {
        "scenario": label,
        "n_clusters": n_clusters,
        "sims": sims,
        "reps": reps,
        "coverage": p,
        "se": math.sqrt(p * (1 - p) / sims),
        "mean_width": widths / sims,
    }


def run(
    scenarios: dict[str, list[int]],
    cluster_counts: list[int],
    sims: int,
    reps: int,
    seed: int,
    alpha: float = 0.05,
    sd_cluster: float = SD_CLUSTER,
    sd_system: float = SD_SYSTEM,
) -> dict[str, object]:
    mu_a, mu_b, true_diff = model(sd_cluster, sd_system)
    rows = [
        coverage(
            sizes,
            k,
            sims,
            reps,
            seed,
            label,
            alpha,
            mu_a,
            mu_b,
            true_diff,
            sd_cluster,
            sd_system,
        )
        for label, sizes in scenarios.items()
        for k in cluster_counts
    ]
    return {
        "true_difference": true_diff,
        "targets": [TARGET_A, TARGET_B],
        "mu": [mu_a, mu_b],
        "sd_cluster_case_system": [sd_cluster, SD_CASE, sd_system],
        "alpha": alpha,
        "seed": seed,
        "size_scenarios": scenarios,
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", type=Path, help="JSON output path")
    ap.add_argument("--sims", type=int, default=500)
    ap.add_argument("--reps", type=int, default=2000)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--sd-cluster", type=float, default=SD_CLUSTER)
    ap.add_argument("--sd-system", type=float, default=SD_SYSTEM)
    ap.add_argument("--seed", type=int, default=20260101)
    ap.add_argument(
        "--clusters", type=int, nargs="+", default=[5, 10, 15, 20, 30, 55, 100]
    )
    ap.add_argument(
        "--sizes-json", type=Path, help='{"name": [sizes...]} replaces stand-ins'
    )
    args = ap.parse_args()
    scenarios = SIZE_SCENARIOS
    if args.sizes_json:
        scenarios = json.loads(args.sizes_json.read_text())
    t0 = time.time()
    out = run(
        scenarios,
        args.clusters,
        args.sims,
        args.reps,
        args.seed,
        args.alpha,
        args.sd_cluster,
        args.sd_system,
    )
    out["runtime_seconds"] = time.time() - t0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))
    print(
        f"true difference {out['true_difference']:.5f}  "
        f"runtime {out['runtime_seconds']:.0f}s"
    )
    print("scenario  clusters  coverage   se    mean_width")
    for r in out["rows"]:  # type: ignore[attr-defined]
        print(
            f"{r['scenario']:8}  {r['n_clusters']:8}  {r['coverage']:.3f}  "
            f"{r['se']:.3f}  {r['mean_width']:.3f}"
        )


if __name__ == "__main__":
    main()
