"""
Composer alpha-fold golden — texture + renpy.alpha composition.

Gate name: composer_combo_alpha  (RENPY_HOST_GATE=composer_combo_alpha)

Baseline: testcases/wgpu_golden/composer_texture_alpha/baseline.rgba

Proves:
  1) renpy.alpha is composition-only (stripped from composer effect set).
  2) get(["renpy.texture", "renpy.alpha"]) → texture-only composed pipeline
     (no fragment alpha merge; no uniforms from alpha).
  3) Alpha is applied via vertex-color fold (manual verts matching draw.py).
  4) MAE golden under composer_texture_alpha/.
"""

import os
import sys
from pathlib import Path

import renpy_host
from golden_mae import compare_or_bootstrap, gate_result_path

# --- Harness sample migration -----------------------------------------------
# This gate keeps its explicit flow as the reference template, but proves
# the new parametrized harness is importable without migrating all 134 files.
# Required import per spec: `from _harness import gate_harness` (gates/ on
# sys.path when run via renpy_host). The try-wrapper also supports
# `python -m host.python.gates.composer_combo_alpha` / namespace import.
try:
    from _harness import gate_harness  # noqa: F401 — sample import, see below
except ImportError:  # pragma: no cover — fallback for namespace import
    from host.python.gates._harness import gate_harness  # type: ignore[no-redef]  # noqa: F401

# How to rewrite with harness (not yet switched — example only):
# ------------------------------------------------------------------
# from _harness import gate_harness
# from golden_mae import compare_or_bootstrap
#
# def run_one(case: dict):
#     # ... setup Composer, create tex/mesh, single draw call ...
#     # w, h, rgba = renpy_host.read_game_rt_rgba()
#     # return w, h, rgba
#     return w, h, rgba
#
# def golden_compare(w, h, rgba):
#     return compare_or_bootstrap("composer_texture_alpha", w, h, rgba)
#
# # single-case gate (params=[{}]); parametrized example:
# # @parametrized_gate("dissolve", [{"amount": 0.0}, {"amount": 0.5}, {"amount": 1.0}])
# # def run_case(case): ...
# gate_harness("composer_combo_alpha", [{}], run_one, golden_compare)
# ------------------------------------------------------------------

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

# --- Policy: alpha is composition-only ---------------------------------------
if sh.composition_mode("renpy.alpha") != "vertex_color_alpha":
    ok = False
    notes.append(
        "FAIL: renpy.alpha composition_mode=%r expected vertex_color_alpha"
        % (sh.composition_mode("renpy.alpha"),)
    )
else:
    notes.append("renpy.alpha composition_mode=vertex_color_alpha")

if sh.host_pipeline_key("renpy.alpha") is not None:
    ok = False
    notes.append("FAIL: renpy.alpha must not expose a host pipeline key")
else:
    notes.append("renpy.alpha host_pipeline_key=None")

if sh.is_mergeable("renpy.alpha"):
    ok = False
    notes.append("FAIL: renpy.alpha must not be mergeable (composition_only)")
else:
    notes.append("renpy.alpha is_mergeable=False")

# --- Composer strips alpha; effect set is texture-only -----------------------
cache = get_shader_cache()
cache.clear()

try:
    result = cache.get(
        ["renpy.texture", "renpy.alpha"], hard_fail=True, has_texture=True
    )
except ComposerError as e:
    ok = False
    notes.append("FAIL: compose raised: %s" % e)
    result = None
except Exception as e:
    ok = False
    notes.append("FAIL: compose %s: %s" % (type(e).__name__, e))
    result = None

if result is None:
    ok = False
    notes.append("FAIL: no ComposerResult for texture+alpha")
