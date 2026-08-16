"""
A2 chrome_transparent_frame — fully transparent Frame over known BG is invisible.

Gate name: chrome_transparent_frame  (RENPY_HOST_GATE=chrome_transparent_frame)

Simulates product navigation_button background:
  - Source PNG-like Surface fully transparent (a=0 all pixels), size like
    idle_background.png (300x36) but smaller for speed.
  - 9-slice Frame tree matching imagelike.Frame.draw_pattern (non-tile)
    with Borders(4,4,4,4) into a larger dest (button-sized).
  - Drawn over a known non-black background (green).

Pass:
  - Dest center and mid-edge samples match BG (transparent Frame contributes nothing)
  - Outside dest also BG
  - Fail if dest becomes black / dark opaque slab (pre-A1 RTT-clear symptom)

Also checks reverse-scale path still draws a non-transparent orange Frame border
piece correctly (structure not broken by HostTexture extract).

Note: no from __future__; host run_file prepends imports.
"""

import os
from pathlib import Path

import renpy_host  # type: ignore
from renpy.pygame.surface import Surface
from renpy.wgpu.draw import HostTexture, WgpuDraw

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-chrome_transparent_frame.txt"
out.parent.mkdir(parents=True, exist_ok=True)

BG = (0, 180, 80, 255)  # green — distinct from arena clear / black slabs
ORANGE = (204, 102, 0, 255)
TRANSPARENT = (0, 0, 0, 0)

SRC_W, SRC_H = 60, 36
DST_W, DST_H = 300, 48  # product-ish button size
LEFT = TOP = RIGHT = BOTTOM = 4
OX, OY = 100, 100  # place on virtual canvas


class Mat2:
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
        self.cached_texture = None
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


def _near(c, target, tol=35):
    return all(abs(int(c[i]) - int(target[i])) <= tol for i in range(3))


def _is_blackish(c, tol=25):
    return int(c[0]) <= tol and int(c[1]) <= tol and int(c[2]) <= tol


def _make_source_tex(draw, rgba_color, w, h):
    s = Surface((w, h))
    s.fill(rgba_color)
    return draw.load_texture(s)


def _frame_tree(draw, src_tex, sw, sh, dw, dh, left, top, right, bottom):
    """Build multipiece Frame-like tree (parent no reverse; pieces reverse)."""

    def piece(sx0, sx1, sy0, sy1, dx0, dx1, dy0, dy1):
        csw, csh = sx1 - sx0, sy1 - sy0
        cdw, cdh = dx1 - dx0, dy1 - dy0
        if csw <= 0 or csh <= 0 or cdw <= 0 or cdh <= 0:
            return None
        # subsurface HostTexture (product image leaf)
        if isinstance(src_tex, HostTexture):
            sub = src_tex.subsurface((sx0, sy0, csw, csh))
        else:
            sub = src_tex
        # product: if size mismatch, wrap with reverse
        if csw != cdw or csh != cdh:
            pr = FakeRender(cdw, cdh)
            pr.reverse = Mat2(cdw / float(csw), 0, 0, cdh / float(csh))
            pr.forward = Mat2(csw / float(cdw), 0, 0, csh / float(cdh))
            # intermediate subsurface Render (no cached_texture) — product-like
            mid = FakeRender(csw, csh)
            mid.blit(sub, 0, 0)
            pr.blit(mid, 0, 0)
            return pr, dx0, dy0
        mid = FakeRender(cdw, cdh)
        mid.blit(sub, 0, 0)
        return mid, dx0, dy0

    parent = FakeRender(dw, dh)
    # 9-slice regions (same order as imagelike.draw_pattern)
    regions = [
        # top row
        (0, left, 0, top, 0, left, 0, top),
        (left, sw - right, 0, top, left, dw - right, 0, top),
        (sw - right, sw, 0, top, dw - right, dw, 0, top),
        # mid row
        (0, left, top, sh - bottom, 0, left, top, dh - bottom),
        (left, sw - right, top, sh - bottom, left, dw - right, top, dh - bottom),
        (sw - right, sw, top, sh - bottom, dw - right, dw, top, dh - bottom),
        # bottom row
        (0, left, sh - bottom, sh, 0, left, dh - bottom, dh),
        (left, sw - right, sh - bottom, sh, left, dw - right, dh - bottom, dh),
        (sw - right, sw, sh - bottom, sh, dw - right, dw, dh - bottom, dh),
    ]
    for sx0, sx1, sy0, sy1, dx0, dx1, dy0, dy1 in regions:
        p = piece(sx0, sx1, sy0, sy1, dx0, dx1, dy0, dy1)
        if p is None:
            continue
        node, xo, yo = p
        parent.blit(node, xo, yo)
    return parent


