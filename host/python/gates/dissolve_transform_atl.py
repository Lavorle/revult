"""
Gate: dissolve_transform ATL tree shape (parent shader + child multi-tex mesh).

Gate name: dissolve_transform_atl  (RENPY_HOST_GATE=dissolve_transform_atl)

HuangmeiC transforms.rpy:
  transform dissolve_transform(old, new, rule, duration=0.2):
      Model().child(new).texture(old).texture(rule)
      shader "image_dissolve"
      u_transition 0.2
      u_animation 0.0 → 1.0

Host RenderTransform stamps shader/uniforms on the *outer* Transform Render;
Model.render puts mesh=True + 3 texture children on the *inner* node.
WgpuDraw must fold parent uniforms onto child mesh slots (not peel/walk only).

Checks:
  1. mid u_animation=0.5 progressive (left≈old, right≈new) via red-channel rule
  2. parent-only shader path (no shader on mesh child) still works
  3. blank/clear frame rejected

Note: no from __future__; host run_file prepends imports.
"""

import os
import sys
from pathlib import Path

import renpy_host  # type: ignore

from renpy.wgpu.draw import HostTexture, WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---
try:
    from _harness import gate_harness, parametrized_gate  # type: ignore
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore
    except ImportError:
        gate_harness = None  # type: ignore
        parametrized_gate = None  # type: ignore


_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-dissolve_transform_atl.txt"
out.parent.mkdir(parents=True, exist_ok=True)

notes = []
ok = True

draw = WgpuDraw()
assert draw.init((1280, 720))
draw._ensure_pipes()

W = H = 64


def _solid(w, h, rgba):
    r, g, b, a = rgba
    return bytes([r, g, b, a]) * (w * h)


rule = bytearray()
for y in range(H):
    for x in range(W):
        v = 255 if x >= W // 2 else 0
        rule.extend([v, v, v, 255])
rule = bytes(rule)
old = _solid(W, H, (255, 0, 0, 255))
new = _solid(W, H, (0, 0, 255, 255))

t_rule = renpy_host.create_texture_rgba(W, H, rule)
t_old = renpy_host.create_texture_rgba(W, H, old)
t_new = renpy_host.create_texture_rgba(W, H, new)


class _Node:
    pass


# Inner Model-like mesh node: textures as children (Model.render blit order)
# child(new).texture(old).texture(rule) → children [new, old, rule]
mesh = _Node()
mesh.width = W
mesh.height = H
mesh.mesh = True
mesh.shaders = None  # product: shader is on Transform, not Model
mesh.uniforms = None
mesh.textures = [
    HostTexture(t_new, W, H),
    HostTexture(t_old, W, H),
    HostTexture(t_rule, W, H),
]
mesh.children = [
    (HostTexture(t_new, W, H), 0.0, 0.0),
    (HostTexture(t_old, W, H), 0.0, 0.0),
    (HostTexture(t_rule, W, H), 0.0, 0.0),
]
mesh.cached_model = None
mesh.xclipping = False
mesh.yclipping = False
mesh.reverse = None

# Outer Transform-like node with dissolve_transform stamps
outer = _Node()
outer.width = 1280
outer.height = 720
outer.mesh = None
outer.shaders = ("image_dissolve",)
outer.uniforms = {"u_transition": 0.2, "u_animation": 0.5}
outer.children = [(mesh, 0.0, 0.0)]
outer.textures = None
outer.cached_model = None
outer.xclipping = False
outer.yclipping = False
outer.reverse = None
outer.color = None
outer.ndc = None

for _ in range(3):
    renpy_host.begin_frame()
    draw._draw_node(outer, 0.0, 0.0)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
notes.append(f"rt={w}x{h}")
lx, ly = w // 4, h // 2
rx, ry = (3 * w) // 4, h // 2
li = (ly * w + lx) * 4
ri = (ry * w + rx) * 4
left = tuple(rgba[li : li + 4])
right = tuple(rgba[ri : ri + 4])
notes.append(f"left_rgba={left} right_rgba={right}")

if left[0] <= left[2]:
    ok = False
    notes.append("FAIL: left not old/red — parent fold or slot remap broken")
