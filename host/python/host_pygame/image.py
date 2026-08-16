"""Image load for host — Pillow when available."""

from __future__ import annotations

import os
from io import BytesIO

from .surface import Surface


def _is_svg(fi, namehint):
    def _hint_str(v):
        if isinstance(v, bytes):
            return v.decode("utf-8", "replace")
        return str(v) if v else ""

    candidates = [_hint_str(namehint)]
    if isinstance(fi, (str, bytes, os.PathLike)):
        candidates.append(_hint_str(os.fspath(fi)))
    for c in candidates:
        cl = c.lower()
        if cl.endswith(".svg") or cl in ("svg", ".svg"):
            return True
    return False


def load(fi, namehint="", size=None):
    """
    Load an image into an RGBA Surface.

    `fi`
        A path (str/bytes/os.PathLike) or a file-like object with .read().
    `namehint`
        Optional filename/extension used for format detection.
    `size`
        (width, height) — only meaningful for SVG; unsupported here.
    """
    # PAINT_PROBE (Slice1 temp): log paint-time image loads when env set.
    import os as _os
    _probe = _os.environ.get("RENPY_HOST_IMAGE_PROBE") in ("1", "true", "yes", "on")
    if size is not None and _is_svg(fi, namehint):
        raise ValueError(
            "SVG sized load is not supported by host_pygame.image"
        )

    try:
        from PIL import Image  # type: ignore

        if isinstance(fi, (str, bytes, os.PathLike)):
            im = Image.open(fi)
        elif hasattr(fi, "read"):
            data = fi.read()
            bio = BytesIO(data)
            if namehint:
                bio.name = (
                    namehint
                    if isinstance(namehint, str)
                    else namehint.decode("utf-8", "replace")
                )
            im = Image.open(bio)
        else:
            im = Image.open(fi)

        im = im.convert("RGBA")
        w, h = im.size
        s = Surface((w, h))
        s._pixels = bytearray(im.tobytes())
        if _probe:
            # mean RGB of first sample
            px = s._pixels
            n = max(1, (w * h))
            rs = sum(px[i] for i in range(0, len(px), 4)) / n
            gs = sum(px[i + 1] for i in range(0, len(px), 4)) / n
            bs = sum(px[i + 2] for i in range(0, len(px), 4)) / n
            print(
                f"[IMAGE_PROBE] ok namehint={namehint!r} size={(w,h)} mean=({rs:.1f},{gs:.1f},{bs:.1f}) fi={type(fi).__name__}",
                flush=True,
            )
        return s
    except Exception as _e:
        if _probe:
            print(f"[IMAGE_PROBE] FAIL namehint={namehint!r} fi={type(fi).__name__}: {type(_e).__name__}: {_e}", flush=True)
        if os.environ.get("RENPY_HOST_IMAGE_MAGENTA") == "1":
            s = Surface((1, 1))
            s.set_at((0, 0), (255, 0, 255, 255))
            return s
        raise


def save(surface, path):
    try:
        from PIL import Image  # type: ignore
        im = Image.frombytes("RGBA", surface.get_size(), bytes(surface._pixels))
        im.save(path)
    except Exception:
        with open(str(path) + ".raw", "wb") as f:
            f.write(bytes(surface._pixels))


def tostring(surface, format="RGBA", flipped=False):
    return bytes(surface._pixels)


def fromstring(string, size, format="RGBA", flipped=False):
    s = Surface(size)
    data = bytearray(string)
    n = len(s._pixels)
    if len(data) >= n:
        s._pixels = data[:n]
    else:
        s._pixels[: len(data)] = data
    return s


def frombuffer(buffer, size, format="RGBA"):
    return fromstring(buffer, size, format)
