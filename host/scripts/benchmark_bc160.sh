#!/usr/bin/env bash
# benchmark_bc160.sh - BC-160 (Navi 12) performance harness (honesty-first).
#
# This script MUST NOT mint release-grade PERFORMANCE_TARGET_MET evidence from
# hardcoded FPS numbers. Until a real measurement path is wired and verified,
# outputs are explicitly NOT_MEASURED / NOT_RELEASE_EVIDENCE.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOST_DIR="$ROOT/host"

OUT_DEFAULT="$HOST_DIR/target/bc160_perf_metrics.json"
MODE="placeholder"
RUN_GATE=0
SMOKE_SECS="${RENPY_HOST_SMOKE_SECS:-10}"
GATE_NAME="${RENPY_HOST_GATE:-hmc_menu_video_product}"
OUT="$OUT_DEFAULT"

_usage() {
  cat <<'USAGE'
Usage: benchmark_bc160.sh [--help] [--placeholder] [--run-gate] [--out PATH]

  --placeholder   Write explicit NOT_MEASURED metrics JSON (default).
                  Cannot be mistaken for release evidence.
  --run-gate      Optionally run RENPY_HOST_GATE once for health only.
                  Still writes NOT_MEASURED metrics (does not invent FPS).
  --out PATH      Metrics output path (default: host/target/bc160_perf_metrics.json)
  --help          Show this help.

Honesty contract:
  - Never writes average_fps / 1_percent_low_fps as measured values without a
    real collector.
  - pass_status is never PERFORMANCE_TARGET_MET.
  - release_evidence_eligible is always false from this script.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      _usage
      exit 0
      ;;
    --placeholder)
      MODE="placeholder"
      shift
      ;;
    --run-gate)
      RUN_GATE=1
      shift
      ;;
    --out)
      [[ $# -ge 2 ]] || { echo "ERROR: --out requires PATH" >&2; exit 2; }
      OUT="$2"
      shift 2
      ;;
    --out=*)
      OUT="${1#--out=}"
      shift
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      _usage >&2
      exit 2
      ;;
  esac
done

# Resolve relative --out against repo root (not cwd after cd host).
if [[ "$OUT" != /* ]]; then
  OUT="$ROOT/$OUT"
fi

cd "$HOST_DIR"

export RUST_LOG="${RUST_LOG:-info,wgpu_hal=off,wgpu_core=off,naga=off}"
export RENPY_HOST_BASE="$ROOT"
export PYTHONPATH="${ROOT}/host/python/gates${PYTHONPATH:+:$PYTHONPATH}"

echo "=== BC-160 Native GPU Performance Harness (honesty-first) ==="
GPU_INFO=$(lspci 2>/dev/null | grep -i "VGA compatible controller.*Navi 12" || true)
if [[ -z "$GPU_INFO" ]]; then
  GPU_INFO="AMD Radeon Pro BC-160 / Navi 12 (lspci Navi 12 not matched)"
fi
echo "Target GPU: $GPU_INFO"
echo "Mode: $MODE  run_gate=$RUN_GATE"

GATE_EXIT=""
GATE_COMMAND=""
if [[ "$RUN_GATE" -eq 1 ]]; then
  echo "--- Optional gate health run (not a FPS measurement) ---"
  GATE_COMMAND="RENPY_HOST_GATE=${GATE_NAME} RENPY_HOST_SMOKE_SECS=${SMOKE_SECS} cargo run -p renpy-host"
  set +e
  RENPY_HOST_GATE="$GATE_NAME" RENPY_HOST_SMOKE_SECS="$SMOKE_SECS" cargo run -p renpy-host
  GATE_EXIT=$?
  set -e
  echo "gate_exit_code=$GATE_EXIT (health only; metrics remain NOT_MEASURED)"
fi

mkdir -p "$(dirname "$OUT")"
TS_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
# JSON nulls for unmeasured numeric fields — never hardcoded FPS.
cat >"$OUT" <<EOF
{
  "schema": "bc160_perf_metrics.v1",
  "gpu_target": "AMD BC-160 (Navi 12 / Radeon Pro 5600M)",
  "vulkan_driver": "radv / Mesa (declared, not probed)",
  "timestamp_utc": "$TS_UTC",
  "measurement_status": "NOT_MEASURED",
  "release_evidence_eligible": false,
  "average_fps": null,
  "one_percent_low_fps": null,
  "frame_presentation_time_ns": null,
  "render_pass_duration_ns": null,
  "pass_status": "INCONCLUSIVE_NOT_RELEASE_EVIDENCE",
  "notes": [
    "Placeholder metrics only. Hardcoded PERFORMANCE_TARGET_MET paths were removed in L5 closeout.",
    "Do not treat this file as AC-6 / release SSOT evidence.",
    "Wire a real frame-time collector before claiming BC-160 performance PASS."
  ],
  "optional_gate": {
    "ran": $([[ "$RUN_GATE" -eq 1 ]] && echo true || echo false),
    "name": $([[ "$RUN_GATE" -eq 1 ]] && printf '%s' "\"$GATE_NAME\"" || echo null),
    "exit_code": $([[ -n "$GATE_EXIT" ]] && echo "$GATE_EXIT" || echo null),
    "command": $([[ -n "$GATE_COMMAND" ]] && python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$GATE_COMMAND" || echo null)
  }
}
EOF

echo "Wrote non-authoritative placeholder metrics to $OUT"
echo "pass_status=INCONCLUSIVE_NOT_RELEASE_EVIDENCE release_evidence_eligible=false"
# Always exit 0 for placeholder honesty path; gate failure is recorded in JSON.
# Callers must not interpret this script exit as PERFORMANCE_TARGET_MET.
exit 0
