"""Plain HTML pages for the observable-input baseline (PLAN.md M5, carried from
the M2 gate: "baseline evidence packets and report templates").

    uv run --group eval python evaluation/baselines/observable/report.py RUN_DIR OUT_DIR

RUN_DIR is a baseline run folder (`artifacts/baselines/<tag>`). One page per
case plus an index, from the run's predictions, requests and snapshot only.

The pages show what the baseline has and nothing more: the primary record, the
predicted category, its raw probability, whether each protocol configuration
answers, and the record-availability requirements of `evidence.py`. They are
NOT evidence packets, on purpose: `evidence.py` is a relaxed upper bound and a
contract-grade packet would be a second implementation of the investigator.
Every page says so.

Label isolation: this module opens no links, labels or cases file. From the
predictions file it reads only `request_id`, `predicted` and `confidence`
(the file's `abstained` flag is recomputed from the protocol threshold); from
the requests file only `request_id`, `cutoff` and `primary_entity`.
"""

import json
import sys
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import evidence  # noqa: E402
from cohort import FAMILY_KEYS  # noqa: E402
from features import Snapshot  # noqa: E402

from reconcile.reports.explain import TITLES  # noqa: E402
from reconcile.reports.html import Element, document, el  # noqa: E402

PROTOCOL = Path(__file__).resolve().parents[2] / "protocol.toml"

NOTICE = (
    "This baseline gives no exact calculations, no citations, no search scope "
    "for absence checks and no next steps. 'Records available' means the "
    "records a category needs exist in the snapshot; it does not mean the "
    "evidence contract is satisfied, and it is not an investigation."
)


def configurations(protocol_path: Path = PROTOCOL) -> list[tuple[str, float | None]]:
    """(name, abstention threshold) of the frozen baseline configurations;
    `answer_all` has no threshold."""
    names = tomllib.loads(protocol_path.read_text())["systems"][
        "baseline_configurations"
    ]
    return [
        (n, None if n == "answer_all" else float(n.removeprefix("threshold_")))
        for n in names
    ]


def plain_title(predicted: str) -> str:
    category = FAMILY_KEYS.get(predicted, predicted)
    if category in TITLES:
        return TITLES[category]
    return f"{predicted} (no plain title: outside the investigator's categories)"


def decision(confidence: float, threshold: float | None) -> str:
    # Frozen convention: abstain iff the raw probability is below the threshold.
    if threshold is not None and confidence < threshold:
        return "abstains"
    return "answers"


def _table(headers: tuple[str, ...], rows: list[list[str | Element]]) -> Element:
    head = el("tr", None, *[el("th", {"scope": "col"}, h) for h in headers])
    body = [el("tr", None, *[el("td", None, c) for c in r]) for r in rows]
    return el("table", None, el("thead", None, head), el("tbody", None, *body))


def render_case(
    case: dict[str, Any],
    requirements: list[tuple[str, bool]],
    configs: list[tuple[str, float | None]],
) -> str:
    """`case` holds request_id, table, record_id, predicted, confidence."""
    head = plain_title(case["predicted"])
    conf = case["confidence"]
    if requirements:
        req_block: Element = _table(
            ("Requirement", "Records available"),
            [[n, "met" if ok else "not met"] for n, ok in requirements],
        )
    else:
        req_block = el(
            "p", None, "The contract defines no requirements for this category here."
        )
    body = (
        el("h1", None, head),
        el("p", None, "Baseline case ", el("code", None, case["request_id"])),
        el("div", {"class": "note"}, NOTICE),
        el("h2", None, "Primary record"),
        el("p", None, el("code", None, f"{case['table']} {case['record_id']}")),
        el("h2", None, "Prediction"),
        el(
            "dl",
            None,
            el("dt", None, "Predicted category"),
            el("dd", None, el("code", None, case["predicted"])),
            el("dt", None, "Raw probability"),
            el("dd", None, el("code", None, repr(float(conf)))),
        ),
        el("h2", None, "Protocol configurations"),
        _table(
            ("Configuration", "Threshold", "Outcome"),
            [
                [n, "none" if t is None else str(t), decision(conf, t)]
                for n, t in configs
            ],
        ),
        el("h2", None, "Record availability"),
        req_block,
        el("p", None, el("a", {"href": "index.html"}, "All cases")),
    )
    return document(f"{head} - {case['request_id']}", body)


def render_index(
    cases: list[dict[str, Any]], configs: list[tuple[str, float | None]]
) -> str:
    rows: list[list[str | Element]] = []
    for c in cases:
        rows.append(
            [
                el("a", {"href": f"{c['request_id']}.html"}, c["request_id"]),
                f"{c['table']} {c['record_id']}",
                plain_title(c["predicted"]),
                repr(float(c["confidence"])),
                ", ".join(f"{n}: {decision(c['confidence'], t)}" for n, t in configs),
            ]
        )
    body = (
        el("h1", None, "Baseline cases"),
        el("p", None, f"{len(cases)} cases."),
        el("div", {"class": "note"}, NOTICE),
        _table(
            ("Request", "Primary record", "Predicted", "Probability", "Outcomes"),
            rows,
        ),
    )
    return document("Baseline cases", body)


def _read(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_report(
    predictions: Path,
    requests: Path,
    snapshot: Path,
    out_dir: Path,
    configs: list[tuple[str, float | None]],
) -> int:
    """Write `index.html` and one `<request_id>.html` per prediction. Returns
    the number of cases."""
    reqs = {r["request_id"]: r for r in _read(requests)}
    cases: list[dict[str, Any]] = []
    for p in _read(predictions):  # safe fields only
        r = reqs[p["request_id"]]
        cases.append(
            {
                "request_id": p["request_id"],
                "predicted": p["predicted"],
                "confidence": p["confidence"],
                "table": r["primary_entity"]["table"],
                "record_id": r["primary_entity"]["record_id"],
                "cutoff": r["cutoff"],
            }
        )
    cases.sort(key=lambda c: c["request_id"])
    snaps: dict[str, Snapshot] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in cases:
        snap = snaps.setdefault(
            c["cutoff"], Snapshot(snapshot, date.fromisoformat(c["cutoff"]))
        )
        reqs_met = evidence.retrieve(
            snap,
            FAMILY_KEYS.get(c["predicted"], c["predicted"]),
            c["table"],
            c["record_id"],
        )
        (out_dir / f"{c['request_id']}.html").write_text(
            render_case(c, reqs_met, configs), encoding="utf-8"
        )
    (out_dir / "index.html").write_text(render_index(cases, configs), encoding="utf-8")
    return len(cases)


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: report.py RUN_DIR OUT_DIR")
    run, out = Path(sys.argv[1]), Path(sys.argv[2])
    n = write_report(
        run / "predictions_validation.jsonl",
        run / "prepared/requests/validation/requests.jsonl",
        run / "prepared/snapshot/snapshot.sqlite",
        out,
        configurations(),
    )
    print(f"wrote {n} case pages and index.html to {out}")


if __name__ == "__main__":
    main()
