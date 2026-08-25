"""
Gate: text_descender — prove y/g/p descender pixels survive FTFont.draw.

Gate name: text_descender
Runnable standalone (no renpy-host binary / renpy bootstrap required).

Renders "ygp" via host Pillow FTFont onto a software Surface and asserts
non-zero alpha in rows below the baseline (ascent line).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

# Allow `python3 host/python/gates/text_descender.py` from repo root.
_HERE = Path(__file__).resolve().parent
_HOST_PY = _HERE.parent
if str(_HOST_PY) not in sys.path:
    sys.path.insert(0, str(_HOST_PY))

# Load Surface without importing host_pygame package (it pulls renpy_host).
import importlib.util

_surf_spec = importlib.util.spec_from_file_location(
    "host_pygame_surface_gate",
    _HOST_PY / "host_pygame" / "surface.py",
)
_surf_mod = importlib.util.module_from_spec(_surf_spec)
assert _surf_spec and _surf_spec.loader
_surf_spec.loader.exec_module(_surf_mod)
Surface = _surf_mod.Surface  # type: ignore

from renpy_text_ftfont_host import FTFace, FTFont
from renpy_text_ftfont_host import init as ft_init


class _Glyph:
    """Minimal stand-in for renpy.text.textsupport.Glyph (no renpy import)."""

    __slots__ = (
        "advance",
        "ascent",
        "character",
        "descent",
        "glyph",
        "line_spacing",
        "width",
        "x",
        "x_offset",
        "y",
        "y_offset",
    )

    def __init__(self):
        self.character = 0
        self.glyph = 0
        self.advance = 0.0
        self.width = 0.0
        self.ascent = 0.0
        self.descent = 0.0
        self.line_spacing = 0.0
        self.x = 0.0
        self.y = 0.0
        self.x_offset = 0.0
        self.y_offset = 0.0


def _find_ttf() -> Path:
    root = Path(__file__).resolve().parents[3]  # revult/
    candidates = [
        root / "launcher" / "game" / "fonts" / "Roboto-Regular.ttf",
        root / "renpy" / "common" / "_theme_awt" / "Quicksand-Regular.ttf",
        root / "sdk-fonts" / "SourceHanSansLite.ttf",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError("no TTF found for text_descender gate")


def _make_glyphs(font: FTFont, s: str):
    """Same advance/ascent/descent logic as FTFont.glyphs without renpy import."""
    out = []
    x = 0.0
    for ch in s:
        g = _Glyph()
        g.character = ord(ch)
        g.glyph = ord(ch)
        adv = float(font.size) * 0.6
        if font._font is not None:
            try:
                if hasattr(font._font, "getlength"):
                    adv = float(font._font.getlength(ch))
                else:
                    box = font._font.getbbox(ch)
                    adv = float(box[2] - box[0]) if box else adv
            except Exception:
                pass
        g.advance = adv
        g.width = adv
        g.ascent = float(font.ascent)
        g.descent = float(-font.descent)
        g.line_spacing = float(font.lineskip)
        g.x = x
        g.y = 0.0
        out.append(g)
        x += adv
    return out


def _row_alpha_any(px: memoryview | bytearray, w: int, row: int) -> bool:
    off = row * w * 4
    for x in range(w):
        if px[off + x * 4 + 3]:
            return True
    return False


def main() -> int:
    ft_init()
    ttf = _find_ttf()
    size = 32
    face = FTFace(str(ttf))
    font = FTFont(face, size, False, False, 0, True, False, 0)

    # Before-fix style fake metrics for the report.
    fake_ascent = int(size * 0.8)
    fake_descent = int(size * 0.2)

    text = "ygp"
    glyphs = _make_glyphs(font, text)
    # Surface tall enough for full metrics; width from advance sum + pad.
    total_w = int(sum(g.advance for g in glyphs)) + 8
    total_h = int(font.height) + 4
    surf = Surface((max(total_w, 8), max(total_h, 8)))
    # Layout surface convention: yo = ascent so draw y = yo - g.ascent = 0
    # (glyph top at row 0, baseline at ascent, descenders below).
    font.draw(surf, 2, float(font.ascent), (255, 255, 255, 255), glyphs, False, False, None)

    w, h = surf.get_size()
    px = surf._pixels
    baseline = int(font.ascent)
    # Rows strictly below baseline should hold descender ink for y/g/p.
    desc_rows = []
    for row in range(baseline + 1, h):
        if _row_alpha_any(px, w, row):
            desc_rows.append(row)

    # Also count any alpha at all (sanity).
    any_ink = any(_row_alpha_any(px, w, row) for row in range(h))
    ok = bool(desc_rows) and any_ink and font.descent > fake_descent

    result = {
        "ok": ok,
        "gate": "text_descender",
        "font": str(ttf),
        "size": size,
        "text": text,
        "before": {"ascent": fake_ascent, "descent": fake_descent, "height": fake_ascent + fake_descent},
        "after": {
            "ascent": int(font.ascent),
            "descent": int(font.descent),
            "height": int(font.height),
            "lineskip": int(font.lineskip),
        },
        "surface": {"w": w, "h": h},
        "baseline_row": baseline,
        "descender_rows": desc_rows[:16],
        "descender_row_count": len(desc_rows),
        "any_ink": any_ink,
    }
    print(json.dumps(result, sort_keys=True))
    # Optional file report when RENPY_HOST_BASE is set (host gate runner style).
    base = Path(__file__).resolve().parents[2]  # host/
    out = base / "target" / "gate-text_descender.txt"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
