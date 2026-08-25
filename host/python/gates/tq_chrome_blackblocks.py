"""
AC-B3 tq_chrome_blackblocks gate — product chrome Frame orange border structure.

Gate name: tq_chrome_blackblocks  (RENPY_HOST_GATE=tq_chrome_blackblocks)

Loads real the_question/game/gui/frame.png (orange 204,102,0 border + solid black
center by design) and draws a multipiece Frame-like tree matching product
confirm chrome (Borders(40,40,40,40) from gui.confirm_frame_borders).

Checks:
  1. Outer border band has orange — structure present (not shattered / missing)
  2. Deep center may be black (intentional product frame.png semantics)
  3. Outside dest stays background
  4. Fail if dest is featureless black with no border structure

Do NOT assert center non-black. Do NOT edit the_question/game/**.

Note: no from __future__; host run_file prepends imports.
"""

import os
import struct
import zlib
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
# fallback

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-tq_chrome_blackblocks.txt"
out.parent.mkdir(parents=True, exist_ok=True)

ORANGE = (204, 102, 0, 255)
BG = (20, 20, 30, 255)

# Product confirm frame borders (gui.confirm_frame_borders = Borders(40,40,40,40)).
LEFT = TOP = RIGHT = BOTTOM = 40

# Dest size: confirm-ish panel (larger than source so edges stretch).
DST_W, DST_H = 480, 280
OX, OY = 120, 100  # placement on 1280x720


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


def _is_orange(c, tol=50):
    """Orange border: high R, mid G, low B (product 204,102,0)."""
    r, g, b = int(c[0]), int(c[1]), int(c[2])
    return r >= 150 and 40 <= g <= 160 and b <= 60 and (r - g) >= 40


def _is_blackish(c, tol=30):
    return int(c[0]) <= tol and int(c[1]) <= tol and int(c[2]) <= tol


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


def _build_frame(tex, src_w, src_h):
    """9-slice Frame tree matching imagelike.Frame.draw_pattern (non-tile)."""
    sw, sh = src_w, src_h
    dw, dh = DST_W, DST_H
    left, top, right, bottom = LEFT, TOP, RIGHT, BOTTOM

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

    def draw(x0, x1, y0, y1):
        sx0, sy0, csw, csh, dx0, dy0, cdw, cdh = regions(x0, x1, y0, y1)
        if csw <= 0 or csh <= 0 or cdw <= 0 or cdh <= 0:
            return
        piece = _piece(tex, sx0, sy0, csw, csh, cdw, cdh)
        frame.blit(piece, dx0, dy0)

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

    return frame


def main():
    vw, vh = 1280, 720
    png = _base / "the_question" / "game" / "gui" / "frame.png"
    try:
        src_w, src_h, rgba_src = _png_rgba(png)
        src_tag = "gui/frame.png"
    except Exception as e:  # noqa: BLE001
        msg = f"ok=False reason=load_frame_png err={e} path={png}"
        out.write_text(msg + "\n")
        print("[tq_chrome_blackblocks]", msg, flush=True)
        return

    draw = WgpuDraw()
    draw.init((vw, vh))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:  # noqa: BLE001, S110
        pass

    surf = _Surf(src_w, src_h, rgba_src)
    tex = draw.load_texture(surf)
    if not isinstance(tex, HostTexture) or tex.handle <= 0:
        if isinstance(tex, int) and tex > 0:
            tex = HostTexture(tex, src_w, src_h)
        else:
            msg = f"ok=False reason=load_texture_failed tex={tex!r} src={src_tag}"
            out.write_text(msg + "\n")
            print("[tq_chrome_blackblocks]", msg, flush=True)
            return

    frame = _build_frame(tex, src_w, src_h)

    # Background + frame (confirm-ish chrome over dark field).
    root = FakeRender(vw, vh)
    bg = _Surf(vw, vh, bytes([BG[0], BG[1], BG[2], BG[3]]) * (vw * vh))
    root.blit(bg, 0, 0)
    root.blit(frame, OX, OY)

    draw.draw_screen(root, flip=True)
    try:
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:  # noqa: BLE001
        msg = f"ok=False reason=read_rt err={e}"
        out.write_text(msg + "\n")
        print("[tq_chrome_blackblocks]", msg, flush=True)
        return

    sx = rw / float(vw)
    sy = rh / float(vh)

    # frame.png orange ring is only ~3px at source edge; after 9-slice with
    # Borders(40,...) the outer dest edge still carries that orange. Sample
    # very close to the outer edge (inset=1).
    inset = 1
    samples = {
        "top": (OX + DST_W // 2, OY + inset),
        "bottom": (OX + DST_W // 2, OY + DST_H - 1 - inset),
        "left": (OX + inset, OY + DST_H // 2),
        "right": (OX + DST_W - 1 - inset, OY + DST_H // 2),
        "tl": (OX + inset, OY + inset),
        "center": (OX + DST_W // 2, OY + DST_H // 2),
        "outside": (10, 10),
    }
    rgb = {}
    for name, (px, py) in samples.items():
        r, g, b, a = _sample(rgba, rw, rh, px * sx, py * sy)
        rgb[name] = (r, g, b, a)

    border_hits = sum(
        1 for k in ("top", "bottom", "left", "right", "tl") if _is_orange(rgb[k])
    )
    # Need at least 3 of 5 border samples orange → structure present.
    border_ok = border_hits >= 3
    center_black = _is_blackish(rgb["center"])
    center_orange = _is_orange(rgb["center"])
    outside_ok = _near(rgb["outside"], BG)
    featureless_black = border_hits == 0 and all(
        _is_blackish(rgb[k])
        for k in ("top", "bottom", "left", "right", "tl", "center")
    )

    ok = border_ok and outside_ok and not featureless_black
    msg = (
        "ok=%s border_hits=%d/%d border_ok=%s center_rgb=(%d,%d,%d) "  # noqa: UP031
        "center_black=%s center_orange=%s outside_ok=%s featureless_black=%s "
        "top=%s left=%s dest=%dx%d src=%dx%d border=%d src_tag=%s "
        "confirm_borders=40 product_frame=True"
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
            src_w,
            src_h,
            LEFT,
            src_tag,
        )
    )
    out.write_text(msg + "\n")
    print("[tq_chrome_blackblocks]", msg, flush=True)


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
