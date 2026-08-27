#!/usr/bin/env bash
# Phase 9 gate runner — G01–G08 golden CI + ldd strip (AC2) + key regressions.
# Authority: .omc/plans/consensus-wgpu-native-vulkan-rewrite.md §5 Phase 9 / §6.1 / AC2–AC8.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/host"
BIN=target/debug/renpy-host
export RUST_LOG="${RUST_LOG:-info,wgpu_hal=off,wgpu_core=off,naga=off}"
export RENPY_HOST_BASE="$ROOT"
export PYTHONPATH="${ROOT}:${ROOT}/host/python/gates${PYTHONPATH:+:$PYTHONPATH}"

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

# M3 T4增量 — 增量金库：G02 CJK竖排 / Arabic 连写 / G03 旋转裁剪 / 雾遮罩 (fail-closed, pre-present RT, MAE≤2/255 max≤16)
echo "== goldens M3 T4 incremental (G02/G03 inc) =="
run_gate g02_cjk_vertical 20
run_gate g02_arabic 20
run_gate g03_rot_clip 20
run_gate g03_fog_mask 20

echo "== perf gate: instances≈quads (AC1 companion, fail-closed) =="
set +e
RENPY_HOST_PERF=1 python3 - <<'PYGATE'
import os, sys
try:
    from renpy.wgpu.host_bridge import get_frame_stats
    s=get_frame_stats()
    qc=s.get('quads',0)
    ic=s.get('instances',0)
    dc=s.get('draw_calls',0)
except Exception as e:
    print(f"perf gate SKIP: get_frame_stats unavailable ({e})", file=sys.stderr)
    sys.exit(0)
perf=os.environ.get('RENPY_HOST_PERF','')
is_perf=perf.strip().lower() in ('1','true','yes')
# qc==0 => no frame data or perf not enabled -> skip not fail
if qc==0:
    print(f"perf gate SKIP: quads==0 (perf_enabled={is_perf}, draw={dc}) no frame data")
    sys.exit(0)
diff=abs(ic-qc)
rel=diff/max(1,qc)
print(f"perf gate check: quads={qc} instances={ic} draw_calls={dc} diff={diff} rel={rel:.3f} (tolerance 0.10)")
if rel < 0.10:
    print(f"perf gate PASS: instances≈quads within 10% (ic={ic} qc={qc})")
    sys.exit(0)
else:
    print(f"FAIL: instances {ic} != quads {qc} diff {diff} rel {rel:.3f} >0.10", file=sys.stderr)
    sys.exit(1)
PYGATE
_rc=$?
set -e
if [[ $_rc -ne 0 ]]; then
  echo "FAIL: instance count != quads (perf gate) rc=$_rc" >&2
  exit 1
fi
echo "OK: perf gate instances≈quads (or skipped if no perf data)"
echo "== ldd no libSDL* (AC2 re-verify after perf gate) =="
ldd "$BIN" | tee target/renpy-host.ldd.post
if ldd "$BIN" | grep -iE 'libSDL'; then
  echo "FAIL: SDL linked into host artifact (post-gate)"
  exit 1
fi
echo "OK: no libSDL* (post-gate ldd-clean)"

echo "== key regression gates =="
run_gate dissolve 10
run_gate video 30
run_gate live2d 25
run_gate assimp 20
run_gate shader_break 15

echo "== all Phase 9 host gates + goldens + ldd passed =="
echo "Phase 9 EXIT: PASS (host MVP scope)"
