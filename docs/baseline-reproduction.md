# Baseline reproduction: FinRCA Rules/SQL and classical ML

Status: M0 reproduction, run 2026-11-01. Engineering reproduction of upstream code, not an operational comparison.

## Environment

- Upstream: https://github.com/PratikGhawate/FinRCA-AI-Bench, revision `76c93ec32536e29826e2ab9e68b58e0351410bc7` (MIT licence), cloned to `data/upstream/finrca-baseline` (ignored by git), checked out and verified with `git rev-parse HEAD`.
- Image `finrca-upstream-baselines:76c93ec` (id `sha256:608ec7538aff1b713807635ba3dbedf998808de530b734c979fb8fabbc7f795e`), built from `evaluation/baselines/finrca_upstream/Dockerfile`: `python:3.11.3-slim`, numpy 1.26.0, pandas 2.1.1, scikit-learn 1.4.1.post1, joblib 1.3.2, PyYAML 6.0.1, pytest 8.4.2. These match the versions recorded in the upstream run manifests (`results/*/run_manifest.json`). The base image tag was not pinned by digest.
- `requirements.txt` upstream uses ranges (and lists openai, pydantic, tiktoken, used only by the LLM baselines, which were not run and not installed).
- Hardware: Intel Core i7-5820K, 12 logical CPUs, 46 GB RAM, Linux 6.8.0-79 x86_64, Docker 29.2.1. Upstream published runs were on macOS 15.7.4 arm64, Python 3.11.3.
- Isolation: `--network none`, `--read-only` root, `--cap-drop ALL`, `no-new-privileges`, non-root user, checkout mounted read-only at `/src`, only `artifacts/m0-baselines/` writable at `/out`. No home directory or credentials mounted. The pip install happened at image build time (network); the runs had no network.

## Pre-run review of upstream code

Read before running: `README.md`, `requirements.txt`, `.env.example`, `src/rules_sql/{cli,evaluation,store}.py`, `src/classical_ml/{cli,data}.py`, and grepped `src/rules_sql`, `src/classical_ml`, `src/utils` for network, subprocess, environment and path use.

- No network access, uploads, downloads or credentials in either baseline. `OPENAI_API_KEY` is used only by the Direct-LLM/RAG/GraphRAG baselines (not run).
- Writes outside the run output directory: both CLIs overwrite upstream files in the repo root (`docs/rules_sql_baseline_evaluation_v1.0.md`, `docs/rules_sql_schema_mapping_v1.0.csv`, `docs/classical_ml_baseline_evaluation_v1.0.md`, `docs/ml_feature_pipeline_audit_v1.0.md`) and create `__pycache__`/pytest caches. These are in the checkout, not outside it. To keep the mount read-only, the container runs on a private copy in a tmpfs (`/work`).
- Subprocesses: both runners run `pytest` on their own tests; the ML runner calls `git rev-parse HEAD` (crashes if `git` is absent; see deviations).
- The ML runner reads upstream's preserved Rules/SQL run (`results/rules_sql/phase2_..._auditfix1/`) for its comparison; so the Rules/SQL-vs-ML comparison numbers use upstream's Rules/SQL predictions, not ours (ours are identical, below).
- Neither baseline references `challenge_test` (grep over `src/rules_sql src/classical_ml` returned nothing).

## Commands

```bash
cd /home/charalambos/Documents/fin-tech-projects/reconcile
git clone https://github.com/PratikGhawate/FinRCA-AI-Bench data/upstream/finrca-baseline
git -C data/upstream/finrca-baseline checkout 76c93ec32536e29826e2ab9e68b58e0351410bc7
docker build -t finrca-upstream-baselines:76c93ec evaluation/baselines/finrca_upstream
mkdir -p artifacts/m0-baselines
docker run --rm --network none --read-only --user $(id -u):$(id -g) --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /work:exec,uid=$(id -u),size=3g --tmpfs /tmp:size=512m \
  -e HOME=/tmp -e RUN_TAG=20261101 \
  -v "$PWD/data/upstream/finrca-baseline:/src:ro" -v "$PWD/artifacts/m0-baselines:/out" \
  finrca-upstream-baselines:76c93ec
```

`run_baselines.sh` (in the image) copies `/src` into `/work`, excluding `.git`, `data/benchmark/challenge_test`, `data/benchmark/internal` (mutation log), `data/raw_clean` and the Direct-LLM/RAG/GraphRAG results; then runs the upstream commands unchanged except for output paths:

```bash
python -m src.rules_sql.cli run --data-root data/benchmark --output-root /out/rules_sql --run-id rules_sql_repro_$RUN_TAG
python -m src.classical_ml.cli run --data-root data/benchmark --output-root /out/classical_ml --run-id classical_ml_repro_$RUN_TAG
```

