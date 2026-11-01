#!/bin/bash
# /src is the read-only upstream checkout; /out is the only writable mount.
# Upstream writes into docs/ and __pycache__ inside its repo root, so we run on a
# private in-container copy. The copy omits the challenge split, internal
# provenance, clean data, and unrelated results.
set -euo pipefail
RUN_TAG="${RUN_TAG:?set RUN_TAG}"
mkdir -p /work && cd /work
tar -C /src --exclude=.git --exclude=data/benchmark/challenge_test --exclude=data/benchmark/internal \
    --exclude=data/raw_clean --exclude=results/direct_llm --exclude=results/rag --exclude=results/graphrag \
    -cf - . | tar -xf -
export PYTHONHASHSEED=314159 PYTHONPATH=/work
STAGES="${STAGES:-rules_sql classical_ml}"
if [[ " $STAGES " == *" rules_sql "* ]]; then
export READ_LOG=/out/files_read_rules_sql.txt
python /opt/audit_run.py src.rules_sql.cli run --data-root data/benchmark \
    --output-root /out/rules_sql --run-id "rules_sql_repro_${RUN_TAG}" 2>&1 | tee /out/rules_sql.stdout.txt
fi
if [[ " $STAGES " == *" classical_ml "* ]]; then
export READ_LOG=/out/files_read_classical_ml.txt
python /opt/audit_run.py src.classical_ml.cli run --data-root data/benchmark \
    --output-root /out/classical_ml --run-id "classical_ml_repro_${RUN_TAG}" 2>&1 | tee /out/classical_ml.stdout.txt
fi
