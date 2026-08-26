"""Minimal Rect type for host — true SDL-compatible geometry."""

from __future__ import annotations



class Rect:
    __slots__ = ("_x", "_y", "_w", "_h")

    def __init__(self, *args):
        if len(args) == 1 and isinstance(args[0], Rect):
            r = args[0]
            self._x, self._y, self._w, self._h = r._x, r._y, r._w, r._h
        elif len(args) == 1 and isinstance(args[0], (tuple, list)) and len(args[0]) == 4:
            self._x, self._y, self._w, self._h = map(int, args[0])
        elif len(args) == 4:
            self._x, self._y, self._w, self._h = map(int, args)
        elif len(args) == 2:
            # ((x,y),(w,h))
            try:
                (x, y), (w, h) = args  # type: ignore[misc]
            except Exception as e:
                raise TypeError(f"Rect expects 4 ints or 2 tuples, got {args!r}") from e
            self._x, self._y, self._w, self._h = int(x), int(y), int(w), int(h)
        else:
            raise TypeError(f"Rect expects Rect | (x,y,w,h) | (x,y,w,h) 4 ints | ((x,y),(w,h)), got {args!r}")

    # ---- basic fields ----
    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, v):
        self._x = int(v)

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, v):
        self._y = int(v)

    @property
    def w(self):
        return self._w

    @w.setter
    def w(self, v):
        self._w = int(v)

    @property
    def h(self):
        return self._h

    @h.setter
    def h(self, v):
        self._h = int(v)

    # aliases
    @property
    def width(self):
        return self._w

    @width.setter
    def width(self, v):
        self._w = int(v)

    @property
    def height(self):
        return self._h

    @height.setter
    def height(self, v):
        self._h = int(v)

    @property
    def left(self):
        return self._x

    @left.setter
    def left(self, v):
        self._x = int(v)

    @property
    def top(self):
        return self._y

    @top.setter
    def top(self, v):
        self._y = int(v)

    @property
    def right(self):
        return self._x + self._w

    @right.setter
    def right(self, v):
        self._x = int(v) - self._w

    @property
    def bottom(self):
        return self._y + self._h

    @bottom.setter
    def bottom(self, v):
        self._y = int(v) - self._h

    @property
    def center(self):
        return (self._x + self._w // 2, self._y + self._h // 2)

    @center.setter
    def center(self, pos):
        cx, cy = pos
        self._x = int(cx) - self._w // 2
        self._y = int(cy) - self._h // 2

    @property
    def centerx(self):
        return self._x + self._w // 2

    @centerx.setter
    def centerx(self, v):
        self._x = int(v) - self._w // 2

    @property
    def centery(self):
        return self._y + self._h // 2

    @centery.setter
    def centery(self, v):
        self._y = int(v) - self._h // 2

    @property
    def size(self):
        return (self._w, self._h)

    @size.setter
    def size(self, v):
        w, h = v
        self._w, self._h = int(w), int(h)

    @property
    def topleft(self):
        return (self._x, self._y)

    @topleft.setter
    def topleft(self, v):
        x, y = v
        self._x, self._y = int(x), int(y)

    @property
    def topright(self):
        return (self._x + self._w, self._y)

    @topright.setter
    def topright(self, v):
        x, y = v
        self._x = int(x) - self._w
        self._y = int(y)

    @property
    def bottomleft(self):
        return (self._x, self._y + self._h)

    @bottomleft.setter
    def bottomleft(self, v):
        x, y = v
        self._x = int(x)
        self._y = int(y) - self._h

    @property
    def bottomright(self):
        return (self._x + self._w, self._y + self._h)

    @bottomright.setter
    def bottomright(self, v):
        x, y = v
        self._x = int(x) - self._w
        self._y = int(y) - self._h

    @property
    def midtop(self):
        return (self._x + self._w // 2, self._y)

    @midtop.setter
    def midtop(self, v):
        x, y = v
        self._x = int(x) - self._w // 2
        self._y = int(y)

    @property
    def midbottom(self):
        return (self._x + self._w // 2, self._y + self._h)

    @midbottom.setter
    def midbottom(self, v):
        x, y = v
        self._x = int(x) - self._w // 2
        self._y = int(y) - self._h

    @property
    def midleft(self):
        return (self._x, self._y + self._h // 2)

    @midleft.setter
    def midleft(self, v):
        x, y = v
        self._x = int(x)
        self._y = int(y) - self._h // 2

    @property
    def midright(self):
        return (self._x + self._w, self._y + self._h // 2)

    @midright.setter
    def midright(self, v):
        x, y = v
        self._x = int(x) - self._w
        self._y = int(y) - self._h // 2

    # ---- helpers ----
    def _ensure_rect(self, other):
        if isinstance(other, Rect):
            return other
        if isinstance(other, (tuple, list)):
            # Expand: Rect(*other) covers (x,y,w,h) and ((x,y),(w,h))
            try:
                return Rect(*other)
            except TypeError:
                # fallback: single 4-tuple wrapped
                return Rect(other)
        raise TypeError(f"Rect operation expects Rect or tuple, got {type(other).__name__}: {other!r}")

    # ---- methods ----
    def copy(self):
        return Rect(self._x, self._y, self._w, self._h)

    def move(self, dx, dy):
        return Rect(self._x + int(dx), self._y + int(dy), self._w, self._h)

    def inflate(self, dx, dy):
        dx = int(dx)
        dy = int(dy)
        return Rect(self._x - dx // 2, self._y - dy // 2, self._w + dx, self._h + dy)

    def clip(self, other):
        other = self._ensure_rect(other)
        x1 = max(self._x, other._x)
        y1 = max(self._y, other._y)
        x2 = min(self._x + self._w, other._x + other._w)
        y2 = min(self._y + self._h, other._y + other._h)
        if x2 <= x1 or y2 <= y1:
            return Rect(x1, y1, 0, 0)
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def union(self, other):
        other = self._ensure_rect(other)
        x1 = min(self._x, other._x)
        y1 = min(self._y, other._y)
        x2 = max(self._x + self._w, other._x + other._w)
        y2 = max(self._y + self._h, other._y + other._h)
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def colliderect(self, other):
        other = self._ensure_rect(other)
        return not (
            self._x + self._w <= other._x
            or other._x + other._w <= self._x
            or self._y + self._h <= other._y
            or other._y + other._h <= self._y
        )

    def contains(self, other):
        other = self._ensure_rect(other)
        return (
            self._x <= other._x
            and self._y <= other._y
            and self._x + self._w >= other._x + other._w
            and self._y + self._h >= other._y + other._h
        )

    def collidepoint(self, *args):
        if len(args) == 1:
            x, y = args[0]
        else:
            x, y = args
        return self._x <= int(x) < self._x + self._w and self._y <= int(y) < self._y + self._h

    def normalize(self):
        if self._w < 0:
            self._x += self._w
            self._w = -self._w
        if self._h < 0:
            self._y += self._h
            self._h = -self._h

    def __iter__(self):
        return iter((self._x, self._y, self._w, self._h))

    def __getitem__(self, i):
        return (self._x, self._y, self._w, self._h)[i]

    def __len__(self):
        return 4

    def __eq__(self, other):
        if isinstance(other, Rect):
            return tuple(self) == tuple(other)
        if isinstance(other, (tuple, list)):
            try:
                return tuple(self) == tuple(other)
            except Exception:
                return False
        return False

    def __repr__(self):
        return f"Rect({self._x}, {self._y}, {self._w}, {self._h})"

    __str__ = __repr__
