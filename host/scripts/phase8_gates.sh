#!/usr/bin/env bash
# Phase 8 gate runner — assimp/model buffer upload + draw sample.
# Authority: .omc/plans/consensus-wgpu-native-vulkan-rewrite.md §5 Phase 8.
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

echo "== gate: assimp (20s max) =="
RENPY_HOST_GATE=assimp RENPY_HOST_SMOKE_SECS=20 cargo run -p renpy-host
out=target/gate-assimp.txt
if [[ ! -f "$out" ]]; then
  echo "FAIL: missing $out"
  exit 1
fi
echo "OK: assimp ($(tr -d '\n' < "$out"))"
echo "Phase 8 EXIT: PASS"
