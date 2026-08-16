"""
Product Fade via real Solid black (not BlackFill bypass) + hold=0 default timing.

Gate: fade_product_solid  (RENPY_HOST_GATE=fade_product_solid)

Proves the product Fade path that the_question uses:
  Fade(.5, 0, .5) → MultipleTransition with Solid((0,0,0,255)) mid widget
  (Solid is 10×10 + reverse Matrix2D, not a full-window surface).

Stage samples (hold=0, total delay=1.0):
  st0=0.0, out_mid=0.25, black_boundary=0.5, in_mid=0.75, late=0.95

Also samples hold=0.1 Fade for comparison black_hold.

ok=True requires out_darkens, boundary_black or hold_black, in_brightens, product images.
"""

import os
import struct
import traceback
import zlib
from pathlib import Path

import renpy_host  # type: ignore

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
if not (_base / "renpy").is_dir():
    # host/ cwd → parent
    if (_base.parent / "renpy").is_dir():
        _base = _base.parent
out = _base / "host" / "target" / "gate-fade_product_solid.txt"
out.parent.mkdir(parents=True, exist_ok=True)

VW, VH = 1280, 720
TRANSITIONS_PREF = 2


def _log(lines, msg):
    lines.append(str(msg))
    print("[fade_product_solid]", msg, flush=True)


def _write(lines, ok, **extra):
    body = list(lines)
    for k, v in extra.items():
        body.append("%s=%s" % (k, v))
    body.append("ok=%s" % ok)
    text = "\n".join(body) + "\n"
    out.write_text(text)
    print("[fade_product_solid] WROTE", out, "ok=%s" % ok, flush=True)


def _request_quit():
    try:
        renpy_host.request_quit()
    except Exception:
        pass


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
        raise RuntimeError("not png")
    pos = 8
    w = h = None
    raw = b""
    color_type = bit_depth = None
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
    path = Path(path)
    if path.suffix.lower() == ".png":
        w, h, rgba = _png_rgba(path)
        return w, h, rgba, path.name
    from PIL import Image  # type: ignore

    im = Image.open(path).convert("RGBA")
    return im.size[0], im.size[1], im.tobytes(), path.name


