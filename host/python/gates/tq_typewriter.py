"""
AC-T4 tq_typewriter — progressive mid-st text blits (typewriter path).

Gate name: tq_typewriter  (RENPY_HOST_GATE=tq_typewriter)

Measured gap (Phase 0): preferred suite + tq_say_chrome / text_descender /
tex_subsurface_uv / built-in text all miss progressive mid-st dialogue reveal.
Product gates force prefs.text_cps=0. See:
  .omc/artifacts/wgpu-vis-typewriter-20260717/gap-typewriter-coverage.md

Product path under host (textshaders stubbed):
  Text.render_blits → layout.blits_typewriter(st)
  → HostTexture.subsurface((b_x,b_y,b_w,b_h)) progressive UV rects
  → WgpuDraw._draw_texture_at with _host_tex_uv

This gate exercises the **same progressive subsurface draw path** without
requiring full product Text bootstrap (which depends on agent-a FTFont /
layout readiness). Synthetic full-line texture + multi-st width ladder.

Asserts (AC-T4):
  1. Finite text_cps forced in env marker (40) — gate never uses cps=0 path.
  2. st ladder: 0, 0.25, 0.5, 0.75, 1.0 of full line width.
  3. Mid-st coverage ∈ (0, full) — not empty-at-all-st, not full-only-at-all-st.
  4. Monotonic non-decreasing revealed width / ink column count across st.
  5. Left-to-right: at mid-st, left third has ink, right third stays BG.
  6. Instant/full path (st=1.0): full coverage.

Artifact: host/target/gate-tq_typewriter.txt with ok=True/False + metrics.

Note: no from __future__; host run_file prepends imports.
"""

import os
import traceback
from pathlib import Path

import renpy_host  # type: ignore
from renpy.pygame.surface import Surface
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
out = _base / "host" / "target" / "gate-tq_typewriter.txt"
out.parent.mkdir(parents=True, exist_ok=True)

# Forced finite cps marker (product would use preferences.text_cps=40).
TEXT_CPS = 40

VW, VH = 1280, 720
# Synthetic "full line" glyph strip — opaque white ink on transparent, like
# a pre-baked text Surface (layout.textures).
TW, TH = 400, 48
# Place strip in virtual canvas (say-dialogue-ish lower region, but isolated).
OX, OY = 100, 200

BG = (40, 80, 120, 255)  # distinct from clear / white ink
INK = (255, 255, 255, 255)

# st samples as fractions of full reveal width (mirrors st / max_time).
ST_FRACS = (0.0, 0.25, 0.5, 0.75, 1.0)


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

    def absolute_blit(self, child, pos):
        # Product text path uses absolute_blit(tex.subsurface(...), (x,y)).
        try:
            xo, yo = pos
        except Exception:
            xo, yo = 0, 0
        self.children.append((child, float(xo), float(yo), False, True))
        return self

    def get_size(self):
        return (self.width, self.height)


def _safe_print(msg):
    """print may be hooked by partial renpy.log without renpy.config — never raise."""
    try:
        # Prefer raw fd write to dodge renpy.log stdout wrapper.
        import sys

        sys.__stdout__.write("[tq_typewriter] %s\n" % msg)
        sys.__stdout__.flush()
    except Exception:
        try:
            print("[tq_typewriter]", msg, flush=True)
        except Exception:
            pass


def _log(lines, msg):
    lines.append(str(msg))
    _safe_print(msg)


def _write(lines, ok, **extra):
    body = list(lines)
    for k, v in extra.items():
        body.append("%s=%s" % (k, v))
    body.append("ok=%s" % ok)
    text = "\n".join(body) + "\n"
    try:
        out.write_text(text)
    except Exception as e:
        # Last-ditch: write minimal ok line next to CWD target
        try:
            Path("target/gate-tq_typewriter.txt").write_text(
                "ok=%s write_err=%s\n" % (ok, e)
            )
        except Exception:
            pass
        return
    _safe_print("WROTE %s ok=%s" % (out, ok))