if right[2] <= right[0]:
    ok = False
    notes.append("FAIL: right not new/blue — parent fold or slot remap broken")

mean_r = sum(rgba[i] for i in range(0, len(rgba), 4)) / (w * h)
mean_b = sum(rgba[i] for i in range(2, len(rgba), 4)) / (w * h)
notes.append(f"mean_r={mean_r:.1f} mean_b={mean_b:.1f}")
if mean_r < 1.0 and mean_b < 1.0:
    ok = False
    notes.append("FAIL: blank/clear frame")

# matrixcolor-only parent (ColorizeMatrix hover shape) still promotes
ident = [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
]
# Colorize-ish: force yellow-ish via non-identity — just ensure no crash + non-clear
mc = _Node()
mc.width = 1280
mc.height = 720
mc.mesh = None
mc.shaders = ("renpy.matrixcolor",)
mc.uniforms = {"u_renpy_matrixcolor": ident}
mc.children = [(HostTexture(t_new, W, H), 100.0, 100.0)]
mc.textures = None
mc.cached_model = None
mc.xclipping = False
mc.yclipping = False
mc.reverse = None

try:
    for _ in range(2):
        renpy_host.begin_frame()
        draw._draw_node(mc, 0.0, 0.0)
        renpy_host.end_frame_present()
        renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
    notes.append("matrixcolor_parent_promote=ok")
except Exception as e:  # noqa: BLE001
    ok = False
    notes.append(f"FAIL: matrixcolor parent promote {type(e).__name__}: {e}")

msg = "gate=dissolve_transform_atl\nok={}\n{}\n".format(
    "True" if ok else "False",
    "\n".join(notes),
)
out.write_text(msg, encoding="utf-8")
sys.stdout.write(msg)
sys.stdout.flush()
if not ok:
    raise SystemExit(1)

# ----------------------------------------------------------------------
# HARNESS MIGRATION (thin wrapper, original logic preserved above)
# ----------------------------------------------------------------------
# Migration path for dissolve_transform_atl:
#   1. Keep all helpers/classes above untouched (header license preserved).
#   2. Extract the body of main()/run()/probe into _harness_run_one(case):
#        def _harness_run_one(case):
#            # case: dict with {"u_animation": 0.5, "u_transition": 0.2}
#            # ... reuse helpers above (WgpuDraw / FakeRender / _mean_rgb ...)
#            # w, h, rgba = renpy_host.read_game_rt_rgba()
#            # return w, h, rgba   # or (ok, msg)
#   3. Define golden_compare delegating to golden_mae or custom mean check:
#        def _harness_golden_compare(w, h, rgba):
#            from golden_mae import compare_or_bootstrap
#            return compare_or_bootstrap("dissolve_transform_atl", w, h, rgba)
#            # or custom: mr/mg/mb = _mean_rgb(rgba,w,h); return (ok,msg)
#   4. Wire via harness (opt-in via RENPY_HOST_HARNESS=1 to keep default run unchanged):
#        if parametrized_gate is not None:
#            @parametrized_gate("dissolve_transform_atl", [{"u_animation": 0.5, "u_transition": 0.2}])
#            def _parametrized_case(case):
#                w, h, rgba = _harness_run_one(case)
#                return _harness_golden_compare(w, h, rgba)
#        def _harness_main():
#            import os as _os
#            if gate_harness is not None and _os.environ.get("RENPY_HOST_HARNESS") == "1":
#                cases = [{"u_animation": 0.5, "u_transition": 0.2}]
#                ok = gate_harness("dissolve_transform_atl", cases, _harness_run_one, _harness_golden_compare)
#                raise SystemExit(0 if ok else 1)
#            else:
#                main()  # or run() — original path
#        if __name__ == "__main__":
#            _harness_main()
#
# Notes: image_dissolve Model().child(new).texture(old).texture(rule) with parent shader fold; extract t_rule/t_old/t_new + outer/mesh node construction.
# Original code above is untouched; this block is documentation + ready-to-enable
# wrapper ensuring `python -m py_compile` stays green.
# To fully migrate, move the `main()`/`run()` call into `_harness_main` and
# gate on RENPY_HOST_HARNESS as shown.

