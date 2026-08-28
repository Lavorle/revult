"""
Composer true-merge golden — texture + matrixcolor.

Gate name: composer_combo_matrixcolor
  (RENPY_HOST_GATE=composer_combo_matrixcolor)

Baseline: testcases/wgpu_golden/composer_texture_matrixcolor/baseline.rgba

Composes ["renpy.texture", "renpy.matrixcolor"] via WgslShaderCache.get,
draws a checker with a grayscale matrixcolor uniform, MAE vs golden.
"""

import os
import sys
from pathlib import Path


import renpy_host
from golden_mae import compare_or_bootstrap, gate_result_path


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

cache = get_shader_cache()
cache.clear()

try:
    result = cache.get(
        ["renpy.texture", "renpy.matrixcolor"], hard_fail=True, has_texture=True
    )
except ComposerError as e:
    ok = False
    notes.append(f"FAIL: compose raised: {e}")
    result = None
except Exception as e:
    ok = False
    notes.append(f"FAIL: compose {type(e).__name__}: {e}")
    result = None

if result is None:
    ok = False
    notes.append("FAIL: no ComposerResult for texture+matrixcolor")
else:
    notes.append(
        f"key={result.key} pipeline={result.pipeline} "
        f"tex_count={result.tex_count} layout={result.uniform_layout_id} "
        f"parts={result.partnames} has_uniforms={result.has_uniforms}"
    )
    if int(result.pipeline) <= 0:
        ok = False
        notes.append("FAIL: composed pipeline handle <= 0")
    if result.uniform_layout_id != "matrixcolor16":
        ok = False
        notes.append(
            f"FAIL: expected layout matrixcolor16 got {result.uniform_layout_id}"
        )
    if not result.has_uniforms:
        ok = False
        notes.append("FAIL: has_uniforms must be True for matrixcolor merge")
    if set(result.partnames) != {"renpy.matrixcolor", "renpy.texture"}:
        ok = False
        notes.append(f"FAIL: unexpected effect parts {result.partnames}")
    # Both hooks must appear in emitted WGSL (true merge, not re-register).
    wgsl = result.wgsl or ""
    if "textureSample" not in wgsl:
        ok = False
        notes.append("FAIL: wgsl missing texture sample (texture hook)")
    if "mat4x4" not in wgsl and "u.col0" not in wgsl:
        ok = False
        notes.append("FAIL: wgsl missing matrixcolor hook")
    if "composed by renpy.wgpu.composer" not in wgsl:
        ok = False
        notes.append("FAIL: wgsl missing composer header")

# Grayscale luminance matrix (column-major 4x4, m * color).
# R'=G'=B' = 0.299R + 0.587G + 0.114B; A' = A.
GRAY = [
    0.299, 0.299, 0.299, 0.0,  # col0
    0.587, 0.587, 0.587, 0.0,  # col1
    0.114, 0.114, 0.114, 0.0,  # col2
    0.0,   0.0,   0.0,   1.0,  # col3
]

if result is not None and int(result.pipeline) > 0:
    # 8x8 saturated checker so grayscale is distinctive vs plain texture.
    pix = []
    for y in range(8):
        for x in range(8):
            if (x + y) & 1:
                pix.extend([255, 32, 32, 255])  # red
            else:
                pix.extend([32, 32, 255, 255])  # blue
    tex = renpy_host.create_texture_rgba(8, 8, bytes(pix))
    verts = [
        -0.80, -0.80, 0.0, 1.0, 1, 1, 1, 1,
         0.80, -0.80, 1.0, 1.0, 1, 1, 1, 1,
         0.80,  0.80, 1.0, 0.0, 1, 1, 1, 1,
        -0.80,  0.80, 0.0, 0.0, 1, 1, 1, 1,
    ]
    mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
    pipe = int(result.pipeline)
    u = list(GRAY)

    try:
        for _ in range(8):
            renpy_host.begin_frame()
            renpy_host.draw_model(pipe, mesh, tex, None, u)
            renpy_host.end_frame_present()
            renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

        w, h, rgba = renpy_host.read_game_rt_rgba()
        assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))
        notes.append(f"rt={w}x{h}")

        # Sanity: grayscale should collapse chroma (mean |R-G| + |G-B| small-ish
        # over non-clear samples). Soft check only — MAE is the hard gate.
        chroma = 0
        samples = 0
        for i in range(0, len(rgba), 32):
            r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
            if a < 8 and r + g + b < 8:
                continue
            chroma += abs(r - g) + abs(g - b)
            samples += 1
        mean_chroma = chroma / max(samples, 1)
        notes.append(f"mean_chroma={mean_chroma:.2f} samples={samples}")

        mae_ok, mae_msg = compare_or_bootstrap(
            "composer_texture_matrixcolor", w, h, rgba
        )
        notes.append(mae_msg)
        if not mae_ok:
            ok = False
            notes.append("FAIL: MAE golden")
    except Exception as e:
        ok = False
        notes.append(f"FAIL: draw/mae {type(e).__name__}: {e}")

msg = "gate=composer_combo_matrixcolor\nok={}\n{}\n".format(
    "True" if ok else "False",
    "\n".join(notes),
)
out = gate_result_path("composer_combo_matrixcolor")
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
