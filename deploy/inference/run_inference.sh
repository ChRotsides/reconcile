#!/bin/bash
# Usage:
#   run_inference.sh --build
#   run_inference.sh SNAPSHOT_DIR REQUESTS_DIR OUT_DIR        # OUT_DIR must not exist
#   ABSENT_PATHS=/a/key:/b/links.jsonl run_inference.sh --check SNAPSHOT_DIR REQUESTS_DIR OUT_DIR
# Mounts exactly three host directories: the snapshot (read-only), the requests
# (read-only, holds requests.jsonl) and one fresh output directory (writable).
# --check runs an absence check inside the container instead of the batch.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE="${IMAGE:-reconcile-inference:dev}"

if [[ "${1:-}" == "--build" ]]; then
  exec docker build -f "$HERE/Dockerfile" -t "$IMAGE" "$HERE/../../src"
fi

check=0
if [[ "${1:-}" == "--check" ]]; then check=1; shift; fi
[[ $# -eq 3 ]] || { echo "usage: $0 [--check] SNAPSHOT_DIR REQUESTS_DIR OUT_DIR" >&2; exit 2; }
SNAP="$(cd "$1" && pwd)"; REQ="$(cd "$2" && pwd)"
[[ -f "$SNAP/snapshot.sqlite" ]] || { echo "no snapshot.sqlite in $SNAP" >&2; exit 1; }
[[ -f "$REQ/requests.jsonl" ]] || { echo "no requests.jsonl in $REQ" >&2; exit 1; }
mkdir -p "$(dirname "$3")"
mkdir "$3"   # fails if it exists: the output directory is always fresh
OUT="$(cd "$3" && pwd)"

# Paths that must not exist in the container. ABSENT_PATHS adds host paths
# (the key file, links, the upstream checkout, ...), colon-separated.
CHECK='
bad=0
for p in /in/snapshot/links.jsonl /in/requests/links.jsonl /out/links.jsonl \
         /app/evaluation /app/tests /app/fixtures /app/reconcile/adapters \
         /src /evaluation /tests /fixtures /data /app/data /work \
         $(echo "$ABSENT_PATHS" | tr : " "); do
  if test -e "$p"; then echo "PRESENT $p"; bad=1; else echo "absent  $p"; fi
done
for p in /in/snapshot/snapshot.sqlite /in/snapshot/manifest.json /in/requests/requests.jsonl /out; do
  if test -e "$p"; then echo "present $p (expected)"; else echo "MISSING $p"; bad=1; fi
done
exit $bad'

ep=(); cmd=(/in/snapshot /in/requests/requests.jsonl /out)
if [[ $check -eq 1 ]]; then ep=(--entrypoint sh); cmd=(-c "$CHECK"); fi
exec docker run --rm --network none --read-only --user "$(id -u):$(id -g)" --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:size=64m -e HOME=/tmp \
  -e ABSENT_PATHS="${ABSENT_PATHS:-}" \
  -v "$SNAP:/in/snapshot:ro" -v "$REQ:/in/requests:ro" -v "$OUT:/out" \
  "${ep[@]}" "$IMAGE" "${cmd[@]}"
