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
        # Explicitly pin to BASIC layout to suppress hinting/RAQM drift across Pillow versions.
        # Fallback to no-layout_engine if Pillow does not support the kwarg.
        try:
            return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)
        except (TypeError, AttributeError):
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            # Any FreeType-specific failure with BASIC, retry without it
            return ImageFont.truetype(path, size)
    except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
    # Measure — prefer font.getbbox (more stable than draw.textbbox across versions).
    bbox = None
    # Try font.getbbox with explicit anchor lt for determinism
    try:
        bbox = font.getbbox(text, anchor="lt")  # type: ignore[call-arg]
    except TypeError:
        try:
            bbox = font.getbbox(text)  # type: ignore[no-untyped-call]
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            bbox = None
    except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
        bbox = None
    if bbox is None:
        # Fallback to draw.textbbox for very old Pillow / bitmap fonts
        try:
            dummy = Image.new("RGBA", (1, 1))
            d = ImageDraw.Draw(dummy)
            bbox = d.textbbox((0, 0), text, font=font, anchor="lt")  # type: ignore[call-arg]
        except TypeError:
            dummy = Image.new("RGBA", (1, 1))
            d = ImageDraw.Draw(dummy)
            bbox = d.textbbox((0, 0), text, font=font)
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            dummy = Image.new("RGBA", (1, 1))
            d = ImageDraw.Draw(dummy)
            bbox = d.textbbox((0, 0), text, font=font)
    tw = max(1, bbox[2] - bbox[0])
    th = max(1, bbox[3] - bbox[1])
    w = tw + padding * 2
    h = th + padding * 2
    # Even-align w/h so Linear 2.5x sampling centre stays pixel-aligned (avoids 0.5-texel drift)
    if w % 2 == 1:
        w += 1
    if h % 2 == 1:
        h += 1
    im = Image.new("RGBA", (w, h), bg)
    draw = ImageDraw.Draw(im)
    # Explicit anchor lt for deterministic origin; fallback if Pillow version lacks it
    try:
        draw.text((padding - bbox[0], padding - bbox[1]), text, font=font, fill=color, anchor="lt")  # type: ignore[call-arg]
    except TypeError:
        draw.text((padding - bbox[0], padding - bbox[1]), text, font=font, fill=color)
    return w, h, im.tobytes()
