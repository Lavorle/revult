"""Host mouse shim."""

from __future__ import annotations

_pos = (0, 0)
_buttons = (False, False, False)
_last_pos = (0, 0)
_rel = (0, 0)


class ColorCursor:
    """Hardware cursor backed by a hotspot + surface (SDL3-compatible shape)."""

    __slots__ = ("hotspot", "surface")

    def __init__(self, hotspot, surface):
        self.hotspot = tuple(hotspot)
        self.surface = surface

    def __eq__(self, other):
        return (
            isinstance(other, ColorCursor)
            and self.hotspot == other.hotspot
            and self.surface is other.surface
        )

    def __hash__(self):
        return hash((self.hotspot, id(self.surface)))

    def __repr__(self):
        return f"ColorCursor(hotspot={self.hotspot}, surface={self.surface!r})"


def get_pos():
    return _pos


def set_pos(pos):
    global _pos, _last_pos, _rel
    nx, ny = int(pos[0]), int(pos[1])
    _rel = (nx - _pos[0], ny - _pos[1])
    _last_pos = _pos
    _pos = (nx, ny)


def get_pressed():
    return _buttons


def set_pressed(buttons):
    """Update pressed-button triple (left, middle, right).

    Called from host_pygame.event.poll when MOUSEBUTTON*/MOTION arrive so
    pygame.mouse.get_pressed() tracks live input (Slice 2 residual).
    """
    global _buttons
    if isinstance(buttons, (tuple, list)) and len(buttons) >= 3:
        _buttons = (bool(buttons[0]), bool(buttons[1]), bool(buttons[2]))
    elif isinstance(buttons, (tuple, list)) and len(buttons) == 1:
        _buttons = (bool(buttons[0]), False, False)
    else:
        _buttons = (False, False, False)


def get_rel():
    return _rel


def set_visible(visible):
    try:
        import renpy_host  # type: ignore
        renpy_host.set_cursor_visible(bool(visible))
    except Exception:
        pass
    return None


def get_focused():
    return True


def reset():
    """Stock pygame.mouse.reset / focus-reset — host no-op."""
    global _pos, _buttons, _rel
    _pos = (0, 0)
    _buttons = (False, False, False)
    _rel = (0, 0)
