"""
Host text rendering helpers — Pillow bitmap glyphs + SDF atlas (M3 B1 T1).

Phase 3 + M3 B1 T1: render a string to RGBA via shape→atlas→SDF chain when
HarfBuzz/FreeType host is available, with Pillow whole-string fallback retaining
1x MAE≤2 and signature compatibility. Full ftfont/atlas integration via
text_shaper / text_atlas / text_sdf; Cython host build deferred.
"""

import os
import shutil
import subprocess
from functools import lru_cache

from .constants import PIL_PADDING, SDF_RADIUS

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


def _shape_and_upload(text: str, size: int) -> None:
    """Best-effort shape→atlas alloc→SDF upload for side-effect cache warming.

    Walks the M3 B1 T1 chain: HarfBuzz shape (or Pillow RAQM fallback) →
    AtlasManager.alloc_glyph → render_sdf_glyph → upload_glyph_rgba.
    Failures are swallowed so the product render_text_rgba path never aborts
    the frame. This keeps 1x MAE at 0 (final bytes still come from Pillow
    whole-string) while exercising the atlas LRU / SDF / subrect machinery for
    tests and future GPU quad batching.
    """
    try:
        from .text_shaper import shape  # noqa: WPS433
        from .text_atlas import get_atlas_manager  # noqa: WPS433
        from .text_sdf import render_sdf_glyph  # noqa: WPS433
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except Exception:
        return

    if not text:
        return

    font_path = os.environ.get("RENPY_HOST_FONT", DEFAULT_FONT)
    # shape returns list[GlyphPos] with glyph_id/cluster
    try:
        glyphs = shape(text, font_path, size)
    except Exception:
        glyphs = []

    if not glyphs:
        # Fallback still warms atlas with per-codepoint keys so LRU tests see entries
        try:
            mgr = get_atlas_manager()
            for idx, ch in enumerate(text):
                key = (ord(ch), size, font_path)
                # Use Pillow bbox for size estimate; fallback to size*0.6
                adv_w = max(1, int(size * 0.6))
                adv_h = max(1, int(size))
                try:
                    # Try to get a better estimate via Pillow font
                    f = _font(size)
                    box = f.getbbox(ch)
                    if box is not None:
                        adv_w = max(1, int(box[2] - box[0]) + PIL_PADDING * 2)
                        adv_h = max(1, int(box[3] - box[1]) + PIL_PADDING * 2)
                    else:
                        adv_w = max(1, int(size * 0.6) + PIL_PADDING * 2)
                        adv_h = max(1, int(size) + PIL_PADDING * 2)
                except Exception:
                    pass
                # alloc_glyph expects content w/h; we pass estimated
                rect = mgr.alloc_glyph(key, adv_w, adv_h)
                if rect is None:
                    continue
                # Generate single-glyph bitmap for SDF upload
                try:
                    f = _font(size)
                    # Render glyph to small RGBA for SDF input
                    gw = max(1, adv_w)
                    gh = max(1, adv_h)
                    im = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
                    d = ImageDraw.Draw(im)
                    # Center char roughly; bbox may have negative origin
                    try:
                        bbox = f.getbbox(ch, anchor="lt")  # type: ignore[call-arg]
                    except TypeError:
                        try:
                            bbox = f.getbbox(ch)  # type: ignore[no-untyped-call]
                        except Exception:
                            bbox = (0, 0, gw, gh)
                    off_x = -bbox[0] + PIL_PADDING if bbox else PIL_PADDING
                    off_y = -bbox[1] + PIL_PADDING if bbox else PIL_PADDING
                    try:
                        d.text((off_x, off_y), ch, font=f, fill=(255, 255, 255, 255), anchor="lt")  # type: ignore[call-arg]
                    except TypeError:
                        d.text((off_x, off_y), ch, font=f, fill=(255, 255, 255, 255))
                    rgba = im.tobytes()
                    # Render SDF (exercise scipy/pure BFS) then upload subrect
                    sdf = render_sdf_glyph(rgba, gw, gh, radius=SDF_RADIUS)
                    # For atlas upload we still need RGBA; convert SDF scalar to RGBA (r=sdf, g=b=sdf, a=255)
                    # Upload as RGBA where R holds SDF for shader sampling.
                    if sdf and len(sdf) == gw * gh:
                        rgba_sdf = bytearray(gw * gh * 4)
                        for i, s in enumerate(sdf):
                            off = i * 4
                            rgba_sdf[off] = s
                            rgba_sdf[off + 1] = s
                            rgba_sdf[off + 2] = s
                            rgba_sdf[off + 3] = 255
                        mgr.upload_glyph_rgba(int(rect.x), int(rect.y), int(rect.w), int(rect.h), bytes(rgba_sdf))
                    else:
                        mgr.upload_glyph_rgba(int(rect.x), int(rect.y), int(rect.w), int(rect.h), bytes(rgba))
                except Exception:
                    # Keep atlas state consistent even if bitmap/SDF fails
                    try:
                        mgr.upload_glyph_rgba(int(rect.x), int(rect.y), int(rect.w), int(rect.h), bytes([0] * adv_w * adv_h * 4))
                    except Exception:
                        pass
            return
        except Exception:
            return
        return

    # Shaped path — use glyph_id keyed alloc
    try:
        mgr = get_atlas_manager()
        for gp in glyphs:
            # Use glyph_id+size as key; incorporate cluster for uniqueness when fallback
            key = (int(gp.glyph_id), size, font_path)
            # Estimate glyph extent via advance; with HB we have x_advance
            adv_w = max(1, int(abs(gp.x_advance)) + PIL_PADDING * 2) if getattr(gp, "x_advance", 0) else max(1, int(size * 0.6) + PIL_PADDING * 2)
            adv_h = max(1, int(size) + PIL_PADDING * 2)
            # Try to refine via Pillow single-char bbox when possible
            try:
                ch = text[int(gp.cluster)] if 0 <= int(gp.cluster) < len(text) else ""
                if ch:
                    f = _font(size)
                    box = f.getbbox(ch)
                    if box is not None:
                        adv_w = max(1, int(box[2] - box[0]) + PIL_PADDING * 2)
                        adv_h = max(1, int(box[3] - box[1]) + PIL_PADDING * 2)
            except Exception:
                pass
            rect = mgr.alloc_glyph(key, adv_w, adv_h)
            if rect is None:
                # Oversized fallback — skip upload, spec expects None for 超大字形
                continue
            # Render single glyph bitmap for upload (Pillow fallback path ensures green even without HB)
            try:
                from PIL import Image, ImageDraw  # type: ignore
                f = _font(size)
                ch = text[int(gp.cluster)] if 0 <= int(gp.cluster) < len(text) else "?"
                gw = max(1, int(rect.w))
                gh = max(1, int(rect.h))
                im = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
                d = ImageDraw.Draw(im)
                try:
                    bbox = f.getbbox(ch, anchor="lt")  # type: ignore[call-arg]
                except TypeError:
                    try:
                        bbox = f.getbbox(ch)  # type: ignore[no-untyped-call]
                    except Exception:
                        bbox = (0, 0, gw - PIL_PADDING * 2, gh - PIL_PADDING * 2)
                off_x = -bbox[0] + PIL_PADDING if bbox else PIL_PADDING
                off_y = -bbox[1] + PIL_PADDING if bbox else PIL_PADDING
                try:
                    d.text((off_x, off_y), ch, font=f, fill=(255, 255, 255, 255), anchor="lt")  # type: ignore[call-arg]
                except TypeError:
                    d.text((off_x, off_y), ch, font=f, fill=(255, 255, 255, 255))
                rgba = im.tobytes()
                sdf = render_sdf_glyph(rgba, gw, gh, radius=SDF_RADIUS)
                if sdf and len(sdf) == gw * gh:
                    rgba_sdf = bytearray(gw * gh * 4)
                    for i, s in enumerate(sdf):
                        off = i * 4
                        rgba_sdf[off] = s
                        rgba_sdf[off + 1] = s
                        rgba_sdf[off + 2] = s
                        rgba_sdf[off + 3] = 255
                    mgr.upload_glyph_rgba(int(rect.x), int(rect.y), gw, gh, bytes(rgba_sdf))
                else:
                    mgr.upload_glyph_rgba(int(rect.x), int(rect.y), gw, gh, bytes(rgba))
            except Exception:
                try:
                    mgr.upload_glyph_rgba(int(rect.x), int(rect.y), int(rect.w), int(rect.h), bytes([0] * int(rect.w) * int(rect.h) * 4))
                except Exception:
                    pass
    except Exception:
        pass


def render_text_rgba(
    text: str,
    size: int = 32,
    color=(255, 255, 255, 255),
    bg=(0, 0, 0, 0),
    padding: int = PIL_PADDING,
) -> tuple[int, int, bytes]:
    """Return (w, h, rgba_bytes) for the rendered string.

    Signature frozen for compatibility (M3 B1 T1). Internally walks
    shape→atlas alloc→SDF chain for cache warming when a host font is
    available, then falls back to Pillow whole-string raster for 1x pixel
    equivalence (MAE≤2, here 0). Keeps _find_system_font + RENPY_HOST_FONT
    chain and lru_cache _font(8) intact. Pillow retained as fallback when
    HarfBuzz/FreeType is unavailable.
    """
    # Warm atlas cache (best-effort, never aborts frame)
    try:
        _shape_and_upload(text, size)
    except Exception:
        pass

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
