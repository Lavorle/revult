"""Transform helpers for host.

Hot paths use memoryview u32 pixel copies — per-pixel get_at/set_at was
measured at ~140 ms per 1280x720→640x360 scale and multi-second freezes on
main-menu predict/redraw (Task #24 residual F1). After bulk-byte fix still
~36 ms/call; cast('I') drops it further.
"""

from .surface import Surface


def scale(surface, size, dest=None):
    w, h = int(size[0]), int(size[1])
    out = Surface((w, h)) if dest is None else dest
    if surface.width == 0 or surface.height == 0 or w == 0 or h == 0:
        return out
    sw, sh = surface.width, surface.height
    if sw == w and sh == h:
        out._pixels[:] = surface._pixels
        return out
    # u32-per-pixel view: one assignment instead of 4-byte slice per pixel.
    sp = memoryview(surface._pixels).cast("I")
    dp = memoryview(out._pixels).cast("I")
    xs = [min(sw - 1, (x * sw) // w) for x in range(w)]
    for y in range(h):
        sy = min(sh - 1, (y * sh) // h)
        si_row = sy * sw
        di_row = y * w
        for x, sx in enumerate(xs):
            dp[di_row + x] = sp[si_row + sx]
    return out


def smoothscale(surface, size, dest=None):
    return scale(surface, size, dest)


def rotate(surface, angle):
    return surface.copy()


def flip(surface, xbool, ybool):
    w, h = surface.get_size()
    out = Surface((w, h))
    if w == 0 or h == 0:
        return out
    if not xbool and not ybool:
        out._pixels[:] = surface._pixels
        return out
    spb = memoryview(surface._pixels)
    dpb = memoryview(out._pixels)
    pitch = w * 4
    if xbool:
        sp = spb.cast("I")
        dp = dpb.cast("I")
        for y in range(h):
            sy = h - 1 - y if ybool else y
            si_row = sy * w
            di_row = y * w
            # Reverse row via two-pointer style on u32 view.
            for x in range(w):
                dp[di_row + x] = sp[si_row + (w - 1 - x)]
    else:
        for y in range(h):
            sy = h - 1 - y
            dpb[y * pitch : (y + 1) * pitch] = spb[sy * pitch : (sy + 1) * pitch]
    return out


def rotozoom(surface, angle, scale_factor):
    w = max(1, int(surface.width * scale_factor))
    h = max(1, int(surface.height * scale_factor))
    return scale(surface, (w, h))
