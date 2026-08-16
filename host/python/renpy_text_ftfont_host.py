"""
Host Pillow-based stand-in for renpy.text.ftfont (Class C / SDL-linked on SDL tree).

Provides enough FTFace/FTFont surface for renpy.text.font import and bitmap glyph
layout. Not a FreeType feature-complete port.
"""

from __future__ import annotations

import io
import os
from typing import Any

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


def init():
    global _initialized
    _initialized = True


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
                    a2 = max(0, int(round(-box[1])))
                    d2 = max(0, int(round(box[3])))
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
                            ascent = max(1, int(round(h * 0.8)))
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
        """Rasterize glyphs onto host Surface via Pillow, then blit pixels."""
        if Image is None or not glyphs:
            return
        w, h = pysurf.get_size()
        if w <= 0 or h <= 0:
            return
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        if len(color) == 3:
            fill = (int(color[0]), int(color[1]), int(color[2]), 255)
        else:
            fill = tuple(int(c) for c in color[:4])
        font = self._font
        # black_color is the outline/shadow color when present (GL2 outline path).
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
        for g in glyphs:
            ch = chr(g.character) if g.character < 0x110000 else "?"
            x = float(xo) + float(g.x) + float(getattr(g, "x_offset", 0))
            y = float(yo) + float(g.y) + float(getattr(g, "y_offset", 0)) - float(g.ascent)
            try:
                if font is not None:
                    # Soft outline without full O(r²) disk of draw.text calls.
                    # 8-neighbor ring for r=1; for r>1 sample ~12 points on the
                    # circle plus cardinals (good enough for prefs CJK labels).
                    if outline_fill is not None and self.outline > 0:
                        r = int(self.outline)
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
                            # Cardinals + diagonals at r, plus mid-angle samples.
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
                            # Approximate circle samples (no math import needed).
                            # 0.707 ≈ cos(45°) for intermediate radius points.
                            m = max(1, int(round(r * 0.707)))
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
                                (x + dx, y + dy), ch, font=font, fill=outline_fill
                            )
                    draw.text((x, y), ch, font=font, fill=fill)
                else:
                    draw.text((x, y), ch, fill=fill)
            except Exception as e:
                # Always swallow for product parity; once-log under UI_TRACE.
                _ui_trace_once(
                    "draw_text_exc",
                    f"draw.text fail ch={ch!r} fill={fill} err={type(e).__name__}:{e}",
                )
            if underline:
                uy = y + float(g.ascent) + self.underline_offset
                draw.line((x, uy, x + g.width, uy), fill=fill, width=self.underline_thickness)
            if strikethrough:
                sy = y + float(g.ascent) * 0.5
                draw.line((x, sy, x + g.width, sy), fill=fill, width=self.underline_thickness)
        # write into surface pixels
        raw = img.tobytes("raw", "RGBA")
        px = getattr(pysurf, "_pixels", None)
        if px is not None and len(px) >= len(raw):
            # alpha composite over existing
            sp = memoryview(px)
            for i in range(0, len(raw), 4):
                sa = raw[i + 3]
                if sa == 0:
                    continue
                if sa == 255:
                    sp[i : i + 4] = raw[i : i + 4]
                else:
                    inv = 255 - sa
                    sp[i] = (raw[i] * sa + sp[i] * inv) // 255
                    sp[i + 1] = (raw[i + 1] * sa + sp[i + 1] * inv) // 255
                    sp[i + 2] = (raw[i + 2] * sa + sp[i + 2] * inv) // 255
                    sp[i + 3] = min(255, sa + (sp[i + 3] * inv) // 255)
        # Phase 1 probe: full-surface non-zero alpha after glyph write.
        if os.environ.get("RENPY_HOST_UI_TRACE") == "1" and "alpha_zero" not in _UI_TRACE_LOGGED:
            try:
                # Prefer post-composite surface pixels when available.
                scan = bytes(px) if px is not None else raw
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
