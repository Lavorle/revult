"""
Composer basic gate — single-part path via create_pipeline_wgsl.

Gate name: composer_get_basic  (RENPY_HOST_GATE=composer_get_basic)

Proves:
  1) WgslShaderCache.get(["renpy.texture"]) creates a composed pipeline
     through renpy_host.create_pipeline_wgsl (not only prebaked factory).
  2) Pipeline handle > 0, key starts with "composed:".
  3) Drawing a textured quad yields non-black game-RT pixels.
  4) Cache hit returns the same pipeline handle.
"""

import os
import sys
from pathlib import Path
try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

import renpy_host
from golden_mae import gate_result_path


def _repo_root():
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path.cwd()


def _safe_write(msg):
    data = (msg if msg.endswith("\n") else msg + "\n").encode("utf-8", "replace")
    try:
        os.write(1, data)
    except Exception:
        try:
            sys.__stdout__.write(msg if msg.endswith("\n") else msg + "\n")
            sys.__stdout__.flush()
        except Exception:
            pass


notes = []
ok = True

root = _repo_root()
sys.path.insert(0, str(root))

import renpy

renpy.host_build = True  # type: ignore[attr-defined]

from renpy.wgpu import shaders as sh
from renpy.wgpu.composer import ComposerError, get_shader_cache

if not sh.list_wgsl_parts():
    sh.register_builtin_core()
notes.append(f"parts={len(sh.list_wgsl_parts())}")

# --- create_pipeline_wgsl must exist (host FFI path) -------------------------
if not hasattr(renpy_host, "create_pipeline_wgsl"):
    ok = False
    notes.append("FAIL: renpy_host.create_pipeline_wgsl missing")
else:
    notes.append("create_pipeline_wgsl=present")

cache = get_shader_cache()
cache.clear()

try:
    result = cache.get(["renpy.texture"], hard_fail=True, has_texture=True)
except ComposerError as e:
    ok = False
    notes.append(f"FAIL: composer.get raised ComposerError: {e}")
    result = None
except Exception as e:
    ok = False
    notes.append(f"FAIL: composer.get {type(e).__name__}: {e}")
    result = None

if result is None:
    ok = False
    notes.append("FAIL: no ComposerResult")
else:
    notes.append(
        f"key={result.key} pipeline={result.pipeline} "
        f"tex_count={result.tex_count} layout={result.uniform_layout_id} "
        f"parts={result.partnames}"
    )
    if not str(result.key).startswith("composed:"):
        ok = False
        notes.append("FAIL: key must start with 'composed:'")
    if int(result.pipeline) <= 0:
        ok = False
        notes.append("FAIL: pipeline handle must be > 0 (create_pipeline_wgsl path)")
    if "composed by renpy.wgpu.composer" not in (result.wgsl or ""):
        ok = False
        notes.append("FAIL: wgsl missing composer header (not a composed module)")
    if result.tex_count != 1:
        ok = False
        notes.append(f"FAIL: expected tex_count=1 got {result.tex_count}")
    if result.has_uniforms:
        ok = False
        notes.append("FAIL: renpy.texture alone should have no uniforms")

    # Cache hit must return same handle
    hit = cache.get(["renpy.texture"], hard_fail=True)
    if hit is None or int(hit.pipeline) != int(result.pipeline):
        ok = False
        notes.append("FAIL: cache hit pipeline mismatch")
    else:
        notes.append(f"cache_hit_pipeline={hit.pipeline}")

# --- Draw textured quad with composed pipeline -------------------------------
if result is not None and int(result.pipeline) > 0:
    # Distinctive 4x4 checker — not black.
    pix = []
    for y in range(4):
        for x in range(4):
            if (x + y) & 1:
                pix.extend([40, 160, 255, 255])
            else:
                pix.extend([255, 220, 40, 255])
    tex = renpy_host.create_texture_rgba(4, 4, bytes(pix))
    verts = [
        -0.75, -0.75, 0.0, 1.0, 1, 1, 1, 1,
         0.75, -0.75, 1.0, 1.0, 1, 1, 1, 1,
         0.75,  0.75, 1.0, 0.0, 1, 1, 1, 1,
        -0.75,  0.75, 0.0, 0.0, 1, 1, 1, 1,
    ]
    mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
    pipe = int(result.pipeline)

    try:
        for _ in range(6):
            renpy_host.begin_frame()
            renpy_host.draw_model(pipe, mesh, tex)
            renpy_host.end_frame_present()
            renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

        w, h, rgba = renpy_host.read_game_rt_rgba()
        notes.append(f"rt={w}x{h} bytes={len(rgba)}")
        if w <= 0 or h <= 0 or len(rgba) != w * h * 4:
            ok = False
            notes.append("FAIL: bad RT readback")
        else:
            # Sample center region mean — must be non-black.
            total = 0
            n = 0
            # subsample every 16th pixel
            for i in range(0, len(rgba), 16):
                total += rgba[i] + rgba[i + 1] + rgba[i + 2]
                n += 3
            mean = (total / max(n, 1))
            notes.append(f"mean_rgb={mean:.2f}")
            if mean < 1.0:
                ok = False
                notes.append("FAIL: RT is essentially black after composed draw")
            else:
                notes.append("draw_non_black=True")
    except Exception as e:
        ok = False
        notes.append(f"FAIL: draw {type(e).__name__}: {e}")

msg = "gate=composer_get_basic\nok=%s\n%s\n" % (
    "True" if ok else "False",
    "\n".join(notes),
)
out = gate_result_path("composer_get_basic")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg, encoding="utf-8")
_safe_write(msg)
renpy_host.request_quit()
if not ok:
    raise SystemExit(1)

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
