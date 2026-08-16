"""Software RGBA surface for host (Phase 2+)."""

from __future__ import annotations


class Surface:
    def __init__(self, size=(0, 0), flags=0, depth=32, masks=None):
        self.width = int(size[0]) if size else 0
        self.height = int(size[1]) if size else 0
        self._pixels = bytearray(self.width * self.height * 4)

    def get_size(self):
        return (self.width, self.height)

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def get_bytesize(self):
        return 4

    def get_bitsize(self):
        return 32

    def get_pitch(self):
        return self.width * 4

    def fill(self, color):
        if len(color) == 3:
            r, g, b = color
            a = 255
        else:
            r, g, b, a = color[:4]
        px = bytes((int(r), int(g), int(b), int(a)))
        # Row-bulk fill: one pixel-loop is O(n) Python steps and stalls main-menu
        # predict (observed 99% CPU in set_at/subsurface during interact_core).
        if self.width <= 0 or self.height <= 0:
            return
        row = px * self.width
        view = memoryview(self._pixels)
        pitch = self.width * 4
        for y in range(self.height):
            off = y * pitch
            view[off : off + pitch] = row

    def blit(self, source, dest, area=None, special_flags=0):
        if not isinstance(source, Surface):
            return dest
        if isinstance(dest, (tuple, list)):
            dx, dy = int(dest[0]), int(dest[1])
        else:
            dx, dy = int(getattr(dest, "x", 0)), int(getattr(dest, "y", 0))
        sw, sh = source.width, source.height
        sx = sy = 0
        if area is not None:
            sx, sy, sw, sh = int(area[0]), int(area[1]), int(area[2]), int(area[3])
        # Clip source rect to source bounds.
        if sx < 0:
            sw += sx
            dx -= sx
            sx = 0
        if sy < 0:
            sh += sy
            dy -= sy
            sy = 0
        if sx + sw > source.width:
            sw = source.width - sx
        if sy + sh > source.height:
            sh = source.height - sy
        # Clip dest rect to self bounds.
        if dx < 0:
            sw += dx
            sx -= dx
            dx = 0
        if dy < 0:
            sh += dy
            sy -= dy
            dy = 0
        if dx + sw > self.width:
            sw = self.width - dx
        if dy + sh > self.height:
            sh = self.height - dy
        if sw <= 0 or sh <= 0:
            return dest
        spitch = source.width * 4
        dpitch = self.width * 4
        row_bytes = sw * 4
        src = memoryview(source._pixels)
        dst = memoryview(self._pixels)
        for row in range(sh):
            si = ((sy + row) * spitch) + (sx * 4)
            di = ((dy + row) * dpitch) + (dx * 4)
            dst[di : di + row_bytes] = src[si : si + row_bytes]
        return dest

    def copy(self):
        s = Surface((self.width, self.height))
        s._pixels = bytearray(self._pixels)
        return s

    def convert(self, *args, **kwargs):
        return self.copy()

    def convert_alpha(self, *args, **kwargs):
        return self.copy()

    def get_at(self, pos):
        x, y = int(pos[0]), int(pos[1])
        i = (y * self.width + x) * 4
        return tuple(self._pixels[i : i + 4])

    def set_at(self, pos, color):
        x, y = int(pos[0]), int(pos[1])
        i = (y * self.width + x) * 4
        if len(color) == 3:
            r, g, b = color
            a = 255
        else:
            r, g, b, a = color[:4]
        self._pixels[i : i + 4] = bytes((int(r), int(g), int(b), int(a)))

    def get_rect(self, **kwargs):
        from .rect import Rect
        r = Rect(0, 0, self.width, self.height)
        for k, v in kwargs.items():
            setattr(r, k, v)
        return r

    def get_bounding_rect(self, min_alpha=1):
        """Return the minimal rect enclosing pixels with alpha >= min_alpha.

        Empty surfaces, zero-sized surfaces, and surfaces with no qualifying
        pixels return the full rect (0, 0, w, h).

        Scan is row-directional with early exits — the prior full O(w*h)
        Python loop was ~76 ms on a 1280x720 opaque surface and contributed
        to multi-second main-menu freezes (Task #24 residual F1).
        """
        from .rect import Rect

        w, h = self.width, self.height
        if w == 0 or h == 0:
            return Rect(0, 0, w, h)

        px = memoryview(self._pixels)
        min_a = int(min_alpha)
        pitch = w * 4

        # Fast path: fully opaque (or all alpha >= min_a) → full rect.
        # Sample corners + center; if any fail, fall through to full scan.
        samples = (
            3,
            pitch - 1,
            (h - 1) * pitch + 3,
            h * pitch - 1,
            ((h // 2) * pitch) + ((w // 2) * 4) + 3,
        )
        if all(0 <= i < len(px) and px[i] >= min_a for i in samples):
            # Still verify a sparse grid; if all pass treat as full bounds.
            step_y = max(1, h // 16)
            step_x = max(1, w // 16)
            sparse_ok = True
            for y in range(0, h, step_y):
                row = y * pitch + 3
                for x in range(0, w, step_x):
                    if px[row + x * 4] < min_a:
                        sparse_ok = False
                        break
                if not sparse_ok:
                    break
            if sparse_ok:
                return Rect(0, 0, w, h)

        miny = None
        for y in range(h):
            row = y * pitch + 3
            for x in range(w):
                if px[row + x * 4] >= min_a:
                    miny = y
                    break
            if miny is not None:
                break
        if miny is None:
            return Rect(0, 0, w, h)

        maxy = miny
        for y in range(h - 1, miny - 1, -1):
            row = y * pitch + 3
            for x in range(w):
                if px[row + x * 4] >= min_a:
                    maxy = y
                    break
            else:
                continue
            break

        minx = w
        maxx = -1
        for y in range(miny, maxy + 1):
            row = y * pitch + 3
            # Left edge
            x = 0
            while x < minx:
                if px[row + x * 4] >= min_a:
                    minx = x
                    break
                x += 1
            # Right edge
            x = w - 1
            while x > maxx:
                if px[row + x * 4] >= min_a:
                    maxx = x
                    break
                x -= 1

        if maxx < 0:
            return Rect(0, 0, w, h)

        return Rect(minx, miny, maxx - minx + 1, maxy - miny + 1)

    def subsurface(self, rect):
        x, y, w, h = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
        if w <= 0 or h <= 0:
            return Surface((max(0, w), max(0, h)))
        s = Surface((w, h))
        # Row-bulk copy instead of per-pixel get_at/set_at (main-menu predict hotspot).
        spitch = self.width * 4
        dpitch = w * 4
        src = memoryview(self._pixels)
        dst = memoryview(s._pixels)
        for row in range(h):
            sy = y + row
            if sy < 0 or sy >= self.height:
                continue
            # Clip horizontal span to source.
            sx0 = x
            dx0 = 0
            rw = w
            if sx0 < 0:
                dx0 = -sx0
                rw += sx0
                sx0 = 0
            if sx0 + rw > self.width:
                rw = self.width - sx0
            if rw <= 0:
                continue
            si = (sy * spitch) + (sx0 * 4)
            di = (row * dpitch) + (dx0 * 4)
            nbytes = rw * 4
            dst[di : di + nbytes] = src[si : si + nbytes]
        return s

    def get_parent(self):
        return None

    def get_abs_parent(self):
        return self

    def get_flags(self):
        return 0

    def get_masks(self):
        return (0xFF000000, 0x00FF0000, 0x0000FF00, 0x000000FF)

    def get_shifts(self):
        return (24, 16, 8, 0)

    def get_losses(self):
        return (0, 0, 0, 0)

    def lock(self):
        return None

    def unlock(self):
        return None

    def mustlock(self):
        return False

    def get_locked(self):
        return False

    def get_locks(self):
        return ()

    def get_view(self, kind="0"):
        return memoryview(self._pixels)

    def get_buffer(self):
        return bytes(self._pixels)