`PYTHONHASHSEED=314159` was set globally (the upstream ML test subprocess sets it). `STAGES="classical_ml"` selects one stage. `audit_run.py` wraps each CLI with a Python audit hook that logs every file opened under `data/` (`files_read_*.txt`). The hook does not see the pytest subprocesses (they use synthetic fixtures).

## Experiments run

1. Frozen Rules/SQL baseline (Phase 2): spec checksum, 40 upstream tests (passed), F01 validation grid (12 configs), test inference, evaluation.
2. Frozen classical ML baseline (Phase 3): checksums, 20 upstream tests (passed), train/validation features, 22-config validation grid, test inference, evaluation.

Not run: Direct LLM (Phase 4), RAG (Phase 5), GraphRAG (Phase 6b) (need paid API/credentials; out of M0 task scope); `classical_ml.cli verify`; the dataset generator; any `challenge_test` use.

Deviation: the first ML attempt failed with `FileNotFoundError: 'git'` (slim image has no git). apt-get in the base image failed (exit 100), so the image carries a stub `git` that exits 128; the runner then records `UNAVAILABLE_NOT_A_GIT_WORKTREE`. No effect on modelling. Rules/SQL was run once; ML rerun once after the stub (the Rules/SQL run was not repeated).

## Results (439 test cases: 144 NO_FAILURE, 295 failures)

"Published" = upstream's own reports in the checkout (`docs/*_baseline_evaluation_v1.0.md`, `results/...auditfix1/`). The paper (arXiv 2608.18534) could not be fetched from this environment (WebFetch blocked); the paper's numbers have NOT been compared. Published figures below are from the upstream repository.

| Metric | Rules/SQL ours | Rules/SQL published | ML ours (HistGB) | ML published |
|---|---|---|---|---|
| Exact 16-class accuracy | 0.849658 (373/439) | 0.849658 | 0.954442 (419/439) | 0.9544 |
| Macro F1 | 0.951773 | 0.951773 | 0.962393 | 0.9624 |
| Binary anomaly F1 | 0.980066 | 0.980066 | 0.966330 | 0.9663 |
| Binary precision / recall | 0.9609 / 1.0000 | same | 0.9599 / 0.9729 (TP 287, FP 12, FN 8, TN 132) | 0.9599 / 0.9729 |
| False-positive rate | 12/144 = 0.0833 | 0.0833 | 12/144 = 0.0833 | 0.0833 |
| INSUFFICIENT_EVIDENCE | 50 | 50 | n/a | n/a |
| ML-vs-Rules McNemar | | | 58 ML-only, 12 rules-only, p = 2.249e-08 | same |
| Evidence: correct anomaly predictions with complete evidence | 38/291 = 0.1306 | 0.1306 | none returned | none returned |

Counts derived as accuracy x 439 (373 = 0.849658 x 439; 419 = 0.954442 x 439).

Three-family subset (PLAN.md F06, F12, F14; per-class recall numerators; test counts 12/21/16):

| Family | Rules/SQL recall | Rules/SQL precision | ML recall | ML precision |
|---|---|---|---|---|
| F06 invoice paid twice | 10/12 | 10/10 | 12/12 | 12/12 |
| F12 ERP payment missing from bank | 19/21 | 19/19 | 21/21 | 21/21 |
| F14 bank/ERP amount mismatch | 16/16 | 16/16 | 16/16 | 16/20 (4 FP) |

Hidden confusions are in `confusion_matrix.csv` and `error_analysis.csv` in the run directories. The three-family subset has 49 positive cases; no uncertainty is given here.

## Differences and investigation

- Rules/SQL: `predictions.jsonl` case IDs, predicted types and statuses identical for all 439 cases; `metrics.json`, per-failure/tier/hop metrics, tri-state, collisions, error analysis, evidence metrics byte-identical to upstream. Model-visible dataset SHA-256 `d8dcd682...b7c662` equals the published one; selected F01 parameters (7 days, similarity 1.00) equal. Only latency differs (mean 23.1 ms vs published 9.83 ms; Linux x86 container vs macOS arm64).
- ML: predicted labels identical for all 439 cases; class probabilities differ by at most 4.3e-19; test metrics, confusion matrix, calibration, McNemar, bootstrap, robustness and `features_test.csv` byte-identical. Selected model `hist_gradient_boosting_06`, validation macro F1 0.97266, equals published. Top-5 validation ranking identical.
- Unexplained small difference: validation metrics for 7 of 22 candidates (2 logistic regression: 03, 05; 5 random forest: 01, 03, 04, 05, 08) differ from upstream by up to about 0.005 macro F1 (e.g. random_forest_08 0.9306 vs 0.9258; logistic_regression_03 0.8849 vs 0.8837). None changes the selection, and all 8 HistGB candidates match. Plausible cause: different BLAS/solver/threading numerics on x86 versus arm64 (unverified; not tested further). Test predictions are unaffected because only the winner is applied to test.
- The agreement is with upstream's own recorded results on the same dataset snapshot (same `data/benchmark` in the checkout), not an independent regeneration; the dataset generator was not run, so generation determinism is not tested here.

