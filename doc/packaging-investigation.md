# Packaging Investigation — wgpu-host (Debt D pre-research)

> Scope: D2 docs only; no build script mutation. Compares `sdist` vs `AppImage` vs `renpy-build` for Linux Vulkan host.

## 1. Constraints (from consensus & host README)

- **Dual-tree locked**: SDL reference tree must stay buildable until Phase 9 strip; host artifact must `ldd | grep libSDL` empty (AC2) and `WGPU_BACKEND` unset (forces Vulkan).
- **Evidence SSHOT**: `bc160_perf_metrics.v1.json` MEASURED + `release_acceptance.v1.json` bound to `evidence_revision`; packaging must not mint `PERFORMANCE_TARGET_MET` without measurement.
- **Current artifact**: `host/target/release/renpy-host` (13 MB, `libpython3.14.so` + `libvulkan.so.1` + libc, no `libSDL*`), `renpy/wgpu` Python tree, `host/python` shims. `renpy-build` today assumes `setup.py packages=sdl3`; host does **not** run it for shimmed modules.
- **No multi-platform MVP**: Linux x86_64 + RADV only for D; other platforms deferred.

## 2. Options

| # | Option | What ships | Pros | Cons | Host delta |
|---|--------|------------|------|------|------------|
| **A** | **sdist + host binary split** | `sdist` (pure Python `renpy/` without SDL extensions) + separate `renpy-host` binary (cargo release) | Minimal change; keeps PEP 517; host binary `ldd`-clean stays independent; upstream `renpy` stays pip-installable for SDL reference | Two artifacts to version; user must align `renpy` sdist rev with `renpy-host` rev | No `setup.py` change; docs only — `host/README` already describes dual graph |
| **B** | **AppImage (single file)** | `renpy-host` + `renpy/` + `libpython3.14` + `libvulkan` + game dir in one squashfs, via `linuxdeploy` | Single-file distro; works on Steam Deck / Fedora without `pip`; can embed `BENCH 1800` evidence | Size ~180 MB; needs `appimagetool`; `Vulkan ICD` still host-dependent; signing/notarization deferred; collides with dual-tree if AppImage bundles SDL by mistake | New `host/appimage/` recipe, not touching `host/renpy-host/src` |
| **C** | **renpy-build (existing)** | Extend `renpy-build` / `distribute.py` to emit `renpy-host` flavor | Reuses existing distribution & launcher logic; familiar to Ren'Py users | Tightly couples to `SDL` tree assumptions (`install.renpy` manifest, `pyproject.toml packages=sdl3`); would need `host_build` flag in `setup.py`; risk of re-introducing `libSDL` link if not gated | Requires `setup.py` + `renpy-build` fork — out of scope for D |

## 3. Recommendation

**Short-term (D→E): adopt A**. Keep `sdist` (Python side) + `renpy-host` binary as two version-locked artifacts sharing `evidence_revision` (same `git rev-parse HEAD`). Rationale:

1. Zero `host/renpy-host/src` / `renpy/wgpu` code change — matches Debt D's "docs only for packaging" rule.
2. Preserves `ldd libSDL = 0` and `backend=Vulkan` gates; CI (`host.yml` Tier1+2) already validates this split.
3. Lets `benchmark_bc160.sh --measured` evidence be archived as `host/target/bc160_perf_metrics.json` alongside binary without squashfs rebuild.

**AppImage (B) as opt-in after E**: once D1's `TIMESTAMP_QUERY` true timing is green and `bc160` 1% low is stable, add `host/scripts/build_appimage.sh` that consumes `host/target/release/renpy-host` + `renpy/` + `libpython` via `linuxdeploy`. Do **not** block D on it.

**renpy-build (C) deferred to Phase 9 strip**: only after SDL tree is deleted can `renpy-build` stop assuming SDL; until then it would fight dual-tree.

## 4. Next steps (for E — DONE 78b21d7b4)

- [x] `host/scripts/build_appimage.sh --check` (dry-run, no network): verifies `ldd` no SDL, `VULKAN` backend log, `libpython` path, size budget. — `host/scripts/build_appimage.sh` green, `host/README.md §Packaging --check`
- [x] `sdist` manifest: ensure `host/python` shims are included, `src/Setup` excludes `sdl3` when `RENPY_HOST_BUILD=1`. — `host/scripts/build_sdist_manifest.sh --check` green (164 host/python + 19 renpy/wgpu), `setup.py` gating deferred to Phase 9 per §3 C
- [x] Artifact naming: `renpy-host-<rev>-bc160-measured.tar.gz` + `bc160_perf_metrics.v1.json` + `release_acceptance.v1.json` SHA256 bundle. — `build_sdist_manifest.sh` probes `renpy-host-78b21d7b4-bc160-measured.tar.gz` pattern + `release_acceptance.v1.json` digest

## 5. Verification (D2 + E1 --check)

```bash
test -f doc/packaging-investigation.md
grep -q "AppImage" doc/packaging-investigation.md
grep -q "sdist" doc/packaging-investigation.md
grep -q "renpy-build" doc/packaging-investigation.md
bash host/scripts/build_appimage.sh --check
bash host/scripts/build_sdist_manifest.sh --check
```

## 6. Notes

- `TIMESTAMP_QUERY` (D1) is orthogonal to packaging; packaging must preserve `render_pass_cpu_proxy` flag in `bc160_perf_metrics` rather than re-timings.
- No secret or recovered HuangmeiC path is bundled; `recovered_project` stays read-only (HMC playtest guard).
