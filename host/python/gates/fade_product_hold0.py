"""
fade_product_hold0 — product default Fade(.5, 0, .5) stage table.

Gate name: fade_product_hold0  (RENPY_HOST_GATE=fade_product_hold0)

Closes the evidence gap left by fade_live_st (which uses hold=0.1):
product default is renpy/common/00definitions.rpy → Fade(.5, 0, .5).

With hold_time=0, MultipleTransition is:
  old → Dissolve(0.5) → Solid black → Dissolve(0.5) → new
  (no NoTransition hold segment). Black mid is the boundary at st≈0.5.

Stage table (absolute st on Fade/MultipleTransition, total delay=1.0):
  st0       0.00  → old scene energy
  out_mid   0.25  → darkening vs st0 (F1)
  black_mid 0.50  → near-black boundary (F2)
  in_mid    0.75  → brightening toward new (F3)
  late      0.95  → closer to new

Also dense-samples st across [0,1] for frame-to-frame mean-luma spikes
(flicker flag; does not alone fail the gate unless extreme + stage fail).

Never prefs.transitions=0. path_kind=product_fade_hold0_render.

Note: no from __future__; host run_file prepends imports.
"""

import os
import struct
import traceback
import zlib
from pathlib import Path

from host.python.gates._harness import gate_harness, parametrized_gate

import renpy_host  # type: ignore

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-fade_product_hold0.txt"
out.parent.mkdir(parents=True, exist_ok=True)

VW, VH = 1280, 720
# Product default: renpy/common/00definitions.rpy define fade = Fade(.5, 0, .5)
OUT_T = 0.5
HOLD_T = 0.0
IN_T = 0.5
# Total delay = 0.5 + 0 + 0.5 = 1.0
TRANSITIONS_PREF = 2

# Stage sample times (absolute st). hold=0 → black is boundary at st≈0.5.
STAGE_STS = {
    "st0": 0.0,
    "out_mid": 0.25,  # mid of out Dissolve
    "black_mid": 0.50,  # boundary: end-out / start-in (black)
    "in_mid": 0.75,  # mid of in Dissolve
    "late": 0.95,  # near end of in
}

# Dense samples for flicker metric (frame-to-frame mean-luma delta).
DENSE_STS = [i * 0.05 for i in range(21)]  # 0.00 .. 1.00 step 0.05

# Spike threshold: |Δluma| above this between adjacent dense samples is a "spike".
# Product Fade should darken/brighten smoothly; dual-draw thrash can produce
# near-full-scene jumps (>80 luma) mid-out/mid-in.
SPIKE_LUMA = 80.0
# Max allowed extreme spikes outside the expected black-boundary jump.
# One jump around black_mid (out→black or black→in) is expected to be large;
# continuous thrash produces many spikes.
MAX_EXTREME_SPIKES = 3


def _log(lines, msg):
    lines.append(str(msg))
    print("[fade_product_hold0]", msg, flush=True)


def _write(lines, ok, **extra):
    body = list(lines)
    for k, v in extra.items():
        body.append(f"{k}={v}")
    body.append(f"ok={ok}")
    text = "\n".join(body) + "\n"
    out.write_text(text)
    print("[fade_product_hold0] WROTE", out, f"ok={ok}", flush=True)


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


def _bootstrap(lines):
    import bootstrap as boot

    good, miss, err, extra = boot.stage_import_renpy()  # noqa: RUF059
    _log(lines, f"import_renpy good={good} err={err}")
    if not good:
        raise RuntimeError(f"import_renpy failed: {err}")
    good, _miss, err, _extra = boot.stage_import_all()
    _log(lines, f"import_all good={good} err={err}")
    if not good:
        raise RuntimeError(f"import_all failed: {err}")

    import renpy.display.render as render_mod
    import renpy.style as style_mod

    import renpy.display.displayable as disp_mod
    from renpy import game

    render_mod.models = True
    game.less_updates = False
    if game.preferences is None:
        from renpy.preferences import Preferences

        game.preferences = Preferences()
    game.preferences.transitions = TRANSITIONS_PREF

    # Seed default style so Solid (product Fade widget) works under bare import_all.
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
        _log(lines, f"render_ready softfail {e}")

    less_updates = bool(game.less_updates)
    models = bool(render_mod.models)
    transitions_pref = int(getattr(game.preferences, "transitions", -1))
    _log(
        lines,
        f"less_updates={less_updates} models={models} transitions_pref={transitions_pref}",
    )
    return less_updates, models, transitions_pref


