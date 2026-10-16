"""
Host Pillow-based stand-in for renpy.text.ftfont (Class C / SDL-linked on SDL tree).

Provides enough FTFace/FTFont surface for renpy.text.font import and bitmap glyph
layout. Not a FreeType feature-complete port.
"""

from __future__ import annotations

import io
import os
import time as _time

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFont = None  # type: ignore


_initialized = False

# Env-gated once-log keys for RENPY_HOST_UI_TRACE=1 (Phase 1 evidence matrix).
_UI_TRACE_LOGGED: set[str] = set()


def _ui_trace_once(key: str, msg: str) -> None:
    """Once-log under RENPY_HOST_UI_TRACE=1; keys fixed by plan (no spam)."""
    if os.environ.get("RENPY_HOST_UI_TRACE") != "1":
        return
    if key in _UI_TRACE_LOGGED:
        return
    _UI_TRACE_LOGGED.add(key)
    print(f"[UI_TRACE {key}] {msg}", flush=True)


# ============================================================================
# Glyph skin cache (project O1, default OFF).
#
# Left in USS is the Pillow ``draw.text`` C-side rasterizer for each glyph —
# even after the tight-crop change (commit 8bf89af92) it still owns ~6.4ms of
# the 19.3ms/frame settings-page python budget, mostly re-rasterizing the same
# (font, character, ink-color, outline params, subpixel phase) tuples every
# frame (hover/typewriter/UI labels share the same shapes frame after frame).
#
# The cache keys on the *quantized subpixel phase* (Pillow's freetype
# getmask2 ``start`` fraction grid / 256) so a hit draws the identical
# rasterization Pillow would produce live; the skin is stamped with
# ``Image.paste`` which uses the same per-channel linear blend math as
# draw.text's draw_bitmap. Bit-equality is verified by gate Q-AB
# (RENPY_HOST_TEXT_GLYPH_SKIN=1 runs are compared to =0 runs on the text
# heavy settings pages) before the default may flip to ON.
#
# Fallbacks: sized conservatively (asc+desc facing box), ring-pass kept inside
# the skin so ring-vs-silhouette z-order stays as drawn live; underline /
# strikethrough lines stay out of the cache (they depend on glyph width lines).
# ============================================================================
_GLYPH_SKIN_CACHE: dict[tuple, tuple] = {}
_GLYPH_SKIN_CAP = 2048
_GLYPH_SKIN_ENV: int | None = None
_GLYPH_SKIN_STATS: dict[str, int] = {"stamp": 0, "hit": 0, "miss": 0, "live": 0}
_GLYPH_SKIN_STATS_LAST_T: list[float] = []


def _glyph_skin_stats_tick():
    """10s cadence skin hit/miss telemetry (PHASE0 opt-in only)."""
    if not _glyph_skin_enabled():
        return
    if os.environ.get("RENPY_HOST_PHASE0_SIGNALS") != "1":
        return
    try:
        now = _time_now()
    except Exception:  # noqa: BLE001
        return
    if not _GLYPH_SKIN_STATS_LAST_T:
        _GLYPH_SKIN_STATS_LAST_T.append(now)
        return
    if (now - _GLYPH_SKIN_STATS_LAST_T[0]) < 10.0:
        return
    s = _GLYPH_SKIN_STATS
    total = s["stamp"]
    try:
        print(
            f"[GLYPHSKIN] t={now - _GLYPH_SKIN_STATS_LAST_T[0]:.1f} "
            f"stamp={total} hit={s['hit']} miss={s['miss']} live={s['live']} "
            f"cache={len(_GLYPH_SKIN_CACHE)}",
            flush=True,
        )
    except Exception:  # noqa: BLE001
        return
    for k in ("stamp", "hit", "miss", "live"):
        s[k] = 0
    _GLYPH_SKIN_STATS_LAST_T[0] = now


def _glyph_skin_enabled() -> bool:
    """Default ON (proven bit-equal by the golden A/B in c2892491e/0b397c7a7).

    ``RENPY_HOST_TEXT_GLYPH_SKIN=0`` rolls back to the live draw.text path
    for measurement A/B; the value is parsed once per process.
    """
    global _GLYPH_SKIN_ENV
    if _GLYPH_SKIN_ENV is None:
        raw = os.environ.get("RENPY_HOST_TEXT_GLYPH_SKIN")
        if raw is None:
            _GLYPH_SKIN_ENV = 1
        else:
            try:
                _GLYPH_SKIN_ENV = 0 if str(raw).strip().lower() in ("0", "false", "no", "off") else 1
            except Exception:  # noqa: BLE001 — malformed env must not abort frames
                _GLYPH_SKIN_ENV = 1
    return _GLYPH_SKIN_ENV == 1


