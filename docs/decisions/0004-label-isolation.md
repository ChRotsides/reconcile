# 0004. Label isolation

**Status:** accepted, 2026-11-02 (decided in M0 and M1, container added in M2). Isolation of a real run still depends on the operator.

## Context
The investigator must not see answer labels, generator provenance or oracle evidence (`PLAN.md` sections 2 and 4). The M0 audit found that FinRCA's own inputs leak the class (`docs/data-audit.md` section 4), so a library-level convention was not enough.

## Decision
- Inference reads only a prepared snapshot and a requests file. Preparation builds the snapshot from the allowlist, replaces every ID and reference with a keyed HMAC token, re-sorts rows, drops class-encoding tables and columns (for example `audit_log`), and writes the request-to-case links to a separate evaluator directory. The key lives outside both directories (`src/reconcile/adapters/finrca.py`, `finrca_requests.py`).
- Requests carry a fixed, class-free symptom sentence (`docs/data-audit.md` section 3.1).
- `src/reconcile/` never imports `evaluation/` and names no label file, except one adapter that refuses `challenge_test` (static test).
- Inference runs in a container with the package only and three mounts; an absence check shows the key, links, upstream checkout, `evaluation/` and `challenge_test` paths are not there (`docs/isolation.md`).
- Evaluation joins labels only after packets are written and hashed (`evaluation/dev_run.py`).
- `test` is a reproduction set only. `challenge_test` is the sealed final holdout and is not to be opened, listed or counted before the M7 run (`AGENTS.md` section 7, `docs/data-audit.md` section 11).

## Alternatives considered
1. A module-import convention only (the simplest option): rejected. `PLAN.md` section 2 says it is insufficient, and a process can simply open a path it can reach.
2. Keep upstream inputs and filter known leaks with a denylist: rejected. The audit found leaks that a denylist would have missed (sequential reference numbers, row order); the plan requires an allowlist.
3. Allowlist plus tokenisation plus restricted mounts: chosen.

## Evidence
- `tests/unit/test_import_boundaries.py::test_inference_code_respects_the_boundary` and `test_checker_detects_violations`.
- `tests/unit/test_finrca_adapter.py::test_digest_is_reproducible_and_byte_identical`, `test_key_is_not_in_the_snapshot_directory`; `tests/unit/test_finrca_requests.py::test_key_inside_an_output_directory_is_refused`.
- `tests/integration/test_inference_container.py::test_absence_check`, `test_adapters_not_importable_in_image`, `test_container_packets_equal_in_process` (skipped without Docker).
- `docs/leakage-test.md`: row order, token order and inserted-payment identity are not recoverable from the allowed fields. Five residual signals remain and are declared there (inserted-invoice status, FX ledger codes, entity type, invoice age, weak order proxies). These are findings, not fixed.
- Provenance: D025, D026, D037, D042, D050, D073, D075, D099.

## Consequences and limits
- The operator must mount only the intended directories; the script checks only that two files exist (A047). The base image is pulled by tag, not pinned by digest (A048). The in-process tests do not stop dynamic imports.
- The residuals mean the benchmark is repaired, not proven clean. The absence check proves only the paths it tests.
- The test split was exposed during M0 and cannot be called untouched. For `challenge_test`, the audit recorded file hashes and line counts but did not open the files (`docs/data-audit.md` section 11.3).

## Revisit if
Inference and evaluation ever share a filesystem without restricted mounts, a further leak is found, or the base image is pinned and the network/write restrictions are tested directly.
