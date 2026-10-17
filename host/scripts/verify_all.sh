#!/usr/bin/env bash
# verify_all.sh — one-shot full verification battery for the revult host tree.
#
# Runs, in order, with a single build+single instance discipline:
#   1. cargo check -p renpy-host                (Rust compile)
#   2. cargo test -p renpy-host                 (Rust suites)
#   3. python -m pytest tests/ -q               (Python suites)
#   4. cargo build -p renpy-host --release      (AC2 release artifact)
#      ldd | grep libSDL                        (must be empty)
#   5. phase9 goldens (20 gates) under Xvfb     (winit needs the env closed;
#      single-instance serial: the same Xvfb by the playtest protocol)
#
# Exit code: 0 only when everything passes. Usage:
#   bash host/scripts/verify_all.sh            (full battery)
#   bash host/scripts/verify_all.sh --quick    (skip phase9 gates)
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
QUICK="${1:-}"

pass(){ printf '\033[32mPASS\033[0m %s\n' "$1"; }
fail(){ printf '\033[31mFAIL\033[0m %s\n' "$1"; EXIT_CODE=1; }
EXIT_CODE=0

echo "== verify_all: $ROOT"
BIN="$ROOT/host/target/release/renpy-host"
if command -v stat >/dev/null 2>&1; then
  mt="$(stat -c %Y "$BIN" 2>/dev/null || echo 0)"
  if [ "$mt" -gt 0 ]; then
    now="$(date +%s)"
    printf 'binary mtime %s minutes ago (%s)\n' "$(( (now - mt) / 60 ))" "$BIN"
  fi
fi

cd "$ROOT/host"

if cargo check -p renpy-host >/tmp/verify_check.log 2>&1; then pass "cargo check"; else
  tail -20 /tmp/verify_check.log; fail "cargo check"; fi

if cargo test -p renpy-host >/tmp/verify_cargo_test.log 2>&1; then
  n=$(grep -c 'test result: ok' /tmp/verify_cargo_test.log)
  pass "cargo test ($n suites ok)"; else
  tail -30 /tmp/verify_cargo_test.log; fail "cargo test"; fi

cd "$ROOT"
if python -m pytest tests/ -q >/tmp/verify_pytest.log 2>&1; then
  tail -1 /tmp/verify_pytest.log | sed 's/^/      /'; pass "pytest"; else
  tail -20 /tmp/verify_pytest.log; fail "pytest"; fi

cd "$ROOT/host"
if cargo build -p renpy-host --release >/tmp/verify_release.log 2>&1; then
  pass "cargo build --release";
  if ldd target/release/renpy-host | grep -iqE 'libSDL'; then
    fail "ldd — libSDL linked (AC2)"; else pass "ldd — no libSDL (AC2)"; fi
else
  tail -20 /tmp/verify_release.log; fail "cargo build --release"; fi

if [[ "$QUICK" == "--quick" ]]; then
  echo "== verify_all: (skipped phase9 goldens per --quick)"
else
  # Phase9 needs the product environment: x11/Xvfb must be selected (winit 0.30
  # prefers Wayland when WAYLAND_DISPLAY is set — close it for the gate run)
  if env -u WAYLAND_DISPLAY -u WAYLAND_SOCKET DISPLAY=:99 \
     xvfb-run -a -s "-screen 0 1920x1080x24" bash "$ROOT/host/scripts/phase9_gates.sh" \
     >/tmp/verify_gates.log 2>&1; then
    grep -E 'all Phase 9|EXIT: PASS' /tmp/verify_gates.log | tail -2 | sed 's/^/      /'
    pass "phase9 goldens (20 gates + ldd)"
  else
    tail -40 /tmp/verify_gates.log; fail "phase9 goldens"
  fi
fi

rm -f /tmp/verify_check.log /tmp/verify_cargo_test.log /tmp/verify_pytest.log \
      /tmp/verify_release.log /tmp/verify_gates.log

echo "== verify_all: EXIT=$EXIT_CODE"
exit $EXIT_CODE
