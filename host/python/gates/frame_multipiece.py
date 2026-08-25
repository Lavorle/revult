"""
AC-B2 frame_multipiece gate — 9-slice Frame with orange border + black center.

Gate name: frame_multipiece  (RENPY_HOST_GATE=frame_multipiece)

Simulates imagelike.Frame product tree:
  - Source image: orange (204,102,0) border + solid black center (by design)
  - Dest larger than source with Borders(10,10,10,10) non-tile
  - 9 pieces: corners unscaled; edges/center reverse-scaled into dest slots
  - Parent Frame Render blits pieces at dest offsets (no reverse on parent)

Checks:
  1. Outer border band has orange (R high) — structure present
  2. Deep center may be black (intentional product frame.png semantics)
  3. Outside dest stays background
  4. Fail if entire dest is featureless black with no border structure

Do NOT assert center non-black.

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

from renpy.wgpu.draw import HostTexture, WgpuDraw

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-frame_multipiece.txt"
out.parent.mkdir(parents=True, exist_ok=True)

ORANGE = (204, 102, 0, 255)
BLACK = (0, 0, 0, 255)
BG = (20, 20, 30, 255)

# Source / dest / border sizes (product-shaped, smaller than full frame.png).
SRC_W, SRC_H = 60, 40
DST_W, DST_H = 300, 200
LEFT = TOP = RIGHT = BOTTOM = 10


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


def _near(c, target, tol=50):
    return all(abs(int(c[i]) - int(target[i])) <= tol for i in range(3))


def _is_orange(c, tol=50):
    """Orange border: high R, mid G, low B (product 204,102,0)."""
    r, g, b = int(c[0]), int(c[1]), int(c[2])
    return r >= 150 and 40 <= g <= 160 and b <= 60 and (r - g) >= 40


def _is_blackish(c, tol=30):
    return int(c[0]) <= tol and int(c[1]) <= tol and int(c[2]) <= tol


def _make_source_surface():
    """60x40: 10px orange border, black interior (product frame.png semantics)."""
    s = Surface((SRC_W, SRC_H))
    s.fill(ORANGE)
    # Black center interior.
    for y in range(TOP, SRC_H - BOTTOM):
        for x in range(LEFT, SRC_W - RIGHT):
            s.set_at((x, y), BLACK)
    return s


def _piece(tex, sx0, sy0, csw, csh, cdw, cdh):
    """Build one Frame piece: subsurface (+ reverse when sizes differ)."""
    sub = tex.subsurface((sx0, sy0, csw, csh))
    if csw == cdw and csh == cdh:
        return sub
    piece = FakeRender(cdw, cdh)
    piece.reverse = Mat2(cdw / float(csw), 0, 0, cdh / float(csh))
    piece.forward = Mat2(csw / float(cdw), 0, 0, csh / float(cdh))
    piece.blit(sub, 0, 0)
    return piece


def _build_frame(tex):
    """9-slice Frame tree matching imagelike.Frame.draw_pattern (non-tile)."""
    sw, sh = SRC_W, SRC_H
    dw, dh = DST_W, DST_H
    left, top, right, bottom = LEFT, TOP, RIGHT, BOTTOM

    # Dest / source region helpers (same signs as imagelike draw()).
    def regions(x0, x1, y0, y1):
        # left side
        if x0 >= 0:
            dx0, sx0 = x0, x0
        else:
            dx0, sx0 = dw + x0, sw + x0
        if x1 > 0:
            dx1, sx1 = x1, x1
        else:
            dx1, sx1 = dw + x1, sw + x1
        if y0 >= 0:
            dy0, sy0 = y0, y0
        else:
            dy0, sy0 = dh + y0, sh + y0
        if y1 > 0:
            dy1, sy1 = y1, y1
        else:
            dy1, sy1 = dh + y1, sh + y1
        csw, csh = sx1 - sx0, sy1 - sy0
        cdw, cdh = dx1 - dx0, dy1 - dy0
        return sx0, sy0, csw, csh, dx0, dy0, cdw, cdh

    frame = FakeRender(dw, dh)

    def draw(x0, x1, y0, y1):
        sx0, sy0, csw, csh, dx0, dy0, cdw, cdh = regions(x0, x1, y0, y1)
        if csw <= 0 or csh <= 0 or cdw <= 0 or cdh <= 0:
            return
        piece = _piece(tex, sx0, sy0, csw, csh, cdw, cdh)
        frame.blit(piece, dx0, dy0)

    # Top row
    if top:
        if left:
            draw(0, left, 0, top)
        draw(left, -right, 0, top)
        if right:
            draw(-right, 0, 0, top)
    # Middle row
    if left:
        draw(0, left, top, -bottom)
    draw(left, -right, top, -bottom)
    if right:
        draw(-right, 0, top, -bottom)
    # Bottom row
    if bottom:
        if left:
            draw(0, left, -bottom, 0)
        draw(left, -right, -bottom, 0)
        if right:
            draw(-right, 0, -bottom, 0)

    return frame


def main():
    vw, vh = 1280, 720
    draw = WgpuDraw()
    draw.init((vw, vh))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    src = _make_source_surface()
    tex = draw.load_texture(src)
    if not isinstance(tex, HostTexture) or tex.handle <= 0:
        # Fallback: wrap raw handle if load_texture returns int.
        if isinstance(tex, int) and tex > 0:
            tex = HostTexture(tex, SRC_W, SRC_H)
        else:
            msg = f"ok=False reason=load_texture_failed tex={tex!r}"
            out.write_text(msg + "\n")
            print("[frame_multipiece]", msg, flush=True)
            return

    frame = _build_frame(tex)
    ox, oy = 100, 100

    root = FakeRender(vw, vh)
    bg = Surface((vw, vh))
    bg.fill(BG)
    root.blit(bg, 0, 0)
    root.blit(frame, ox, oy)

    draw.draw_screen(root, flip=True)
    try:
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:
        msg = f"ok=False reason=read_rt err={e}"
        out.write_text(msg + "\n")
        print("[frame_multipiece]", msg, flush=True)
        return

    sx = rw / float(vw)
    sy = rh / float(vh)

    # Border band samples (a few px inside each outer edge of dest).
    inset = 3
    samples = {
        "top": (ox + DST_W // 2, oy + inset),
        "bottom": (ox + DST_W // 2, oy + DST_H - 1 - inset),
        "left": (ox + inset, oy + DST_H // 2),
        "right": (ox + DST_W - 1 - inset, oy + DST_H // 2),
        "tl": (ox + inset, oy + inset),
        "center": (ox + DST_W // 2, oy + DST_H // 2),
        "outside": (10, 10),
    }
    rgb = {}
    for name, (px, py) in samples.items():
        r, g, b, a = _sample(rgba, rw, rh, px * sx, py * sy)
        rgb[name] = (r, g, b, a)

    border_hits = sum(
        1
        for k in ("top", "bottom", "left", "right", "tl")
        if _is_orange(rgb[k])
    )
    # Need at least 3 of 5 border samples orange → structure present.
    border_ok = border_hits >= 3
    # Center may be black (product design) — record only, do not fail on black.
    center_black = _is_blackish(rgb["center"])
    center_orange = _is_orange(rgb["center"])
    outside_ok = _near(rgb["outside"], BG)
    # Featureless black dest = all border samples blackish and not orange.
    featureless_black = border_hits == 0 and all(
        _is_blackish(rgb[k]) for k in ("top", "bottom", "left", "right", "tl", "center")
    )

    ok = border_ok and outside_ok and not featureless_black
    msg = (
        "ok=%s border_hits=%d/%d border_ok=%s center_rgb=(%d,%d,%d) "  # noqa: UP031
        "center_black=%s center_orange=%s outside_ok=%s featureless_black=%s "
        "top=%s left=%s dest=%dx%d src=%dx%d border=%d"
        % (
            ok,
            border_hits,
            5,
            border_ok,
            rgb["center"][0],
            rgb["center"][1],
            rgb["center"][2],
            center_black,
            center_orange,
            outside_ok,
            featureless_black,
            rgb["top"][:3],
            rgb["left"][:3],
            DST_W,
            DST_H,
            SRC_W,
            SRC_H,
            LEFT,
        )
    )
    out.write_text(msg + "\n")
    print("[frame_multipiece]", msg, flush=True)


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
