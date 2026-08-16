#!/usr/bin/env bash
# Phase 7 Live2D gate — multi-mesh + mask RTT + idle animation sample.
# Authority: .omc/plans/consensus-wgpu-native-vulkan-rewrite.md §5 Phase 7.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/host"
BIN=target/debug/renpy-host
export RUST_LOG="${RUST_LOG:-info,wgpu_hal=off,wgpu_core=off,naga=off}"
export RENPY_HOST_BASE="$ROOT"
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

echo "== gate: live2d =="
RENPY_HOST_GATE=live2d RENPY_HOST_SMOKE_SECS=30 cargo run -p renpy-host
OUT=target/gate-live2d.txt
if [[ ! -f "$OUT" ]]; then
  echo "FAIL: missing $OUT"
  exit 1
fi
echo "OK: live2d ($(tr -d '\n' < "$OUT"))"
echo "== Phase 7 Live2D host gate passed =="