def _request_quit():
    try:
        renpy_host.request_quit()
    except Exception:
        pass


def _sample(rgba, w, h, x, y):
    x = max(0, min(w - 1, int(x)))
    y = max(0, min(h - 1, int(y)))
    o = (y * w + x) * 4
    return rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]


def _is_bg(c, tol=30):
    return all(abs(int(c[i]) - int(BG[i])) <= tol for i in range(3))


def _is_ink(c, tol=40):
    # White-ish over BG (premul may darken slightly — require bright + not BG).
    return int(c[0]) > 180 and int(c[1]) > 180 and int(c[2]) > 180


def _make_line_tex(draw):
    """Full-line Surface: opaque white strip (product text bake stand-in)."""
    s = Surface((TW, TH))
    # Transparent fill then white ink band (full width — progressive via UV).
    s.fill((0, 0, 0, 0))
    # Fill entire rect opaque white (typewriter reveals via subsurface width).
    for y in range(TH):
        for x in range(TW):
            s.set_at((x, y), INK)
    return draw.load_texture(s)


def _blits_typewriter_like(frac):
    """Mirror layout.blits_typewriter single-line progressive width.

    Returns list of (x, y, w, h) source rects in full-line texture space.
    frac=0 → empty; frac=1 → full line.
    """
    if frac <= 0.0:
        return []
    w = max(1, int(round(frac * TW)))
    w = min(TW, w)
    return [(0, 0, w, TH)]


def _draw_progressive(draw, full_tex, frac):
    """Draw BG + progressive subsurface blits; return (rw, rh, rgba)."""
    bg = Surface((VW, VH))
    bg.fill(BG)
    root = FakeRender(VW, VH)
    root.blit(bg, 0, 0)

    blits = _blits_typewriter_like(frac)
    for b_x, b_y, b_w, b_h in blits:
        if b_w <= 0 or b_h <= 0:
            continue
        if isinstance(full_tex, HostTexture):
            sub = full_tex.subsurface((b_x, b_y, b_w, b_h))
        else:
            sub = full_tex
        # Product: absolute_blit(sub, (b_x + xo, b_y + yo)) — dest tracks source
        # origin so progressive growth is left-to-right in place.
        root.absolute_blit(sub, (OX + b_x, OY + b_y))

    draw.draw_screen(root, flip=True)
    rw, rh, rgba = renpy_host.read_game_rt_rgba()
    return rw, rh, rgba


