"""
AC-X4 HuangmeiC image_dissolve product alias (dissolve_transform).

Gate name: image_dissolve_alias
  (RENPY_HOST_GATE=image_dissolve_alias)

Synthetic Model-like tree matching transforms.rpy:
  Model().child(new).texture(old).texture(rule)
  shader "image_dissolve"
  u_transition / u_animation

Checks:
  1. shaders map image_dissolve → imagedissolve_pipeline
  2. register_shader alias soft-accepts under host_build
  3. mid animation (u_animation=0.5) is progressive (not hard-cut / blank)
  4. slot remap control=rule, bottom=old, top=new with red-channel control
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

import renpy_host  # type: ignore

from renpy.wgpu.draw import HostTexture, WgpuDraw

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-image_dissolve_alias.txt"
out.parent.mkdir(parents=True, exist_ok=True)

notes = []
ok = True

# Pipeline map honesty
import renpy

renpy.host_build = True  # type: ignore[attr-defined]
from renpy.wgpu import shaders as sh

if not sh.list_wgsl_parts():
    sh.register_builtin_core()

key = sh.host_pipeline_key("image_dissolve")
notes.append(f"map_image_dissolve={key}")
if key != "imagedissolve_pipeline":
    ok = False
    notes.append("FAIL: image_dissolve not mapped to imagedissolve_pipeline")

# Soft-register product GLSL alias (HuangmeiC transforms.rpy path)
try:
    from renpy.gl2.gl2shadercache import register_shader

    register_shader(
        "image_dissolve",
        variables="uniform float u_transition; uniform float u_animation;",
        fragment_300="/* stub */",
    )
    notes.append("register_shader_image_dissolve=ok")
except Exception as e:  # noqa: BLE001
    ok = False
    notes.append(f"FAIL: register_shader image_dissolve {type(e).__name__}: {e}")

draw = WgpuDraw()
assert draw.init((1280, 720))
draw._ensure_pipes()

W = H = 64


def _solid(w, h, rgba):
    r, g, b, a = rgba
    return bytes([r, g, b, a]) * (w * h)


# rule: left black, right white (grayscale RGB, a=255) — product rule.png shape
rule = bytearray()
for y in range(H):
    for x in range(W):
        v = 255 if x >= W // 2 else 0
        rule.extend([v, v, v, 255])
rule = bytes(rule)
old = _solid(W, H, (255, 0, 0, 255))  # red
new = _solid(W, H, (0, 0, 255, 255))  # blue

t_rule = renpy_host.create_texture_rgba(W, H, rule)
t_old = renpy_host.create_texture_rgba(W, H, old)
t_new = renpy_host.create_texture_rgba(W, H, new)

# Product Model texture order: child(new), texture(old), texture(rule)
# → slots [new, old, rule]; draw remaps to control=rule, bottom=old, top=new


class _Node:
    pass


n = _Node()
n.width = 1280
n.height = 720
n.shaders = ("image_dissolve",)
n.uniforms = {"u_transition": 0.2, "u_animation": 0.5}
n.textures = [
    HostTexture(t_new, W, H),
    HostTexture(t_old, W, H),
    HostTexture(t_rule, W, H),
]
n.ndc = (-1.0, -1.0, 1.0, 1.0)
n.color = (1, 1, 1, 1)
n.mesh = True
n.children = None
n.cached_model = None

# Pack check
u = draw._pack_uniforms(n.uniforms, n.shaders)
notes.append(f"pack_u={u[:4] if u else None}")
if u is None or abs(u[2] - 1.0) > 1e-5:
    ok = False
    notes.append("FAIL: product alias must set data0.z=1 (red channel)")

for _ in range(3):
    renpy_host.begin_frame()
    draw._draw_node(n, 0.0, 0.0)
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

# anim=0.5, offset=0: a = control.r → left a=0 bottom/red; right a=1 top/blue
if left[0] <= left[2]:
    ok = False
    notes.append("FAIL: left not old/red (slot remap or red-channel control broken)")
if right[2] <= right[0]:
    ok = False
    notes.append("FAIL: right not new/blue")

# Blank / clear guard
mean_r = sum(rgba[i] for i in range(0, len(rgba), 4)) / (w * h)
mean_b = sum(rgba[i] for i in range(2, len(rgba), 4)) / (w * h)
notes.append(f"mean_r={mean_r:.1f} mean_b={mean_b:.1f}")
if mean_r < 1.0 and mean_b < 1.0:
    ok = False
    notes.append("FAIL: blank/clear frame")

msg = "gate=image_dissolve_alias\nok={}\n{}\n".format(
    "True" if ok else "False",
    "\n".join(notes),
)
out.write_text(msg, encoding="utf-8")
sys.stdout.write(msg)
sys.stdout.flush()
if not ok:
    raise SystemExit(1)

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
