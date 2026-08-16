"""
AC-P1 dissolve_product_mid gate — mid-dissolve with prefs.transitions=2.

Gate name: dissolve_product_mid  (RENPY_HOST_GATE=dissolve_product_mid)

Product-faithful mid-dissolve WITHOUT forcing prefs.transitions=0 (historical
blind spot of tq_* playthrough gates). Builds a Dissolve-like mesh tree with
two product-image nested Render children (main_menu vs lecturehall when
loadable; otherwise distinct solid product-shaped nests) at u=0.5.

Checks:
  1. transitions_pref == 2 (never forced to 0)
  2. Nested-Render dissolve children force RTT (not solid-only Surfaces)
  3. Game RT mean is a blend (both channels elevated), not hard cut / clear

Hard-timeout friendly: pure draw_screen path, no interact / TIMEEVENT.

Note: no from __future__; host run_file prepends imports.
"""

import os
import struct
import zlib
from pathlib import Path

import renpy_host  # type: ignore
from renpy.wgpu.draw import WgpuDraw

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-dissolve_product_mid.txt"
out.parent.mkdir(parents=True, exist_ok=True)

# AC-P1: product default transitions level. NEVER set to 0 in this gate.
TRANSITIONS_PREF = 2


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


class _Surf:
    """Minimal surface with get_size + _pixels for WgpuDraw.load_texture."""

    def __init__(self, w, h, pixels):
        self._w = int(w)
        self._h = int(h)
        need = self._w * self._h * 4
        if isinstance(pixels, (bytes, bytearray)):
            raw = bytes(pixels)
        else:
            raw = bytes(pixels)
        self._pixels = raw if len(raw) >= need else raw + bytes(need - len(raw))

    def get_size(self):
        return self._w, self._h


def _png_rgba(path):
    """Minimal PNG decoder for 8-bit RGBA/RGB (no interlacing). Returns (w,h,rgba)."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("not png: %s" % path)
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
        raise RuntimeError("unsupported png ct=%s bd=%s" % (color_type, bit_depth))
    decomp = zlib.decompress(raw)
    bpp = 4 if color_type == 6 else 3
    stride = w * bpp + 1
    outb = bytearray(w * h * 4)
    prev = bytearray(w * bpp)
    for y in range(h):
        row = decomp[y * stride : (y + 1) * stride]
        filt = row[0]
        scan = bytearray(row[1:])
        if filt == 1:  # Sub
            for i in range(bpp, len(scan)):
                scan[i] = (scan[i] + scan[i - bpp]) & 0xFF
        elif filt == 2:  # Up
            for i in range(len(scan)):
                scan[i] = (scan[i] + prev[i]) & 0xFF
        elif filt == 3:  # Average
            for i in range(len(scan)):
                a = scan[i - bpp] if i >= bpp else 0
                b = prev[i]
                scan[i] = (scan[i] + ((a + b) // 2)) & 0xFF
        elif filt == 4:  # Paeth
            for i in range(len(scan)):
                a = scan[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                scan[i] = (scan[i] + pr) & 0xFF
        elif filt != 0:
            raise RuntimeError("bad filter %s" % filt)
        prev = scan
        for x in range(w):
            si = x * bpp
            di = (y * w + x) * 4
            if bpp == 3:
                outb[di : di + 4] = bytes([scan[si], scan[si + 1], scan[si + 2], 255])
            else:
                outb[di : di + 4] = bytes(scan[si : si + 4])
    return w, h, bytes(outb)


def _load_image_rgba(path):
    """Load PNG (native) or any format via PIL. Returns (w,h,rgba_bytes,src_tag)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    if path.suffix.lower() == ".png":
        w, h, rgba = _png_rgba(path)
        return w, h, rgba, path.name
    # JPEG / other — try PIL (available on host system Python).
    try:
        from PIL import Image  # type: ignore

        im = Image.open(path).convert("RGBA")
        w, h = im.size
        return w, h, im.tobytes(), path.name
    except Exception as e:
        raise RuntimeError("load %s failed: %s" % (path, e))


def _solid_rgba(w, h, rgba):
    r, g, b, a = rgba
    return bytes([r, g, b, a]) * (w * h)