def _fit(vw, vh, w, h, rgba):
    if w == vw and h == vh:
        return _Surf(vw, vh, rgba)
    buf = bytearray(bytes([0, 0, 0, 255]) * (vw * vh))
    x0 = max(0, (vw - w) // 2)
    y0 = max(0, (vh - h) // 2)
    cw, ch = min(w, vw), min(h, vh)
    for y in range(ch):
        src = y * w * 4
        dst = ((y0 + y) * vw + x0) * 4
        buf[dst : dst + cw * 4] = rgba[src : src + cw * 4]
    return _Surf(vw, vh, bytes(buf))


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


def _luma(m):
    r, g, b = m
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _bootstrap(lines):
    import bootstrap as boot

    good, miss, err, extra = boot.stage_import_renpy()
    _log(lines, "import_renpy good=%s err=%s" % (good, err))
    if not good:
        raise RuntimeError("import_renpy failed: %s" % err)
    good, miss, err, extra = boot.stage_import_all()
    _log(lines, "import_all good=%s err=%s missing=%s" % (good, err, miss))
    if not good:
        raise RuntimeError("import_all failed: %s" % err)

    import renpy.game as game
    import renpy.display.render as render_mod
    import renpy.style as style_mod
    import renpy.display.displayable as disp_mod

    render_mod.models = True
    game.less_updates = False
    if game.preferences is None:
        from renpy.preferences import Preferences

        game.preferences = Preferences()
    game.preferences.transitions = TRANSITIONS_PREF

    # Minimal default style so Solid.xyminimums work (product imagelike path).
    default = style_mod.Style(None, name=("default",))
    default.properties.append(
        {
            "xminimum": 0,
            "yminimum": 0,
            "xmaximum": None,
            "ymaximum": None,
            "xfill": False,
            "yfill": False,
            "minwidth": 0,
            "xpadding": 0,
            "ypadding": 0,
            "xmargin": 0,
            "ymargin": 0,
            "background": None,
            "color": (255, 255, 255, 255),
        }
    )
    style_mod.styles[("default",)] = default
    disp_mod.default_style = default

    try:
        render_mod.render_ready()
    except Exception as e:
        _log(lines, "render_ready softfail %s" % e)

    return (
        bool(game.less_updates),
        bool(render_mod.models),
        int(getattr(game.preferences, "transitions", -1)),
    )


def _sample_fade(draw, f, stages, lines, label):
    stage_means = {}
    for name, st in stages.items():
        rv = f.render(VW, VH, st, st)
        # Count dissolve-ish markers on tree
        oc = getattr(rv, "operation_complete", None)
        shaders = getattr(rv, "shaders", None)
        uniforms = getattr(rv, "uniforms", None)
        draw.draw_screen(rv, flip=True)
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
        m = _mean_rgb(rgba, rw, rh)
        stage_means[name] = m
        _log(
            lines,
            "%s stage=%s st=%.3f complete=%s shaders=%s uniforms=%s mean=(%.1f,%.1f,%.1f)"
            % (label, name, st, oc, shaders, uniforms, m[0], m[1], m[2]),
        )
    return stage_means


def main():
    lines = []
    ok = False
    try:
        less_updates, models, transitions_pref = _bootstrap(lines)
        _log(
            lines,
            "less_updates=%s models=%s transitions_pref=%s"
            % (less_updates, models, transitions_pref),
        )

        from renpy.display.displayable import Displayable
        from renpy.display.render import Render
        from renpy.display.transition import Fade, MultipleTransition
        from renpy.display.imagelike import Solid
        from renpy.wgpu.draw import WgpuDraw

        class ProductImage(Displayable):
            def __init__(self, surf, tag="img"):
                super(ProductImage, self).__init__()
                self.surf = surf
                self.tag = tag

            def render(self, w, h, st, at):
                sw, sh = self.surf.get_size()
                rw = int(w) if w and w > 0 else sw
                rh = int(h) if h and h > 0 else sh
                rv = Render(rw, rh)
                rv.blit(self.surf, (0, 0))
                return rv

            def visit(self):
                return []

        game_dir = _base / "the_question" / "game"
        try:
            ow, oh, orgba, old_tag = _load_image_rgba(game_dir / "gui" / "main_menu.png")
            old_surf = _fit(VW, VH, ow, oh, orgba)
        except Exception as e:
            old_tag = "solid_red_fallback(%s)" % e
            old_surf = _Surf(VW, VH, bytes([220, 40, 40, 255]) * (VW * VH))
        try:
            nw, nh, nrgba, new_tag = _load_image_rgba(
                game_dir / "images" / "bg lecturehall.jpg"
            )
            new_surf = _fit(VW, VH, nw, nh, nrgba)
        except Exception as e:
            new_tag = "solid_blue_fallback(%s)" % e
            new_surf = _Surf(VW, VH, bytes([40, 40, 220, 255]) * (VW * VH))

        used_product = ("fallback" not in old_tag) and ("fallback" not in new_tag)
        _log(lines, "old=%s new=%s used_product=%s" % (old_tag, new_tag, used_product))

        old = ProductImage(old_surf, old_tag)
        new = ProductImage(new_surf, new_tag)

        # REAL product Solid black (10×10 + reverse) — not BlackFill.
        solid_black = Solid((0, 0, 0, 255))

        draw = WgpuDraw()
        draw.init((VW, VH))
        try:
            draw.physical_size = renpy_host.window_size()
        except Exception:
            pass
        # Solid.render needs renpy.display.draw.draw_to_virt
        import renpy.display as renpy_display
        renpy_display.draw = draw

        # Probe A: hold=0 product default timing
        f0 = Fade(0.5, 0.0, 0.5, old_widget=old, new_widget=new, widget=solid_black)
        _log(lines, "fade0 type=%s delay=%s" % (type(f0).__name__, getattr(f0, "delay", None)))
        if not isinstance(f0, MultipleTransition):
            raise RuntimeError("Fade hold0 not MultipleTransition")

        stages0 = {
            "st0": 0.0,
            "out_mid": 0.25,
            "black_boundary": 0.50,
            "in_mid": 0.75,
            "late": 0.95,
        }
        m0 = _sample_fade(draw, f0, stages0, lines, "hold0")

        # Probe B: hold=0.1 with real Solid (compare to fade_live_st BlackFill)
        f1 = Fade(0.5, 0.1, 0.5, old_widget=old, new_widget=new, widget=solid_black)
        stages1 = {
            "st0": 0.0,
            "out_mid": 0.25,
            "black_hold": 0.55,
            "in_mid": 0.85,
            "late": 1.05,
        }
        m1 = _sample_fade(draw, f1, stages1, lines, "hold01")

        def eval_hold0(means):
            l0 = _luma(means["st0"])
            l_out = _luma(means["out_mid"])
            l_b = _luma(means["black_boundary"])
            l_in = _luma(means["in_mid"])
            l_late = _luma(means["late"])
            mb = means["black_boundary"]
            out_darkens = l_out < l0 - 5.0
            # At hold=0 boundary may be start of in (complete~0 black) or end of out
            boundary_black = (mb[0] + mb[1] + mb[2]) / 3.0 < 40.0
            in_brightens = l_in > l_b + 5.0
            late_ok = l_late > l_b + 10.0
            st0_ok = (means["st0"][0] + means["st0"][1] + means["st0"][2]) > 60.0
            return out_darkens, boundary_black, in_brightens, late_ok, st0_ok, (l0, l_out, l_b, l_in, l_late)

        def eval_hold01(means):
            l0 = _luma(means["st0"])
            l_out = _luma(means["out_mid"])
            l_h = _luma(means["black_hold"])
            l_in = _luma(means["in_mid"])
            mh = means["black_hold"]
            out_darkens = l_out < l0 - 5.0
            hold_black = (mh[0] + mh[1] + mh[2]) / 3.0 < 40.0
            in_brightens = l_in > l_h + 5.0
            st0_ok = (means["st0"][0] + means["st0"][1] + means["st0"][2]) > 60.0
            return out_darkens, hold_black, in_brightens, st0_ok, (l0, l_out, l_h, l_in)

        a_out, a_black, a_in, a_late, a_st0, a_lums = eval_hold0(m0)
        b_out, b_black, b_in, b_st0, b_lums = eval_hold01(m1)

        # Primary: hold=0 product default with real Solid
        ac = (
            less_updates is False
            and models is True
            and transitions_pref >= 2
            and a_out
            and a_black
            and a_in
            and a_late
            and a_st0
            and used_product
        )
        # Secondary Soft: hold=0.1 solid also
        soft_hold01 = b_out and b_black and b_in and b_st0

        ok = bool(ac)
        reason = (
            "hold0: out_darkens=%s boundary_black=%s in_brightens=%s late_ok=%s st0_ok=%s lums=%s; "
            "hold01: out=%s black=%s in=%s st0=%s lums=%s soft_hold01=%s product=%s solid=True"
            % (
                a_out,
                a_black,
                a_in,
                a_late,
                a_st0,
                a_lums,
                b_out,
                b_black,
                b_in,
                b_st0,
                b_lums,
                soft_hold01,
                used_product,
            )
        )
        _log(lines, ("PASS " if ok else "FAIL ") + reason)

        _write(
            lines,
            ok,
            path_kind="fade_product_solid",
            transitions_pref=transitions_pref,
            used_product=used_product,
            solid_widget=True,
            hold0_boundary_black=a_black,
            hold01_black=b_black,
            soft_hold01=soft_hold01,
            reason=reason,
        )
    except Exception as e:
        _log(lines, "EXCEPTION %s" % e)
        _log(lines, traceback.format_exc())
        _write(lines, False, reason="exception:%s" % e)
    finally:
        _request_quit()


if __name__ == "__main__":
    main()
else:
    main()
