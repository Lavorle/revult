"""
Host text rendering helpers — Pillow bitmap glyphs uploaded via renpy_host.

Phase 3 toward G-VN: render a string to RGBA, upload texture, draw textured quad.
Full ftfont/atlas integration remains for later when Cython host build is wired.
"""

from __future__ import annotations

import os
from functools import lru_cache

DEFAULT_FONT = "/usr/share/fonts/google-noto/NotoSans-Regular.ttf"


@lru_cache(maxsize=8)
def _font(size: int):
    from PIL import ImageFont  # type: ignore

    path = os.environ.get("RENPY_HOST_FONT", DEFAULT_FONT)
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def render_text_rgba(
    text: str,
    size: int = 32,
    color=(255, 255, 255, 255),
    bg=(0, 0, 0, 0),
    padding: int = 4,
) -> tuple[int, int, bytes]:
    """Return (w, h, rgba_bytes) for the rendered string."""
    from PIL import Image, ImageDraw  # type: ignore

    font = _font(size)
    # Measure
    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = max(1, bbox[2] - bbox[0])
    th = max(1, bbox[3] - bbox[1])
    w = tw + padding * 2
    h = th + padding * 2
    im = Image.new("RGBA", (w, h), bg)
    draw = ImageDraw.Draw(im)
    draw.text((padding - bbox[0], padding - bbox[1]), text, font=font, fill=color)
    return w, h, im.tobytes()


def draw_text_screen(
    text: str,
    ndc_x: float = -0.8,
    ndc_y: float = 0.2,
    ndc_w: float = 1.6,
    ndc_h: float = 0.4,
    size: int = 48,
):
    """Upload text texture and draw a textured quad in NDC via draw_model."""
    import renpy_host  # type: ignore

    w, h, rgba = render_text_rgba(text, size=size)
    tex = renpy_host.create_texture_rgba(w, h, rgba)
    # Quad corners in NDC
    x0, y0 = ndc_x, ndc_y
    x1, y1 = ndc_x + ndc_w, ndc_y - ndc_h
    verts = [
        x0,
        y1,
        0.0,
        1.0,
        1,
        1,
        1,
        1,
        x1,
        y1,
        1.0,
        1.0,
        1,
        1,
        1,
        1,
        x1,
        y0,
        1.0,
        0.0,
        1,
        1,
        1,
        1,
        x0,
        y0,
        0.0,
        0.0,
        1,
        1,
        1,
        1,
    ]
    mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
    pipe = renpy_host.textured_pipeline()
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, tex)
    renpy_host.end_frame_present()
    return {"tex": tex, "mesh": mesh, "pipe": pipe, "size": (w, h)}
