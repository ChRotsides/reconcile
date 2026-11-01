# Inference and evaluator isolation

Status at M1. Rationale: `PLAN.md` section 2, "Input and label separation". Directory names below are chosen by the caller of the preparation functions; the paths are the project's convention, not enforced.

## Zones and where they live

| Zone | Concrete location today | Who reads it |
|---|---|---|
| Operational inputs | Prepared snapshot directory (`snapshot.sqlite`, `manifest.json`, from `adapters/finrca.py`) and requests directory (`requests.jsonl`, from `adapters/finrca_requests.py`) | Baseline and investigator processes |
| Development labels | Upstream train and validation answer files in the upstream checkout (`data/upstream/...`); evaluator links directory (`links.jsonl`, request ID to case ID) | Training and evaluation tooling |
| Sealed labels | Upstream `challenge_test` split and the scoring provenance | Evaluator, after predictions are final (M7 only) |

Other artifacts: the token key file lies outside both output directories (kept by the operator, hex, mode 0600). `artifacts/` holds untracked run outputs and reviews and is not an input to inference.

## What inference may mount

Read-only, and only these two: the prepared snapshot directory and the requests directory.

Inference must never see: the key file, the links directory, the upstream checkout, `evaluation/` code, or anything under `challenge_test`. The key can re-derive tokens; the links map requests to cases; the upstream files hold answers.

## Planned (M2, not implemented)

Run the investigator in a container started like the M0 baseline runner (`evaluation/baselines/finrca_upstream/run_docker.sh`): no network, read-only root, explicit read-only mounts, with only the two directories above mounted. Nothing like this exists for `src/reconcile/` yet, because there is no investigator.

## Enforced today

- `tests/unit/test_import_boundaries.py` (static, `ast` and text scan of `src/reconcile/`):
  - no module imports `evaluation`;
  - no module names a label or answer file (`rca_ground_truth`, `failure_manifest`, `causal_edges`, `mutation_log`, `challenge_test`, `links.jsonl`, `benchmark_questions`), except `adapters/finrca_requests.py`, which reads `benchmark_questions.jsonl`, writes `links.jsonl` and names `challenge_test` to refuse it;
  - no inference module imports the preparation adapters (`adapters/finrca.py`, `adapters/finrca_requests.py`), which hold key handling and read upstream files.
- Preparation refusals: the `challenge_test` split is refused before any file is touched; the key file may not lie inside the output directories (snapshot, requests, links); the snapshot `out_dir` must not already exist; request outputs refuse to overwrite existing files.

## Still an assumption (A024)

The tests are a guard against accidents. They do not stop dynamic imports, strings built at runtime, or a process that simply opens a path it can reach. Until M2 runs inference with only the two mounts, isolation of the real process rests on the operator mounting correctly (A024: unverified).
