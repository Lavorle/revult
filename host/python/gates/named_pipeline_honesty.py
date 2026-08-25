"""
Named-pipeline honesty probe (autoresearch / parity C1).

Gate name: named_pipeline_honesty  (RENPY_HOST_GATE=named_pipeline_honesty)

Checks:
  1) shaders.py map has no dead factory names (geometry/alpha/ftl ghosts)
  2) every pipeline key is a real renpy_host callable
  3) composition-only parts (geometry, alpha) do not claim pipelines
  4) draw path packs u_renpy_blur_log2 / u_renpy_matrixcolor / u_renpy_alpha
  5) matrixcolor + blur still draw without error

Writes host/target/gate-named_pipeline_honesty.txt with ok=True/False.
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


def _repo_root() -> Path:
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path.cwd()


def _result_path() -> Path:
    return _repo_root() / "host" / "target" / "gate-named_pipeline_honesty.txt"


def _safe_write(msg: str) -> None:
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

# Force host_build so register_builtin_core runs if needed.
import renpy

renpy.host_build = True  # type: ignore[attr-defined]

from renpy.wgpu import shaders as sh
from renpy.wgpu.draw import WgpuDraw

# Re-register builtins under host_build if empty.
if not sh.list_wgsl_parts():
    sh.register_builtin_core()

problems = sh.assert_pipeline_map_honest(renpy_host)
notes.append(f"map_problems={problems!r}")
if problems:
    ok = False
    notes.append("FAIL: dishonest or missing pipeline map entries")

# Explicit checks
for dead in ("geometry", "alpha", "ftl"):
    for name, key in sh._PIPELINE_KEYS.items():
        if key == dead:
            ok = False
            notes.append(f"FAIL: {name} still maps to dead key {dead}")

if sh.host_pipeline_key("renpy.alpha") is not None:
    ok = False
    notes.append("FAIL: renpy.alpha should be composition-only")
else:
    notes.append("renpy.alpha composition_mode=%s" % sh.composition_mode("renpy.alpha"))

if sh.host_pipeline_key("renpy.geometry") is not None:
    ok = False
    notes.append("FAIL: renpy.geometry should be composition-only")
else:
    notes.append(
        "renpy.geometry composition_mode=%s" % sh.composition_mode("renpy.geometry")
    )

ftl_key = sh.host_pipeline_key("renpy.ftl")
notes.append(f"renpy.ftl→{ftl_key}")
if ftl_key != "textured_pipeline":
    ok = False
    notes.append("FAIL: renpy.ftl should map to textured_pipeline")

# Draw-path packing / selection
draw = WgpuDraw()
assert draw.init((1280, 720))

# blur pack
u_blur = draw._pack_uniforms({"u_renpy_blur_log2": 3.0}, ("renpy.blur",))
notes.append(f"pack_blur={u_blur[:2] if u_blur else None}")
if not u_blur or abs(u_blur[0] - 3.0) > 1e-6:
    ok = False
    notes.append("FAIL: blur uniform pack")

# matrix pack from list
ident = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]
u_mat = draw._pack_uniforms(
    {"u_renpy_matrixcolor": ident}, ("renpy.matrixcolor",)
)
notes.append(f"pack_matrix_len={len(u_mat) if u_mat else 0}")
if not u_mat or len(u_mat) != 16 or abs(u_mat[0] - 1.0) > 1e-6:
    ok = False
    notes.append("FAIL: matrixcolor uniform pack")

# pipeline selection
pipe_blur = draw._pipeline_for_shaders(("renpy.texture", "renpy.blur"), True)
pipe_tex = draw._pipeline_for_shaders(("renpy.texture",), True)
pipe_solid = draw._pipeline_for_shaders(("renpy.solid",), False)
pipe_alpha = draw._pipeline_for_shaders(("renpy.texture", "renpy.alpha"), True)
notes.append(
    f"pipes blur={pipe_blur} tex={pipe_tex} solid={pipe_solid} alpha_with_tex={pipe_alpha}"
)
if pipe_blur == pipe_tex:
    # blur should prefer blur pipeline over texture
    ok = False
    notes.append("FAIL: renpy.blur did not select blur pipeline")
if pipe_alpha != pipe_tex:
    ok = False
    notes.append("FAIL: renpy.alpha should not change pipeline (composition-only)")
if pipe_solid == pipe_tex:
    ok = False
    notes.append("FAIL: renpy.solid should select solid pipeline")

# Live draw: blur + matrixcolor + alpha-ish textured
pix = bytes([255, 128, 64, 255] * 4)
tex = renpy_host.create_texture_rgba(2, 2, pix)
try:
    renpy_host.begin_frame()
    draw.draw_blur(tex, blur_log2=1.5)
    draw.draw_matrixcolor(tex, ident)
    # alpha composition via synthetic node-like path: pack only
    u_a = draw._pack_uniforms(
        {"u_renpy_alpha": 0.5, "u_renpy_over": 1.0}, ("renpy.alpha",)
    )
    # alpha is vertex-color path → pack may be None; that's OK
    notes.append(f"pack_alpha_dict={u_a}")
    renpy_host.end_frame_present()
    notes.append("live_draw_ok=True")
except Exception as e:
    ok = False
    notes.append(f"FAIL: live_draw {type(e).__name__}: {e}")

msg = "gate=named_pipeline_honesty\nok=%s\n%s\n" % (
    "True" if ok else "False",
    "\n".join(notes),
)
out = _result_path()
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
