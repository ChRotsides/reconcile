# 0008. Dataset and licence handling

**Status:** accepted, 2026-11-02 (decided in M0). The data licence is an unverified assumption.

## Context
FinRCA-Bench is the only dataset retrieved. Its `LICENSE` is MIT and names "software and associated documentation files", not data; no data card exists (`DATA_SOURCES.md` section 3). The project also needs to protect the sealed holdout and, later, any practitioner exports.

## Decision
- Pin the upstream revision `76c93ec32536e29826e2ab9e68b58e0351410bc7` (re-checked equal to upstream `main` on 2026-11-01) and record URL, retrieval date, licence reading and file digests in `DATA_SOURCES.md`.
- `data/` and `artifacts/` are ignored by git as whole directories; upstream files live unmodified under `data/upstream/`, private practitioner exports would live under `data/private/`.
- Tests and fixtures use our own synthetic rows (`fixtures/public/`). No upstream row is copied. If anything from upstream is ever redistributed, the MIT notice (`Copyright (c) 2026 Pratik11`) goes with it.
- Upstream code, notebooks and pickled model files are treated as data and not run or loaded outside an isolated environment (`AGENTS.md` section 7). Baselines were reproduced in an isolated container (`docs/baseline-reproduction.md`).
- Exposure of label files is recorded in one place, `docs/data-audit.md` section 11, and `challenge_test` is not opened, listed or counted until the M7 sealed run.
- Real practitioner data never enters the repository, tests, logs or model prompts (`AGENTS.md` section 2; `PLAN.md` section 2). Practitioners are recorded by role, not name.

## Alternatives considered
1. Copy sample upstream rows into fixtures (the simplest option): rejected. The data licence is unclear, and `PLAN.md` prefers our own fixtures.
2. Track `data/upstream/` in the repository: rejected. It would hold the answer labels next to the code.
3. Ignore only `data/upstream/` and `data/private/`: rejected. A new subdirectory (prepared inputs, labels) would be tracked by default.

## Evidence
`DATA_SOURCES.md` sections 2 to 4; `.gitignore`; `fixtures/public/README.md`; `docs/data-audit.md` section 11; `docs/baseline-reproduction.md`; `tests/unit/test_fixtures.py::test_expected_covers_exactly_the_scenarios`. Provenance: D001, D023, D026, D030, D038.

## Consequences and limits
- Whether MIT covers the generated data is unverified (A011). Internal evaluation is treated as permitted; publishing copied rows would need the author's confirmation.
- The project code licence was chosen on 2026-11-03, ahead of M7: PolyForm Noncommercial 1.0.0 (`LICENSE`, D156). Commercial use needs a separate licence from the author. Checking output redistribution terms is still an M7 task.
- RecBench, FinBalance and BenchRec have not been retrieved; their rights are unconfirmed.
- The test split was read during reproduction and cannot be described as unseen.
- Practitioner-data handling rules exist (`docs/practitioner-findings.md`), but no practitioner data has been received.

## Revisit if
The author states a data licence, upstream changes the revision, a second dataset is added, or any practitioner export is offered.
