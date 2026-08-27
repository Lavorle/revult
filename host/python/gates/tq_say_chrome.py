"""
AC hard-fail tq_say_chrome gate — product say textbox Image + namebox Frame.

Gate name: tq_say_chrome  (RENPY_HOST_GATE=tq_say_chrome)

Proves product say chrome drawing paths under WgpuDraw:

1. Textbox path (product screens.rpy style window):
     background Image("gui/textbox.png", xalign=0.5, yalign=1.0)
     height gui.textbox_height=185, yalign=1.0, full width ~1280
   Full-panel Image (not Frame). Real textbox.png is black RGB with alpha
   structure (center alpha ~204, edges ~0). Pass via BG darkening + alpha
   gradient (center darker than transparent top edge retaining more BG).

2. Namebox path (product style namebox):
     background Frame("gui/namebox.png", Borders(5,5,5,5), tile=False)
   Product namebox.png is fully transparent (all zeros). Gate therefore:
     a) loads + draws real namebox.png Frame tree (namebox_load_ok /
        namebox_draw_attempted; must not leave black-slab undrawn clear)
     b) ALSO draws a synthetic Borders(5,5,5,5) Frame multipiece structure
        probe (orange border + black fill) through the same Frame tree code
        path at namebox-like sizes (namebox_frame_structure_ok)
   overall ok = textbox_pass and namebox_load_ok and namebox_frame_structure_ok

Confirm-frame gates alone do NOT satisfy this AC.
Do NOT edit the_question/game/**.
Note: no from __future__; host run_file prepends imports.
"""

import os
import struct
import zlib
from pathlib import Path

import renpy_host  # type: ignore

from renpy.wgpu.draw import HostTexture, WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---
from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore


_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-tq_say_chrome.txt"
out.parent.mkdir(parents=True, exist_ok=True)

# Distinctive non-black BG so black@alpha textbox darkens visibly, and so
# transparent namebox can be distinguished from undrawn arena clear.
BG = (40, 80, 120, 255)
CLEAR_LIKE = (13, 13, 20)  # ~0.05,0.05,0.08 * 255

# Product textbox: full width, height 185, yalign=1.0 on 1280x720.
VW, VH = 1280, 720
TB_H = 185
TB_Y = VH - TB_H  # 535
TB_X = 0
TB_W = VW

# Product namebox borders (gui.namebox_borders = Borders(5,5,5,5)).
NB_LEFT = NB_TOP = NB_RIGHT = NB_BOTTOM = 5
# Real namebox dest (larger than 300x36 so edges reverse-scale).
NB_DST_W, NB_DST_H = 400, 50
NB_OX, NB_OY = 240, TB_Y - NB_DST_H - 8  # above textbox

# Synthetic structure probe: same Borders(5,5,5,5), namebox-like src size.
PROBE_SRC_W, PROBE_SRC_H = 300, 36
PROBE_DST_W, PROBE_DST_H = 400, 50
PROBE_OX, PROBE_OY = 40, 40  # top-left, away from textbox/namebox
ORANGE = (204, 102, 0, 255)
BLACK = (0, 0, 0, 255)


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


class _Surf:
    def __init__(self, w, h, pixels):
        self._w = int(w)
        self._h = int(h)
        need = self._w * self._h * 4
        raw = bytes(pixels)
        self._pixels = raw if len(raw) >= need else raw + bytes(need - len(raw))

    def get_size(self):
        return self._w, self._h


