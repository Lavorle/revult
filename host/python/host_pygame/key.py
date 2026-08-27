"""Host key shim."""

from __future__ import annotations

import renpy_host  # type: ignore

from .locals import KMOD_LSHIFT, KMOD_RSHIFT, KMOD_LCTRL, KMOD_RCTRL, KMOD_LALT, KMOD_RALT, KMOD_LGUI, KMOD_RGUI

_mods = 0
_pressed = set()
_ime_rect = None


def get_pressed():
    # Sparse: return a dict-like that is false for missing keys.
    class _P(dict):
        def __getitem__(self, k):
            return dict.get(self, k, False)

    return _P({k: True for k in _pressed})


def get_mods():
    return _mods


def set_mods(mods):
    global _mods
    _mods = mods


def name(key):
    return str(key)


def start_text_input():
    renpy_host.start_text_input()


def stop_text_input():
    renpy_host.stop_text_input()


def set_text_input_rect(x, y=None, w=None, h=None):
    """Store IME candidate rect; forward to host when available.

    Accepts either ``set_text_input_rect(x,y,w,h)`` or
    ``set_text_input_rect(rect)`` where rect is (x,y,w,h) or has .x/.y/.w/.h,
    plus ``None`` to clear (mirrors SDL3 pygame API).
    """
    global _ime_rect
    # Normalize flexible args
    if y is None and w is None and h is None:
        # Single-arg form: rect or None
        if x is None:
            _ime_rect = None
            return None
        rect = x
        try:
            if isinstance(rect, (tuple, list)):
                if len(rect) >= 4:
                    x, y, w, h = rect[0], rect[1], rect[2], rect[3]
                else:
                    return None
            elif hasattr(rect, "x"):
                x, y, w, h = rect.x, rect.y, rect.w, rect.h
            else:
                return None
        except Exception:
            return None
    if x is None or y is None or w is None or h is None:
        _ime_rect = None
        return None
    try:
        xi, yi, wi, hi = int(x), int(y), int(w), int(h)
    except Exception:
        return None
    _ime_rect = (xi, yi, wi, hi)
    try:
        renpy_host.set_text_input_rect(xi, yi, wi, hi)
    except Exception:
        pass
    return None


def get_text_input_rect():
    """Return last IME rect or None."""
    return _ime_rect


def has_screen_keyboard_support():
    return False


def is_screen_keyboard_shown():
    return False
