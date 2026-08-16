#!/usr/bin/env bash
# Phase 5 gate runner — dissolve/blur/matrixcolor/mask/rtt/readback + G03/G04 + ldd.
# Authority: .omc/plans/consensus-wgpu-native-vulkan-rewrite.md §5 Phase 5 / §6.1.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/host"
BIN=target/debug/renpy-host
export RUST_LOG="${RUST_LOG:-info,wgpu_hal=off,wgpu_core=off,naga=off}"
export RENPY_HOST_BASE="$ROOT"
# gates/ import path for golden_mae
export PYTHONPATH="${ROOT}/host/python/gates${PYTHONPATH:+:$PYTHONPATH}"

echo "== build =="
cargo build -p renpy-host

echo "== ldd no libSDL* =="
if ldd "$BIN" | grep -iE 'libSDL'; then
  echo "FAIL: SDL linked"
  ldd "$BIN"
  exit 1
fi
echo "OK: no libSDL*"

run_gate() {
  local name="$1"
  local secs="${2:-15}"
  echo "== gate: $name (${secs}s max) =="
  RENPY_HOST_GATE="$name" RENPY_HOST_SMOKE_SECS="$secs" cargo run -p renpy-host
  local out="target/gate-${name}.txt"
  if [[ ! -f "$out" ]]; then
    echo "FAIL: missing $out"
    exit 1
  fi
  echo "OK: $name ($(cat "$out" | tr -d '\n'))"
}

# Phase 5 GFX primitives (worker-p5-gpu)
run_gate dissolve 10
run_gate blur 10
run_gate matrixcolor 10
run_gate mask 10
run_gate rtt 10
run_gate readback 10

# Phase 5 goldens G03–G04 (task #20) — first run may bootstrap baselines
run_gate g03 20
run_gate g04 20

# Optional WgpuDraw + shader registry smoke
run_gate g_wgpudraw 15

echo "== all Phase 5 host gates + goldens passed =="
