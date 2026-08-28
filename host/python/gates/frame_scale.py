"""
AC-C1 frame_scale gate — reverse-matrix stretch with correct UV mapping.

Gate name: frame_scale  (RENPY_HOST_GATE=frame_scale)

Mirrors Frame imagelike path: small source tile stretched into a larger dest
via reverse Matrix2D. Uses a NON-UNIFORM source (magenta edges, cyan center)
so ClampToEdge edge-fill cannot false-pass when UV is wrong.

Checks:
  1. Dest center is cyan (source center mapped correctly under stretch)
  2. Dest left-edge interior is magenta (source edge, not wrong crop)
  3. Outside dest region stays background

Note: no from __future__; host run_file prepends imports.
"""

import os
from pathlib import Path


import renpy_host  # type: ignore
from renpy.pygame.surface import Surface

from renpy.wgpu.draw import WgpuDraw

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-frame_scale.txt"
out.parent.mkdir(parents=True, exist_ok=True)

# Distinct colors so edge-clamp cannot look like a correct center sample.
MAGENTA = (220, 0, 220, 255)
CYAN = (0, 220, 220, 255)
BG = (20, 20, 30, 255)


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

    # 16x16 source: magenta border, cyan 4x4 center (rows/cols 6..9).
    src_w, src_h = 16, 16
    dst_w, dst_h = 320, 180
    src = Surface((src_w, src_h))
    src.fill(MAGENTA)
    for y in range(6, 10):
        for x in range(6, 10):
            src.set_at((x, y), CYAN)

    piece = FakeRender(dst_w, dst_h)
    piece.reverse = Mat2(dst_w / float(src_w), 0, 0, dst_h / float(src_h))
    piece.forward = Mat2(src_w / float(dst_w), 0, 0, src_h / float(dst_h))
    piece.blit(src, 0, 0)

    root = FakeRender(vw, vh)
    bg = Surface((vw, vh))
    bg.fill(BG)
    root.blit(bg, 0, 0)
    root.blit(piece, 100, 100)

    draw.draw_screen(root, flip=True)
    try:
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:
        msg = f"ok=False reason=read_rt err={e}"
        out.write_text(msg + "\n")
        print("[frame_scale]", msg, flush=True)
        return

    sx = rw / float(vw)
    sy = rh / float(vh)
    # Dest center → source center (cyan).
    cx = 100 + dst_w // 2
    cy = 100 + dst_h // 2
    # Dest left interior (a few px inside left edge) → source left (magenta).
    lx = 100 + max(2, dst_w // 40)
    ly = 100 + dst_h // 2
    # Outside dest (screen corner) → background.
    r_c, g_c, b_c, _a_c = _sample(rgba, rw, rh, cx * sx, cy * sy)
    r_l, g_l, b_l, _a_l = _sample(rgba, rw, rh, lx * sx, ly * sy)
    r_o, g_o, b_o, _a_o = _sample(rgba, rw, rh, 10 * sx, 10 * sy)

    center_ok = _near((r_c, g_c, b_c), CYAN)
    edge_ok = _near((r_l, g_l, b_l), MAGENTA)
    outside_ok = _near((r_o, g_o, b_o), BG)
    ok = center_ok and edge_ok and outside_ok
    msg = (
        "ok=%s center_rgb=(%d,%d,%d) edge_rgb=(%d,%d,%d) outside_rgb=(%d,%d,%d) "  # noqa: UP031
        "center_ok=%s edge_ok=%s outside_ok=%s dest=%dx%d src=%dx%d"
        % (
            ok,
            r_c,
            g_c,
            b_c,
            r_l,
            g_l,
            b_l,
            r_o,
            g_o,
            b_o,
            center_ok,
            edge_ok,
            outside_ok,
            dst_w,
            dst_h,
            src_w,
            src_h,
        )
    )
    out.write_text(msg + "\n")
    print("[frame_scale]", msg, flush=True)


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
