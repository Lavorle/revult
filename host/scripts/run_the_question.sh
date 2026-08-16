#!/usr/bin/env bash
# Launch the_question under renpy-host (wgpu-native / Vulkan).
#
# Usage (from anywhere):
#   ./host/scripts/run_the_question.sh
#   ./host/scripts/run_the_question.sh --release
#   ./host/scripts/run_the_question.sh --build-only
#   ./host/scripts/run_the_question.sh --smoke 30   # auto-quit after N seconds
#
# Env overrides (optional):
#   RENPY_HOST_BASE   repo root (auto-detected)
#   RENPY_HOST_GAME   game dir (default: $RENPY_HOST_BASE/the_question)
#   RUST_LOG          default quiets wgpu noise
#   WGPU_BACKEND      leave unset (host forces Vulkan)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/host"

export RENPY_HOST_BASE="${RENPY_HOST_BASE:-$ROOT}"
export RENPY_HOST_GAME="${RENPY_HOST_GAME:-$ROOT/the_question}"
export RUST_LOG="${RUST_LOG:-info,wgpu_hal=off,wgpu_core=off,naga=off}"
# Gates dir is only needed when RENPY_HOST_GATE is set; product entry still
# benefits from having host/python on path for renpy_*_host stubs.
export PYTHONPATH="$ROOT/host/python/gates${PYTHONPATH:+:$PYTHONPATH}"

# Interactive product path: do NOT set RENPY_HOST_GATE (main.rs → product gate).
unset RENPY_HOST_GATE 2>/dev/null || true
# Clear leftover smoke/max deadlines from prior shell sessions so interactive
# play is not auto-killed by nested should_exit.
if [[ -z "${SMOKE_SECS:-}" ]]; then
  unset RENPY_HOST_SMOKE_SECS RENPY_HOST_MAX_SECS 2>/dev/null || true
fi
# 00start.rpy uses truthiness of environ.get(...):
#   elif not renpy.os.environ.get("RENPY_SKIP_MAIN_MENU", False): call _main_menu
# Any non-empty string (including "0") is truthy → main menu is SKIPPED.
# Only set these vars when the operator explicitly wants skip ("1"/"true"/...).
# Bare interactive default = unset (enter main menu + splash as stock Ren'Py).
_normalize_skip_env() {
  # $1 = var name. Treat 0/false/no/off/empty as unset (do not skip).
  local name="$1"
  local val="${!name-}"
  case "${val,,}" in
    ""|0|false|no|off|n)
      unset "$name" 2>/dev/null || true
      ;;
    1|true|yes|on|y)
      export "$name=1"
      ;;
    *)
      # Unknown explicit value — keep as-is (operator override).
      export "$name=$val"
      ;;
  esac
}
_normalize_skip_env RENPY_SKIP_MAIN_MENU
_normalize_skip_env RENPY_SKIP_SPLASHSCREEN
# Stock Ren'Py GL performance dialog (renpy/common/00gltest.rpy:_gl_performance_test):
#   performance_test = int(os.environ.get("RENPY_PERFORMANCE_TEST")) when set;
#   performance_test == 0 -> return immediately (do not run the interactive GL
#   performance / "Do you want to run a performance test?" path).
# renpy-host product playtests intentionally default this to 0 so interactive
# launch is not blocked by that dialog. Set RENPY_PERFORMANCE_TEST=1 (or leave
# unset and enable via preferences) only when the operator wants the stock
# performance-test flow. This is a documented Ren'Py env contract, not a
# silent host bypass of product FPS gates.
export RENPY_PERFORMANCE_TEST="${RENPY_PERFORMANCE_TEST:-0}"

RELEASE=0
BUILD_ONLY=0
SMOKE_SECS=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release|-r)
      RELEASE=1
      shift
      ;;
    --build-only|-b)
      BUILD_ONLY=1
      shift
      ;;
    --smoke|-s)
      SMOKE_SECS="${2:-30}"
      shift 2
      ;;
    --smoke=*)
      SMOKE_SECS="${1#--smoke=}"
      shift
      ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    --)
      shift
      EXTRA_ARGS+=("$@")
      break
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ ! -d "$RENPY_HOST_GAME" ]]; then
  echo "ERROR: game dir not found: $RENPY_HOST_GAME" >&2
  echo "  set RENPY_HOST_GAME or place the_question under $ROOT/" >&2
  exit 1
fi

if [[ -n "$SMOKE_SECS" ]]; then
  export RENPY_HOST_SMOKE_SECS="$SMOKE_SECS"
  echo "== smoke auto-quit after ${SMOKE_SECS}s =="
else
  unset RENPY_HOST_SMOKE_SECS 2>/dev/null || true
fi

echo "== renpy-host / the_question =="
echo "  RENPY_HOST_BASE=$RENPY_HOST_BASE"
echo "  RENPY_HOST_GAME=$RENPY_HOST_GAME"
echo "  cwd=$(pwd)"

if [[ "$RELEASE" -eq 1 ]]; then
  echo "== cargo build -p renpy-host --release =="
  cargo build -p renpy-host --release
  BIN="$ROOT/host/target/release/renpy-host"
else
  echo "== cargo build -p renpy-host =="
  cargo build -p renpy-host
  BIN="$ROOT/host/target/debug/renpy-host"
fi

if [[ "$BUILD_ONLY" -eq 1 ]]; then
  echo "OK: built $BIN"
  exit 0
fi

if [[ ! -x "$BIN" ]]; then
  echo "ERROR: binary missing: $BIN" >&2
  exit 1
fi

# Prefer direct binary so we don't re-link every launch; argv path is also
# accepted by main.rs (sets RENPY_HOST_GAME when unset — already set above).
echo "== run $BIN the_question =="
exec "$BIN" "$RENPY_HOST_GAME" "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
