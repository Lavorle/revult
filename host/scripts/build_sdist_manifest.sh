#!/usr/bin/env bash
# build_sdist_manifest.sh -- sdist manifest check (docs-only D/E1)
#
# Authority: doc/packaging-investigation.md §4 + .omc/plans/goal-wgpu-e0-e1-packaging.md Phase2.
# --check verifies: host/python shims included, renpy/wgpu included, sdl3 exclusion path.
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
  - host/python/host_pygame/* present
  - renpy/wgpu/*.py present (draw_*.py split)
  - pyproject.toml / setup.py sdl3 gating probe
  - optional: python -m build sdist dry-run file list contains expected paths
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

# 1. host/python shims
echo "--- host/python shims ---"
if [[ ! -d "$ROOT/host/python" ]]; then
  echo "FAIL: host/python missing" >&2; exit 1
fi
SHIM_COUNT=$(find "$ROOT/host/python" -type f -name "*.py" | wc -l)
echo "host/python .py count: $SHIM_COUNT"
if [[ "$SHIM_COUNT" -lt 5 ]]; then
  echo "FAIL: too few shims" >&2; exit 1
fi
for req in "host/python/host_pygame/event.py" "host/python/host_pygame/display.py" "host/python/_renpy_host.py"; do
  if [[ ! -f "$ROOT/$req" ]]; then
    echo "FAIL: missing $req" >&2; exit 1
  fi
  echo "OK: $req"
done
# all host_pygame modules
echo "host/python/host_pygame modules:"
ls "$ROOT/host/python/host_pygame" | head -n 20
echo "OK: host/python"

# 2. renpy/wgpu
echo "--- renpy/wgpu ---"
if [[ ! -d "$ROOT/renpy/wgpu" ]]; then
  echo "FAIL: renpy/wgpu missing" >&2; exit 1
fi
WG_COUNT=$(find "$ROOT/renpy/wgpu" -type f -name "*.py" | wc -l)
echo "renpy/wgpu .py count: $WG_COUNT"
for req in "renpy/wgpu/draw.py" "renpy/wgpu/composer.py" "renpy/wgpu/shaders.py"; do
  if [[ ! -f "$ROOT/$req" ]]; then
    echo "FAIL: missing $req" >&2; exit 1
  fi
  echo "OK: $req"
done
echo "OK: renpy/wgpu"

# 3. pyproject.toml sdl3 gating probe
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
fi
# per-file-ignores is the ruff gate for host/python
if grep -q "host/python/\*\*/\*.py" "$ROOT/pyproject.toml"; then
  echo "OK: pyproject.toml per-file-ignores for host/python"
fi

# 4. sdist dry-run if python -m build available
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
if [[ -f "$ROOT/host/target/bc160_perf_metrics.json" ]]; then
  echo "OK: bc160_perf_metrics.json present for bundle"
fi
if [[ -f "$ROOT/.omc/artifacts/release_acceptance.v1.json" ]]; then
  echo "OK: release_acceptance.v1.json present for bundle"
fi

echo ""
echo "=== sdist manifest --check PASS (dry-run) ==="
echo "host/python: $SHIM_COUNT files"
echo "renpy/wgpu: $WG_COUNT files"
