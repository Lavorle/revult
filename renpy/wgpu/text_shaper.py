"""
HarfBuzz bridge — shape text into positioned glyphs.

Tries uharfbuzz (HB) first; falls back to Pillow RAQM per-codepoint
when HB is missing or shaping fails, keeping behavior compatible and
not breaking old API (render_text_rgba still works).

Spec: try import uharfbuzz as hb HAS_HB flag,
      def shape(text, font_path, size, features=None) -> list[GlyphPos]
      (glyph_id/offset), Arabic/Devanagari/Thai features if missing HB then
      Pillow RAQM per-codepoint bbox→single-glyph list fallback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:  # HarfBuzz via uharfbuzz (optional — no cargo hard dep)
    import uharfbuzz as hb  # type: ignore

    HAS_HB: bool = True
except Exception:  # ImportError or load error in hermetic gates
    hb = None  # type: ignore
    HAS_HB = False


@dataclass(frozen=False)
class GlyphPos:
    """Positioned glyph from HarfBuzz or Pillow fallback."""

    glyph_id: int
    x_offset: float = 0.0
    y_offset: float = 0.0
    x_advance: float = 0.0
    y_advance: float = 0.0
    cluster: int = 0


# Language-specific default features that stress HB shaping.
# Arabic (ar), Devanagari (deva), Thai — ensure fallback still covers them.
_COMPLEX_FEATURES = {
    "arab": ["ccmp", "init", "medi", "fina", "rlig", "calt", "liga"],
    "deva": ["nukt", "akhn", "rphf", "blwf", "half", "pstf", "vatu", "cjct"],
    "thai": ["ccmp", "liga", "calt"],
}


def _fallback_shape(text: str, font_path: str | None, size: int) -> list[GlyphPos]:
    """Pillow RAQM per-codepoint fallback — single glyph per cluster."""
    # Try to load Pillow font for metrics; else uniform advance.
    font = None
    if font_path and os.path.exists(font_path):
        try:
            from PIL import ImageFont  # type: ignore

            try:
                font = ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.RAQM)
            except TypeError:
                font = ImageFont.truetype(font_path, size)
        except Exception:
            font = None
    else:
        # Try default chain without path.
        try:
            from PIL import ImageFont  # type: ignore

            font = ImageFont.load_default()
        except Exception:
            font = None

    out: list[GlyphPos] = []
    pen_x = 0.0
    for idx, ch in enumerate(text):
        # glyph_id = codepoint in fallback (preserves identity for atlas key)
        gid = ord(ch)
        adv = float(size) * 0.6
        if font is not None:
            try:
                if hasattr(font, "getlength"):
                    adv = float(font.getlength(ch))
                else:
                    box = font.getbbox(ch)
                    if box is not None:
                        adv = float(box[2] - box[0])
            except Exception:
                pass
            # per-codepoint bbox for x_offset/y_offset if needed
            # Use RAQM anchor ls? Keep zero offset for simple LTR.
        out.append(GlyphPos(glyph_id=gid, x_offset=0.0, y_offset=0.0, x_advance=adv, y_advance=0.0, cluster=idx))
        pen_x += adv
    return out


def shape(
    text: str,
    font_path: str | None,
    size: int,
    features: dict | None = None,
) -> list[GlyphPos]:
    """
    Shape ``text`` with HarfBuzz if available, else Pillow fallback.

    Returns list[GlyphPos] with glyph_id + offsets/advances.
    ``features`` may be dict of HB feature overrides; for Arabic/Devanagari/
    Thai the caller may pass script tag or let HB guess.
    """
    if not text:
        return []

    # Resolve features: merge complex-script defaults when HB present.
    feat_list: list[str] | None = None
    if features is not None:
        # Accept {"arab": True} or {"features": ["liga=0"]} style.
        if isinstance(features, dict):
            # flatten {"liga": 1, "calt": 0} -> ["liga", "calt=0"]
            feat_list = []
            for k, v in features.items():
                if k in _COMPLEX_FEATURES:
                    # script shorthand — expand to its features
                    if v:
                        feat_list.extend(_COMPLEX_FEATURES[k])
                    continue
                if isinstance(v, bool):
                    feat_list.append(f"{k}=1" if v else f"{k}=0")
                elif isinstance(v, int):
                    feat_list.append(f"{k}={v}")
                else:
                    feat_list.append(str(k))
        elif isinstance(features, (list, tuple)):
            feat_list = [str(x) for x in features]

    if HAS_HB and hb is not None and font_path and os.path.exists(font_path):
        try:
            # Load font bytes for HB.
            with open(font_path, "rb") as f:
                font_data = f.read()
            face = hb.Face(font_data)  # type: ignore
            font = hb.Font(face)  # type: ignore
            # Set scale to font pixel size (HB font units -> pixels via set_scale)
            # face.upem is unitsPerEm; we map size px to that.
            upem = face.upem if getattr(face, "upem", 0) else 1000
            # Keep HB scale proportional to requested size.
            font.scale = (size * 64, size * 64)
            # Some builds need explicit ot var? skip.
            buf = hb.Buffer()  # type: ignore
            buf.add_str(text)
            buf.guess_segment_properties()
            # Apply features if caller supplied; HB defaults otherwise handle
            # Arabic/Devanagari/Thai correctly without extra flags.
            hb_features = {}
            if feat_list:
                hb_features["features"] = {f.split("=")[0]: int(f.split("=")[1]) if "=" in f else 1 for f in feat_list}
                # Older uharfbuzz uses dict; newer shape kw is features dict.
                try:
                    hb.shape(font, buf, hb_features)  # type: ignore
                except TypeError:
                    # fallback to plain shape without features
                    hb.shape(font, buf)  # type: ignore
            else:
                hb.shape(font, buf)  # type: ignore
            infos = buf.glyph_infos
            positions = buf.glyph_positions
            out: list[GlyphPos] = []
            for info, pos in zip(infos, positions):
                gid = int(info.codepoint)
                xa = float(pos.x_advance) / 64.0
                ya = float(pos.y_advance) / 64.0
                xo = float(pos.x_offset) / 64.0
                yo = float(pos.y_offset) / 64.0
                out.append(GlyphPos(glyph_id=gid, x_offset=xo, y_offset=yo, x_advance=xa, y_advance=ya, cluster=int(info.cluster)))
            if out:
                return out
        except Exception:
            # Any HB failure → fallback path (keeps frame alive).
            pass

    # Fallback: Pillow RAQM per-codepoint bbox → single-glyph list.
    return _fallback_shape(text, font_path, size)


__all__ = ["HAS_HB", "GlyphPos", "shape"]
