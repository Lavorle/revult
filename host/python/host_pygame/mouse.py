"""Host mouse shim."""

from __future__ import annotations

_pos = (0, 0)
_buttons = (False, False, False)


def get_pos():
    return _pos


def set_pos(pos):
    global _pos
    _pos = tuple(pos)


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
    return (0, 0)


def set_visible(visible):
    return None


def get_focused():
    return True


def reset():
    """Stock pygame.mouse.reset / focus-reset — host no-op."""
    global _pos, _buttons
    _pos = (0, 0)
    _buttons = (False, False, False)