## Label and answer files read

From the audit hook (`artifacts/m0-baselines/files_read_*.txt`), all under `data/benchmark/` of the checkout copy:

- Both runs: `full/*.csv` (14 operational tables), `{validation,test}/case_ids.txt`, `{validation,test}/benchmark_questions.jsonl`, `{validation,test}/failure_manifest.csv`.
- Rules/SQL additionally: `test/rca_ground_truth.jsonl` (evidence ID scoring), and its F01 grid selection reads validation labels.
- ML additionally: `train/case_ids.txt`, `train/benchmark_questions.jsonl`, `train/failure_manifest.csv`, `dataset_quality_report.json`.
- Not present/read: `challenge_test/` (excluded from the copy; the checkout was mounted read-only and `git status` of the checkout is clean), `internal/mutation_log.jsonl`, `causal_edges.csv`, `raw_clean/`.
- The `benchmark_questions.jsonl` files contain expected answers; the upstream code extracts only `case_id` and `primary_entity.{type,id}`.

Consequence (PLAN.md section 2): the upstream `test` split (439 cases, `data/benchmark/test/`), and the train and validation splits, are now a reproduction set and must not be called unseen. The challenge split `data/benchmark/challenge_test/` (203 cases, files `case_ids.txt`, `benchmark_questions.jsonl`, `case_entity_records.jsonl`, `failure_manifest.csv`, `rca_ground_truth.jsonl`) was not touched.

## Privileged or oracle inputs

For a faithful published-task reproduction these are inputs the upstream task provides; an operational system would not necessarily have them:

1. Routing metadata: `primary_entity_type` and `primary_entity_id` taken from the benchmark question rows. The entity type is chosen by the benchmark's failure injection and encodes class: in `train/failure_manifest.csv`, `gl_journal` -> F10 59/59, `bank_transaction` -> F13 or NO_FAILURE only, and an entity-type-only majority rule gets 0.397 exact accuracy on train. `primary_entity_type` is ML feature CAT001 and is among the top permutation-importance features (`categorical__primary_entity_type_GL_JOURNAL`). Rules/SQL uses the same routing to choose which rules apply.
2. Case-scoped, generated snapshot: `full/` contains all splits and the post-injection world. Cutoff `AS_OF_DATE` is 2026-06-30; no availability timestamps (upstream spec uses a snapshot assumption).
3. Upstream excludes some risky fields (`FORBIDDEN_FIELDS`: `invoices.duplicate_reference`, `audit_log.field/old_value/new_value/actor_id`, comments, memos), but `audit_log` event types (e.g. `FX_CONVERSION_APPLIED`) and other generator-created audit records are used, and have not been audited for class-encoding artifacts yet (to be done in the data audit).
4. Evidence correctness for Rules/SQL is checked against `rca_ground_truth.jsonl` required evidence IDs (evaluator side only, after predictions); the ML baseline returns no evidence at all, so its correct classifications are not evidence-complete.
5. The ML grid uses train and validation labels (allowed under the declared protocol) and the test routing; no test labels were used for selection (by upstream design; not independently verified beyond the read log, which cannot show read order).

An observable-input counterpart (neutral request, no entity-type routing) is not built here.

## Limitations

- Same-dataset reproduction only; the paper was not accessible, so paper-vs-repo consistency is unchecked.
- Linux x86 container vs published macOS arm64; latency not comparable.
- Base image not digest-pinned; pip install requires network at build time.
- The audit-hook log records files opened, not read order, and does not cover pytest subprocesses.
- Per-class numbers for 12-21 cases have wide uncertainty; no confidence intervals computed for the subset.
- Rules/SQL single run; no repeated-seed variance (rules are deterministic).
- Upstream's own docs were overwritten only inside the container copy; the checkout is unmodified.

## Artifacts

`artifacts/m0-baselines/` (ignored): `rules_sql/rules_sql_repro_20261101/`, `classical_ml/classical_ml_repro_20261101/` (predictions, metrics, locked model files, manifests), `files_read_*.txt`, `*.stdout.txt`, `docker_run*.log`.
