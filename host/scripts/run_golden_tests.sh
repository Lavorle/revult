#!/usr/bin/env bash
# run_golden_tests.sh - Tier 2 Golden Visual Regression via parent_runner envelopes
# G01-G08 verdict/envelope ownership is parent_runner.py (AC-4).
# Composer combo gates remain optional extras.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export RUST_LOG="${RUST_LOG:-info,wgpu_hal=off,wgpu_core=off,naga=off}"
export RENPY_HOST_BASE="$ROOT"
export PYTHONPATH="${ROOT}/host/python/gates${PYTHONPATH:+:$PYTHONPATH}"

RUNNER_PY="$ROOT/host/scripts/runner/parent_runner.py"
ENVELOPE_DIR="$ROOT/host/target/envelopes"
mkdir -p "$ENVELOPE_DIR"

if [[ ! -f "$RUNNER_PY" ]]; then
  echo "ERROR: parent_runner.py missing at $RUNNER_PY" >&2
  exit 1
fi

echo "=== Building Host for Golden Suite Validation ==="
(cd "$ROOT/host" && cargo build -p renpy-host)

# Map gate id -> testcases/wgpu_golden/<dir>/baseline.rgba
gate_case_dir() {
  case "$1" in
    g01) echo "G01_solid_image" ;;
    g02) echo "G02_text" ;;
    g03) echo "G03_dissolve" ;;
    g04) echo "G04_blur" ;;
    g05) echo "G05_movie" ;;
    g06) echo "G06_live2d" ;;
    g07) echo "G07_model" ;;
    g08) echo "G08_mask" ;;
    composer_combo_matrixcolor) echo "composer_texture_matrixcolor" ;;
    composer_combo_alpha) echo "composer_texture_alpha" ;;
    *) echo "" ;;
  esac
}

FAILED=0
PASSED=0
OPTIONAL_PASSED=0
OPTIONAL_FAILED=0
RESIDUALS=()

# Run one gate under parent_runner; writes host/target/envelopes/<gate>.json
# $1=gate  $2=mandatory(1)|optional(0)
run_gate_via_parent() {
  local gate="$1"
  local mandatory="${2:-1}"
  local case_dir
  case_dir="$(gate_case_dir "$gate")"
  local envelope_out="$ENVELOPE_DIR/${gate}.json"
  local baseline=""
  local gate_py="$ROOT/host/python/gates/${gate}.py"
  local out="$ROOT/host/target/gate-${gate}.txt"
  local rc=0
  local input_args=()

  echo "--- Running Golden Gate via parent_runner: $gate ---"

  if [[ -n "$case_dir" ]]; then
    baseline="$ROOT/testcases/wgpu_golden/${case_dir}/baseline.rgba"
  fi

  if [[ -n "$baseline" ]]; then
    if [[ -f "$baseline" ]]; then
      input_args+=(--input "$baseline")
    else
      echo "FAIL: $gate missing declared baseline input: $baseline"
      if [[ "$mandatory" -eq 1 ]]; then
        FAILED=$((FAILED + 1))
        RESIDUALS+=("${gate}:missing_baseline")
      else
        OPTIONAL_FAILED=$((OPTIONAL_FAILED + 1))
      fi
      return 0
    fi
  fi

  if [[ -f "$gate_py" ]]; then
    input_args+=(--input "$gate_py")
  fi

  set +e
  RENPY_HOST_GATE="$gate" \
  RENPY_HOST_SMOKE_SECS="${RENPY_HOST_SMOKE_SECS:-20}" \
  RUST_LOG="$RUST_LOG" \
  RENPY_HOST_BASE="$ROOT" \
  PYTHONPATH="$PYTHONPATH" \
  python3 "$RUNNER_PY" \
    --envelope-out "$envelope_out" \
    "${input_args[@]}" \
    --cwd "$ROOT/host" \
    -- \
    cargo run -p renpy-host
  rc=$?
  set -e

  local envelope_state="missing"
  if [[ -f "$envelope_out" ]]; then
    envelope_state="present"
  fi

  local gate_ok=0
  if [[ $rc -eq 0 && "$envelope_state" == "present" ]]; then
    if [[ -f "$out" ]] && ! grep -q 'ok=False' "$out"; then
      gate_ok=1
    elif [[ ! -f "$out" ]]; then
      # No gate txt but clean exit + envelope: treat as pass only if exit 0
      # Prefer explicit gate log when present.
      gate_ok=0
      echo "WARN: $gate missing gate log $out"
    fi
  fi

  if [[ "$gate_ok" -eq 1 ]]; then
    echo "PASS: $gate rc=$rc envelope=$envelope_out $(tr -d '\n' < "$out" 2>/dev/null || true)"
    if [[ "$mandatory" -eq 1 ]]; then
      PASSED=$((PASSED + 1))
    else
      OPTIONAL_PASSED=$((OPTIONAL_PASSED + 1))
    fi
  else
    echo "FAIL: $gate rc=$rc envelope=$envelope_state path=$envelope_out"
    if [[ -f "$out" ]]; then
      echo "  gate log: $(tr -d '\n' < "$out")"
    fi
    if [[ "$mandatory" -eq 1 ]]; then
      FAILED=$((FAILED + 1))
      if [[ -f "$out" ]] && grep -q 'ok=False' "$out"; then
        RESIDUALS+=("${gate}:mae_or_gate_fail")
      else
        RESIDUALS+=("${gate}:rc=${rc},envelope=${envelope_state}")
      fi
    else
      OPTIONAL_FAILED=$((OPTIONAL_FAILED + 1))
      echo "  (optional gate; does not fail suite)"
    fi
  fi
}

