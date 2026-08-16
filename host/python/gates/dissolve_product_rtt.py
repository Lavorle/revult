"""
AC-D1 dissolve_product_rtt gate — product-faithful dissolve with nested Renders.

Gate name: dissolve_product_rtt  (RENPY_HOST_GATE=dissolve_product_rtt)

Unlike dissolve_blend (solid Surfaces as dissolve children — never RTT), this
gate builds nested FakeRender trees as dissolve children so `_child_to_texture`
must go through `render_to_texture`. That exercises the prepare-in-RTT contract
required for product Dissolve/Fade of full scene Renders.

At u_renpy_dissolve=0.5, game RT mean must show a blend (R and B elevated), not
clear, not hard red/blue.

Note: no from __future__; host run_file prepends imports.
"""

import os
from pathlib import Path

import renpy_host  # type: ignore
from renpy.pygame.surface import Surface
from renpy.wgpu.draw import WgpuDraw

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-dissolve_product_rtt.txt"
out.parent.mkdir(parents=True, exist_ok=True)


class FakeRender:
    """Minimal Render-like node (children + mesh attrs) for WgpuDraw walks."""

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

    def blit(self, child, xo=0, yo=0):
        self.children.append((child, float(xo), float(yo), False, True))
        return self

    def get_size(self):
        return (self.width, self.height)


def _solid_surface(w, h, rgba):
    s = Surface((w, h))
    s.fill(rgba)
    return s


def _scene_render(w, h, rgba):
    """Build a nested Render tree whose leaf is a solid Surface.

    Product dissolve children are full scene Renders, not Surfaces. The leaf
    Surface sits under a container Render so `_child_to_texture` must RTT.
    """
    leaf = FakeRender(w, h, mesh=False)
    leaf.blit(_solid_surface(w, h, rgba), 0, 0)
    # Extra nesting mirrors scene → layer → displayable depth.
    mid = FakeRender(w, h, mesh=False)
    mid.blit(leaf, 0, 0)
    root = FakeRender(w, h, mesh=False)
    root.blit(mid, 0, 0)
    return root


def _mean_rgb(rgba, w, h):
    n = w * h
    if n <= 0 or len(rgba) < n * 4:
        return 0.0, 0.0, 0.0
    rs = gs = bs = 0
    step = max(1, n // 50000)  # subsample large RTs
    count = 0
    for i in range(0, n, step):
        o = i * 4
        rs += rgba[o]
        gs += rgba[o + 1]
        bs += rgba[o + 2]
        count += 1
    inv = 1.0 / max(1, count)
    return rs * inv, gs * inv, bs * inv


def main():
    w, h = 1280, 720
    draw = WgpuDraw()
    draw.init((w, h))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    # Nested Render trees (NOT Surfaces) as dissolve children → forces RTT.
    old = _scene_render(w, h, (255, 0, 0, 255))
    new = _scene_render(w, h, (0, 0, 255, 255))
    root = FakeRender(w, h, mesh=True)
    root.shaders = ("renpy.dissolve",)
    root.uniforms = {"u_renpy_dissolve": 0.5}
    root.blit(old, 0, 0)
    root.blit(new, 0, 0)

    draw.draw_screen(root, flip=True)
    try:
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:
        msg = "ok=False reason=read_rt err=%s" % e
        out.write_text(msg + "\n")
        print("[dissolve_product_rtt]", msg, flush=True)
        return

    mr, mg, mb = _mean_rgb(rgba, rw, rh)
    # Pure red ~ (255,0,0); pure blue ~ (0,0,255); 50% blend ~ (127,0,127).
    # Blank/black RTT slots → clear_like or hard cut to one side.
    clear_like = mr < 40 and mg < 40 and mb < 40
    hard_red = mr > 200 and mb < 40
    hard_blue = mb > 200 and mr < 40
    blended = (mr > 40 and mb > 40) and not clear_like
    ok = blended and not hard_red and not hard_blue
    msg = (
        "ok=%s mean=(%.1f,%.1f,%.1f) blended=%s hard_red=%s hard_blue=%s "
        "clear_like=%s nested_renders=True u=0.5"
        % (ok, mr, mg, mb, blended, hard_red, hard_blue, clear_like)
    )
    out.write_text(msg + "\n")
    print("[dissolve_product_rtt]", msg, flush=True)


main()
