"""
Host text rendering helpers — Pillow bitmap glyphs uploaded via renpy_host.

Phase 3 toward G-VN: render a string to RGBA, upload texture, draw textured quad.
Full ftfont/atlas integration remains for later when Cython host build is wired.
"""

import os
import shutil
import subprocess
from functools import lru_cache

from .constants import PIL_PADDING

FALLBACK_FONT = "/usr/share/fonts/google-noto/NotoSans-Regular.ttf"


def _find_system_font():
    """Locate a usable sans-serif .ttf/.ttc via known paths, then fontconfig.

    Returns an absolute path string or None. Tries the distro DejaVu / Noto
    locations first, then delegates to ``fc-match`` when available so the host
    still renders text in containers that ship no bundled NotoSans.
    """
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    ]
    if shutil.which("fc-match"):
        try:
            out = subprocess.run(
                ["fc-match", "--format", "%{file}", "sans"],
                capture_output=True,
                text=True,
                check=False,
            )
            path = (out.stdout or "").strip()
            if path:
                candidates.append(path)
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


DEFAULT_FONT = _find_system_font() or FALLBACK_FONT


@lru_cache(maxsize=8)
def _font(size: int):
    from PIL import ImageFont  # type: ignore

    path = os.environ.get("RENPY_HOST_FONT", DEFAULT_FONT)
    # Missing/unset font path: degrade to Pillow's built-in default rather
    # than aborting the wgpu host frame.
    if not path or not os.path.exists(path):
        return ImageFont.load_default()
    # Pin RAQM layout for stable complex-script shaping. Older Pillow lacks
    # the layout_engine kwarg -> fall back to the plain signature.
    try:
        return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
    except TypeError:
        return ImageFont.truetype(path, size)


def render_text_rgba(
    text: str,
    size: int = 32,
    color=(255, 255, 255, 255),
    bg=(0, 0, 0, 0),
    padding: int = PIL_PADDING,
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
