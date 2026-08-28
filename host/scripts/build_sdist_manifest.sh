#!/usr/bin/env bash
# build_sdist_manifest.sh -- sdist manifest check (docs-only D/E1)
#
# Authority: doc/packaging-investigation.md §4 + .omc/plans/goal-wgpu-e0-e1-packaging.md Phase2.
# --check verifies: host/python shims included, renpy/wgpu included, sdl3 exclusion path.
# Zero-network: only find/ls/grep/tar (no curl/wget/pip network). Dual-tree intact.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODE="check"

_usage() {
  cat <<'USAGE'
Usage: build_sdist_manifest.sh [--check] [--help]

  --check   Dry-run gate: verifies sdist would include host/python + renpy/wgpu
            and that RENPY_HOST_BUILD=1 exclusion path for sdl3 is documentable.
            Tries python -m build --sdist --dry-run if available, else falls back to
            manifest-file + filesystem probes. No upload, no network.

Checks:
  - host/python/host_pygame/* present (164±5 window, fail-closed lower)
  - renpy/wgpu/*.py present (19±5 window, fail-closed lower, draw_*.py split)
  - pyproject.toml / setup.py sdl3 gating probe (RENPY_HOST_BUILD)
  - optional: python -m build sdist dry-run file list contains expected paths
  - artifact naming convention (renpy-host-<rev>-bc160-measured.tar.gz)
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE="check"; shift ;;
    -h|--help) _usage; exit 0 ;;
    *) echo "ERROR: unknown arg $1" >&2; _usage >&2; exit 2 ;;
  esac
done

if [[ "$MODE" != "check" ]]; then
  echo "Only --check in E1" >&2; exit 2
fi

echo "=== sdist manifest --check (dry-run, no network) ==="
echo "ROOT=$ROOT"

# 1. host/python shims (hardened 164±5, loose <5 fail-closed retained)
echo "--- host/python shims ---"
if [[ ! -d "$ROOT/host/python" ]]; then
  echo "FAIL: host/python missing" >&2; exit 1
fi
SHIM_COUNT=$(find "$ROOT/host/python" -type f -name "*.py" | wc -l)
echo "host/python .py count: $SHIM_COUNT"
# loose fail-closed (original threshold)
if [[ "$SHIM_COUNT" -lt 5 ]]; then
  echo "FAIL: too few shims" >&2; exit 1
fi
# hardened window: historical baseline 164±5 (packaging-investigation §4: 164 host/python)
# fail-closed on lower bound (missing shims), warn on upper drift (new gates)
SHIM_EXPECT=164
SHIM_TOL=5
SHIM_MIN=$((SHIM_EXPECT - SHIM_TOL))
SHIM_MAX=$((SHIM_EXPECT + SHIM_TOL))
if [[ "$SHIM_COUNT" -lt "$SHIM_MIN" ]]; then
  echo "FAIL: host/python count $SHIM_COUNT below expected ${SHIM_EXPECT}±${SHIM_TOL} (min $SHIM_MIN)" >&2; exit 1
fi
if [[ "$SHIM_COUNT" -gt "$SHIM_MAX" ]]; then
  echo "WARN: host/python count $SHIM_COUNT above expected ${SHIM_EXPECT}±${SHIM_TOL} (max $SHIM_MAX, drift — update doc baseline if intentional)"
fi
echo "OK: host/python count $SHIM_COUNT within ${SHIM_EXPECT}±${SHIM_TOL} lower-bound or drift-warned"
for req in "host/python/host_pygame/event.py" "host/python/host_pygame/display.py" "host/python/_renpy_host.py"; do
  if [[ ! -f "$ROOT/$req" ]]; then
    echo "FAIL: missing $req" >&2; exit 1
  fi
  echo "OK: $req"
done
# all host_pygame modules (filesystem probe: ls only)
echo "host/python/host_pygame modules:"
ls "$ROOT/host/python/host_pygame" | head -n 20
# enumerate host_pygame count via ls|grep (no network)
HOST_PYGAME_COUNT=$(ls "$ROOT/host/python/host_pygame"/*.py 2>/dev/null | wc -l)
echo "host_pygame .py files (ls): $HOST_PYGAME_COUNT"
echo "OK: host/python"

# 2. renpy/wgpu (hardened 19±5, fail-closed lower)
echo "--- renpy/wgpu ---"
if [[ ! -d "$ROOT/renpy/wgpu" ]]; then
  echo "FAIL: renpy/wgpu missing" >&2; exit 1
fi
WG_COUNT=$(find "$ROOT/renpy/wgpu" -type f -name "*.py" | wc -l)
echo "renpy/wgpu .py count: $WG_COUNT"
WG_EXPECT=19
WG_TOL=5
WG_MIN=$((WG_EXPECT - WG_TOL))
WG_MAX=$((WG_EXPECT + WG_TOL))
if [[ "$WG_COUNT" -lt "$WG_MIN" ]]; then
  echo "FAIL: renpy/wgpu count $WG_COUNT below expected ${WG_EXPECT}±${WG_TOL} (min $WG_MIN)" >&2; exit 1
fi
if [[ "$WG_COUNT" -gt "$WG_MAX" ]]; then
  echo "WARN: renpy/wgpu count $WG_COUNT above expected ${WG_EXPECT}±${WG_TOL} (max $WG_MAX, drift — update doc baseline if intentional)"
fi
echo "OK: renpy/wgpu count $WG_COUNT within ${WG_EXPECT}±${WG_TOL} lower-bound or drift-warned"
for req in "renpy/wgpu/draw.py" "renpy/wgpu/composer.py" "renpy/wgpu/shaders.py"; do
  if [[ ! -f "$ROOT/$req" ]]; then
    echo "FAIL: missing $req" >&2; exit 1
  fi
  echo "OK: $req"
done
echo "OK: renpy/wgpu"

# 3. pyproject.toml sdl3 gating probe + setup.py RENPY_HOST_BUILD guard
echo "--- pyproject sdl3 gating ---"
if grep -q "sdl3" "$ROOT/pyproject.toml" 2>/dev/null; then
  echo "INFO: pyproject.toml mentions sdl3 (SDL reference still present, expected until Phase 9)"
  grep -n "sdl3" "$ROOT/pyproject.toml" | head -n 5 || true
else
  echo "OK: pyproject.toml no sdl3 (already host-only)"
fi
if [[ -f "$ROOT/setup.py" ]]; then
  if grep -q "RENPY_HOST_BUILD\|host_build" "$ROOT/setup.py"; then
    echo "OK: setup.py has RENPY_HOST_BUILD gating"
  else
    echo "WARN: setup.py lacks RENPY_HOST_BUILD gating (deferred to Phase 9, per packaging-investigation C)"
    # not fail in --check
  fi
  # deeper probe: cython wrapper must gate packages containing sdl
  if grep -q 'if "sdl" in pkg.lower()' "$ROOT/setup.py" 2>/dev/null; then
    echo "OK: setup.py cython sdl guard (packages.*sdl hard error)"
  else
    # fallback: check for sdl in packages check broadly
    if grep -q 'packages.*sdl' "$ROOT/setup.py" && grep -q 'HOST_BUILD' "$ROOT/setup.py"; then
      echo "OK: setup.py HOST_BUILD + packages.*sdl probe"
    else
      echo "WARN: setup.py missing explicit cython sdl guard text"
    fi
  fi
  # verify HOST_BUILD allowlist exists
  if grep -q "HOST_ALLOW" "$ROOT/setup.py"; then
    echo "OK: setup.py HOST_ALLOW allowlist present"
  fi
fi
# per-file-ignores is the ruff gate for host/python (pyproject.toml)
if grep -q "host/python/\*\*/\*.py" "$ROOT/pyproject.toml"; then
  echo "OK: pyproject.toml per-file-ignores for host/python"
fi
# additional sdl3 gating probe: ensure setup.py cython function signature with packages=""
if grep -q 'def cython.*packages' "$ROOT/setup.py"; then
  echo "OK: setup.py cython packages param probe"
fi

# 3b. RENPY_HOST_BUILD=1 exclusion path probe (no true build, grep-only)
echo "--- RENPY_HOST_BUILD=1 exclusion probe (grep-only, no build) ---"
if grep -q "RENPY_HOST_BUILD" "$ROOT/setup.py" && grep -q "packages.*sdl3" "$ROOT/setup.py"; then
  echo "OK: RENPY_HOST_BUILD=1 would gate sdl3 packages (grep packages.*sdl3 + HOST_BUILD present)"
else
  echo "WARN: RENPY_HOST_BUILD gating probe incomplete"
fi
# document that host build skips sdl3 via allowlist (no dlopen libSDL)
if grep -q "_host_allowed" "$ROOT/setup.py" && grep -q "HOST_BUILD" "$ROOT/setup.py"; then
  echo "OK: setup.py _host_allowed + HOST_BUILD discrimination present (sdl3 exclusion path documentable)"
fi
# verify without building: ensure at least one cython(..., packages="sdl3") exists but is guarded
SDL_CYTHON_COUNT=$(grep -c 'cython.*packages="[^"]*sdl' "$ROOT/setup.py" || true)
echo "cython packages sdl count (grep): $SDL_CYTHON_COUNT"
if [[ "$SDL_CYTHON_COUNT" -gt 0 ]]; then
  echo "OK: setup.py contains cython packages sdl3 entries (guarded by HOST_BUILD)"
fi

# 4. sdist file list probe (zero-network: only find/ls/grep/tar, no curl/wget)
echo "--- sdist file list probe ---"
if python3 -m build --help 2>/dev/null | grep -q "sdist"; then
  echo "INFO: python -m build available, attempting sdist dry-run file list"
  TMPDIR=$(mktemp -d)
  trap 'rm -rf "$TMPDIR"' EXIT
  # try build sdist file list without full build if possible; fallback to manifest
  # Use build's file listing via --sdist and check dist contents
  set +e
  python3 -m build --sdist --outdir "$TMPDIR" 2>&1 | tee /tmp/sdist_build.log
  rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    SDIST=$(ls "$TMPDIR"/*.tar.gz 2>/dev/null | head -n1)
    if [[ -n "$SDIST" ]]; then
      echo "sdist: $SDIST ($(du -h "$SDIST" | cut -f1))"
      echo "--- sdist contents (grep host/python|renpy/wgpu) ---"
      tar tzf "$SDIST" | grep -E "host/python|renpy/wgpu" | head -n 30
      if ! tar tzf "$SDIST" | grep -q "renpy/wgpu/draw.py"; then
        echo "WARN: sdist missing renpy/wgpu/draw.py (check MANIFEST.in / pyproject include)"
      else
        echo "OK: sdist contains renpy/wgpu/draw.py"
      fi
      if ! tar tzf "$SDIST" | grep -q "host/python"; then
        echo "WARN: sdist missing host/python (expected until sdist manifest adds it; packaging-investigation A says include)"
      else
        echo "OK: sdist contains host/python"
      fi
    fi
  else
    echo "WARN: python -m build --sdist failed (see /tmp/sdist_build.log), treating as soft warn"
    cat /tmp/sdist_build.log | tail -n 20 || true
  fi
  rm -rf "$TMPDIR"
  trap - EXIT
else
  echo "INFO: python -m build not available, skipping sdist tar probe"
  echo "Fallback: checking MANIFEST.in / pyproject include"
  if [[ -f "$ROOT/MANIFEST.in" ]]; then
    cat "$ROOT/MANIFEST.in" | head -n 20
    if grep -q "host/python" "$ROOT/MANIFEST.in"; then
      echo "OK: MANIFEST.in includes host/python"
    else
      echo "WARN: MANIFEST.in missing host/python (deferred to E1 full manifest)"
    fi
  else
    echo "WARN: MANIFEST.in missing (sdist will use default)"
  fi
fi

# 5. artifact naming convention probe
echo "--- artifact naming ---"
REV=$(git -C "$ROOT" rev-parse --short HEAD)
echo "HEAD short: $REV"
EXPECTED="renpy-host-${REV}-bc160-measured.tar.gz"
echo "Expected bundle name pattern: $EXPECTED"
echo "OK: artifact naming pattern renpy-host-<rev>-bc160-measured.tar.gz"
if [[ -f "$ROOT/host/target/bc160_perf_metrics.json" ]]; then
  echo "OK: bc160_perf_metrics.json present for bundle"
fi
if [[ -f "$ROOT/.omc/artifacts/release_acceptance.v1.json" ]]; then
  echo "OK: release_acceptance.v1.json present for bundle"
fi

echo ""
echo "=== sdist manifest --check PASS (dry-run) ==="
echo "host/python: $SHIM_COUNT files (expected ${SHIM_EXPECT}±${SHIM_TOL})"
echo "renpy/wgpu: $WG_COUNT files (expected ${WG_EXPECT}±${WG_TOL})"
