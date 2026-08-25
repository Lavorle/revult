# renpy-host (Linux MVP)

[![host](https://github.com/Lavorle/revult/actions/workflows/host.yml/badge.svg)](https://github.com/Lavorle/revult/actions/workflows/host.yml) — Tier1+2 (fmt/check/test 34 + 8/8 + ruff + phase1)

Rust host for Ren'Py on the **wgpu + Vulkan** product path. Authority plan:


---

## 1. Two build graphs (dual-tree)

| Graph | Entry | Window / GPU | pygame extensions | Link SDL? |
|-------|-------|--------------|-------------------|-----------|
| **SDL reference** | `./run.sh <game>` or `python renpy.py <game>` | SDL3 + GL2Draw | `setup.py` with `packages=sdl3` (today) | **Yes** |
| **Host MVP** | `cargo run -p renpy-host -- <game>` | winit + wgpu Vulkan | Host stubs / reimpls; **no** `packages=sdl3` for replaced modules | **No** |

Rules (locked):

- SDL/GL tree stays **buildable** for human reference until Phase 9 strip.
- Host build is **additive** under `host/`.
- Shared Python files (e.g. `renpy/display/core.py`) use explicit host branches
  (`renpy.host_build` / compile flag). Do **not** break the SDL reference tree.
- Product host exposes **only** `WgpuDraw` (no GL coexistence).

---

## 2. Exact build commands

### Host artifact (this graph)

```bash
# From repo root
cd /path/to/revult

# Optional: ensure system deps (Fedora example)
# sudo dnf install -y python3-devel vulkan-loader-devel gcc

# Debug host (clear-color window + embedded Python smoke)
cargo run -p renpy-host

# Release host
cargo build -p renpy-host --release
./host/target/release/renpy-host

# Later phases (game path argv):
# cargo run -p renpy-host -- the_question
```

Workspace lives at `host/Cargo.toml`. Crate package name: `renpy-host`.
Binary output: `host/target/debug/renpy-host` or `host/target/release/renpy-host`.

Environment overrides:

| Variable | Meaning |
|----------|---------|
| `RENPY_HOST_BASE` | Repo root override (default: parent of `host/`) |
| `RENPY_HOST_PYTHONHOME` | Optional `PYTHONHOME` for embed |
| `RENPY_HOST_GAME` | Default game dir if no argv |
| `WGPU_BACKEND` | Leave unset; host forces Vulkan in code |
| `RENPY_HOST_HEADLESS` | If `1`, skip window present (CI smoke only; Phase 0 still prefers real present) |

### SDL reference (untouched)

```bash
# From repo root — existing workflow
uv sync                 # if using project venv
./run.sh the_question   # builds Cython inplace + runs renpy.py
# or
./run.sh --build        # build extensions only
python renpy.py the_question
```

Do **not** point the host binary at SDL-linked extension modules that `dlopen` libSDL.

### HuangmeiC playtest (host + MangoHud)

Product-path exercise of recovered HuangmeiC assets under renpy-host (Vulkan/wgpu).
Basedir: `host/playtests/HuangmeiC/` (runtime `game` → `HUANGMEIC_GAME_SRC`).

```bash
# Smoke (default 30s, release binary, mangohud required)
./host/scripts/run_huangmeic_playtest.sh --smoke
./host/scripts/run_huangmeic_playtest.sh --smoke 60

# Interactive (no timeout)
./host/scripts/run_huangmeic_playtest.sh --normal

# Override recovered tree location if needed
HUANGMEIC_GAME_SRC=/path/to/recovered_project ./host/scripts/run_huangmeic_playtest.sh --smoke
```

Defaults: **release** build, **mangohud** always wrap (hard-fail if missing). See `host/playtests/HuangmeiC/README.md`.

---

## 3. Cython / extension policy for host

Host does **not** run `setup.py packages=sdl3` for modules that the shim replaces.
Phase ownership follows plan §4.5:

| Module | Host policy | Phase |
|--------|-------------|-------|
| `renpy.pygame.event` | Reimplement (host-fed queue; `wait` → nested `host.wait_until`) | 1 |
| `renpy.pygame.display` | Reimplement (no `SDL_CreateWindow`) | 1 |
| `renpy.pygame.pygame_time` / time | Reimplement → TimerWheel | 1 |
| `renpy.pygame.key`, `mouse` | Reimplement from winit | 1 |
| text input / IME | Reimplement `TEXTINPUT` / `TEXTEDITING` | 1–3 |
| `renpy.pygame.surface` | Software RGBA buffer | 1–2 |
| `renpy.pygame.image` | Non-SDL decode | 2 |
| `renpy.pygame.transform`, `draw` | Reimplement | 2 |
| `renpy.pygame.iostream` | Host IO bridge (no `SDL_IOStream`) | 1–2 |
| `color`, `rect`, `locals`, `error` | Pure Python / reimpl | 1 |
| joystick / controller / scrap / power | Stub (import OK, empty devices) | 1 |
| `renpy.audio.renpysound` | cpal adapter (not SDL audio) | 4 |
| `renpy.gl2.*` | Not used on host; `WgpuDraw` only | 1+ |
| mesh math (`gl2mesh*`) without GL | Keep if SDL-free | as needed |

Build switch (Phase 1+):

```bash
export RENPY_HOST_BUILD=1
# Future: python setup.py build_ext … host extra that skips packages=sdl3
```

Phase 0 does not yet compile Ren'Py Cython for host; it only embeds the interpreter and proves the window/GPU/pump path.

---

## 4. libpython, package path, game dir discovery

### libpython

- Host links against the **same** CPython used to build extensions later (system or venv).
- PyO3 `auto-initialize` embeds the interpreter; shared lib expected:
  - Fedora: `/usr/lib64/libpython3.14.so` (this machine: Python 3.14.6).
- Headers: `python3-devel` (`Python.h` under `/usr/include/python3.14`).

### `renpy` package path

At process start the host inserts (in order):

1. `$RENPY_HOST_BASE` (repo root) — so `import renpy` resolves to `./renpy/`.
2. Existing `PYTHONPATH` entries (if any).

### Game directory

Argv convention (aligned with `renpy.py`):

```text
renpy-host [options] <basedir-or-game>
```

Resolution (Phase 1+ will call into Ren'Py bootstrap):

1. Explicit argv path if present.
2. `$RENPY_HOST_GAME`.
3. Fallback demos: `the_question`, then `tutorial` under repo root.

Phase 0 smoke does not require a game directory.

---

## 5. `ldd` checklist (AC2 / Phase 0–1 gate)

```bash
BIN=host/target/release/renpy-host   # or debug
ldd "$BIN" | tee /tmp/renpy-host.ldd
# MUST be empty:
ldd "$BIN" | grep -iE 'libSDL' && echo 'FAIL: SDL linked' && exit 1
echo 'OK: no libSDL* in host link line'
```

Expected **present** (illustrative, not exhaustive):

- `libpython3.xx.so`
- `libvulkan.so.1` (via wgpu/ash) or loader pulled transitively
- standard libc / pthread / dl / m

Expected **absent**:

- `libSDL3.so*`, `libSDL2.so*`, `libSDL.so*`
- `libSDL3_image`, `libSDL3_ttf`, etc.

CI should archive the `ldd` output as an artifact (plan § observability).

---

## 6. Nested pump API (`wait_until`) — Mechanism 1

**Locked:** plan §4.1.1 — nested host wait only. No stack unwind, no greenlet, no
day-one `interact_core` → `tick()` rewrite.

### winit version + API

| Item | Value |
|------|-------|
| Crate | `winit` **0.30** (stable ApplicationHandler era) |
| Trait | `winit::platform::pump_events::EventLoopExtPumpEvents` |
| Call | `event_loop.pump_app_events(timeout, &mut app)` |
| Timeout | `Some(Duration)` until deadline; `None` = block indefinitely (avoid on host product path) |
| Status | `PumpStatus::Continue` / `PumpStatus::Exit(code)` |

`Host.wait_until(deadline_ms)` (PyO3):

1. Compute remaining duration from host monotonic clock vs `deadline_ms`.
2. Call `pump_app_events(Some(remaining), …)` on the **same** thread / event loop.
3. OS events land in the host queue; timer wheel firings inject `PERIODIC` /
   `REDRAW` / `TIMEEVENT` (Phase 1).
4. Return to the **same** Python stack frame in `event_wait` (no exception unwind).
5. **GIL:** release GIL around the OS wait inside `wait_until`; re-acquire before return.

Phase 0 exposes a smoke binding:

```python
import renpy_host
renpy_host.wait_until(deadline_ms)  # pumps winit; may present frames
```

Phase 1 wires this into `renpy.display.core.event_wait` under `renpy.host_build`.

### Thread model (AC1)

| Thread | Work |
|--------|------|
| Main (winit) | Events, timers, Python under GIL (bounded), wgpu encode/submit/present |
| cpal callback (Phase 4) | PCM ring only — **no Python** |

---

## 7. GPU / color format (locked ADR §4.3.1)

| Decision | Value |
|----------|-------|
| Game / intermediate RT | `Rgba8Unorm` + PMA via WGSL `renpy.ftl` |
| Swapchain | `Rgba8Unorm` (non-sRGB) |
| Golden capture | **pre-present** game RT |
| MAE | mean ≤ 2/255; max channel delta ≤ 16 |
| Backend | **Vulkan only** on Linux MVP (`Backends::VULKAN`) |

Phase 0 presents a solid clear color to the swapchain and logs adapter backend.

Startup log must include a line equivalent to:

```text
[renpy-host] wgpu adapter backend=Vulkan name="…"
```

---

## 8. FFI sketch (Phase 0 subset → Phase 2+)

Module name: `renpy_host` (PyO3 extension linked into the host binary).

| Surface | Phase | Notes |
|---------|-------|-------|
| `wait_until(deadline_ms)` | 0 | Nested pump |
| `get_ticks_ms()` | 0/1 | Monotonic host clock |
| `Host` window size/title/fullscreen/DPI | 1 | |
| `register_timer` / `clear_timer` | 1 | TimerWheel |
| `start_text_input` / `stop_text_input` / `set_text_input_rect` | 1–3 | IME |
| `Gpu.begin_frame` / `draw_model` / `end_frame_present` | 2+ | **Primary** draw path — not `submit_frame` |
| `Audio` / renpysound adapter | 4 | |

Handles: `u64` into Rust arenas (`SlotMap` / `GpuArena`) from Phase 2.

---

## 9. Phase 0 exit criteria (gate before Phase 1)

- [ ] `cargo run -p renpy-host` opens a window and presents clear color (Vulkan).
- [ ] Log shows adapter backend **Vulkan**.
- [ ] Embedded CPython runs a smoke snippet / `import renpy_host`.
- [ ] Nested `wait_until` pumps events without stack unwind.
- [ ] This README documents the §4.9 matrix (this file).
- [ ] `ldd` on host binary has **no** `libSDL*`.

Phase 1 adds soak / PERIODIC / input / stack-depth automation (plan §7).

---

## 10. Directory layout

```text
host/
  Cargo.toml              # workspace
  README.md               # this file
  renpy-host/
    Cargo.toml
    src/
      main.rs             # entry, ApplicationHandler shell
      app.rs              # window + pump state
      gpu.rs              # wgpu instance/device/surface/clear
      pump.rs             # wait_until / EventLoopExtPumpEvents
      python.rs           # PyO3 embed + renpy_host module
```

Python product modules (later phases):

```text
renpy/wgpu/               # WgpuDraw
renpy/pygame/             # host reimpl branches
renpy/common/_shaders_wgsl.rpym
testcases/wgpu_golden/
```

---

## 11. Non-goals (MVP)

- Multi-platform (macOS Metal / Windows DX12) product path
- GL2Draw / SWDraw coexistence on host
- GLSL auto-translate
- renpy-build packaging / distributor installers
- Fantasy day-one rewrite of `interact_core` into `tick()` / `draw_frame()`

---

## 12. Decision log pointer

Phase completion notes and gate evidence: `.omc/logs/wgpu-phase-decisions.md`.

---

## 13. Golden CI (Phase 9 / AC6)

Suite G01–G08 lives under `testcases/wgpu_golden/` with MAE limits:

| Limit | Value |
|-------|-------|
| mean absolute error | ≤ 2/255 |
| max channel delta | ≤ 16 |
| capture | **pre-present** game RT (`read_game_rt_rgba`) |
| format | `Rgba8Unorm` tight RGBA + `b"RGBA"+u32le w/h` header |

| ID | Gate | Focus |
|----|------|-------|
| G01 | `g01` | solid + image (texture, geometry, PMA) |
| G02 | `g02` | say dialogue / bitmap text atlas |
| G03 | `g03` | dissolve |
| G04 | `g04` | blur |
| G05 | `g05` | movie frame (video texture path) |
| G06 | `g06` | Live2D idle sample |
| G07 | `g07` | Model mesh (assimp procedural) |
| G08 | `g08` | alpha mask |

First run with missing `baseline.rgba` **bootstraps** the baseline and logs `baseline written`. Re-runs compare MAE and fail on regression.

### Run all Phase 9 gates (recommended)

```bash
cd host
bash scripts/phase9_gates.sh
```

This script:

1. `cargo build -p renpy-host`
2. **AC2** `ldd` — fails if any `libSDL*` appears
3. Runs G01–G08 golden gates
4. Runs key regressions: `dissolve`, `video`, `live2d`, `assimp`, `shader_break`
5. Fails if any gate file is missing or contains `ok=False`

### Run individual goldens

```bash
cd host
export RENPY_HOST_BASE=$(cd .. && pwd)
export PYTHONPATH=$RENPY_HOST_BASE/host/python/gates
for g in g01 g02 g03 g04 g05 g06 g07 g08; do
  RENPY_HOST_GATE=$g cargo run -p renpy-host || exit 1
done
ldd target/debug/renpy-host | grep -iE 'libSDL' || echo ldd-clean
```

Gate sources: `host/python/gates/g0N.py` + shared `golden_mae.py`.
Artifacts: `host/target/gate-g0N.txt`, baselines under `testcases/wgpu_golden/G0N_*/`.

### Release checklist (host artifact) — v0.6.0 9f62ab39c (06ce113b rebased)

- [x] `cargo build -p renpy-host --release` — 13MB, `RUSTFLAGS='-D warnings' cargo check --workspace --all-targets` 0
- [x] `ldd target/release/renpy-host | grep -iE 'libSDL'` is **empty** (AC2) — `host/target/verify-ldd-release.log`
- [x] Startup log: `wgpu adapter backend=Vulkan` — RADV NAVI12, `host/target/verify-phase1.log` / `verify-bench.log`
- [x] `bash scripts/phase9_gates.sh` green (or CI equivalent) — **replaced by `bash host/scripts/run_golden_tests.sh` via `parent_runner` 8/8 + 2 composer combos**, `host/target/envelopes/*.json` (10) + `host/target/verify-golden.log`; also `bash host/scripts/phase1_gates.sh` green
- [x] Dual-tree: **do not delete** SDL/GL source tree — strip is host-artifact only
- [x] Shader migration docs current: `doc/wgsl_shader_migration.md`

**Release evidence (rebound to HEAD 78b21d7b4, 2026-08-25):** `release_acceptance.v1.json PASS` + `product_acceptance.v1.json PASS` (evidence_revision 78b21d7b4), `bc160_perf_metrics.v1.json MEASURED 2262.28fps 1800 frames 830.91 1%low render_pass 5098ns TIMESTAMP_QUERY true eligible true` (host/target/), `cargo test 34`, `ruff renpy/wgpu 0 + host/python/gates 0 (per-file-ignores)`, `phase1_gates 0`. Full digest: `.omc/artifacts/release_artifacts.sha256`. See `CHANGELOG.md` wgpu-host v0.6.0 and `.omc/artifacts/release_acceptance.v1.json`.

```bash
# Fresh verify at any HEAD (Phase 1 replica):
cd host && cargo fmt --check && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets && cargo test --workspace
cargo build -p renpy-host --release && ldd ../host/target/release/renpy-host | grep -qi libSDL && echo FAIL || echo OK
bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json
bash host/scripts/run_golden_tests.sh   # 8/8 via parent_runner
ruff check ../renpy/wgpu ../host/python/gates
bash host/scripts/phase1_gates.sh
python3 host/scripts/build_release_acceptance.py --out ../.omc/artifacts/release_acceptance.v1.json
```

### Packaging --check (E1 dry-run, no network)

```bash
# AppImage dry-run: ldd no SDL + libpython + WGPU_BACKEND unset + backend Vulkan + size <220MB
bash host/scripts/build_appimage.sh --check
# sdist manifest dry-run: host/python + renpy/wgpu inclusion probe
bash host/scripts/build_sdist_manifest.sh --check
```
### Migration / AC8

GLSL `register_shader` / `register_textshader` hard-error on host (`renpy.host_build`).
See `doc/wgsl_shader_migration.md`. Smoke: `RENPY_HOST_GATE=shader_break cargo run -p renpy-host`.
