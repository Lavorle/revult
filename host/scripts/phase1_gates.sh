#!/usr/bin/env bash
# Phase 1 gate runner (plan §7).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/host"
BIN=target/debug/renpy-host
export RUST_LOG="${RUST_LOG:-info,wgpu_hal=off,wgpu_core=off,naga=off}"
export RENPY_HOST_BASE="$ROOT"

echo "== cargo check (zero warnings/errors) =="
RUSTFLAGS="-D warnings" cargo check --workspace --all-targets

echo "== cargo test =="
cargo test --workspace

echo "== build =="
cargo build -p renpy-host

echo "== ldd no libSDL* =="
if ldd "$BIN" | grep -iE 'libSDL'; then
  echo "FAIL: SDL linked"
  ldd "$BIN"
  exit 1
fi
echo "OK: no libSDL*"

echo "== symbol table no SDL symbols =="
if nm "$BIN" 2>/dev/null | grep -iE 'sdl_' | grep -vE 'renpy_host'; then
  echo "FAIL: SDL symbols detected in binary"
  exit 1
fi
echo "OK: no SDL symbols"

run_gate() {
  local name="$1"
  local secs="${2:-30}"
  echo "== gate: $name (${secs}s max) =="
  RENPY_HOST_GATE="$name" RENPY_HOST_SMOKE_SECS="$secs" cargo run -p renpy-host
  echo "OK: $name"
}

run_gate smoke 2
run_gate nested 30
run_gate input 10
# Full 60s PERIODIC soak (plan §7 #3)
run_gate periodic 60

echo "== all Phase 1 host gates passed =="
