#!/usr/bin/env bash
# Phase 9 gate runner — G01–G08 golden CI + ldd strip (AC2) + key regressions.
# Authority: .omc/plans/consensus-wgpu-native-vulkan-rewrite.md §5 Phase 9 / §6.1 / AC2–AC8.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/host"
BIN=target/debug/renpy-host
export RUST_LOG="${RUST_LOG:-info,wgpu_hal=off,wgpu_core=off,naga=off}"
export RENPY_HOST_BASE="$ROOT"
export PYTHONPATH="${ROOT}/host/python/gates${PYTHONPATH:+:$PYTHONPATH}"

echo "== build =="
cargo build -p renpy-host

echo "== ldd no libSDL* (AC2) =="
if [[ ! -x "$BIN" ]]; then
  echo "FAIL: missing binary $BIN"
  exit 1
fi
ldd "$BIN" | tee target/renpy-host.ldd
if ldd "$BIN" | grep -iE 'libSDL'; then
  echo "FAIL: SDL linked into host artifact"
  exit 1
fi
echo "OK: no libSDL* (ldd-clean)"

run_gate() {
  local name="$1"
  local secs="${2:-25}"
  echo "== gate: $name (${secs}s max) =="
  RENPY_HOST_GATE="$name" RENPY_HOST_SMOKE_SECS="$secs" cargo run -p renpy-host
  local out="target/gate-${name}.txt"
  if [[ ! -f "$out" ]]; then
    echo "FAIL: missing $out"
    exit 1
  fi
  # Fail if gate log explicitly reports ok=False (MAE regression / assert).
  if grep -q 'ok=False' "$out"; then
    echo "FAIL: $name reported ok=False"
    cat "$out"
    exit 1
  fi
  echo "OK: $name ($(tr -d '\n' < "$out"))"
}

echo "== goldens G01–G08 (AC6) =="
# First run may bootstrap baselines (compare_or_bootstrap logs 'baseline written').
run_gate g01 20
run_gate g02 25
run_gate g03 20
run_gate g04 20
run_gate g05 20
run_gate g06 25
run_gate g07 20
run_gate g08 20

echo "== key regression gates =="
run_gate dissolve 10
run_gate video 30
run_gate live2d 25
run_gate assimp 20
run_gate shader_break 15

echo "== all Phase 9 host gates + goldens + ldd passed =="
echo "Phase 9 EXIT: PASS (host MVP scope)"