else:
    notes.append(
        "key=%s pipeline=%s tex_count=%s layout=%s parts=%s has_uniforms=%s"
        % (
            result.key,
            result.pipeline,
            result.tex_count,
            result.uniform_layout_id,
            result.partnames,
            result.has_uniforms,
        )
    )
    if int(result.pipeline) <= 0:
        ok = False
        notes.append("FAIL: composed pipeline handle <= 0")
    if result.partnames != ["renpy.texture"]:
        ok = False
        notes.append(
            "FAIL: alpha must be stripped; effect parts=%s (expected ['renpy.texture'])"
            % (result.partnames,)
        )
    if result.has_uniforms or result.uniform_layout_id != "none":
        ok = False
        notes.append(
            "FAIL: texture+alpha fold must not introduce uniforms (layout=%s)"
            % (result.uniform_layout_id,)
        )
    # Same key as texture-only compose (order-insensitive, alpha stripped).
    try:
        tex_only = cache.get(["renpy.texture"], hard_fail=True)
    except Exception as e:
        tex_only = None
        ok = False
        notes.append("FAIL: texture-only re-get: %s" % e)
    if tex_only is not None and tex_only.key != result.key:
        ok = False
        notes.append(
            "FAIL: texture+alpha key %s != texture-only %s"
            % (result.key, tex_only.key)
        )
    elif tex_only is not None:
        notes.append("texture_only_key_match=%s" % result.key)

# Manual vertex-color fold matching draw.py WgpuDraw:
#   draw_color = (cr*a, cg*a, cb*a, ca*a*over) with a=0.5, over=1.0
# This is the composition path — NOT a fragment-shader alpha merge.
ALPHA = 0.5
OVER = 1.0
vr = 1.0 * ALPHA
vg = 1.0 * ALPHA
vb = 1.0 * ALPHA
va = 1.0 * ALPHA * OVER
notes.append("vertex_fold rgba=(%s,%s,%s,%s)" % (vr, vg, vb, va))
notes.append("alpha_policy=vertex_color_fold (not fragment merge, not uniforms)")

if result is not None and int(result.pipeline) > 0:
    # Saturated checker; half-alpha fold darkens via vertex color * tex.
    pix = []
    for y in range(8):
        for x in range(8):
            if (x + y) & 1:
                pix.extend([255, 200, 40, 255])
            else:
                pix.extend([40, 120, 255, 255])
    tex = renpy_host.create_texture_rgba(8, 8, bytes(pix))
    verts = [
        -0.80, -0.80, 0.0, 1.0, vr, vg, vb, va,
         0.80, -0.80, 1.0, 1.0, vr, vg, vb, va,
         0.80,  0.80, 1.0, 0.0, vr, vg, vb, va,
        -0.80,  0.80, 0.0, 0.0, vr, vg, vb, va,
    ]
    mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
    pipe = int(result.pipeline)

    try:
        # Single present: product RT uses LoadOp::Load after first frame, so
        # multi-frame translucent redraws accumulate premul alpha toward opaque.
        # Baseline encodes the single-composite appearance.
        renpy_host.begin_frame()
        # No uniforms — alpha is already folded into vertex color.
        renpy_host.draw_model(pipe, mesh, tex)
        renpy_host.end_frame_present()


        w, h, rgba = renpy_host.read_game_rt_rgba()
        if not (w > 0 and h > 0 and len(rgba) == w * h * 4):
            ok = False
            notes.append("FAIL: bad RT readback %sx%s bytes=%s" % (w, h, len(rgba)))
        else:
            notes.append("rt=%sx%s" % (w, h))

            # Non-black check
            total = 0
            n = 0
            for i in range(0, len(rgba), 16):
                total += rgba[i] + rgba[i + 1] + rgba[i + 2]
                n += 3
            mean = total / max(n, 1)
            notes.append("mean_rgb=%.2f" % mean)
            if mean < 1.0:
                ok = False
                notes.append("FAIL: RT essentially black after alpha-fold draw")

            try:
                mae_ok, mae_msg = compare_or_bootstrap(
                    "composer_texture_alpha", w, h, rgba
                )
                notes.append(mae_msg)
                if not mae_ok:
                    ok = False
                    notes.append("FAIL: MAE golden")
            except Exception as e:
                ok = False
                notes.append("FAIL: mae %s: %s" % (type(e).__name__, e))
    except Exception as e:
        ok = False
        notes.append("FAIL: draw %s: %s" % (type(e).__name__, e))

msg = "gate=composer_combo_alpha\nok=%s\n%s\n" % (
    "True" if ok else "False",
    "\n".join(notes),
)
out = gate_result_path("composer_combo_alpha")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg, encoding="utf-8")
_safe_write(msg)
renpy_host.request_quit()
if not ok:
    raise SystemExit(1)
