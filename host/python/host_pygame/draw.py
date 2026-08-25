"""Draw primitives for host (software).

Filled rects use row-bulk writes — the previous per-pixel set_at path was
measured at ~160 ms for an 800x600 fill (Task #24 residual F1 freezes).
"""

import math

from .rect import Rect


def _color_bytes(color):
    if len(color) == 3:
        r, g, b = color
        a = 255
    else:
        r, g, b, a = color[:4]
    return bytes((int(r), int(g), int(b), int(a)))


def _fill_rect_bulk(surface, color, x0, y0, w, h):
    """Clip and row-bulk fill a rectangle on a software Surface."""
    if w <= 0 or h <= 0 or surface.width <= 0 or surface.height <= 0:
        return
    # Clip to surface.
    if x0 < 0:
        w += x0
        x0 = 0
    if y0 < 0:
        h += y0
        y0 = 0
    if x0 + w > surface.width:
        w = surface.width - x0
    if y0 + h > surface.height:
        h = surface.height - y0
    if w <= 0 or h <= 0:
        return
    px = _color_bytes(color)
    row = px * w
    view = memoryview(surface._pixels)
    pitch = surface.width * 4
    nbytes = w * 4
    xoff = x0 * 4
    for y in range(y0, y0 + h):
        off = y * pitch + xoff
        view[off : off + nbytes] = row


def rect(surface, color, rect, width=0):
    r = Rect(rect) if not isinstance(rect, Rect) else rect
    x0, y0, w, h = int(r.x), int(r.y), int(r.w), int(r.h)
    if width == 0:
        _fill_rect_bulk(surface, color, x0, y0, w, h)
    else:
        # Outline: four bulk strips (top/bottom/left/right), thickness=width.
        t = max(1, int(width))
        _fill_rect_bulk(surface, color, x0, y0, w, t)
        _fill_rect_bulk(surface, color, x0, y0 + h - t, w, t)
        _fill_rect_bulk(surface, color, x0, y0, t, h)
        _fill_rect_bulk(surface, color, x0 + w - t, y0, t, h)
    return r


def polygon(surface, color, points, width=0):
    return None


def circle(surface, color, center, radius, width=0):
    cx, cy = int(center[0]), int(center[1])
    r = int(radius)
    if r < 0:
        return
    # Row-span fill: for each y compute x span of the circle (no set_at).
    r2 = r * r
    inner = max(0, r - (0 if width == 0 else max(1, int(width))))
    inner2 = inner * inner
    px = _color_bytes(color)
    view = memoryview(surface._pixels)
    pitch = surface.width * 4
    sw, sh = surface.width, surface.height
    y0 = max(0, cy - r)
    y1 = min(sh - 1, cy + r)
    for y in range(y0, y1 + 1):
        dy = y - cy
        dy2 = dy * dy
        # outer half-width
        rem = r2 - dy2
        if rem < 0:
            continue
        half = math.isqrt(rem)
        x_left = max(0, cx - half)
        x_right = min(sw - 1, cx + half)
        if width == 0:
            if x_right < x_left:
                continue
            span = x_right - x_left + 1
            row = px * span
            off = y * pitch + x_left * 4
            view[off : off + span * 4] = row
        else:
            # Ring: fill outer span, then punch/skip inner if any.
            if rem >= 0:
                if inner > 0 and dy2 <= inner2:
                    irem = inner2 - dy2
                    ihalf = math.isqrt(irem) if irem >= 0 else -1
                    # left outer segment
                    lx1 = max(0, cx - half)
                    lx2 = min(sw - 1, cx - ihalf - 1)
                    if lx2 >= lx1:
                        span = lx2 - lx1 + 1
                        off = y * pitch + lx1 * 4
                        view[off : off + span * 4] = px * span
                    # right outer segment
                    rx1 = max(0, cx + ihalf + 1)
                    rx2 = min(sw - 1, cx + half)
                    if rx2 >= rx1:
                        span = rx2 - rx1 + 1
                        off = y * pitch + rx1 * 4
                        view[off : off + span * 4] = px * span
                else:
                    span = x_right - x_left + 1
                    if span > 0:
                        off = y * pitch + x_left * 4
                        view[off : off + span * 4] = px * span
    return


def ellipse(surface, color, rect, width=0):
    return None


def line(surface, color, start_pos, end_pos, width=1):
    x0, y0 = int(start_pos[0]), int(start_pos[1])
    x1, y1 = int(end_pos[0]), int(end_pos[1])
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    px = _color_bytes(color)
    view = memoryview(surface._pixels)
    pitch = surface.width * 4
    sw, sh = surface.width, surface.height
    while True:
        if 0 <= x0 < sw and 0 <= y0 < sh:
            off = y0 * pitch + x0 * 4
            view[off : off + 4] = px
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def lines(surface, color, closed, points, width=1):
    if not points:
        return
    for i in range(len(points) - 1):
        line(surface, color, points[i], points[i + 1], width)
    if closed and len(points) > 2:
        line(surface, color, points[-1], points[0], width)
    return


def aaline(surface, color, startpos, endpos, blend=1):
    return line(surface, color, startpos, endpos)
