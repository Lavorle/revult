"""Host display shim — window owned by renpy-host / winit."""

from __future__ import annotations

import renpy_host  # type: ignore

from .surface import Surface

_mode = None


def init():
    return None


def quit():
    return None


def set_mode(size, flags=0, depth=0, display=0):
    global _mode
    w, h = size
    # Host window already created; record virtual size.
    _mode = Surface((w, h))
    return _mode


def get_surface():
    return _mode


def flip():
    renpy_host.request_redraw()


def update(rectangle=None):
    renpy_host.request_redraw()


def set_caption(title):
    if isinstance(title, bytes):
        title = title.decode("utf-8", "replace")
    renpy_host.set_window_title(title)


def get_caption():
    return ""


def set_icon(surface):
    return None


def iconify():
    return None


def get_wm_info():
    return {}


def Info():
    class _Info:
        hw = False
        wm = True
        video_mem = 0
        bitsize = 32
        bytesize = 4
        masks = (0xFF000000, 0x00FF0000, 0x0000FF00, 0x000000FF)
        shifts = (24, 16, 8, 0)
        losses = (0, 0, 0, 0)

    return _Info()


def list_modes(depth=0, flags=0, display=0):
    return [(1280, 720), (1920, 1080)]


def mode_ok(size, flags=0, depth=0):
    return 32


def gl_get_attribute(flag):
    return 0


def gl_set_attribute(flag, value):
    return None


def get_driver():
    return "renpy-host"


def set_gamma(r, g=None, b=None):
    return False


def set_gamma_ramp(*args):
    return False


def hint(name, value=None):
    """SDL_SetHint no-op on host (window owned by winit)."""
    return True


def get_size():
    try:
        return tuple(renpy_host.window_size())
    except Exception:
        return (1280, 720)


def get_display_bounds(index=0):
    w, h = get_size()
    return (0, 0, w, h)


def get_position():
    return (0, 0)


def set_screensaver(enabled=True):
    return None


def get_active():
    return True


def destroy():
    return None


def get_num_displays():
    return 1


def get_desktop_sizes():
    return [get_size()]


class Window:
    """Minimal SDL3-style Window handle (host: window owned by winit)."""

    def __init__(self, title, size):
        self.title = title
        self.size = tuple(size)

    def destroy(self):
        return None

    def __repr__(self):
        return f"Window(title={self.title!r}, size={self.size})"

    # M3 B3 T3: IME cursor area pass-through (winit Window::set_ime_cursor_area)
    def set_ime_cursor_area(self, x: int, y: int, w: int, h: int) -> None:
        try:
            from . import key as _key
            _key.set_text_input_rect(int(x), int(y), int(w), int(h))
        except Exception:
            pass
        # Also forward directly to host if key shim unavailable
        try:
            renpy_host.set_text_input_rect(int(x), int(y), int(w), int(h))
        except Exception:
            pass

    # Alias for SDL3 pygame naming
    def set_ime_rect(self, rect) -> None:
        try:
            if rect is None:
                return
            if isinstance(rect, (tuple, list)) and len(rect) >= 4:
                self.set_ime_cursor_area(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
            elif hasattr(rect, "x"):
                self.set_ime_cursor_area(int(rect.x), int(rect.y), int(rect.w), int(rect.h))
        except Exception:
            pass


def set_ime_cursor_area(x: int, y: int, w: int, h: int) -> None:
    """Top-level helper for code that does not hold a Window handle."""
    try:
        from . import key as _key
        _key.set_text_input_rect(int(x), int(y), int(w), int(h))
    except Exception:
        pass
    try:
        renpy_host.set_text_input_rect(int(x), int(y), int(w), int(h))
    except Exception:
        pass

# Back-compat alias for older gates
set_text_input_rect = set_ime_cursor_area
