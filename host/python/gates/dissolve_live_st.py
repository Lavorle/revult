"""
AC-L1 dissolve_live_st — real Dissolve multi-st mid blend with product images.

Gate name: dissolve_live_st  (RENPY_HOST_GATE=dissolve_live_st)

Anti-cheat contract (plan SSOT):
  - Real renpy.display.transition.Dissolve class
  - Product Displayables (main_menu.png / lecturehall.jpg), not solid-only sole content
  - Multi-st: 0, 0.25T, 0.5T, 0.75T via Dissolve.render(w,h,st,st)
  - Assert abs(operation_complete - st/T) < 1e-3
  - draw_screen each; read_game_rt_rgba; anti-hard-cut mid blend
  - NEVER prefs.transitions=0; NEVER forced uniforms without Dissolve.render
  - path_kind=transition_render

Note: no from __future__; host run_file prepends imports.
"""

import os
import struct
import traceback
import zlib
from pathlib import Path

import renpy_host  # type: ignore

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-dissolve_live_st.txt"
out.parent.mkdir(parents=True, exist_ok=True)

VW, VH = 1280, 720
T = 0.5  # dissolve duration
ST_SAMPLES = (0.0, 0.25 * T, 0.5 * T, 0.75 * T)
TRANSITIONS_PREF = 2


def _log(lines, msg):
    lines.append(str(msg))
    print("[dissolve_live_st]", msg, flush=True)


def _write(lines, ok, **extra):
    # Flatten artifact as key=value lines + final ok=
    body = list(lines)
    for k, v in extra.items():
        body.append("%s=%s" % (k, v))
    body.append("ok=%s" % ok)
    text = "\n".join(body) + "\n"
    out.write_text(text)
    print("[dissolve_live_st] WROTE", out, "ok=%s" % ok, flush=True)


def _request_quit():
    try:
        renpy_host.request_quit()
    except Exception:
        pass


# --- product image helpers (PNG/JPEG → Surface-like) ---


class _Surf:
    """Minimal surface with get_size + _pixels for WgpuDraw.load_texture."""

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
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    if path.suffix.lower() == ".png":
        w, h, rgba = _png_rgba(path)
        return w, h, rgba, path.name
    from PIL import Image  # type: ignore

    im = Image.open(path).convert("RGBA")
    w, h = im.size
    return w, h, im.tobytes(), path.name


