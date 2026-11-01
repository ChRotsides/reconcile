"""Request preparation on the real FinRCA train split.

Skips cleanly when the pinned checkout is absent. Set FINRCA_BENCHMARK_DIR to
its `data/benchmark` directory or place the checkout at the default path. Only
the train split is read; nothing from challenge_test is touched.
"""

import csv
import json
import os
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from reconcile.adapters.finrca import create_key_file, prepare_finrca_snapshot
from reconcile.adapters.finrca_requests import prepare_finrca_requests

DEFAULT_BENCHMARK = Path("data/upstream/FinRCA-AI-Bench/data/benchmark")
BENCHMARK_DIR = Path(os.environ.get("FINRCA_BENCHMARK_DIR", DEFAULT_BENCHMARK))
FULL_DIR = BENCHMARK_DIR / "full"
QUESTIONS = BENCHMARK_DIR / "train" / "benchmark_questions.jsonl"

pytestmark = pytest.mark.skipif(
    not (FULL_DIR / "payments.csv").exists() or not QUESTIONS.exists(),
    reason="FinRCA benchmark checkout not present",
)

ID_COLUMN = {
    "payments": "payment_id",
    "invoices": "invoice_id",
    "bank_transactions": "bank_transaction_id",
    "gl_entries": "journal_id",
}


def test_train_requests_against_the_prepared_snapshot(tmp_path: Path) -> None:
    key = tmp_path / "private" / "key"
    key.parent.mkdir()
    create_key_file(key)
    snap = tmp_path / "snapshot"
    prepare_finrca_snapshot(
        FULL_DIR, snap, key_path=key, source_revision="test", cutoff=date(2026, 6, 30)
    )
    summary = prepare_finrca_requests(
        BENCHMARK_DIR,
        "train",
        snapshot_dir=snap,
        key_path=key,
        inference_dir=tmp_path / "inference",
        evaluator_dir=tmp_path / "evaluator",
        policy_version="test",
    )

    cases = {json.loads(line)["case_id"] for line in QUESTIONS.read_text().splitlines()}
    requests = [
        json.loads(line)
        for line in (tmp_path / "inference" / "requests.jsonl").read_text().splitlines()
    ]
    assert summary["requests"] == len(requests) == len(cases)

    links = (tmp_path / "evaluator" / "links.jsonl").read_text().splitlines()
    assert {json.loads(line)["case_id"] for line in links} == cases

    con = sqlite3.connect(f"file:{snap / 'snapshot.sqlite'}?mode=ro", uri=True)
    try:
        missing: dict[str, int] = {}
        for rec in requests:
            table = rec["primary_entity"]["table"]
            n = con.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{ID_COLUMN[table]}" = ?',
                (rec["primary_entity"]["record_id"],),
            ).fetchone()[0]
            if n == 0:
                missing[table] = missing.get(table, 0) + 1
    finally:
        con.close()
    print(summary, "tokens absent from the snapshot:", missing)
    # The only legitimate absences are entities whose every source row is after
    # the cutoff (a journal posted wholly after it): the request stays, and the
    # investigator will find nothing to cite.
    assert missing == {"gl_entries": _journals_wholly_after_cutoff()}


def _journals_wholly_after_cutoff() -> int:
    journals: dict[str, list[bool]] = {}
    for line in QUESTIONS.read_text().splitlines():
        entity = json.loads(line)["primary_entity"]
        if entity["type"] == "gl_journal":
            journals[entity["id"]] = []
    with (FULL_DIR / "gl_entries.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            if row["journal_id"] in journals:
                journals[row["journal_id"]].append(row["posting_date"] <= "2026-06-30")
    return sum(1 for observable in journals.values() if not any(observable))
