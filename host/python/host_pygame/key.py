"""Host key shim."""

from __future__ import annotations

import renpy_host  # type: ignore

_mods = 0
_pressed = set()


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
    # Phase 1: IME rect not yet forwarded; API present for core.py callers.
    return None
