# Inference and evaluator isolation

Status at M2. Rationale: `PLAN.md` section 2, "Input and label separation". Directory names below are chosen by the caller of the preparation functions; the paths are the project's convention, not enforced.

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

## Implemented (M2): container with three mounts

`deploy/inference/run_inference.sh` runs `python -m reconcile.runs.batch` in the image built from `deploy/inference/Dockerfile`:

```bash
deploy/inference/run_inference.sh --build        # context is src/ only
deploy/inference/run_inference.sh SNAPSHOT_DIR REQUESTS_DIR OUT_DIR   # OUT_DIR must not exist
```

which executes

```bash
docker run --rm --network none --read-only --user "$(id -u):$(id -g)" --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:size=64m -e HOME=/tmp \
  -v SNAPSHOT_DIR:/in/snapshot:ro -v REQUESTS_DIR:/in/requests:ro -v OUT_DIR:/out \
  reconcile-inference:dev /in/snapshot /in/requests/requests.jsonl /out
```

- Mounts: the snapshot directory (read-only), the requests directory (read-only), one fresh output directory (writable). Nothing else. The script creates `OUT_DIR` with `mkdir` and fails if it exists.
- Image: `python:3.12-slim` plus the `reconcile` package copied from `src/` (no runtime dependencies), with `reconcile/adapters/` deleted. No dev tools, `evaluation/`, tests, fixtures, key handling or upstream readers. The build context is `src/`, so nothing else can be copied in.
- Output: `packets.jsonl`, one packet per request sorted by `request_id`, written atomically. An unexpected exception on one request becomes a `failed` packet with the exception type name only. A malformed or duplicate-ID requests file stops the run before anything is written.
- Check mode: `ABSENT_PATHS=<colon-separated host paths> run_inference.sh --check SNAPSHOT_DIR REQUESTS_DIR OUT_DIR` runs `test -e` inside the container (same mounts) on a fixed list (links, `evaluation/`, tests, fixtures, `/src`, `/data`, the adapters) plus the caller's paths, and on the mounted files as a positive control.
- `tests/integration/test_inference_container.py` (skipped without Docker) runs the check with the key file, `links.jsonl`, the upstream checkout, `challenge_test`, `evaluation/`, `tests/`, `fixtures/` and `src/` host paths, requires every one to be absent and the snapshot and requests present, confirms `reconcile.adapters` is not importable in the image, and requires the container's `packets.jsonl` to be byte-identical to an in-process `run_requests` on the same inputs.

What the absence check proves: in a container started by this script, those paths do not exist at the paths tested, so code in the container cannot open them there. It does not prove that nothing else sensitive is reachable, only that the listed paths and the image contents are as stated.

## Still an assumption

- The operator passes the right directories. The script checks only that `snapshot.sqlite` and `requests.jsonl` exist in them; it cannot know that a directory does not also hold a key or links file. Preparation refuses to put the key or links inside those directories, but a hand-made directory is not checked.
- The container runtime enforces `--network none`, `--read-only` and the mount modes as documented; this was not independently tested beyond the check above.
- The image is built from `python:3.12-slim` pulled at build time; its digest is not pinned.

## Enforced today

- `tests/unit/test_import_boundaries.py` (static, `ast` and text scan of `src/reconcile/`):
  - no module imports `evaluation`;
  - no module names a label or answer file (`rca_ground_truth`, `failure_manifest`, `causal_edges`, `mutation_log`, `challenge_test`, `links.jsonl`, `benchmark_questions`), except `adapters/finrca_requests.py`, which reads `benchmark_questions.jsonl`, writes `links.jsonl` and names `challenge_test` to refuse it;
  - no inference module imports the preparation adapters (`adapters/finrca.py`, `adapters/finrca_requests.py`), which hold key handling and read upstream files.
- Preparation refusals: the `challenge_test` split is refused before any file is touched; the key file may not lie inside the output directories (snapshot, requests, links); the snapshot `out_dir` must not already exist; request outputs refuse to overwrite existing files.

## Still an assumption: in-process tests (A024)

The tests are a guard against accidents. They do not stop dynamic imports, strings built at runtime, or a process that simply opens a path it can reach. Isolation of a real run rests on the container above and on the operator passing the right directories (A024: partly verified, see above).