def main():
    vw, vh = 1280, 720
    draw = WgpuDraw()
    draw.init((vw, vh))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    # --- A2: fully transparent Frame over green BG must leave BG unchanged ---
    bg = Surface((vw, vh))
    bg.fill(BG)
    root = FakeRender(vw, vh)
    root.blit(bg, 0, 0)

    clear_tex = _make_source_tex(draw, TRANSPARENT, SRC_W, SRC_H)
    frame_clear = _frame_tree(
        draw, clear_tex, SRC_W, SRC_H, DST_W, DST_H, LEFT, TOP, RIGHT, BOTTOM
    )
    root.blit(frame_clear, OX, OY)

    draw.draw_screen(root, flip=True)
    try:
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:
        msg = "ok=False reason=read_rt err=%s" % e
        out.write_text(msg + "\n")
        print("[chrome_transparent_frame]", msg, flush=True)
        return

    sx = rw / float(vw)
    sy = rh / float(vh)

    # Samples inside the transparent Frame dest
    samples = {
        "center": (OX + DST_W // 2, OY + DST_H // 2),
        "mid_left": (OX + DST_W // 4, OY + DST_H // 2),
        "mid_right": (OX + 3 * DST_W // 4, OY + DST_H // 2),
        "edge": (OX + 2, OY + 2),
        "outside": (OX + DST_W + 40, OY + DST_H // 2),
        "far": (vw - 20, vh - 20),
    }
    rgb = {}
    for name, (px, py) in samples.items():
        r, g, b, a = _sample(rgba, rw, rh, px * sx, py * sy)
        rgb[name] = (r, g, b, a)

    inside_ok = all(
        _near(rgb[k], BG) and not _is_blackish(rgb[k])
        for k in ("center", "mid_left", "mid_right", "edge")
    )
    outside_ok = _near(rgb["outside"], BG) and _near(rgb["far"], BG)
    # Black slab fail: any inside sample pure black / near-black while BG is green
    black_slab = any(_is_blackish(rgb[k]) for k in ("center", "mid_left", "mid_right", "edge"))

    # --- Structure regression: orange multipiece Frame still paints orange ---
    root2 = FakeRender(vw, vh)
    root2.blit(bg, 0, 0)
    # orange border + black center source
    orange_surf = Surface((SRC_W, SRC_H))
    orange_surf.fill(ORANGE)
    # black center hole
    for y in range(TOP, SRC_H - BOTTOM):
        for x in range(LEFT, SRC_W - RIGHT):
            # fill via small solid uploads is heavy; use Surface fill of whole then re-upload
            pass
    # simpler: full orange is enough for border sample (center not asserted non-black)
    orange_tex = _make_source_tex(draw, ORANGE, SRC_W, SRC_H)
    frame_orange = _frame_tree(
        draw, orange_tex, SRC_W, SRC_H, DST_W, DST_H, LEFT, TOP, RIGHT, BOTTOM
    )
    root2.blit(frame_orange, OX, OY)
    draw.draw_screen(root2, flip=True)
    try:
        rw2, rh2, rgba2 = renpy_host.read_game_rt_rgba()
    except Exception as e:
        msg = "ok=False reason=read_rt_orange err=%s" % e
        out.write_text(msg + "\n")
        print("[chrome_transparent_frame]", msg, flush=True)
        return

    sx2 = rw2 / float(vw)
    sy2 = rh2 / float(vh)
    # top border mid
    tr, tg, tb, ta = _sample(rgba2, rw2, rh2, (OX + DST_W // 2) * sx2, (OY + 1) * sy2)
    # left border mid
    lr, lg, lb, la = _sample(rgba2, rw2, rh2, (OX + 1) * sx2, (OY + DST_H // 2) * sy2)
    orange_border_ok = (
        tr >= 150 and 40 <= tg <= 160 and tb <= 80 and (tr - tg) >= 30
    ) and (
        lr >= 150 and 40 <= lg <= 160 and lb <= 80 and (lr - lg) >= 30
    )

    # --- Direct transparent HostTexture reverse scale (no multipiece) ---
    root3 = FakeRender(vw, vh)
    root3.blit(bg, 0, 0)
    clear2 = _make_source_tex(draw, TRANSPARENT, 10, 10)
    piece = FakeRender(DST_W, DST_H)
    piece.reverse = Mat2(DST_W / 10.0, 0, 0, DST_H / 10.0)
    piece.blit(clear2, 0, 0)
    root3.blit(piece, OX, OY)
    draw.draw_screen(root3, flip=True)
    try:
        rw3, rh3, rgba3 = renpy_host.read_game_rt_rgba()
    except Exception as e:
        msg = "ok=False reason=read_rt_reverse err=%s" % e
        out.write_text(msg + "\n")
        print("[chrome_transparent_frame]", msg, flush=True)
        return
    sx3 = rw3 / float(vw)
    sy3 = rh3 / float(vh)
    cr, cg, cb, ca = _sample(rgba3, rw3, rh3, (OX + DST_W // 2) * sx3, (OY + DST_H // 2) * sy3)
    reverse_clear_ok = _near((cr, cg, cb), BG) and not _is_blackish((cr, cg, cb))

    ok = bool(inside_ok and outside_ok and not black_slab and orange_border_ok and reverse_clear_ok)
    msg = (
        "ok=%s inside_ok=%s outside_ok=%s black_slab=%s orange_border_ok=%s "
        "reverse_clear_ok=%s center=%s edge=%s outside=%s orange_top=(%d,%d,%d) "
        "reverse_center=(%d,%d,%d) dest=%dx%d src=%dx%d border=%d path=frame_multipiece+reverse"
        % (
            ok,
            inside_ok,
            outside_ok,
            black_slab,
            orange_border_ok,
            reverse_clear_ok,
            rgb["center"][:3],
            rgb["edge"][:3],
            rgb["outside"][:3],
            tr,
            tg,
            tb,
            cr,
            cg,
            cb,
            DST_W,
            DST_H,
            SRC_W,
            SRC_H,
            LEFT,
        )
    )
    out.write_text(msg + "\n")
    print("[chrome_transparent_frame]", msg, flush=True)


main()
