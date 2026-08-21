#!/usr/bin/env bash
# The hour of GPU this repo still owes, as one command.
#
# On a rented Linux box with one NVIDIA GPU, the NVIDIA Container Toolkit, docker
# compose, and an nvcr.io login:
#
#   ./run-on-gpu.sh              # break all four, capture the four FAILs
#   ./run-on-gpu.sh --healthy 3  # make boundary 3 healthy, leave the rest broken
#
# Writes runs/<utc-timestamp>/ containing the preflight output, the raw metrics
# scrape, and the versions everything actually ran at. COMMIT that directory. The
# problem this repo has always had is that no evidence exists, so the evidence is
# tracked on purpose rather than gitignored as scratch.
#
# Nothing here edits the README for you. A result belongs in the docs once a human
# has read it, and the README should cite the run directory it came from.
set -euo pipefail
cd "$(dirname "$0")"

HEALTHY=""
[ "${1:-}" = "--healthy" ] && HEALTHY="${2:-}"

die() { echo "  $*" >&2; exit 1; }
echo "== preconditions"
command -v docker >/dev/null || die "docker is not installed."
docker info >/dev/null 2>&1 || die "docker is not running."
command -v nvidia-smi >/dev/null \
  || die "no nvidia-smi. Boundaries 1 and 4 need a real GPU: boundary 4 forces a
  preemption by filling the KV cache, and there is nothing to fill without one.
  Boundaries 2 and 3 run anywhere; use \`python preflight.py --self-test\` for those."
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1 \
  || die "docker cannot see the GPU. Install the NVIDIA Container Toolkit:
  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
echo "  ok  $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)"

# All four break by default; --healthy N flips exactly one off.
export BREAK_ROUTER=1 BREAK_VERSION_PIN=1 BREAK_METRICS=1 BREAK_TRACKER=1
case "$HEALTHY" in
  1) BREAK_ROUTER=0;; 2) BREAK_VERSION_PIN=0;; 3) BREAK_METRICS=0;; 4) BREAK_TRACKER=0;;
  "") ;;
  *) die "--healthy takes 1, 2, 3 or 4";;
esac
[ -n "$HEALTHY" ] && echo "  boundary $HEALTHY healthy, the other three broken" \
                  || echo "  all four broken"

OUT="runs/$(date -u +%Y-%m-%dT%H%M%SZ)${HEALTHY:+-healthy$HEALTHY}"
mkdir -p "$OUT"

echo "== stack"
docker compose -f broken-stack/compose.yml up -d
# Triton pip-installs vLLM at container start, so first boot is minutes, not seconds.
echo "  .. waiting for the router (Triton installs vLLM on boot; allow ~5 min cold)"
for _ in $(seq 1 120); do
  curl -sf http://localhost:8000/v1/models >/dev/null 2>&1 && break
  sleep 5
done
curl -sf http://localhost:8000/v1/models >/dev/null 2>&1 || {
  docker compose -f broken-stack/compose.yml logs --tail 40 triton > "$OUT/triton.log"
  echo "  router never answered. With BREAK_VERSION_PIN=1 that is boundary 2 doing"
  echo "  exactly what it is supposed to do: 0.11.1 deleted vllm.engine.metrics and"
  echo "  the backend cannot load. Logs in $OUT/triton.log"
}

echo "== evidence"
{ nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
  docker compose -f broken-stack/compose.yml exec -T triton python -c \
    "import vllm; print('vllm', vllm.__version__)" 2>/dev/null || echo "vllm unimportable"
  echo "flags: ROUTER=$BREAK_ROUTER PIN=$BREAK_VERSION_PIN METRICS=$BREAK_METRICS TRACKER=$BREAK_TRACKER"
} > "$OUT/versions.txt"
curl -s http://localhost:9091/metrics > "$OUT/metrics-proxy.txt" || true
curl -s http://localhost:9090/api/v1/label/__name__/values > "$OUT/prometheus-names.json" || true

echo "== preflight"
set +e
python preflight.py \
  --base-url http://localhost:8000/v1 --model research \
  --metrics http://localhost:9091/metrics --vllm-version 0.11.0 \
  2>&1 | tee "$OUT/preflight.txt"
rc=${PIPESTATUS[0]}
set -e
echo
echo "  exit $rc  ->  $OUT/preflight.txt"
echo "  paste that file into the README, with $OUT/versions.txt beside it."
exit "$rc"
