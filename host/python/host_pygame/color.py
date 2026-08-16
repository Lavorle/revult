"""Minimal Color type for host."""


class Color:
    def __init__(self, *args):
        if len(args) == 1 and isinstance(args[0], (tuple, list)):
            args = args[0]
        if len(args) == 1 and isinstance(args[0], str):
            # #rrggbb
            s = args[0].lstrip("#")
            if len(s) == 6:
                self.r = int(s[0:2], 16)
                self.g = int(s[2:4], 16)
                self.b = int(s[4:6], 16)
                self.a = 255
            else:
                self.r = self.g = self.b = 0
                self.a = 255
        elif len(args) >= 3:
            self.r, self.g, self.b = int(args[0]), int(args[1]), int(args[2])
            self.a = int(args[3]) if len(args) > 3 else 255
        else:
            self.r = self.g = self.b = 0
            self.a = 255

    def __iter__(self):
        yield self.r
        yield self.g
        yield self.b
        yield self.a

    def __len__(self):
        return 4

    def __getitem__(self, i):
        return (self.r, self.g, self.b, self.a)[i]