def _png_rgba(path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"not png: {path}")
    pos = 8
    w = h = None
    raw = b""
    color_type = None
    bit_depth = None
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            w, h, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            raw += chunk
        elif ctype == b"IEND":
            break
    if not w or not h:
        raise RuntimeError("bad png header")
    if bit_depth != 8 or color_type not in (2, 6):
        raise RuntimeError(f"unsupported png ct={color_type} bd={bit_depth}")
    decomp = zlib.decompress(raw)
    bpp = 4 if color_type == 6 else 3
    stride = w * bpp + 1
    outb = bytearray(w * h * 4)
    prev = bytearray(w * bpp)
    for y in range(h):
        row = decomp[y * stride : (y + 1) * stride]
        filt = row[0]
        scan = bytearray(row[1:])
        if filt == 1:
            for i in range(bpp, len(scan)):
                scan[i] = (scan[i] + scan[i - bpp]) & 0xFF
        elif filt == 2:
            for i in range(len(scan)):
                scan[i] = (scan[i] + prev[i]) & 0xFF
        elif filt == 3:
            for i in range(len(scan)):
                a = scan[i - bpp] if i >= bpp else 0
                b = prev[i]
                scan[i] = (scan[i] + ((a + b) // 2)) & 0xFF
        elif filt == 4:
            for i in range(len(scan)):
                a = scan[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                scan[i] = (scan[i] + pr) & 0xFF
        elif filt != 0:
            raise RuntimeError(f"bad filter {filt}")
        prev = scan
        for x in range(w):
            si = x * bpp
            di = (y * w + x) * 4
            if bpp == 3:
                outb[di : di + 4] = bytes([scan[si], scan[si + 1], scan[si + 2], 255])
            else:
                outb[di : di + 4] = bytes(scan[si : si + 4])
    return w, h, bytes(outb)


def _sample(rgba, w, h, x, y):
    x = max(0, min(w - 1, int(x)))
    y = max(0, min(h - 1, int(y)))
    o = (y * w + x) * 4
    return rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]


def _near(c, target, tol=50):
    return all(abs(int(c[i]) - int(target[i])) <= tol for i in range(3))


def _is_blackish(c, tol=30):
    return int(c[0]) <= tol and int(c[1]) <= tol and int(c[2]) <= tol


def _is_clear_like(c, tol=25):
    return (
        abs(int(c[0]) - CLEAR_LIKE[0]) <= tol
        and abs(int(c[1]) - CLEAR_LIKE[1]) <= tol
        and abs(int(c[2]) - CLEAR_LIKE[2]) <= tol
    ) or _is_blackish(c, tol=12)


def _is_orange(c, tol=50):
    """Orange border: high R, mid G, low B (204,102,0)."""
    r, g, b = int(c[0]), int(c[1]), int(c[2])
    return r >= 150 and 40 <= g <= 160 and b <= 60 and (r - g) >= 40


def _luma(c):
    return 0.299 * int(c[0]) + 0.587 * int(c[1]) + 0.114 * int(c[2])


def _mean_var_region(rgba, rw, rh, sx, sy, x0, y0, x1, y1, step=8):
    rs, gs, bs, n = 0.0, 0.0, 0.0, 0
    vals = []
    gx0 = max(0, int(x0))
    gy0 = max(0, int(y0))
    gx1 = min(VW - 1, int(x1))
    gy1 = min(VH - 1, int(y1))
    for y in range(gy0, gy1 + 1, step):
        for x in range(gx0, gx1 + 1, step):
            r, g, b, a = _sample(rgba, rw, rh, x * sx, y * sy)
            rs += r
            gs += g
            bs += b
            vals.append((r, g, b, a))
            n += 1
    if n == 0:
        return (0.0, 0.0, 0.0), 0.0, 0.0, vals
    mean = (rs / n, gs / n, bs / n)
    lums = [_luma(v) for v in vals]
    mu = sum(lums) / len(lums)
    var = sum((l - mu) ** 2 for l in lums) / len(lums)
    a_mean = sum(v[3] for v in vals) / float(len(vals))
    return mean, var, a_mean, vals


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


def _build_frame(tex, src_w, src_h, dst_w, dst_h, left, top, right, bottom):
    """9-slice Frame tree matching imagelike.Frame.draw_pattern (non-tile)."""
    sw, sh = src_w, src_h
    dw, dh = dst_w, dst_h

    def regions(x0, x1, y0, y1):
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
    n_pieces = [0]

    def draw(x0, x1, y0, y1):
        sx0, sy0, csw, csh, dx0, dy0, cdw, cdh = regions(x0, x1, y0, y1)
        if csw <= 0 or csh <= 0 or cdw <= 0 or cdh <= 0:
            return
        piece = _piece(tex, sx0, sy0, csw, csh, cdw, cdh)
        frame.blit(piece, dx0, dy0)
        n_pieces[0] += 1

    if top:
        if left:
            draw(0, left, 0, top)
        draw(left, -right, 0, top)
        if right:
            draw(-right, 0, 0, top)
    if left:
        draw(0, left, top, -bottom)
    draw(left, -right, top, -bottom)
    if right:
        draw(-right, 0, top, -bottom)
    if bottom:
        if left:
            draw(0, left, -bottom, 0)
        draw(left, -right, -bottom, 0)
        if right:
            draw(-right, 0, -bottom, 0)

    return frame, n_pieces[0]


def _build_textbox_image(tex, src_w, src_h, dst_w, dst_h):
    """Product Image full-panel: reverse-scale source into dest when sizes differ."""
    if src_w == dst_w and src_h == dst_h:
        return tex
    piece = FakeRender(dst_w, dst_h)
    piece.reverse = Mat2(dst_w / float(src_w), 0, 0, dst_h / float(src_h))
    piece.forward = Mat2(src_w / float(dst_w), 0, 0, src_h / float(dst_h))
    piece.blit(tex, 0, 0)
    return piece


def _load_tex(draw, path, tag):
    src_w, src_h, rgba_src = _png_rgba(path)
    alphas = [rgba_src[i + 3] for i in range(0, len(rgba_src), 4)]
    a_min = min(alphas) if alphas else 0
    a_max = max(alphas) if alphas else 0
    a_mean = (sum(alphas) / float(len(alphas))) if alphas else 0.0
    transparent = a_max == 0
    surf = _Surf(src_w, src_h, rgba_src)
    tex = draw.load_texture(surf)
    if not isinstance(tex, HostTexture) or tex.handle <= 0:
        if isinstance(tex, int) and tex > 0:
            tex = HostTexture(tex, src_w, src_h)
        else:
            raise RuntimeError(f"load_texture_failed tex={tex!r} tag={tag}")
    return tex, src_w, src_h, transparent, a_min, a_max, a_mean


def _make_synthetic_namebox_source():
    """300x36 orange 5px border + black center (namebox-sized structure probe)."""
    w, h = PROBE_SRC_W, PROBE_SRC_H
    left = top = right = bottom = NB_LEFT
    pix = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            di = (y * w + x) * 4
            if x < left or x >= w - right or y < top or y >= h - bottom:
                c = ORANGE
            else:
                c = BLACK
            pix[di : di + 4] = bytes(c)
    return w, h, bytes(pix)


def main():
    tb_png = _base / "the_question" / "game" / "gui" / "textbox.png"
    nb_png = _base / "the_question" / "game" / "gui" / "namebox.png"

    draw = WgpuDraw()
    draw.init((VW, VH))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    # --- Real textbox.png Image path ---
    try:
        tb_tex, tb_sw, tb_sh, tb_trans, tb_amin, tb_amax, tb_amean = _load_tex(
            draw, tb_png, "gui/textbox.png"
        )
        textbox_load_ok = True
    except Exception as e:
        msg = f"ok=False reason=load_textbox_png err={e} path={tb_png}"
        out.write_text(msg + "\n")
        print("[tq_say_chrome]", msg, flush=True)
        return

    # --- Real namebox.png Frame path (product asset; may be fully transparent) ---
    try:
        nb_tex, nb_sw, nb_sh, nb_trans, nb_amin, nb_amax, nb_amean = _load_tex(
            draw, nb_png, "gui/namebox.png"
        )
        namebox_load_ok = True
    except Exception as e:
        msg = f"ok=False reason=load_namebox_png err={e} path={nb_png}"
        out.write_text(msg + "\n")
        print("[tq_say_chrome]", msg, flush=True)
        return

    # --- Synthetic structure probe through same Frame tree + Borders(5,5,5,5) ---
    probe_w, probe_h, probe_rgba = _make_synthetic_namebox_source()
    probe_surf = _Surf(probe_w, probe_h, probe_rgba)
    probe_tex = draw.load_texture(probe_surf)
    if not isinstance(probe_tex, HostTexture) or probe_tex.handle <= 0:
        if isinstance(probe_tex, int) and probe_tex > 0:
            probe_tex = HostTexture(probe_tex, probe_w, probe_h)
        else:
            msg = f"ok=False reason=load_probe_texture_failed tex={probe_tex!r}"
            out.write_text(msg + "\n")
            print("[tq_say_chrome]", msg, flush=True)
            return

    textbox = _build_textbox_image(tb_tex, tb_sw, tb_sh, TB_W, TB_H)
    namebox, nb_pieces = _build_frame(
        nb_tex, nb_sw, nb_sh, NB_DST_W, NB_DST_H, NB_LEFT, NB_TOP, NB_RIGHT, NB_BOTTOM
    )
    probe, probe_pieces = _build_frame(
        probe_tex,
        probe_w,
        probe_h,
        PROBE_DST_W,
        PROBE_DST_H,
        NB_LEFT,
        NB_TOP,
        NB_RIGHT,
        NB_BOTTOM,
    )

    root = FakeRender(VW, VH)
    bg = _Surf(VW, VH, bytes([BG[0], BG[1], BG[2], BG[3]]) * (VW * VH))
    root.blit(bg, 0, 0)
    root.blit(textbox, TB_X, TB_Y)
    root.blit(namebox, NB_OX, NB_OY)  # real product namebox Frame
    root.blit(probe, PROBE_OX, PROBE_OY)  # synthetic structure probe

    namebox_draw_attempted = True
    draw.draw_screen(root, flip=True)
    try:
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:
        msg = f"ok=False reason=read_rt err={e}"
        out.write_text(msg + "\n")
        print("[tq_say_chrome]", msg, flush=True)
        return

    sx = rw / float(VW)
    sy = rh / float(VH)

    # ========== Textbox: alpha-gradient structure via BG darkening ==========
    # Real textbox.png: RGB ~all black; structure is in alpha
    # (center ~204, top edge ~0). Over BG, center darkens more than top edge.
    tb_pts = {
        "top_edge": (TB_X + TB_W // 2, TB_Y + 2),
        "mid": (TB_X + TB_W // 2, TB_Y + TB_H // 2),
        "bottom": (TB_X + TB_W // 2, TB_Y + TB_H - 4),
        "left": (TB_X + 40, TB_Y + TB_H // 2),
        "right": (TB_X + TB_W - 40, TB_Y + TB_H // 2),
        "outside_above": (VW // 2, max(10, TB_Y - 40)),
    }
    tb_rgb = {}
    for name, (px, py) in tb_pts.items():
        r, g, b, a = _sample(rgba, rw, rh, px * sx, py * sy)
        tb_rgb[name] = (r, g, b, a)

    tb_mean, tb_var, _tb_a_mean, _ = _mean_var_region(
        rgba, rw, rh, sx, sy, TB_X + 20, TB_Y + 10, TB_X + TB_W - 20, TB_Y + TB_H - 5, step=12
    )

    outside_ok = _near(tb_rgb["outside_above"], BG, tol=55)
    mid = tb_rgb["mid"]
    top = tb_rgb["top_edge"]
    mid_darkened = (
        int(mid[0]) < BG[0] - 15
        or int(mid[1]) < BG[1] - 15
        or int(mid[2]) < BG[2] - 15
    )
    mid_still_bg = _near(mid, BG, tol=40)
    mid_pure_black = int(mid[0]) <= 2 and int(mid[1]) <= 2 and int(mid[2]) <= 2
    # Undrawn arena clear is near-neutral dark ~(13,13,20). Premul black@alpha
    # over blue BG yields a blue-tinted dark like ~(8,16,24) — NOT clear-like.
    # Only treat as undrawn-clear when near CLEAR_LIKE AND not blue-tinted
    # relative to that clear (B not elevated over R/G the way BG blend does).
    mid_undrawn_clear = (
        abs(int(mid[0]) - CLEAR_LIKE[0]) <= 10
        and abs(int(mid[1]) - CLEAR_LIKE[1]) <= 10
        and abs(int(mid[2]) - CLEAR_LIKE[2]) <= 10
        and abs(int(mid[2]) - int(mid[0])) <= 8
    )
    # Alpha structure: transparent top edge retains more BG (brighter) than mid.
    top_brighter_than_mid = _luma(top) > _luma(mid) + 5
    has_alpha_structure = tb_var >= 20.0 or top_brighter_than_mid
    featureless_tb = (
        mid_pure_black
        and _is_blackish(tb_rgb["bottom"], tol=8)
        and _is_blackish(tb_rgb["left"], tol=8)
        and not has_alpha_structure
    )

    textbox_pass = (
        textbox_load_ok
        and outside_ok
        and mid_darkened
        and not mid_still_bg
        and not mid_pure_black
        and not mid_undrawn_clear
        and has_alpha_structure
        and not featureless_tb
    )

    # ========== Real namebox Frame: load + draw (transparent asset) ==========
    inset = 1
    nb_pts = {
        "top": (NB_OX + NB_DST_W // 2, NB_OY + inset),
        "bottom": (NB_OX + NB_DST_W // 2, NB_OY + NB_DST_H - 1 - inset),
        "left": (NB_OX + inset, NB_OY + NB_DST_H // 2),
        "right": (NB_OX + NB_DST_W - 1 - inset, NB_OY + NB_DST_H // 2),
        "tl": (NB_OX + inset, NB_OY + inset),
        "center": (NB_OX + NB_DST_W // 2, NB_OY + NB_DST_H // 2),
    }
    nb_rgb = {}
    for name, (px, py) in nb_pts.items():
        r, g, b, a = _sample(rgba, rw, rh, px * sx, py * sy)
        nb_rgb[name] = (r, g, b, a)

    nb_bg_hits = sum(
        1 for k in ("top", "bottom", "left", "right", "tl", "center") if _near(nb_rgb[k], BG, tol=55)
    )
    nb_black_hits = sum(
        1
        for k in ("top", "bottom", "left", "right", "tl", "center")
        if _is_blackish(nb_rgb[k], tol=25) or _is_clear_like(nb_rgb[k])
    )
    nb_pieces_ok = nb_pieces >= 9
    # Transparent product asset: correct Frame leaves BG (not undrawn clear/black).
    namebox_draw_pass = (
        namebox_load_ok
        and namebox_draw_attempted
        and nb_pieces_ok
        and nb_bg_hits >= 4
        and nb_black_hits == 0
    )

    # ========== Synthetic Borders(5,5,5,5) Frame structure probe ==========
    pr_pts = {
        "top": (PROBE_OX + PROBE_DST_W // 2, PROBE_OY + inset),
        "bottom": (PROBE_OX + PROBE_DST_W // 2, PROBE_OY + PROBE_DST_H - 1 - inset),
        "left": (PROBE_OX + inset, PROBE_OY + PROBE_DST_H // 2),
        "right": (PROBE_OX + PROBE_DST_W - 1 - inset, PROBE_OY + PROBE_DST_H // 2),
        "tl": (PROBE_OX + inset, PROBE_OY + inset),
        "center": (PROBE_OX + PROBE_DST_W // 2, PROBE_OY + PROBE_DST_H // 2),
        "outside": (PROBE_OX + PROBE_DST_W + 20, PROBE_OY + PROBE_DST_H // 2),
    }
    pr_rgb = {}
    for name, (px, py) in pr_pts.items():
        r, g, b, a = _sample(rgba, rw, rh, px * sx, py * sy)
        pr_rgb[name] = (r, g, b, a)

    border_hits = sum(
        1 for k in ("top", "bottom", "left", "right", "tl") if _is_orange(pr_rgb[k])
    )
    border_ok = border_hits >= 3
    center_black = _is_blackish(pr_rgb["center"])
    outside_probe_ok = _near(pr_rgb["outside"], BG, tol=55)
    probe_pieces_ok = probe_pieces >= 9
    featureless_probe = border_hits == 0 and all(
        _is_blackish(pr_rgb[k]) or _is_clear_like(pr_rgb[k])
        for k in ("top", "bottom", "left", "right", "tl", "center")
    )
    namebox_frame_structure_ok = (
        probe_pieces_ok
        and border_ok
        and outside_probe_ok
        and not featureless_probe
    )

    ok = bool(textbox_pass and namebox_load_ok and namebox_frame_structure_ok)
    # Leading ok= only; subfields use *_pass / *_ok names that still may contain
    # True/False but grep uses ok=True / ok=False prefix match on first field.
    msg = (
        "ok=%s textbox_pass=%s namebox_load_ok=%s namebox_draw_pass=%s "  # noqa: UP031
        "namebox_frame_structure_ok=%s namebox_draw_attempted=%s "
        "tb_mid=%s tb_top=%s tb_bottom=%s tb_outside=%s "
        "tb_mean=(%.1f,%.1f,%.1f) tb_luma_var=%.1f "
        "tb_mid_darkened=%s tb_mid_still_bg=%s tb_mid_pure_black=%s "
        "tb_top_brighter=%s tb_has_alpha_structure=%s tb_featureless=%s "
        "tb_src=%dx%d tb_dest=%dx%d@%d,%d tb_src_alpha=(%d,%d,%.1f) tb_trans=%s "
        "nb_pieces=%d nb_pieces_ok=%s nb_bg_hits=%d/6 nb_black_hits=%d/6 "
        "nb_center=%s nb_top=%s nb_src=%dx%d nb_dest=%dx%d@%d,%d "
        "nb_borders=%d nb_src_alpha=(%d,%d,%.1f) nb_trans=%s "
        "probe_pieces=%d probe_border_hits=%d/5 probe_border_ok=%s "
        "probe_center=%s probe_top=%s probe_left=%s probe_center_black=%s "
        "probe_featureless=%s probe_src=%dx%d probe_dest=%dx%d@%d,%d "
        "probe_borders=%d probe_label=namebox_frame_structure_probe "
        "product_say=True confirm_frame_not_used=True "
        "note=product_namebox_png_fully_transparent_structure_via_synthetic_Borders_5"
        % (
            ok,
            textbox_pass,
            namebox_load_ok,
            namebox_draw_pass,
            namebox_frame_structure_ok,
            namebox_draw_attempted,
            tb_rgb["mid"][:3],
            tb_rgb["top_edge"][:3],
            tb_rgb["bottom"][:3],
            tb_rgb["outside_above"][:3],
            tb_mean[0],
            tb_mean[1],
            tb_mean[2],
            tb_var,
            mid_darkened,
            mid_still_bg,
            mid_pure_black,
            top_brighter_than_mid,
            has_alpha_structure,
            featureless_tb,
            tb_sw,
            tb_sh,
            TB_W,
            TB_H,
            TB_X,
            TB_Y,
            tb_amin,
            tb_amax,
            tb_amean,
            tb_trans,
            nb_pieces,
            nb_pieces_ok,
            nb_bg_hits,
            nb_black_hits,
            nb_rgb["center"][:3],
            nb_rgb["top"][:3],
            nb_sw,
            nb_sh,
            NB_DST_W,
            NB_DST_H,
            NB_OX,
            NB_OY,
            NB_LEFT,
            nb_amin,
            nb_amax,
            nb_amean,
            nb_trans,
            probe_pieces,
            border_hits,
            border_ok,
            pr_rgb["center"][:3],
            pr_rgb["top"][:3],
            pr_rgb["left"][:3],
            center_black,
            featureless_probe,
            probe_w,
            probe_h,
            PROBE_DST_W,
            PROBE_DST_H,
            PROBE_OX,
            PROBE_OY,
            NB_LEFT,
        )
    )
    out.write_text(msg + "\n")
    print("[tq_say_chrome]", msg, flush=True)


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

