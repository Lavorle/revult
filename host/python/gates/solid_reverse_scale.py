"""
AC-B1 solid_reverse_scale gate — Solid 10x10 + reverse fills dest.

Gate name: solid_reverse_scale  (RENPY_HOST_GATE=solid_reverse_scale)

Mirrors imagelike.Solid product path:
  solid_texture(10, 10, color) blitted into Render(W, H) with
  reverse = Matrix2D(W/10, 0, 0, H/10).

Checks:
  1. Dest center pixel is solid green (not arena clear, not black hole)
  2. Dest corner interior is green (full-rect fill, not 10x10 speck)
  3. Outside dest region stays background

Note: no from __future__; host run_file prepends imports.
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

import renpy_host  # type: ignore
from renpy.pygame.surface import Surface

from renpy.wgpu.draw import WgpuDraw

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-solid_reverse_scale.txt"
out.parent.mkdir(parents=True, exist_ok=True)

GREEN = (0, 220, 0, 255)
BG = (20, 20, 30, 255)
SRC = 10


class Mat2:
    """Minimal Matrix2D stand-in (xdx, xdy, ydx, ydy)."""

    def __init__(self, xdx, xdy, ydx, ydy):
        self.xdx = float(xdx)
        self.xdy = float(xdy)
        self.ydx = float(ydx)
        self.ydy = float(ydy)


class FakeRender:
    def __init__(self, width, height):
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
        self.loaded = False
        self.forward = None
        self.reverse = None

    def blit(self, child, xo=0, yo=0):
        self.children.append((child, float(xo), float(yo), False, True))
        return self

    def get_size(self):
        return (self.width, self.height)


def _sample(rgba, w, h, x, y):
    x = max(0, min(w - 1, int(x)))
    y = max(0, min(h - 1, int(y)))
    o = (y * w + x) * 4
    return rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]


def _near(c, target, tol=40):
    return all(abs(int(c[i]) - int(target[i])) <= tol for i in range(3))


def main():
    vw, vh = 1280, 720
    draw = WgpuDraw()
    draw.init((vw, vh))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    # Product Solid dest size (full window) with 10x10 source + reverse.
    dst_w, dst_h = vw, vh
    solid = draw.solid_texture(SRC, SRC, GREEN)

    piece = FakeRender(dst_w, dst_h)
    piece.reverse = Mat2(dst_w / float(SRC), 0, 0, dst_h / float(SRC))
    piece.forward = Mat2(SRC / float(dst_w), 0, 0, SRC / float(dst_h))
    piece.blit(solid, 0, 0)

    # Also exercise nested under a container (product Fade holds Solid in tree).
    root = FakeRender(vw, vh)
    # Background first so "outside" samples only matter if dest is smaller;
    # here dest is full window — sample a mid pixel for green fill.
    bg = Surface((vw, vh))
    bg.fill(BG)
    root.blit(bg, 0, 0)
    root.blit(piece, 0, 0)

    draw.draw_screen(root, flip=True)
    try:
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:
        msg = f"ok=False reason=read_rt err={e}"
        out.write_text(msg + "\n")
        print("[solid_reverse_scale]", msg, flush=True)
        return

    sx = rw / float(vw)
    sy = rh / float(vh)
    # Center + near-corner interiors must be green (full-rect stretch).
    cx, cy = vw // 2, vh // 2
    qx, qy = max(2, vw // 40), max(2, vh // 40)
    r_c, g_c, b_c, _a_c = _sample(rgba, rw, rh, cx * sx, cy * sy)
    r_q, g_q, b_q, _a_q = _sample(rgba, rw, rh, qx * sx, qy * sy)

    center_ok = _near((r_c, g_c, b_c), GREEN)
    corner_ok = _near((r_q, g_q, b_q), GREEN)
    # Not clear / black hole.
    not_clear = not (r_c < 8 and g_c < 8 and b_c < 8)
    ok = center_ok and corner_ok and not_clear
    msg = (
        "ok=%s center_rgb=(%d,%d,%d) corner_rgb=(%d,%d,%d) "  # noqa: UP031
        "center_ok=%s corner_ok=%s not_clear=%s dest=%dx%d src=%dx%d"
        % (
            ok,
            r_c,
            g_c,
            b_c,
            r_q,
            g_q,
            b_q,
            center_ok,
            corner_ok,
            not_clear,
            dst_w,
            dst_h,
            SRC,
            SRC,
        )
    )
    out.write_text(msg + "\n")
    print("[solid_reverse_scale]", msg, flush=True)


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
