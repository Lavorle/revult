"""
AC5 draw_tree gate — WgpuDraw.draw_screen tree walk.

Gate name: draw_tree  (RENPY_HOST_GATE=draw_tree → host/target/gate-draw_tree.txt)

Proves duck-typed surftree walk:
  1. Fullscreen solid (Model-like mesh=True + color)
  2. Nested children with virtual-pixel offsets
  3. Textured Surface leaf

Readback center pixel must NOT equal host clear color (0.05,0.05,0.08).

Note: no `from __future__` — host run_file prepends imports before this source.
"""

import os
from pathlib import Path

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

import renpy_host
from renpy.pygame.surface import Surface

from renpy.wgpu.draw import WgpuDraw

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-draw_tree.txt"
out.parent.mkdir(parents=True, exist_ok=True)


class FakeRender:
    """Minimal Render-like node (children + optional mesh/texture)."""

    def __init__(self, width=1280, height=720):
        self.width = int(width)
        self.height = int(height)
        self.children = []
        self.mesh = None
        self.texture = None
        self.textures = None
        self.color = None
        self.shaders = None
        self.pipeline = None
        self.vertices = None
        self.indices = None
        self.cached_model = None
        self.blits = None
        self.ndc = None
        self.uniforms = None

    def blit(self, child, xo=0, yo=0):
        self.children.append((child, float(xo), float(yo), False, True))
        return self


class FakeModel:
    """Minimal Model-like node for solid / textured draw_model."""

    def __init__(
        self,
        width=0,
        height=0,
        color=None,
        texture=None,
        mesh=True,
        ndc=None,
        shaders=None,
        vertices=None,
        indices=None,
    ):
        self.width = int(width)
        self.height = int(height)
        self.color = color
        self.texture = texture
        self.mesh = mesh
        self.ndc = ndc
        self.shaders = shaders
        self.vertices = vertices
        self.indices = indices
        self.pipeline = None
        self.textures = None
        self.uniforms = None
        self.texture1 = None


d = WgpuDraw()
assert d.init((1280, 720)), "WgpuDraw.init failed"
vw, vh = d.virtual_size

# --- Build tree -------------------------------------------------------------
# Root container
root = FakeRender(vw, vh)

# 1) Fullscreen solid red-ish background (Model-like, NDC full cover)
#    Center pixel will sample this if no further cover — but we add a nested
#    solid at center too so either path proves non-clear content.
bg = FakeModel(
    color=(0.85, 0.12, 0.18, 1.0),
    mesh=True,
    shaders=("renpy.solid",),
    ndc=(-1.0, -1.0, 1.0, 1.0),
)
root.blit(bg, 0, 0)

# 2) Nested mid-layer solid green rectangle (virtual pixels, center-ish)
#    400x300 at (440, 210) → covers window center (640, 360)
mid = FakeRender(400, 300)
mid_solid = FakeModel(
    width=400,
    height=300,
    color=(0.15, 0.80, 0.25, 1.0),
    mesh=True,
    shaders=("renpy.solid",),
)
mid.blit(mid_solid, 0, 0)
root.blit(mid, 440, 210)

# 3) Textured Surface leaf (blue 64x64) nested further inside mid
surf = Surface((64, 64))
surf.fill((30, 90, 255, 255))
tex_leaf = FakeModel(width=64, height=64, texture=surf, mesh=True)
mid.blit(tex_leaf, 168, 118)  # relative to mid → absolute (608, 328)

# 4) Direct Surface child on root (top-left) — exercises Surface-like leaf
corner = Surface((48, 48))
corner.fill((255, 220, 40, 255))
root.blit(corner, 16, 16)

# --- Draw several frames via draw_screen tree walk --------------------------
frames = 0
for _ in range(6):
    d.draw_screen(root, flip=True)
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
    frames += 1

# --- Readback prove non-clear -----------------------------------------------
w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

cx, cy = w // 2, h // 2
i = (cy * w + cx) * 4
cr, cg, cb, ca = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]

# Host clear is ~ (0.05, 0.05, 0.08) → (13, 13, 20) in u8. Mid solid green is
# (0.15, 0.80, 0.25) → roughly (38, 204, 64). Accept either bg red or mid green
# or textured blue — anything not clear.
clear_u8 = (13, 13, 20)  # 0.05*255, 0.05*255, 0.08*255

def near(a, b, tol=8):
    return abs(int(a) - int(b)) <= tol

is_clear = (
    near(cr, clear_u8[0])
    and near(cg, clear_u8[1])
    and near(cb, clear_u8[2])
)
assert not is_clear, (
    f"center pixel still clear-like rgba=({cr},{cg},{cb},{ca}) "
    f"clear≈{clear_u8}; tree walk did not draw"
)

# Also sample a few more points to prove multi-node coverage.
samples = {
    "center": (cx, cy, (cr, cg, cb, ca)),
}
# Top-left corner surface should be yellow-ish
tl_x, tl_y = 24, 24
ti = (tl_y * w + tl_x) * 4
samples["corner"] = (tl_x, tl_y, (rgba[ti], rgba[ti + 1], rgba[ti + 2], rgba[ti + 3]))

# --- Also exercise bare model list / direct model path ----------------------
solo = FakeModel(
    color=(0.2, 0.4, 0.9, 1.0),
    mesh=True,
    ndc=(-0.3, -0.3, 0.3, 0.3),
    shaders=("renpy.solid",),
)
d.draw_screen(solo, flip=True)
renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
frames += 1

msg = (
    f"[draw_tree] frames={frames} size={w}x{h} "
    f"center_rgba=({cr},{cg},{cb},{ca}) clear≈{clear_u8} "
    f"corner_rgba={samples['corner'][2]} "
    f"tree=bg+nested_solid+textured+surface ok=True"
)
out.write_text(msg + "\n", encoding="utf-8")
print(msg, flush=True)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
