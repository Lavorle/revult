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


def set_text_input_rect(x, y, w, h):
    """Store IME candidate rect; forward to host when available."""
    global _ime_rect
    _ime_rect = (int(x), int(y), int(w), int(h))
    try:
        renpy_host.set_text_input_rect(int(x), int(y), int(w), int(h))
    except Exception:
        pass


def has_screen_keyboard_support():
    return False


def is_screen_keyboard_shown():
    return False
