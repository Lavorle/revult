"""
HostTexture — lightweight texture handle wrapper extracted from draw.py.

Keeps pickle/import compatibility via re-export in draw.py:
    from renpy.wgpu.draw import HostTexture
    from renpy.wgpu.host_texture import HostTexture
both remain valid.
"""
from __future__ import annotations

import hashlib


def _surf_fingerprint(pixels: bytes, w: int, h: int) -> bytes:
    """Cheap content fingerprint: size + first/mid/last 64 RGBA bytes."""
    hsh = hashlib.blake2b(digest_size=8)
    hsh.update(w.to_bytes(4, "little"))
    hsh.update(h.to_bytes(4, "little"))
    hsh.update(pixels[:64])
    if pixels:
        mid = max(0, (len(pixels) // 2) - 32)
        hsh.update(pixels[mid : mid + 64])
        hsh.update(pixels[-64:])
    return hsh.digest()


class HostTexture:
    """
    Lightweight texture handle wrapper for product call sites that expect
    GLTexture-like objects (``.subsurface``, ``.get_size``, dimensions).

    `handle` is the GpuArena texture id returned by renpy_host.create_texture_rgba.
    """

    def __init__(
        self, handle: int, width: int, height: int, x: int = 0, y: int = 0, w: int | None = None, h: int | None = None
    ):
        self.handle = int(handle)
        self.width = int(width)
        self.height = int(height)
        self.x = int(x)
        self.y = int(y)
        self.w = int(width if w is None else w)
        self.h = int(height if h is None else h)
        # Aliases used by various product paths
        self.texture = self.handle

    def get_size(self):
        return (self.w, self.h)

    def subsurface(self, rect):
        x, y, w, h = rect
        return HostTexture(
            self.handle,
            self.width,
            self.height,
            x=self.x + int(x),
            y=self.y + int(y),
            w=max(0, int(w)),
            h=max(0, int(h)),
        )

    def __int__(self):
        return self.handle

    def __index__(self):
        return self.handle


# Keep pickle path stable: old pickles store ``renpy.wgpu.draw.HostTexture``.
HostTexture.__module__ = "renpy.wgpu.draw"

__all__ = ["HostTexture", "_surf_fingerprint"]


