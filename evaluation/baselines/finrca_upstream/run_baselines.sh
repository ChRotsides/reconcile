#!/bin/bash
# Runs inside the container. /src holds ONLY the allowlisted read-only mounts made by
# run_docker.sh (no challenge_test, no combined top-level answer files); /out is the
# only writable mount. Upstream writes into docs/ and __pycache__ inside its repo
# root, so we run on a private in-container copy of /src.
set -euo pipefail
RUN_TAG="${RUN_TAG:?set RUN_TAG}"
RUN_OUT="/out/${RUN_TAG}"
# No -p: refuses to start if this run's directory exists, so earlier evidence is never overwritten.
mkdir "$RUN_OUT" || { echo "refusing to start: $RUN_OUT already exists" >&2; exit 2; }
exec > >(tee "$RUN_OUT/run.stdout.txt") 2>&1
mkdir -p /work && cd /work
tar -C /src -cf - . | tar -xf -
export PYTHONHASHSEED=314159 PYTHONPATH=/work
STAGES="${STAGES:-rules_sql classical_ml}"
if [[ " $STAGES " == *" rules_sql "* ]]; then
READ_LOG="$RUN_OUT/files_read_rules_sql.txt" python /opt/audit_run.py src.rules_sql.cli run \
    --data-root data/benchmark --output-root "$RUN_OUT/rules_sql" --run-id "rules_sql_repro_${RUN_TAG}"
fi
if [[ " $STAGES " == *" classical_ml "* ]]; then
READ_LOG="$RUN_OUT/files_read_classical_ml.txt" python /opt/audit_run.py src.classical_ml.cli run \
    --data-root data/benchmark --output-root "$RUN_OUT/classical_ml" --run-id "classical_ml_repro_${RUN_TAG}"
fi