def _phase_q(f: float) -> float:
    """Key granularity for the glyph-skin cache.

    Pillow passes the raw fraction into ``getmask2 start`` and FreeType's
    rasterization resolves it at C-level subpixel precision finer than
    26.6 (empirical A/B below showed ±1-px stroke differences between
    adjacent /64 phases on latin text). Therefore the cache key carries the
    *exact* fraction of the live coordinate (float identity), never a
    quantized bucket: layout positions are deterministic per frame, so the
    same glyph draws at exactly the same phase across frames and the cache
    hits stay bit-equal (this keeps the A/B evidence from 8bf89a.. intact).
    """
    try:
        frac = f - float(int(f))
    except Exception:  # noqa: BLE001
        return 0.0
    return float(frac)


def _glyph_skin(font_key, font, ch, fill, outline_fill, r, fx, fy, asc, desc, size, vertical):
    """Rasterize (and cache) one glyph incl. the outline ring; return
    (skin_img, pad_x, pad_y) or None when Pillow is unavailable or the
    live draw must be used instead.

    The live-fallback rule matters for **signed fractional phases**: PIL's
    ``ImageDraw.text`` computes ``start = math.modf(xy)``; for negative xy
    that fraction is negative (modf(-7.3) = -0.3), which a positive pad
    ``pad + frac`` can never reproduce (modf(3.7) = 0.7) and freetype
    rasterizes the ±phase differently (measured ±1px stroke shift).
    Negative-phase / non-negative-only placements therefore take the live
    ``draw.text`` path, which is always byte-identical to the pre-cache
    behavior.
    """
    if font is None:
        return None
    if fx < 0.0 or fy < 0.0:
        # signed phase: live draw.text only (see docstring)
        return None
    key = (
        font_key,
        ch,
        fill,
        outline_fill,
        r,
        _phase_q(fx),
        _phase_q(fy),
    )
    hit = _GLYPH_SKIN_CACHE.get(key)
    if hit is not None:
        return hit
    pad = r + 2 + int(size / 16) + 1
    try:
        sw = max(2, int(float(font.getlength(ch))) + 2 * pad)
    except Exception:  # noqa: BLE001
        sw = max(2, int(size * 0.8) + 2 * pad)
    if vertical:
        sh = int(size) * 2 + 2 * pad
    else:
        sh = int(asc + desc) + 2 * pad
    if sw <= 2 * pad or sh <= 2 * pad:
        return None
    try:
        skin = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        sd = ImageDraw.Draw(skin)
        if outline_fill is not None and r > 0:
            if r <= 1:
                offsets = (
                    (-1, 0), (1, 0), (0, -1), (0, 1),
                    (-1, -1), (-1, 1), (1, -1), (1, 1),
                )
            else:
                offsets = [
                    (-r, 0), (r, 0), (0, -r), (0, r),
                    (-r, -r), (-r, r), (r, -r), (r, r),
                ]
                m = max(1, round(r * 0.707))
                offsets.extend(
                    [
                        (-m, -r), (m, -r), (-m, r), (m, r),
                        (-r, -m), (-r, m), (r, -m), (r, m),
                    ]
                )
            for dx, dy in offsets:
                sd.text((pad + fx + dx, pad + fy + dy), ch, font=font, fill=outline_fill)
        sd.text((pad + fx, pad + fy), ch, font=font, fill=fill)
        if _GLYPH_SKIN_CACHE and len(_GLYPH_SKIN_CACHE) >= _GLYPH_SKIN_CAP:
            _GLYPH_SKIN_CACHE.clear()
        _GLYPH_SKIN_CACHE[key] = (skin, pad, pad)
        return _GLYPH_SKIN_CACHE[key]
    except Exception:  # noqa: BLE001  (draw fail -> live path)
        return None




def init():
    global _initialized
    _initialized = True
def _time_now() -> float:
    return _time.monotonic()



class FreetypeError(Exception):
    pass


