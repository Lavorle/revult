"""Minimal Rect type for host."""


class Rect:
    def __init__(self, *args):
        if len(args) == 1 and isinstance(args[0], Rect):
            r = args[0]
            self.x, self.y, self.w, self.h = r.x, r.y, r.w, r.h
        elif len(args) == 1 and isinstance(args[0], (tuple, list)) and len(args[0]) == 4:
            self.x, self.y, self.w, self.h = args[0]
        elif len(args) == 2:
            self.x, self.y = args[0]
            self.w, self.h = args[1]
        elif len(args) == 4:
            self.x, self.y, self.w, self.h = args
        else:
            self.x = self.y = self.w = self.h = 0

    @property
    def width(self):
        return self.w

    @width.setter
    def width(self, v):
        self.w = v

    @property
    def height(self):
        return self.h

    @height.setter
    def height(self, v):
        self.h = v

    @property
    def top(self):
        return self.y

    @property
    def left(self):
        return self.x

    @property
    def bottom(self):
        return self.y + self.h

    @property
    def right(self):
        return self.x + self.w

    @property
    def center(self):
        return (self.x + self.w // 2, self.y + self.h // 2)

    @center.setter
    def center(self, pos):
        cx, cy = pos
        self.x = cx - self.w // 2
        self.y = cy - self.h // 2

    def copy(self):
        return Rect(self.x, self.y, self.w, self.h)

    def move(self, x, y):
        return Rect(self.x + x, self.y + y, self.w, self.h)

    def inflate(self, x, y):
        return Rect(self.x - x // 2, self.y - y // 2, self.w + x, self.h + y)

    def clip(self, other):
        return self.copy()

    def union(self, other):
        return self.copy()

    def contains(self, other):
        return True

    def collidepoint(self, *args):
        if len(args) == 1:
            x, y = args[0]
        else:
            x, y = args
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    def colliderect(self, other):
        return True

    def __iter__(self):
        yield self.x
        yield self.y
        yield self.w
        yield self.h

    def __getitem__(self, i):
        return (self.x, self.y, self.w, self.h)[i]