echo "=== Executing Tier 2 Golden Visual Regression Gates (parent_runner) ==="
GATES_MANDATORY=(g01 g02 g03 g04 g05 g06 g07 g08)
for gate in "${GATES_MANDATORY[@]}"; do
  run_gate_via_parent "$gate" 1
done

echo "=== Optional Composer Combo Gates (parent_runner; non-blocking) ==="
GATES_OPTIONAL=(composer_combo_matrixcolor composer_combo_alpha)
for gate in "${GATES_OPTIONAL[@]}"; do
  run_gate_via_parent "$gate" 0
done

echo "=== Verifying Strict Fail-Closed Behavior on Corrupted Baseline ==="
python3 -c "
import sys
import golden_mae as gm

# Baseline: 100x100 white (RGBA)
base_bytes = b'\xff\xff\xff\xff' * (100 * 100)
# Corrupted: 100x100 black (RGBA)
actual_bytes = b'\x00\x00\x00\xff' * (100 * 100)

res = gm.evaluate_golden(100, 100, actual_bytes, 100, 100, base_bytes, mean_limit=2/255)
if not res['passed']:
    print(f'CORRUPTION_DETECTION: PASS (Strictly caught mismatch with MAE={res[\"mae\"]:.6f}, status={res[\"status\"]})')
else:
    print('CORRUPTION_DETECTION: FAIL (Should have failed)')
    sys.exit(1)

# Test missing baseline fail-closed
ok, msg = gm.compare_or_bootstrap('non_existent_gate_name_xyz', 100, 100, actual_bytes)
if not ok and 'FAIL-CLOSED' in msg:
    print('MISSING_BASELINE_DETECTION: PASS (Strictly rejected missing baseline)')
else:
    print(f'MISSING_BASELINE_DETECTION: FAIL ({msg})')
    sys.exit(1)
"

echo "=== Tier 2 Golden Suite Summary ==="
echo "Mandatory passed: $PASSED / ${#GATES_MANDATORY[@]}"
echo "Mandatory failed: $FAILED"
echo "Optional passed:  $OPTIONAL_PASSED / ${#GATES_OPTIONAL[@]}"
echo "Optional failed:  $OPTIONAL_FAILED"
if [[ ${#RESIDUALS[@]} -gt 0 ]]; then
  echo "Residual candidates (no bulk baseline resign):"
  for r in "${RESIDUALS[@]}"; do
    echo "  - $r"
  done
fi
echo "Envelopes dir: $ENVELOPE_DIR"
ls -la "$ENVELOPE_DIR" 2>/dev/null || true

if [[ "$FAILED" -gt 0 ]]; then
  echo "Tier 2 Golden Suite: FAILED ($FAILED mandatory failures)"
  exit 1
fi
echo "Tier 2 Golden Suite: ALL MANDATORY GATES PASSED (parent_runner envelopes)"
exit 0
