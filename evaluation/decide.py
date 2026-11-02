"""Executable decision rule of the frozen protocol (evaluation/protocol.toml,
sections [decision] and [statistics]).

    uv run python evaluation/decide.py --protocol evaluation/protocol.toml \
        --comparison answer_all=A.json --comparison threshold_0.85=B.json --out OUT.json

Evaluator side. Each comparison is the output of `compare.py` (investigator
versus one baseline configuration, headline view). The claim holds only if the
primary and false-alarm checks pass against EVERY configuration. Each
comparison carries its configuration, split and input digests; decide refuses
mismatches. The verdict is a "rehearsal" unless the split is the protocol's
sealed split. A check whose interval does not exist (too few clusters, empty
population) is "not_established", never "pass".
"""

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

# Configurations this module knows how to judge; the protocol must list exactly
# configurations from here (tests/unit/test_protocol.py).
KNOWN_CONFIGURATIONS = ("answer_all", "threshold_0.85")

Check = dict[str, Any]
EMPTY = "empty population (zero denominator)"


def _validate(protocol: dict[str, Any], comparisons: dict[str, dict[str, Any]]) -> None:
    expected = set(protocol["systems"]["baseline_configurations"])
    unknown = expected - set(KNOWN_CONFIGURATIONS)
    if unknown:
        raise ValueError(
            f"protocol configurations unknown to decide.py: {sorted(unknown)}"
        )
    missing = expected - comparisons.keys()
    extra = comparisons.keys() - expected
    if missing or extra:
        raise ValueError(
            f"comparisons must match the protocol configurations: missing"
            f" {sorted(missing)}, extra {sorted(extra)}"
        )
    for name, comp in comparisons.items():
        if comp.get("configuration") != name:
            raise ValueError(
                f"{name}: comparison is for configuration {comp.get('configuration')!r}"
            )
        for key in ("split", "a_sha256", "b_sha256"):
            if not comp.get(key):
                raise ValueError(f"{name}: comparison has no {key}")
    if len({c["split"] for c in comparisons.values()}) != 1:
        raise ValueError("comparisons are on different splits")
    if len({c["a_sha256"] for c in comparisons.values()}) != 1:
        raise ValueError(
            "comparisons use different investigator runs (a_sha256 differs)"
        )
    b_digests = [c["b_sha256"] for c in comparisons.values()]
    if len(set(b_digests)) != len(b_digests):
        raise ValueError("two configurations share one baseline file (b_sha256)")
    stats = protocol["statistics"]
    want = {
        "method": stats["method"],
        "seed": stats["seed"],
        "reps": stats["reps"],
        "alpha": stats["alpha"],
        "min_clusters": stats["min_clusters"],
        "view": protocol["cohort"]["headline_view"],
    }
    for name, comp in comparisons.items():
        for key, value in want.items():
            if comp.get(key) != value:
                raise ValueError(
                    f"{name}: comparison {key} is {comp.get(key)!r},"
                    f" protocol says {value!r}"
                )


def _primary(rule: dict[str, Any], comp: dict[str, Any]) -> Check:
    boot = comp["primary_success"]["bootstrap"]
    diff, interval = boot["difference"], boot["interval"]
    check: Check = {"difference": diff, "interval": interval}
    if diff is None:
        return check | {"status": "not_established", "reason": EMPTY}
    if diff < rule["min_primary_difference"]:
        return check | {
            "status": "fail",
            "reason": f"difference {diff:+.4f} < {rule['min_primary_difference']}",
        }
    if rule["primary_interval_must_exclude_zero"]:
        if interval is None:
            return check | {
                "status": "not_established",
                "reason": "no interval (too few clusters)",
            }
        if not interval[0] > 0:
            return check | {
                "status": "fail",
                "reason": f"interval lower bound {interval[0]:+.4f} is not > 0",
            }
    return check | {"status": "pass", "reason": None}


def _false_alarm(rule: dict[str, Any], comp: dict[str, Any]) -> Check:
    boot = comp["false_alarm"]["bootstrap"]
    diff, interval = boot["difference"], boot["interval"]
    # Observed-sample engineering criterion, not a non-inferiority test: the
    # interval is descriptive, and the bound is the protocol's, not a margin.
    check: Check = {
        "difference": diff,
        "interval": interval,
        "criterion": "no observed increase (descriptive interval)",
    }
    if (
        comp["false_alarm"]["a"]["numerator"]
        == 0
        == comp["false_alarm"]["b"]["numerator"]
    ):
        check["note"] = (
            "neither system raised a false alarm; no statistical assurance about rates"
        )
    if diff is None:
        return check | {"status": "not_established", "reason": EMPTY}
    if interval is None:
        return check | {
            "status": "not_established",
            "reason": "no interval (too few clusters)",
        }
    limit = rule["max_false_alarm_difference_upper_bound"]
    if interval[1] > limit:
        return check | {
            "status": "fail",
            "reason": f"interval upper bound {interval[1]:+.4f} > {limit}",
        }
    return check | {"status": "pass", "reason": None}


def decide(
    protocol: dict[str, Any], comparisons: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    _validate(protocol, comparisons)
    rule = protocol["decision"]
    configurations: dict[str, dict[str, Check]] = {}
    split = next(iter(comparisons.values()))["split"]
    reasons: list[str] = []
    statuses: list[str] = []
    for name in protocol["systems"]["baseline_configurations"]:
        checks = {
            "primary": _primary(rule, comparisons[name]),
            "false_alarm": _false_alarm(rule, comparisons[name]),
        }
        configurations[name] = checks
        for check_name, check in checks.items():
            statuses.append(check["status"])
            if check["status"] != "pass":
                reasons.append(f"{name} {check_name}: {check['reason']}")
    if "fail" in statuses:
        verdict = "fails"
    elif "not_established" in statuses:
        verdict = "not_established"
    else:
        verdict = "holds"
    return {
        "verdict": verdict,
        "reasons": reasons,
        "configurations": configurations,
        "split": split,
        "mode": "release" if split == protocol["splits"]["sealed"] else "rehearsal",
        "digests": {
            n: {
                "a_sha256": comparisons[n]["a_sha256"],
                "b_sha256": comparisons[n]["b_sha256"],
            }
            for n in configurations
        },
        "protocol_version": protocol["protocol"]["version"],
    }


def _num(x: float | None) -> str:
    return "undefined" if x is None else f"{x:+.4f}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--protocol", required=True)
    p.add_argument(
        "--comparison", action="append", default=[], metavar="NAME=PATH.json"
    )
    p.add_argument("--out", required=True)
    ns = p.parse_args()
    with Path(ns.protocol).open("rb") as f:
        protocol = tomllib.load(f)
    comparisons: dict[str, dict[str, Any]] = {}
    for item in ns.comparison:
        name, sep, path = item.partition("=")
        if not sep or name in comparisons:
            sys.exit(f"decide: bad or repeated --comparison {item!r}")
        comparisons[name] = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        result = decide(protocol, comparisons)
    except ValueError as e:
        sys.exit(f"decide: {e}")
    Path(ns.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    for name, checks in result["configurations"].items():
        for check_name, c in checks.items():
            print(
                f"{name:16} {check_name:12} {c['status']:16}"
                f" diff {_num(c['difference'])}"
                f" interval {c['interval']}"
            )
    if result["mode"] == "rehearsal":
        print(
            f"REHEARSAL ({result['split']} split) - not a release verdict. verdict:",
            result["verdict"],
        )
    else:
        print("RELEASE verdict:", result["verdict"])
    for r in result["reasons"]:
        print(" -", r)


if __name__ == "__main__":
    main()
