"""
AC-X3 product ImageDissolve with rule.png control (red→alpha matrixcolor).

Gate name: imagedissolve_product_rule
  (RENPY_HOST_GATE=imagedissolve_product_rule)

Builds a product-shaped ImageDissolve mesh tree:
  - control child: matrixcolor red→alpha over a grayscale ramp (rule-like)
  - bottom/old: red solid
  - top/new: blue solid
  - uniforms: u_renpy_dissolve_offset / multiplier at mid complete

Asserts:
  1. shaders map renpy.imagedissolve → imagedissolve_pipeline
  2. left (low red / low control.a after bake) stays bottom/red-dominant
  3. right (high red) shows top/blue-dominant
  4. not hard-cut all red or all blue
  5. matrixcolor control is RTT-baked (not pure HostTexture peel)

Hard-timeout friendly: pure draw path, no interact.
"""

import os
import sys
from pathlib import Path

from host.python.gates._harness import gate_harness, parametrized_gate

import renpy_host  # type: ignore

from renpy.wgpu.draw import HostTexture, WgpuDraw

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-imagedissolve_product_rule.txt"
out.parent.mkdir(parents=True, exist_ok=True)


class FakeRender:
    def __init__(self, width=1280, height=720, mesh=False):
        self.width = int(width)
        self.height = int(height)
        self.children = []
        self.mesh = mesh
        self.texture = None
        self.textures = None
        self.color = None
        self.shaders = None
        self.pipeline = None
        self.vertices = None
        self.indices = None
        self.cached_model = None
        self.cached_texture = None
        self.blits = None
        self.ndc = None
        self.uniforms = None
        self.loaded = False
        self.reverse = None
        self.operation = None
        self.operation_complete = None

    def blit(self, child, xo=0, yo=0):
        self.children.append((child, float(xo), float(yo), False, True))
        return self

    def get_size(self):
        return (self.width, self.height)


class Mat:
    """Row-major list Matrix stand-in matching renpy.display.matrix field names."""

    def __init__(self, vals):
        (
            self.xdx,
            self.xdy,
            self.xdz,
            self.xdw,
            self.ydx,
            self.ydy,
            self.ydz,
            self.ydw,
            self.zdx,
            self.zdy,
            self.zdz,
            self.zdw,
            self.wdx,
            self.wdy,
            self.wdz,
            self.wdw,
        ) = [float(v) for v in vals]


def _solid(w, h, rgba):
    r, g, b, a = rgba
    return bytes([r, g, b, a]) * (w * h)


notes = []
ok = True

draw = WgpuDraw()
assert draw.init((1280, 720))
draw._ensure_pipes()

W = H = 64
# Grayscale ramp control: left black (r=0), right white (r=255), alpha=255.
# Product ImageDissolve matrixcolor copies red → alpha.
control = bytearray()
for y in range(H):
    for x in range(W):
        v = 255 if x >= W // 2 else 0
        control.extend([v, v, v, 255])
control = bytes(control)
bottom = _solid(W, H, (255, 0, 0, 255))
top = _solid(W, H, (0, 0, 255, 255))

t_ctrl = renpy_host.create_texture_rgba(W, H, control)
t_bot = renpy_host.create_texture_rgba(W, H, bottom)
t_top = renpy_host.create_texture_rgba(W, H, top)

# matrixcolor red→alpha (product ImageDissolve reverse=False matrix)
# Matrix list [0,0,0,0, 0,0,0,0, 0,0,0,0, 1,0,0,0] → a = r
mc = FakeRender(W, H, mesh=None)
mc.shaders = ("renpy.matrixcolor",)
mc.uniforms = {
    "u_renpy_matrixcolor": Mat([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0])
}
mc.blit(HostTexture(t_ctrl, W, H))

# Extract must NOT peel past matrixcolor
pure = draw._extract_host_texture(mc)
notes.append(f"extract_matrixcolor={pure!r}")
if pure is not None:
    ok = False
    notes.append("FAIL: matrixcolor control peeled to HostTexture (dropped red→alpha)")

# Product ImageDissolve mesh node
# complete=0.5, ramplen=8 → offset = -1 + (8/256+1)*0.5 ≈ -0.484375; mult=32
offset = -1.0 + (8.0 / 256.0 + 1.0) * 0.5
mult = 256.0 / 8.0
root = FakeRender(1280, 720, mesh=True)
root.shaders = ("renpy.imagedissolve",)
root.uniforms = {
    "u_renpy_dissolve_offset": float(offset),
    "u_renpy_dissolve_multiplier": float(mult),
}
root.operation = 2  # IMAGEDISSOLVE
root.operation_complete = 0.5
root.blit(mc)
root.blit(HostTexture(t_bot, W, H))
root.blit(HostTexture(t_top, W, H))

# Map honesty
from renpy.wgpu import shaders as sh

if not sh.list_wgsl_parts():
    sh.register_builtin_core()
key = sh.host_pipeline_key("renpy.imagedissolve")
notes.append(f"map_imagedissolve={key}")
if key != "imagedissolve_pipeline":
    ok = False
    notes.append("FAIL: renpy.imagedissolve map not imagedissolve_pipeline")

# Draw via full tree path
for _ in range(3):
    renpy_host.begin_frame()
    try:
        draw.load_all_textures(root)
    except Exception as e:
        ok = False
        notes.append(f"FAIL: load_all_textures {type(e).__name__}: {e}")
    draw._draw_node(root, 0.0, 0.0)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
notes.append(f"rt={w}x{h} bytes={len(rgba)}")
lx, ly = w // 4, h // 2
rx, ry = (3 * w) // 4, h // 2
li = (ly * w + lx) * 4
ri = (ry * w + rx) * 4
left = tuple(rgba[li : li + 4])
right = tuple(rgba[ri : ri + 4])
notes.append(f"left_rgba={left} right_rgba={right}")

# Left (control.r=0 → a low after offset) → bottom red; right → top blue
if left[0] <= left[2]:
    ok = False
    notes.append("FAIL: left not bottom/red-dominant (matrixcolor bake or mix broken)")
if right[2] <= right[0]:
    ok = False
    notes.append("FAIL: right not top/blue-dominant")

# Not hard-cut all one color
if left[0] > 200 and right[0] > 200 and left[2] < 40 and right[2] < 40:
    ok = False
    notes.append("FAIL: hard-cut all red (control alpha ignored)")
if left[2] > 200 and right[2] > 200 and left[0] < 40 and right[0] < 40:
    ok = False
    notes.append("FAIL: hard-cut all blue")

notes.append(f"offset={offset} mult={mult}")
msg = "gate=imagedissolve_product_rule\nok={}\n{}\n".format(
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