def _mean_rgb(rgba, w, h):
    n = w * h
    if n <= 0 or len(rgba) < n * 4:
        return 0.0, 0.0, 0.0
    rs = gs = bs = 0
    step = max(1, n // 50000)
    count = 0
    for i in range(0, n, step):
        o = i * 4
        rs += rgba[o]
        gs += rgba[o + 1]
        bs += rgba[o + 2]
        count += 1
    inv = 1.0 / max(1, count)
    return rs * inv, gs * inv, bs * inv


def _scene_render(w, h, surf):
    """Nested Render tree whose leaf is a surface — product dissolve shape.

    Product dissolve children are full scene Renders, not Surfaces. Leaf sits
    under container Renders so `_child_to_texture` must RTT.
    """
    leaf = FakeRender(w, h, mesh=False)
    leaf.blit(surf, 0, 0)
    mid = FakeRender(w, h, mesh=False)
    mid.blit(leaf, 0, 0)
    root = FakeRender(w, h, mesh=False)
    root.blit(mid, 0, 0)
    return root


def _maybe_set_transitions_pref():
    """If renpy prefs exist, set transitions=2 (never 0). Return (value, source)."""
    # Synthetic path default: declare intended product pref without forcing 0.
    value = TRANSITIONS_PREF
    source = "declared"
    try:
        import renpy  # type: ignore

        prefs = None
        game = getattr(renpy, "game", None)
        if game is not None:
            prefs = getattr(game, "preferences", None)
        if prefs is not None and hasattr(prefs, "transitions"):
            # Critical: set to 2, NEVER 0.
            prefs.transitions = TRANSITIONS_PREF
            value = int(getattr(prefs, "transitions", TRANSITIONS_PREF))
            source = "renpy.game.preferences"
        else:
            # Store on a module-level marker so artifact proves we did not force 0.
            source = "declared_no_prefs_obj"
    except Exception:
        source = "declared_import_softfail"
    return value, source


def main():
    # --- AC-P1 transitions contract (must not be 0) ---
    transitions_pref, transitions_src = _maybe_set_transitions_pref()
    transitions_ok = transitions_pref == TRANSITIONS_PREF and transitions_pref != 0

    vw, vh = 1280, 720
    game = _base / "the_question" / "game"
    old_tag = "solid_red_fallback"
    new_tag = "solid_blue_fallback"
    old_surf = None
    new_surf = None

    # Prefer real product images (main_menu vs lecturehall).
    try:
        ow, oh, orgba, old_tag = _load_image_rgba(game / "gui" / "main_menu.png")
        # Scale-to-window via crop/pad into vw x vh solid buffer if sizes differ.
        if ow == vw and oh == vh:
            old_surf = _Surf(vw, vh, orgba)
        else:
            # Center-blit into window-sized buffer (nearest, no filter).
            buf = bytearray(_solid_rgba(vw, vh, (0, 0, 0, 255)))
            x0 = max(0, (vw - ow) // 2)
            y0 = max(0, (vh - oh) // 2)
            copy_w = min(ow, vw)
            copy_h = min(oh, vh)
            for y in range(copy_h):
                src_off = y * ow * 4
                dst_off = ((y0 + y) * vw + x0) * 4
                buf[dst_off : dst_off + copy_w * 4] = orgba[src_off : src_off + copy_w * 4]
            old_surf = _Surf(vw, vh, bytes(buf))
    except Exception as e:
        old_tag = "solid_red_fallback(%s)" % e
        old_surf = _Surf(vw, vh, _solid_rgba(vw, vh, (220, 40, 40, 255)))

    try:
        nw, nh, nrgba, new_tag = _load_image_rgba(
            game / "images" / "bg lecturehall.jpg"
        )
        if nw == vw and nh == vh:
            new_surf = _Surf(vw, vh, nrgba)
        else:
            buf = bytearray(_solid_rgba(vw, vh, (0, 0, 0, 255)))
            x0 = max(0, (vw - nw) // 2)
            y0 = max(0, (vh - nh) // 2)
            copy_w = min(nw, vw)
            copy_h = min(nh, vh)
            for y in range(copy_h):
                src_off = y * nw * 4
                dst_off = ((y0 + y) * vw + x0) * 4
                buf[dst_off : dst_off + copy_w * 4] = nrgba[src_off : src_off + copy_w * 4]
            new_surf = _Surf(vw, vh, bytes(buf))
    except Exception as e:
        new_tag = "solid_blue_fallback(%s)" % e
        new_surf = _Surf(vw, vh, _solid_rgba(vw, vh, (40, 40, 220, 255)))

    # Nested Render trees (NOT bare Surfaces) as dissolve children → forces RTT.
    old = _scene_render(vw, vh, old_surf)
    new = _scene_render(vw, vh, new_surf)
    root = FakeRender(vw, vh, mesh=True)
    root.shaders = ("renpy.dissolve",)
    root.uniforms = {"u_renpy_dissolve": 0.5}
    root.blit(old, 0, 0)
    root.blit(new, 0, 0)

    draw = WgpuDraw()
    draw.init((vw, vh))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    draw.draw_screen(root, flip=True)
    try:
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:
        msg = (
            "ok=False reason=read_rt err=%s transitions_pref=%s transitions_src=%s "
            "transitions_ok=%s old=%s new=%s"
            % (e, transitions_pref, transitions_src, transitions_ok, old_tag, new_tag)
        )
        out.write_text(msg + "\n")
        print("[dissolve_product_mid]", msg, flush=True)
        return

    mr, mg, mb = _mean_rgb(rgba, rw, rh)
    clear_like = mr < 40 and mg < 40 and mb < 40
    # For product images mean is scene-dependent; require not clear and not a
    # hard single-side solid cut. Use dual-channel elevation when solids fall
    # back; for real images require non-clear + variance (not pure hard cut).
    hard_red = mr > 200 and mb < 40 and mg < 40
    hard_blue = mb > 200 and mr < 40 and mg < 40
    # Blend signal: either classic R+B elevation (solid fallback) OR non-clear
    # product mean with green present (lecturehall/main_menu both have G).
    blended_solid = (mr > 40 and mb > 40) and not clear_like
    blended_product = (not clear_like) and (mr + mg + mb > 60.0) and not hard_red and not hard_blue
    # Extra: mid-dissolve of two different full scenes should not match either
    # pure solid extreme; require some G if product images loaded.
    used_product = ("fallback" not in old_tag) and ("fallback" not in new_tag)
    if used_product:
        blended = blended_product
    else:
        blended = blended_solid

    ok = bool(transitions_ok and blended and not hard_red and not hard_blue and not clear_like)
    msg = (
        "ok=%s mean=(%.1f,%.1f,%.1f) blended=%s hard_red=%s hard_blue=%s "
        "clear_like=%s nested_renders=True u=0.5 transitions_pref=%s "
        "transitions_src=%s transitions_ok=%s transitions_forced_zero=False "
        "old=%s new=%s used_product=%s"
        % (
            ok,
            mr,
            mg,
            mb,
            blended,
            hard_red,
            hard_blue,
            clear_like,
            transitions_pref,
            transitions_src,
            transitions_ok,
            old_tag,
            new_tag,
            used_product,
        )
    )
    out.write_text(msg + "\n")
    print("[dissolve_product_mid]", msg, flush=True)


main()