def _fit(vw, vh, w, h, rgba):
    if w == vw and h == vh:
        return _Surf(vw, vh, rgba)
    buf = bytearray(bytes([0, 0, 0, 255]) * (vw * vh))
    x0 = max(0, (vw - w) // 2)
    y0 = max(0, (vh - h) // 2)
    cw = min(w, vw)
    ch = min(h, vh)
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


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def _bootstrap(lines):
    """Import renpy under host, seed styles/models/prefs for real Dissolve.render."""
    import bootstrap as boot

    good, miss, err, extra = boot.stage_import_renpy()
    _log(lines, "import_renpy good=%s err=%s" % (good, err))
    if not good:
        raise RuntimeError("import_renpy failed: %s" % err)
    good, miss, err, extra = boot.stage_import_all()
    _log(lines, "import_all good=%s err=%s missing=%s" % (good, err, (miss or [])[:5]))
    if not good:
        raise RuntimeError("import_all failed: %s" % err)

    import renpy.game as game
    import renpy.display.render as render_mod
    import renpy.style as style_mod
    import renpy.display.displayable as disp_mod

    # AC-L0 nonsuppression
    render_mod.models = True
    game.less_updates = False
    if game.preferences is None:
        from renpy.preferences import Preferences

        game.preferences = Preferences()
    # Critical: set to 2, NEVER 0.
    game.preferences.transitions = TRANSITIONS_PREF

    # Proper default style (parent=None) — mirrors renpy/common/00style.rpy
    default = style_mod.Style(None, name=("default",))
    # Seed size props so style.xmaximum is None-safe; xminimum=0 for Solid later.
    default.properties.append(
        {
            "xminimum": 0,
            "yminimum": 0,
            "xmaximum": None,
            "ymaximum": None,
            "xfill": False,
            "yfill": False,
            "minwidth": 0,
        }
    )
    style_mod.styles[("default",)] = default
    disp_mod.default_style = default

    try:
        render_mod.render_ready()
    except Exception as e:
        _log(lines, "render_ready softfail %s" % e)

    less_updates = bool(game.less_updates)
    models = bool(render_mod.models)
    transitions_pref = int(getattr(game.preferences, "transitions", -1))
    _log(
        lines,
        "less_updates=%s models=%s transitions_pref=%s" % (less_updates, models, transitions_pref),
    )
    return less_updates, models, transitions_pref


def main():
    lines = []
    ok = False
    reason = ""
    completes = []
    means = []
    old_tag = "unset"
    new_tag = "unset"
    less_updates = None
    models = None
    transitions_pref = None
    used_product = False
    path_kind = "transition_render"
    transitions_forced_zero = False

    try:
        less_updates, models, transitions_pref = _bootstrap(lines)

        from renpy.display.displayable import Displayable
        from renpy.display.render import Render
        from renpy.display.transition import Dissolve
        from renpy.wgpu.draw import WgpuDraw

        class ProductImage(Displayable):
            """Displayable wrapping a preloaded product surface (real Displayable)."""

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
            _log(lines, "old load fail %s" % e)
        try:
            nw, nh, nrgba, new_tag = _load_image_rgba(
                game_dir / "images" / "bg lecturehall.jpg"
            )
            new_surf = _fit(VW, VH, nw, nh, nrgba)
        except Exception as e:
            new_tag = "solid_blue_fallback(%s)" % e
            new_surf = _Surf(VW, VH, bytes([40, 40, 220, 255]) * (VW * VH))
            _log(lines, "new load fail %s" % e)

        used_product = ("fallback" not in old_tag) and ("fallback" not in new_tag)
        _log(lines, "old=%s new=%s used_product=%s" % (old_tag, new_tag, used_product))

        old = ProductImage(old_surf, old_tag)
        new = ProductImage(new_surf, new_tag)

        # Real Dissolve class — not FakeRender forced-u.
        d = Dissolve(T, old_widget=old, new_widget=new)
        _log(lines, "Dissolve class=%s time=%s delay=%s" % (type(d).__name__, d.time, d.delay))

        draw = WgpuDraw()
        draw.init((VW, VH))
        try:
            draw.physical_size = renpy_host.window_size()
        except Exception:
            pass

        complete_ok = True
        for st in ST_SAMPLES:
            # Real Transition.render multi-st (Option B).
            rv = d.render(VW, VH, st, st)
            c = getattr(rv, "operation_complete", None)
            if c is None:
                uniforms = getattr(rv, "uniforms", None) or {}
                c = uniforms.get("u_renpy_dissolve")
            expected = min(1.0, float(st) / T)
            if c is None or abs(float(c) - expected) >= 1e-3:
                complete_ok = False
                _log(
                    lines,
                    "complete mismatch st=%s c=%s expected=%s" % (st, c, expected),
                )
            completes.append(float(c) if c is not None else float("nan"))

            shaders = getattr(rv, "shaders", None)
            uniforms = getattr(rv, "uniforms", None)
            _log(
                lines,
                "st=%.4f complete=%s shaders=%s uniforms=%s mesh=%s"
                % (st, c, shaders, uniforms, getattr(rv, "mesh", None)),
            )

            draw.draw_screen(rv, flip=True)
            rw, rh, rgba = renpy_host.read_game_rt_rgba()
            m = _mean_rgb(rgba, rw, rh)
            means.append(m)
            _log(lines, "  mean=(%.1f,%.1f,%.1f)" % m)

        # --- Pass criteria ---
        mid_completes = [c for c in completes if 0.2 <= c <= 0.8]
        mid_count = len(mid_completes)

        # Anti-hard-cut: mid means not within ε of st0 (old) AND not within ε of last mid
        # toward new; not clear; product-channel energy elevated.
        clear_like = all(_luma(m) < 40 for m in means)
        st0 = means[0]
        st_last = means[-1]
        # Mid samples = indices 1..-1 (0.25T, 0.5T, 0.75T)
        mid_means = means[1:]
        eps = 12.0  # RGB L1 distance threshold for "same as endpoint"
        mid_not_hard_old = all(_dist(m, st0) > eps for m in mid_means)
        mid_not_hard_new = all(_dist(m, st_last) > eps for m in mid_means[:2])  # first two mids
        # Monotonic trend: st0 closer to old, last closer to new (luma or channel energy)
        # For product images: R tends to rise main_menu→lecturehall in this pair.
        # Use progressive distance: each mid should move away from st0 toward st_last.
        progressive = True
        if len(means) >= 3:
            d01 = _dist(means[1], st0)
            d02 = _dist(means[2], st0)
            d03 = _dist(means[3], st0) if len(means) > 3 else d02
            progressive = d01 < d02 + 5.0 and d02 < d03 + 5.0  # tolerate noise

        energy_ok = all((m[0] + m[1] + m[2]) > 60.0 for m in means)

        ac_l0 = (
            less_updates is False
            and models is True
            and transitions_pref >= 2
            and transitions_forced_zero is False
        )
        ac_l1 = (
            complete_ok
            and mid_count >= 2
            and not clear_like
            and mid_not_hard_old
            and energy_ok
            and used_product
            and progressive
        )
        ok = bool(ac_l0 and ac_l1)
        if not ok:
            reason = (
                "ac_l0=%s complete_ok=%s mid_count=%s clear=%s hard_old=%s "
                "energy=%s product=%s progressive=%s"
                % (
                    ac_l0,
                    complete_ok,
                    mid_count,
                    clear_like,
                    not mid_not_hard_old,
                    energy_ok,
                    used_product,
                    progressive,
                )
            )
            _log(lines, "FAIL %s" % reason)
        else:
            _log(lines, "PASS mid_count=%s progressive=%s" % (mid_count, progressive))

        _write(
            lines,
            ok,
            path_kind=path_kind,
            transitions_pref=transitions_pref,
            transitions_forced_zero=transitions_forced_zero,
            less_updates=less_updates,
            models=models,
            complete=completes,
            means=["(%.1f,%.1f,%.1f)" % m for m in means],
            mid_count=mid_count,
            used_product=used_product,
            old=old_tag,
            new=new_tag,
            reason=reason or "pass",
        )
    except Exception as e:
        _log(lines, "exception %s\n%s" % (e, traceback.format_exc()))
        _write(
            lines,
            False,
            path_kind=path_kind,
            transitions_pref=transitions_pref,
            transitions_forced_zero=transitions_forced_zero,
            less_updates=less_updates,
            models=models,
            complete=completes,
            means=["(%.1f,%.1f,%.1f)" % m for m in means],
            reason="exception:%s" % e,
            old=old_tag,
            new=new_tag,
        )
    finally:
        _request_quit()


main()