def main():
    lines = []
    ok = False
    reason = ""
    stage_means = {}
    dense_lumas = []
    old_tag = "unset"
    new_tag = "unset"
    less_updates = None
    models = None
    transitions_pref = None
    used_product = False
    path_kind = "product_fade_hold0_render"
    transitions_forced_zero = False
    fade_type = None
    fade_delay = None
    widget_kind = "unset"
    spike_count = 0
    max_spike = 0.0
    flicker_flag = False

    try:
        less_updates, models, transitions_pref = _bootstrap(lines)

        from renpy.display.render import Render

        from renpy.display.displayable import Displayable
        from renpy.display.transition import Fade, MultipleTransition
        from renpy.wgpu.draw import WgpuDraw

        class ProductImage(Displayable):
            def __init__(self, surf, tag="img"):
                super().__init__()
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

        class BlackFill(Displayable):
            """Full-window black Displayable fallback if Solid style path fails.

            Product Fade uses Solid((0,0,0,255)); we try Solid first, then this.
            """

            def __init__(self):
                super().__init__()

            def render(self, w, h, st, at):
                rw = int(w) if w and w > 0 else VW
                rh = int(h) if h and h > 0 else VH
                rv = Render(rw, rh)
                surf = _Surf(rw, rh, bytes([0, 0, 0, 255]) * (rw * rh))
                rv.blit(surf, (0, 0))
                return rv

            def visit(self):
                return []

        game_dir = _base / "the_question" / "game"
        try:
            ow, oh, orgba, old_tag = _load_image_rgba(game_dir / "gui" / "main_menu.png")
            old_surf = _fit(VW, VH, ow, oh, orgba)
        except Exception as e:
            old_tag = f"solid_red_fallback({e})"
            old_surf = _Surf(VW, VH, bytes([220, 40, 40, 255]) * (VW * VH))
        try:
            nw, nh, nrgba, new_tag = _load_image_rgba(
                game_dir / "images" / "bg lecturehall.jpg"
            )
            new_surf = _fit(VW, VH, nw, nh, nrgba)
        except Exception as e:
            new_tag = f"solid_blue_fallback({e})"
            new_surf = _Surf(VW, VH, bytes([40, 40, 220, 255]) * (VW * VH))

        used_product = ("fallback" not in old_tag) and ("fallback" not in new_tag)
        _log(lines, f"old={old_tag} new={new_tag} used_product={used_product}")

        old = ProductImage(old_surf, old_tag)
        new = ProductImage(new_surf, new_tag)

        # Install WgpuDraw as renpy.display.draw BEFORE Solid/Fade.render so the
        # product Solid path (solid_texture 10x10 + reverse scale via
        # draw_to_virt) is exercised — that is the black mid the user misses.
        draw = WgpuDraw()
        draw.init((VW, VH))
        try:
            draw.physical_size = renpy_host.window_size()
        except Exception:
            pass
        try:
            import renpy.display as renpy_display

            renpy_display.draw = draw
            _log(lines, "renpy.display.draw = WgpuDraw (product Solid path enabled)")
        except Exception as e:
            _log(lines, f"renpy.display.draw install softfail {e}")

        # Prefer product Solid black (00definitions.rpy Fade default widget).
        # BlackFill only if Solid still cannot render after draw install.
        black_widget = None
        try:
            from renpy.display.imagelike import Solid

            solid = Solid((0, 0, 0, 255))
            probe = solid.render(VW, VH, 0, 0)
            if probe is None:
                raise RuntimeError("Solid.render returned None")
            # Product Solid sets reverse Matrix2D for 10x10 → dest scale.
            has_reverse = bool(getattr(probe, "reverse", None))
            black_widget = solid
            widget_kind = "Solid_reverse" if has_reverse else "Solid"
            _log(lines, f"Solid probe ok reverse={has_reverse} size={probe.get_size()}")
        except Exception as e:
            _log(lines, f"Solid widget softfail ({e}); using BlackFill")
            black_widget = BlackFill()
            widget_kind = "BlackFill"

        # Product default timing: Fade(.5, 0, .5)
        f = Fade(
            OUT_T,
            HOLD_T,
            IN_T,
            old_widget=old,
            new_widget=new,
            widget=black_widget,
        )
        fade_type = type(f).__name__
        fade_delay = getattr(f, "delay", None)
        _log(
            lines,
            f"Fade type={fade_type} delay={fade_delay} hold={HOLD_T} widget={widget_kind}",
        )
        if not isinstance(f, MultipleTransition):
            raise RuntimeError(f"Fade did not return MultipleTransition: {type(f)}")
        # hold=0 → delay should be OUT+IN = 1.0
        if fade_delay is not None and abs(float(fade_delay) - (OUT_T + IN_T)) > 1e-6:
            _log(lines, f"WARN unexpected delay={fade_delay} expected={OUT_T + IN_T}")

        # --- Named stage samples (F1–F3) ---
        for name, st in STAGE_STS.items():
            rv = f.render(VW, VH, st, st)
            oc = getattr(rv, "operation_complete", None)
            uniforms = getattr(rv, "uniforms", None)
            shaders = getattr(rv, "shaders", None)
            draw.draw_screen(rv, flip=True)
            rw, rh, rgba = renpy_host.read_game_rt_rgba()
            m = _mean_rgb(rgba, rw, rh)
            stage_means[name] = m
            _log(
                lines,
                f"stage={name} st={st:.3f} complete={oc} shaders={shaders} uniforms={uniforms} mean=({m[0]:.1f},{m[1]:.1f},{m[2]:.1f})",
            )

        # --- Dense samples for flicker metric ---
        prev_l = None
        spikes = []
        for st in DENSE_STS:
            rv = f.render(VW, VH, st, st)
            draw.draw_screen(rv, flip=True)
            rw, rh, rgba = renpy_host.read_game_rt_rgba()
            m = _mean_rgb(rgba, rw, rh)
            l = _luma(m)
            dense_lumas.append((st, l, m))
            if prev_l is not None:
                d = abs(l - prev_l)
                max_spike = max(max_spike, d)
                if d >= SPIKE_LUMA:
                    spikes.append((st, d, prev_l, l))
            prev_l = l

        spike_count = len(spikes)
        # Expected large jump(s) near black boundary (st≈0.45–0.55). Count
        # spikes outside that window as thrash/flicker.
        thrash_spikes = [
            s for s in spikes if not (0.40 <= s[0] <= 0.60)
        ]
        thrash_count = len(thrash_spikes)
        flicker_flag = thrash_count > MAX_EXTREME_SPIKES
        _log(
            lines,
            "dense_spikes={} thrash_spikes={} max_spike={:.1f} flicker_flag={} spikes={}".format(
                spike_count,
                thrash_count,
                max_spike,
                flicker_flag,
                [
                    f"st={s[0]:.2f} d={s[1]:.1f} ({s[2]:.1f}→{s[3]:.1f})"
                    for s in spikes[:8]
                ],
            ),
        )

        # --- Stage table assertions (F1–F3) ---
        m0 = stage_means["st0"]
        m_out = stage_means["out_mid"]
        m_black = stage_means["black_mid"]
        m_in = stage_means["in_mid"]
        m_late = stage_means["late"]

        l0 = _luma(m0)
        l_out = _luma(m_out)
        l_black = _luma(m_black)
        l_in = _luma(m_in)
        l_late = _luma(m_late)

        # F1: out-mid darkens vs st0
        out_darkens = l_out < l0 - 5.0
        # F2: black boundary near black (mean RGB low). hold=0 → st=0.5 is the mid.
        black_near_zero = (m_black[0] + m_black[1] + m_black[2]) / 3.0 < 40.0
        # F3: in-mid brightens vs black
        in_brightens = l_in > l_black + 5.0
        # late closer to new / brighter than black
        late_ok = l_late > l_black + 10.0
        # not permanent clear across all stages
        not_all_clear = not all(
            (m[0] + m[1] + m[2]) < 40.0 for m in stage_means.values()
        )
        # st0 has product energy (not blank)
        st0_ok = (m0[0] + m0[1] + m0[2]) > 60.0
        # hold_time contract
        hold_is_zero = abs(HOLD_T) < 1e-9
        # Product black mid must go through Solid reverse (not BlackFill escape).
        solid_product_path = str(widget_kind).startswith("Solid")

        ac_l0 = (
            less_updates is False
            and models is True
            and transitions_pref >= 2
            and transitions_forced_zero is False
            and hold_is_zero
        )
        # F1–F3 product stage table; flicker_flag fails when thrash exceeds
        # MAX_EXTREME_SPIKES. solid_product_path required so we do not
        # false-green on BlackFill-only (fade_live_st already covers that).
        ac_f = (
            out_darkens
            and black_near_zero
            and in_brightens
            and late_ok
            and not_all_clear
            and st0_ok
            and used_product
            and solid_product_path
            and not flicker_flag
        )
        ok = bool(ac_l0 and ac_f)
        reason = (
            f"F1_out_darkens={out_darkens} F2_black_near_zero={black_near_zero} F3_in_brightens={in_brightens} late_ok={late_ok} "
            f"not_all_clear={not_all_clear} st0_ok={st0_ok} product={used_product} hold0={hold_is_zero} solid_path={solid_product_path} "
            f"widget={widget_kind} flicker_flag={flicker_flag} thrash_spikes={thrash_count} "
            f"lums=({l0:.1f},{l_out:.1f},{l_black:.1f},{l_in:.1f},{l_late:.1f})"
        )
        _log(lines, ("PASS " if ok else "FAIL ") + reason)

        _write(
            lines,
            ok,
            path_kind=path_kind,
            transitions_pref=transitions_pref,
            transitions_forced_zero=transitions_forced_zero,
            less_updates=less_updates,
            models=models,
            fade_type=fade_type,
            fade_delay=fade_delay,
            hold_time=HOLD_T,
            out_time=OUT_T,
            in_time=IN_T,
            widget_kind=widget_kind,
            stage_means={k: "({:.1f},{:.1f},{:.1f})".format(*v) for k, v in stage_means.items()},
            dense_lumas=[f"st={s:.2f} L={l:.1f}" for s, l, _m in dense_lumas],
            spike_count=spike_count,
            thrash_spikes=thrash_count,
            max_spike=f"{max_spike:.1f}",
            flicker_flag=flicker_flag,
            used_product=used_product,
            old=old_tag,
            new=new_tag,
            reason=reason,
            # F criteria automated by this gate:
            F1="out_darkens",
            F2="black_near_zero@st0.5",
            F3="in_brightens+no_thrash_flicker",
            F4="not_this_gate(see_dissolve/ac_live)",
        )
    except Exception as e:
        _log(lines, f"exception {e}\n{traceback.format_exc()}")
        _write(
            lines,
            False,
            path_kind=path_kind,
            transitions_pref=transitions_pref,
            transitions_forced_zero=transitions_forced_zero,
            less_updates=less_updates,
            models=models,
            fade_type=fade_type,
            hold_time=HOLD_T,
            widget_kind=widget_kind,
            stage_means={k: "({:.1f},{:.1f},{:.1f})".format(*v) for k, v in stage_means.items()},
            reason=f"exception:{e}",
            old=old_tag,
            new=new_tag,
            flicker_flag=flicker_flag,
        )
    finally:
        _request_quit()


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
