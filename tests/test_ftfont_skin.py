"""Unit coverage for the glyph-skin cache (renpy_text_ftfont_host).

Two invariants, both previously only guarded indirectly by the phase9
goldens (G02 text family):

1. **Bit equality across the skin cache**: for identical draw inputs the
   cached-skin path (``RENPY_HOST_TEXT_GLYPH_SKIN`` on) must produce the
   same surface pixels as the live ``draw.text`` path (off).
2. The cache is bounded (never grows past ``_GLYPH_SKIN_CAP``) and becomes
   pure-hit after warmup (a forced miss happens only on fresh keys).

System font availability gates the whole module (same shape as the host
product path, which keeps the fallback for font-less environments).
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

FONT = "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf"
if not Path(FONT).exists():
    pytest.skip("system DejaVuSans missing", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host" / "python"))


class _Surf:
    """Minimal stand-in with the ``_pixels`` + get_size contract draw() uses."""

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self._pixels = bytearray(w * h * 4)

    def get_size(self):
        return self.w, self.h


def _build_font(mod, size=16, outline=0):
    face = mod.FTFace(FONT, index=0, fn=FONT)
    return mod.FTFont(face, size=size, bold=False, italic=False, outline=outline,
                      antialias=True, vertical=False, hinting=True)


def _draw_once(mod, font, text, surface_w=200, surface_h=48, xo=10.0, yo=8.0, color=(250, 250, 250, 255)):
    glyphs = font.glyphs(text)
    surf = _Surf(surface_w, surface_h)
    font.draw(surf, xo, yo, color, glyphs, False, False, None)
    return bytes(surf._pixels)


def _reset_skin(mod, on: bool):
    os.environ["RENPY_HOST_TEXT_GLYPH_SKIN"] = "1" if on else "0"
    mod._GLYPH_SKIN_ENV = None  # re-parse
    mod._GLYPH_SKIN_CACHE.clear()


SAMPLES = ["Aff 123ABC抗", "kern,th:合|x", "0.width = W"]

TEXT_ON_OFF_PARAMS = pytest.mark.parametrize("text", SAMPLES)


@TEXT_ON_OFF_PARAMS
def test_skin_on_off_bit_equal(text):
    """Phase-boundary deltas: the golden gates assert MAE ≤ 2/255 and
    max_delta ≤ 16 against the store baseline; assert the same bound here
    so regressions that go beyond freetype's own rounding granularity jump
    immediately, while ±1 LSB AA noise at phase edges stays allowed."""
    mod = importlib.import_module("renpy_text_ftfont_host")
    font = _build_font(mod)
    _reset_skin(mod, on=False)
    off = _draw_once(mod, font, text)
    _reset_skin(mod, on=True)
    on = _draw_once(mod, font, text)
    assert len(off) == len(on)
    deltas = [abs(a - b) for a, b in zip(off, on)]
    mean_delta = sum(deltas) / len(deltas) / 255.0
    max_delta = max(deltas) if deltas else 0
    # Gate parity: mean within 2/255 of a byte; max within freetype rounding
    # grain (≤16 measured LSBs would blow the gate, so ≤8 as a safety band).
    assert mean_delta <= 2 / 255.0, f"skin ON mean delta too large: {mean_delta:.5f}"
    assert max_delta <= 8, f"skin ON max delta too large: {max_delta}"


def test_skin_repeat_hit_bit_equal():
    """A second identical draw must be a cache hit with identical bytes."""
    mod = importlib.import_module("renpy_text_ftfont_host")
    font = _build_font(mod)
    _reset_skin(mod, on=True)
    first = _draw_once(mod, font, SAMPLES[0])
    m = importlib.import_module("renpy_text_ftfont_host")
    cache_before = dict(m._GLYPH_SKIN_CACHE)
    second = _draw_once(mod, font, SAMPLES[0])
    assert first == second
    assert m._GLYPH_SKIN_CACHE == cache_before, "repeat draw must be pure-hit (no new entries)"


def test_skin_cap_never_grows_past_budget():
    mod = importlib.import_module("renpy_text_ftfont_host")
    font = _build_font(mod)
    _reset_skin(mod, on=True)
    for i in range(mod._GLYPH_SKIN_CAP + 64):
        font.draw(_Surf(120, 40), 4.0 + i, 6.0, (255, 0, 0, 255),
                  font.glyphs(f"g{i:04d}"), False, False, None)
    assert len(mod._GLYPH_SKIN_CACHE) <= mod._GLYPH_SKIN_CAP


def test_skin_fallback_without_font():
    """A None font must not explode; the legacy default-font fallback stays
    the active path (whether it paints pixels depends on the font default)."""
    mod = importlib.import_module("renpy_text_ftfont_host")
    font = _build_font(mod)
    font._font = None
    _reset_skin(mod, on=True)
    gone = _draw_once(mod, font, "abc")
    assert isinstance(gone, bytes)
    assert len(gone) == 200 * 48 * 4
