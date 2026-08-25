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
MEASURED_FRAMES=1800
MODE="placeholder"
RUN_GATE=0
SMOKE_SECS="${RENPY_HOST_SMOKE_SECS:-10}"
GATE_NAME="${RENPY_HOST_GATE:-hmc_menu_video_product}"
OUT="$OUT_DEFAULT"

_usage() {
  cat <<'USAGE'
Usage: benchmark_bc160.sh [--help] [--placeholder] [--measured] [--measured-frames N] [--run-gate] [--out PATH]

  --placeholder       Write explicit NOT_MEASURED metrics JSON (default).
                      Cannot be mistaken for release evidence.
  --measured          Run --benchmark $MEASURED_FRAMES frames and write MEASURED metrics with real FPS/avg_ns.
  --measured-frames N Frames for --measured (default 1800).
  --run-gate          Optionally run RENPY_HOST_GATE once for health only (placeholder only).
  --out PATH          Metrics output path (default: host/target/bc160_perf_metrics.json)
  --help              Show this help.

Honesty contract:
  - placeholder never writes measured FPS; measured requires real cargo --benchmark collector.
  - measured passes only if average_fps >=60 then PERFORMANCE_TARGET_MET and eligible true.
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
    --measured)
      MODE="measured"
      shift
      ;;
    --measured-frames)
      [[ $# -ge 2 ]] || { echo "ERROR: --measured-frames requires N" >&2; exit 2; }
      MEASURED_FRAMES="$2"
      shift 2
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
if [[ "$MODE" == "measured" ]]; then
  echo "--- Measured benchmark ($MEASURED_FRAMES frames) ---"
  BENCH_JSON="/tmp/bench_${MEASURED_FRAMES}.json"
  set +e
  cargo run -p renpy-host -- --benchmark --benchmark-frames "$MEASURED_FRAMES" --output "$BENCH_JSON"
  BENCH_EXIT=$?
  set -e
  if [[ $BENCH_EXIT -ne 0 ]]; then
    echo "ERROR: benchmark run failed exit $BENCH_EXIT" >&2
    exit $BENCH_EXIT
  fi
  if [[ ! -f "$BENCH_JSON" ]]; then
    echo "ERROR: benchmark output $BENCH_JSON not found" >&2
    exit 1
  fi
  # Parse with python3
  PY_OUT=$(python3 - <<PY
import json, pathlib, sys
p = pathlib.Path("$BENCH_JSON")
j = json.loads(p.read_text())
# host benchmark writes {frames, total_time_sec, avg_frame_time_ms, ...} or similar
frames = j.get("frames") or j.get("benchmark_frames") or $MEASURED_FRAMES
total = j.get("total_time_sec") or j.get("total_time") or 0
avg_ms = j.get("avg_frame_time_ms") or j.get("avg_ms") or None
if avg_ms is not None:
    avg_ns = int(float(avg_ms) * 1e6)
    fps = 1000.0 / float(avg_ms) if float(avg_ms) > 0 else 0
elif total and frames:
    fps = frames / float(total)
    avg_ns = int((float(total) / frames) * 1e9)
else:
    fps = 0
    avg_ns = 0
# New fields from main.rs bench JSON (may be null)
one_low = j.get("one_percent_low_fps")
render_ns = j.get("render_pass_duration_ns")
# Normalize: bench writes number or null; ensure python prints "null" or number
one_low_str = "null" if one_low is None else str(float(one_low))
render_str = "null" if render_ns is None else str(int(render_ns))
print(f"{fps:.2f} {avg_ns} {frames} {total} {one_low_str} {render_str}")
PY
)
  FPS=$(echo "$PY_OUT" | awk '{print $1}')
  AVG_NS=$(echo "$PY_OUT" | awk '{print $2}')
  FRAMES=$(echo "$PY_OUT" | awk '{print $3}')
  ONE_LOW=$(echo "$PY_OUT" | awk '{print $5}')
  RENDER_NS=$(echo "$PY_OUT" | awk '{print $6}')
  # Threshold: 60 fps
  PASS=$(python3 -c "import sys; fps=float(sys.argv[1]); print('true' if fps>=60 else 'false')" "$FPS")
  if [[ "$PASS" == "true" ]]; then
    PSTATUS="PERFORMANCE_TARGET_MET"
    REL="true"
  else
    PSTATUS="PERFORMANCE_TARGET_NOT_MET"
    REL="false"
  fi
  TS_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  mkdir -p "$(dirname "$OUT")"
  cat >"$OUT" <<EOF2
{
  "schema": "bc160_perf_metrics.v1",
  "gpu_target": "AMD BC-160 (Navi 12 / Radeon Pro 5600M)",
  "vulkan_driver": "radv / Mesa (declared, not probed)",
  "timestamp_utc": "$TS_UTC",
  "measurement_status": "MEASURED",
  "release_evidence_eligible": $REL,
  "average_fps": $FPS,
  "one_percent_low_fps": $ONE_LOW,
  "frame_presentation_time_ns": $AVG_NS,
  "render_pass_duration_ns": $RENDER_NS,
  "render_pass_cpu_proxy": true,
  "pass_status": "$PSTATUS",
  "notes": [
    "Measured via cargo run --benchmark (host native).",
    "Frames=$FRAMES source=$BENCH_JSON",
    "render_pass cpu_proxy (wgpu TIMESTAMP_QUERY not wired; Instant proxy, see main.rs benchmark_render_pass_total)"
  ],
  "optional_gate": {
    "ran": $([[ "$RUN_GATE" -eq 1 ]] && echo true || echo false),
    "name": $([[ "$RUN_GATE" -eq 1 ]] && printf '%s' "\"$GATE_NAME\"" || echo null),
    "exit_code": $([[ -n "$GATE_EXIT" ]] && echo "$GATE_EXIT" || echo null),
    "command": $([[ -n "$GATE_COMMAND" ]] && python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$GATE_COMMAND" || echo null)
  },
  "benchmark_source": "$BENCH_JSON"
}
EOF2
  echo "Wrote MEASURED metrics to $OUT (fps=$FPS avg_ns=$AVG_NS one_low=$ONE_LOW render_ns=$RENDER_NS cpu_proxy=true eligible=$REL status=$PSTATUS)"
  exit 0
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