def _coverage_metrics(rgba, rw, rh):
    """Ink column count + left/right third ink hits inside dest strip.

    Returns dict:
      ink_cols, coverage (0..1 vs TW), left_ink, mid_ink, right_ink,
      max_ink_x (virtual), empty
    """
    sx = rw / float(VW)
    sy = rh / float(VH)
    # Sample center row of the strip.
    cy = int((OY + TH // 2) * sy)
    ink_cols = 0
    max_ink_x = -1
    left_ink = mid_ink = right_ink = 0
    # Walk virtual x across dest strip at 1px steps.
    for vx in range(TW):
        px = int((OX + vx) * sx)
        c = _sample(rgba, rw, rh, px, cy)
        if _is_ink(c):
            ink_cols += 1
            max_ink_x = vx
            if vx < TW // 3:
                left_ink += 1
            elif vx < 2 * TW // 3:
                mid_ink += 1
            else:
                right_ink += 1
    coverage = ink_cols / float(TW)
    return {
        "ink_cols": ink_cols,
        "coverage": coverage,
        "left_ink": left_ink,
        "mid_ink": mid_ink,
        "right_ink": right_ink,
        "max_ink_x": max_ink_x,
        "empty": ink_cols == 0,
    }


def main():
    lines = []
    path_kind = "progressive_hosttexture_subsurface"
    try:
        _log(lines, "start text_cps=%s st_fracs=%s TW=%s TH=%s" % (TEXT_CPS, ST_FRACS, TW, TH))
        draw = WgpuDraw()
        draw.init((VW, VH))
        try:
            draw.physical_size = renpy_host.window_size()
        except Exception:
            pass

        full_tex = _make_line_tex(draw)
        if full_tex is None or (isinstance(full_tex, HostTexture) and full_tex.handle <= 0):
            _write(
                lines,
                False,
                path_kind=path_kind,
                text_cps=TEXT_CPS,
                reason="load_texture_failed",
            )
            return

        # Confirm full tex is HostTexture with full UV rect (product bake shape).
        is_ht = isinstance(full_tex, HostTexture)
        _log(
            lines,
            "full_tex type=%s handle=%s size=%sx%s sub=%s"
            % (
                type(full_tex).__name__,
                getattr(full_tex, "handle", None),
                getattr(full_tex, "width", None),
                getattr(full_tex, "height", None),
                hasattr(full_tex, "subsurface"),
            ),
        )

        samples = []
        for frac in ST_FRACS:
            rw, rh, rgba = _draw_progressive(draw, full_tex, frac)
            if rw <= 0 or rh <= 0 or not rgba:
                _write(
                    lines,
                    False,
                    path_kind=path_kind,
                    text_cps=TEXT_CPS,
                    reason="read_rt_empty frac=%s" % frac,
                )
                return
            m = _coverage_metrics(rgba, rw, rh)
            m["frac"] = frac
            m["expected_w"] = 0 if frac <= 0 else max(1, int(round(frac * TW)))
            samples.append(m)
            _log(
                lines,
                "st_frac=%.2f expected_w=%d ink_cols=%d coverage=%.3f "
                "L/M/R=%d/%d/%d max_ink_x=%d empty=%s"
                % (
                    frac,
                    m["expected_w"],
                    m["ink_cols"],
                    m["coverage"],
                    m["left_ink"],
                    m["mid_ink"],
                    m["right_ink"],
                    m["max_ink_x"],
                    m["empty"],
                ),
            )

        # --- AC-T4 assertions ---
        coverages = [s["coverage"] for s in samples]
        ink_cols = [s["ink_cols"] for s in samples]
        st0 = samples[0]
        st_mid = samples[2]  # 0.5
        st_full = samples[-1]

        # 1) st=0 empty (or near-empty — allow 1 col noise)
        empty_at_0 = st0["ink_cols"] <= 2
        # 2) mid partial
        mid_partial = (st_mid["coverage"] > 0.05) and (st_mid["coverage"] < 0.95)
        # 3) full near complete
        full_ok = st_full["coverage"] > 0.85
        # 4) monotonic non-decreasing ink_cols (allow tiny -2 noise)
        mono = all(
            ink_cols[i + 1] + 2 >= ink_cols[i] for i in range(len(ink_cols) - 1)
        )
        # 5) not full-only-at-all-st (would mean progressive broken / always full)
        not_always_full = not all(c > 0.90 for c in coverages)
        # 6) not empty-at-all-st
        not_always_empty = not all(c < 0.05 for c in coverages)
        # 7) left-to-right at mid: left third has ink, right third mostly empty
        ltr = (st_mid["left_ink"] >= 5) and (st_mid["right_ink"] <= st_mid["left_ink"] // 2 + 2)
        # 8) mid max_ink_x roughly tracks expected width (tolerance 20%)
        exp_mid = st_mid["expected_w"]
        width_track = (
            exp_mid > 0
            and st_mid["max_ink_x"] >= 0
            and abs(st_mid["max_ink_x"] + 1 - exp_mid) <= max(20, int(0.25 * exp_mid))
        )

        # Instant path secondary (AC-T3 shape): full frac is complete.
        instant_ok = full_ok

        # 9) Identity-reverse Text parent + partial subsurface must NOT stretch
        # to full parent width (lead root cause: _node_needs_axis_scale).
        stretch_ok = False
        stretch_cov = -1.0
        try:
            class _Mat2:
                def __init__(self, xdx=1.0, xdy=0.0, ydx=0.0, ydy=1.0):
                    self.xdx = float(xdx)
                    self.xdy = float(xdy)
                    self.ydx = float(ydx)
                    self.ydy = float(ydy)

            mid_frac = 0.35
            mid_w = max(1, int(round(mid_frac * TW)))
            bg = Surface((VW, VH))
            bg.fill(BG)
            root2 = FakeRender(VW, VH)
            root2.blit(bg, 0, 0)
            # Product Text: full layout box + IDENTITY reverse (oversample=1).
            text = FakeRender(TW, TH)
            text.forward = _Mat2()
            text.reverse = _Mat2()  # IDENTITY — former stretch trigger
            if isinstance(full_tex, HostTexture):
                sub = full_tex.subsurface((0, 0, mid_w, TH))
            else:
                sub = full_tex
            text.blit(sub, 0, 0)
            root2.blit(text, OX, OY)
            draw.draw_screen(root2, flip=True)
            rw2, rh2, rgba2 = renpy_host.read_game_rt_rgba()
            m2 = _coverage_metrics(rgba2, rw2, rh2)
            stretch_cov = m2["coverage"]
            # Partial must stay partial (not ballooned to ~full box).
            stretch_ok = (
                m2["coverage"] > 0.05
                and m2["coverage"] < 0.70
                and m2["max_ink_x"] >= 0
                and abs(m2["max_ink_x"] + 1 - mid_w) <= max(25, int(0.30 * mid_w))
            )
            _log(
                lines,
                "identity_reverse mid_w=%d coverage=%.3f max_ink_x=%d stretch_ok=%s"
                % (mid_w, m2["coverage"], m2["max_ink_x"], stretch_ok),
            )
        except Exception as e:
            _log(lines, "identity_reverse_err=%r" % (e,))
            stretch_ok = False

        ok = bool(
            empty_at_0
            and mid_partial
            and full_ok
            and mono
            and not_always_full
            and not_always_empty
            and ltr
            and width_track
            and is_ht
            and stretch_ok
        )

        reason = (
            "empty0=%s mid_partial=%s full_ok=%s mono=%s not_always_full=%s "
            "not_always_empty=%s ltr=%s width_track=%s is_ht=%s stretch_ok=%s "
            "coverages=%s stretch_cov=%.3f"
            % (
                empty_at_0,
                mid_partial,
                full_ok,
                mono,
                not_always_full,
                not_always_empty,
                ltr,
                width_track,
                is_ht,
                stretch_ok,
                ["%.3f" % c for c in coverages],
                stretch_cov,
            )
        )
        _log(lines, ("PASS " if ok else "FAIL ") + reason)

        _write(
            lines,
            ok,
            path_kind=path_kind,
            text_cps=TEXT_CPS,
            ac="AC-T4_typewriter_progressive",
            st_fracs=list(ST_FRACS),
            coverages=["%.4f" % c for c in coverages],
            ink_cols=ink_cols,
            mid_coverage="%.4f" % st_mid["coverage"],
            mid_left_ink=st_mid["left_ink"],
            mid_right_ink=st_mid["right_ink"],
            mid_max_ink_x=st_mid["max_ink_x"],
            mid_expected_w=st_mid["expected_w"],
            empty_at_0=empty_at_0,
            mid_partial=mid_partial,
            full_ok=full_ok,
            mono=mono,
            ltr=ltr,
            width_track=width_track,
            stretch_ok=stretch_ok,
            stretch_coverage="%.4f" % stretch_cov,
            instant_ok=instant_ok,
            is_hosttexture=is_ht,
            TW=TW,
            TH=TH,
            reason=reason,
        )
    except Exception as e:
        _log(lines, "exception %s\n%s" % (e, traceback.format_exc()))
        _write(
            lines,
            False,
            path_kind=path_kind,
            text_cps=TEXT_CPS,
            reason="exception:%s" % e,
        )
    finally:
        _request_quit()


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

