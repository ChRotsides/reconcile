#!/bin/bash
# Usage: RUN_TAG=<unique tag> [STAGES="rules_sql classical_ml"] run_docker.sh
# Mounts an explicit allowlist of read-only subpaths of the upstream checkout at /src.
# The combined top-level answer files and challenge_test are never mounted, so the
# container cannot see them. With arguments, the first replaces the entrypoint (for checks):
#   run_docker.sh bash -c 'test -e /src/data/benchmark/challenge_test || echo absent'
set -euo pipefail
RUN_TAG="${RUN_TAG:-}"   # required by run_baselines.sh in the container; optional for checks
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
UP="$REPO/data/upstream/finrca-baseline"
OUT="$REPO/artifacts/m0-baselines"
RULES_RUN=phase2_rules_sql_v1_0_20260808T185000Z_auditfix1   # RULES_RUN_NAME in upstream classical_ml/cli.py
mkdir -p "$OUT"
ro=()
for p in src tests docs data/benchmark/full data/benchmark/train data/benchmark/validation \
         data/benchmark/test data/benchmark/dataset_quality_report.json "results/rules_sql/$RULES_RUN"; do
  [[ -e "$UP/$p" ]] || { echo "missing allowlisted path $p" >&2; exit 1; }
  ro+=(-v "$UP/$p:/src/$p:ro")
done
ep=(); [[ $# -gt 0 ]] && { ep=(--entrypoint "$1"); shift; }
exec docker run --rm --network none --read-only --user "$(id -u):$(id -g)" --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs "/work:exec,uid=$(id -u),size=3g" --tmpfs /tmp:size=512m \
  -e HOME=/tmp -e RUN_TAG="$RUN_TAG" -e STAGES="${STAGES:-rules_sql classical_ml}" \
  "${ro[@]}" -v "$OUT:/out" "${ep[@]}" finrca-upstream-baselines:76c93ec "$@"