class FTFace:
    def __init__(self, f, index=0, fn=""):
        self.fn = fn
        self.index = int(index)
        if hasattr(f, "read"):
            data = f.read()
            try:
                f.seek(0)
            except Exception:
                pass
        elif isinstance(f, (bytes, bytearray)):
            data = bytes(f)
        else:
            with open(f, "rb") as fh:
                data = fh.read()
        self._data = data
        self._pil = None
        if ImageFont is not None:
            try:
                self._pil = ImageFont.truetype(io.BytesIO(data), size=32)
            except Exception:
                try:
                    self._pil = ImageFont.load_default()
                    _ui_trace_once(
                        "face_fallback",
                        f"FTFace truetype fail → load_default fn={fn!r}",
                    )
                except Exception:
                    self._pil = None


class FTFont:
    def __init__(self, face, size, bold, italic, outline, antialias, vertical, hinting):
        self.face = face
        self.size = max(1, int(size))
        self.bold = bold
        self.italic = italic
        # GL2 ftfont: outline is pixel expand radius; expand = outline * 2 for metrics.
        try:
            self.outline = int(outline or 0)
        except Exception:
            self.outline = 0
        self.expand = max(0, self.outline * 2)
        self.antialias = antialias
        self.vertical = vertical
        self.hinting = hinting
        self._font = None
        if ImageFont is not None:
            try:
                if getattr(face, "_data", None):
                    self._font = ImageFont.truetype(io.BytesIO(face._data), size=self.size)
                else:
                    self._font = ImageFont.load_default()
                    _ui_trace_once(
                        "face_fallback",
                        f"FTFont no face._data → load_default size={self.size} fn={getattr(face, 'fn', '')!r}",
                    )
            except Exception as e:
                try:
                    self._font = ImageFont.load_default()
                    _ui_trace_once(
                        "face_fallback",
                        f"FTFont truetype fail → load_default size={self.size} err={type(e).__name__}:{e}",
                    )
                except Exception:
                    self._font = None
        # Real font metrics (fake 0.8/0.2 clipped descenders on y/g/p).
        self.ascent, self.descent = self._compute_metrics()
        # GL2 ftfont.pyx: expand = outline*2; ascent += expand; descent grows by
        # expand (host keeps positive descent). Height grows by 2*expand = 4*outline.
        self._apply_outline_metrics()
        self.underline_offset = int(self.size * 0.1)
        self.underline_thickness = max(1, self.size // 16)
        # Glyph-skin cache key identity (stable string, never a recycled id()).
        try:
            self.skin_font_key = (
                getattr(face, "fn", "") or "",
                int(getattr(face, "index", 0) or 0),
                int(size),
                int(bool(bold)),
                int(bool(italic)),
                int(self.outline),
                int(bool(antialias)),
                int(bool(vertical)),
            )
        except Exception:  # noqa: BLE001 — identity fallback avoids blocking product
            self.skin_font_key = (getattr(face, "fn", "") or "", int(size), "fallback")

    def _apply_outline_metrics(self):
        """Expand ascent/descent/height after raw metrics (or VF recompute)."""
        if self.expand > 0:
            self.ascent = int(self.ascent + self.expand)
            self.descent = int(self.descent + self.expand)
        self.height = int(self.ascent + self.descent)
        self.lineskip = max(self.height, self.ascent + self.descent)

    def _compute_metrics(self):
        """Return (ascent, descent) in positive pixel units from the loaded face."""
        ascent = descent = None
        font = self._font
        if font is not None:
            try:
                if hasattr(font, "getmetrics"):
                    am, dm = font.getmetrics()
                    ascent = int(am)
                    descent = int(abs(dm))
            except Exception:
                pass
            # Measure probe glyphs so tall descenders expand beyond getmetrics.
            try:
                box = None
                try:
                    box = font.getbbox("Ay|gpj", anchor="ls")
                except (TypeError, ValueError):
                    box = None
                if box is not None:
                    # baseline-relative: top <= 0, bottom >= 0 for latin
                    a2 = max(0, round(-box[1]))
                    d2 = max(0, round(box[3]))
                    if ascent is None or ascent <= 0:
                        ascent = a2
                    else:
                        ascent = max(ascent, a2)
                    if descent is None:
                        descent = d2
                    else:
                        descent = max(descent, d2)
                elif ascent is None or descent is None:
                    box = font.getbbox("Ay|gpj")
                    if box is not None:
                        h = max(1, int(box[3] - box[1]))
                        if ascent is None or ascent <= 0:
                            ascent = max(1, round(h * 0.8))
                        if descent is None:
                            descent = max(0, h - int(ascent))
            except Exception:
                pass
        if ascent is None or ascent <= 0:
            ascent = max(1, int(self.size * 0.8))
        if descent is None or descent < 0:
            descent = max(0, int(self.size * 0.2))
        return int(ascent), int(descent)

    def glyphs(self, s: str, level: int = 0):
        from renpy.text.textsupport import Glyph

        out = []
        x = 0.0
        for ch in s:
            g = Glyph()
            g.character = ord(ch)
            g.glyph = ord(ch)
            # rough advance via Pillow
            adv = float(self.size) * 0.6
            if self._font is not None:
                try:
                    if hasattr(self._font, "getlength"):
                        adv = float(self._font.getlength(ch))
                    else:
                        box = self._font.getbbox(ch)
                        adv = float(box[2] - box[0]) if box else adv
                except Exception:
                    pass
            # GL2: g.advance includes expand so outlined runs don't crowd.
            exp = float(self.expand)
            g.advance = adv + exp
            g.width = adv + exp
            g.ascent = float(self.ascent)
            g.descent = float(-self.descent)
            g.line_spacing = float(self.lineskip)
            g.x = x
            g.y = 0.0
            out.append(g)
            x += g.advance
        return out

    def bounds(self, glyphs, bounds):
        # bounds is (x, y, w, h) mutable list/tuple handling like C version
        minx = miny = 1e9
        maxx = maxy = -1e9
        for g in glyphs:
            x0 = g.x + getattr(g, "x_offset", 0)
            y0 = g.y + getattr(g, "y_offset", 0) - g.ascent
            x1 = x0 + g.width
            y1 = y0 + g.ascent - g.descent
            minx = min(minx, x0)
            miny = min(miny, y0)
            maxx = max(maxx, x1)
            maxy = max(maxy, y1)
        if minx > maxx:
            return bounds
        # Expand bounds list in place if possible
        try:
            bx, by, bw, bh = bounds
            nx0 = min(bx, minx)
            ny0 = min(by, miny)
            nx1 = max(bx + bw, maxx)
            ny1 = max(by + bh, maxy)
            bounds[0] = nx0
            bounds[1] = ny0
            bounds[2] = nx1 - nx0
            bounds[3] = ny1 - ny0
        except Exception:
            pass
        return bounds

    def draw(self, pysurf, xo, yo, color, glyphs, underline, strikethrough, black_color):
        """Rasterize glyphs onto host Surface via Pillow, then blit pixels.

        Perf notes (HuangmeiC settings-page: ~40% of frame python budget):
        the old path allocated a full-surface RGBA image and alpha-composited
        it back over every pixel in a pure-Python ``range`` loop. Now:
        - only a tight crop around the glyphs is rasterized (fractional
          subpixel offsets are preserved relative to the crop origin, so
          Pillow's rasterization stays byte-identical);
        - the alpha composite touches only the crop region and uses the very
          same integer formula, vectorized with numpy when available (bit
          same), else the original pure-Python loop on the crop only.
        """
        if Image is None or not glyphs:
            return
        w, h = pysurf.get_size()
        if w <= 0 or h <= 0:
            return
        # fill selection identical to the legacy path
        if len(color) == 3:
            fill = (int(color[0]), int(color[1]), int(color[2]), 255)
        else:
            fill = tuple(int(c) for c in color[:4])
        font = self._font
        outline_fill = None
        if self.outline > 0:
            bc = black_color
            try:
                if bc is not None and len(bc) >= 3:
                    if len(bc) == 3:
                        outline_fill = (int(bc[0]), int(bc[1]), int(bc[2]), 255)
                    else:
                        outline_fill = tuple(int(c) for c in bc[:4])
            except Exception:
                outline_fill = None
            if outline_fill is None:
                outline_fill = (0, 0, 0, 255)
        r = int(self.outline) if (outline_fill is not None and self.outline > 0) else 0
        # ---- 1. glyph bounding box (integer surface coords, clamped) ----
        minx = miny = 1e18
        maxx = maxy = -1e18
        for g in glyphs:
            x = float(xo) + float(g.x) + float(getattr(g, "x_offset", 0))
            y = float(yo) + float(g.y) + float(getattr(g, "y_offset", 0)) - float(g.ascent)
            gw = float(getattr(g, "width", 0.0))
            gh = float(g.ascent) - float(g.descent)
            x0 = min(x - r, x)
            y0 = min(y - r, y)
            x1 = max(x + gw + r, x + 1.0)
            y1 = max(y + gh + r, y + 1.0)
            minx = min(minx, x0); miny = min(miny, y0)
            maxx = max(maxx, x1); maxy = max(maxy, y1)
        # strikethrough/underline only tie into the same bbox (per glyph)
        if (underline or strikethrough) and maxx < 1e17:
            maxx += 2
        ix0 = max(0, int(minx) - 1)
        iy0 = max(0, int(miny) - 1)
        ix1 = min(w, int(maxx) + 1)
        iy1 = min(h, int(maxy) + 1)
        if ix1 <= ix0 or iy1 <= iy0:
            return
        _glyph_skin_stats_tick()
        cw, chh = ix1 - ix0, iy1 - iy0
        # ---- 2. crop overlay (same draw.text calls, crop-relative origin) ----
        img = Image.new("RGBA", (cw, chh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        ox_ofs = float(ix0)  # crop origin: fractional coords stay intact
        oy_ofs = float(iy0)
        for g in glyphs:
            c = chr(g.character) if g.character < 0x110000 else "?"
            x = float(xo) + float(g.x) + float(getattr(g, "x_offset", 0)) - ox_ofs
            y = float(yo) + float(g.y) + float(getattr(g, "y_offset", 0)) - float(g.ascent) - oy_ofs
            try:
                xint = float(int(x))
                yint = float(int(y))
                fx = x - xint
                fy = y - yint
                skin = None
                if _glyph_skin_enabled():
                    skin = _glyph_skin(
                        getattr(self, "skin_font_key", None),
                        font,
                        c,
                        fill,
                        outline_fill,
                        r,
                        fx,
                        fy,
                        self.ascent,
                        self.descent,
                        self.size,
                        self.vertical,
                    )
                    st = _GLYPH_SKIN_STATS
                    st["stamp"] += 1
                    if skin is not None:
                        st["hit"] += 1
                    else:
                        st["miss"] += 1
                        st["live"] += 1
                if skin is not None:
                    skin_img, spad_x, spad_y = skin
                    dst_x = int(xint) - spad_x
                    dst_y = int(yint) - spad_y
                    # Deliberately conservative overflow guard: 
                    # (dst + 2*pad + crop) > surface means "any chance the
                    # stamp could touch the right/bottom edge", and edge-touch
                    # placements fall back to live draw.text so we never rely
                    # on PIL paste's crop semantics at all. The condition is
                    # intentionally stricter than the true skin-bounds test
                    # (dst + skin_w/h > w/h) — do NOT "simplify" it into the
                    # tight shape: that would hand edge clipping back to
                    # paste, which is unverified against draw.text's fired
                    # surface-clamped write.
                    if dst_x < 0 or dst_y < 0 or dst_x + spad_x * 2 + cw > w or dst_y + spad_y * 2 + chh > h:
                        # Pastes near/over the surface edge must clip exactly
                        # like live draw.text's surface-clamped write; take the
                        # live path there (paste's crop semantics on negative /
                        # overflowed anchors are not provably identical).
                        skin = None
                if skin is not None:
                    skin_img, spad_x, spad_y = skin
                    img.paste(
                        skin_img,
                        (int(xint) - spad_x, int(yint) - spad_y),
                        skin_img,
                    )
                elif font is not None:
                    if outline_fill is not None and r > 0:
                        if r <= 1:
                            offsets = (
                                (-1, 0),
                                (1, 0),
                                (0, -1),
                                (0, 1),
                                (-1, -1),
                                (-1, 1),
                                (1, -1),
                                (1, 1),
                            )
                        else:
                            offsets = [
                                (-r, 0),
                                (r, 0),
                                (0, -r),
                                (0, r),
                                (-r, -r),
                                (-r, r),
                                (r, -r),
                                (r, r),
                            ]
                            m = max(1, round(r * 0.707))
                            offsets.extend(
                                [
                                    (-m, -r),
                                    (m, -r),
                                    (-m, r),
                                    (m, r),
                                    (-r, -m),
                                    (-r, m),
                                    (r, -m),
                                    (r, m),
                                ]
                            )
                        for dx, dy in offsets:
                            draw.text(
                                (x + dx, y + dy), c, font=font, fill=outline_fill
                            )
                    draw.text((x, y), c, font=font, fill=fill)
                else:
                    draw.text((x, y), c, fill=fill)
            except Exception as e:
                _ui_trace_once(
                    "draw_text_exc",
                    f"draw.text fail ch={c!r} fill={fill} err={type(e).__name__}:{e}",
                )
            if underline:
                uy = y + float(g.ascent) + self.underline_offset
                draw.line((x, uy, x + g.width, uy), fill=fill, width=self.underline_thickness)
            if strikethrough:
                sy = y + float(g.ascent) * 0.5
                draw.line((x, sy, x + g.width, sy), fill=fill, width=self.underline_thickness)
        # ---- 3. composite the crop back (only crop region) ----
        raw = img.tobytes("raw", "RGBA")
        px = getattr(pysurf, "_pixels", None)
        if px is None:
            return
        nbytes = len(px)
        if nbytes < 4 * w * h:
            return  # malformed surface: bail like legacy loop would
        try:
            import numpy as _np  # type: ignore

            a = _np.frombuffer(px, dtype=_np.uint8)
            if len(a) < nbytes:
                raise MemoryError
            # crop composite (same integer formula as legacy loop)
            row_off = iy0
            # stride per pixel-row
            stride = 4 * w
            flat_crop = _np.frombuffer(raw, dtype=_np.uint8).reshape(chh, cw, 4)
            base = a.reshape(h, stride)[row_off : row_off + chh]
            sub = base.reshape(chh, stride)[
                :, 4 * ix0 : 4 * ix1
            ].reshape(chh, cw, 4)  # alias view over pysurf._pixels crop region

            src = flat_crop[...]
            sa = src[..., 3].astype(_np.uint16)
            inv = (255 - sa).astype(_np.uint16)
            # mask of pixels where any work is needed (legacy: sa==0 -> no-op)
            skip = sa == 0
            full = sa == 255
            mix = ~(skip) & ~(full)
            # full-overwrite pixels
            sub[full] = src[full]
            sect = mix
            aux_r = src[..., 0][sect].astype(_np.uint16)
            aux_g = src[..., 1][sect].astype(_np.uint16)
            aux_b = src[..., 2][sect].astype(_np.uint16)
            aux_a = src[..., 3][sect].astype(_np.uint16)
            dst = sub[sect].astype(_np.uint16)
            out_r = (aux_r * sa[sect] + dst[..., 0] * inv[sect]) // 255
            out_g = (aux_g * sa[sect] + dst[..., 1] * inv[sect]) // 255
            out_b = (aux_b * sa[sect] + dst[..., 2] * inv[sect]) // 255
            out_a = _np.minimum(
                255, aux_a + (dst[..., 3] * inv[sect]) // 255
            )
            sub[sect] = _np.stack([out_r, out_g, out_b, out_a], axis=-1).astype(_np.uint8)
        except Exception:  # numpy unavailable / shape mismatch: pure-Python crop
            sp = memoryview(px)
            row_stride = 4 * w
            for yrow in range(chh):
                row_base = 4 * ((iy0 + yrow) * w + ix0)
                crop_base = 4 * yrow * cw
                for xp in range(cw):
                    i = row_base + 4 * xp
                    j = crop_base + 4 * xp
                    sa = raw[j + 3]
                    if sa == 0:
                        continue
                    if sa == 255:
                        sp[i : i + 4] = raw[j : j + 4]
                    else:
                        inv = 255 - sa
                        sp[i] = (raw[j] * sa + sp[i] * inv) // 255
                        sp[i + 1] = (raw[j + 1] * sa + sp[i + 1] * inv) // 255
                        sp[i + 2] = (raw[j + 2] * sa + sp[i + 2] * inv) // 255
                        sp[i + 3] = min(255, sa + (sp[i + 3] * inv) // 255)
        # Phase 1 probe: full-surface non-zero alpha after glyph write.
        if os.environ.get("RENPY_HOST_UI_TRACE") == "1" and "alpha_zero" not in _UI_TRACE_LOGGED:
            try:
                scan = bytes(memoryview(px))
                nonzero = 0
                for i in range(3, len(scan), 4):
                    if scan[i]:
                        nonzero += 1
                        if nonzero > 0:
                            break
                if nonzero == 0:
                    _ui_trace_once(
                        "alpha_zero",
                        f"FTFont.draw all-zero alpha size=({w},{h}) glyphs={len(glyphs)} "
                        f"fill={fill} face={getattr(getattr(self, 'face', None), 'fn', '')!r}",
                    )
            except Exception:
                pass
