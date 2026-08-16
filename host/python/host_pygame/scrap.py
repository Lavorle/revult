"""Clipboard module for renpy-host (no SDL scrap).

S-key screenshot ends in ``renpy.put_clipboard_image_file`` →
``renpy.pygame.scrap.put_data``. Cython scrap needs SDL and is not safe on
host_build; missing ``put_data`` raised AttributeError and wedged the game
after maximize. Implement the full surface as process-local no-ops so disk
screenshot still succeeds and the interact loop continues.
"""

SCRAP_TEXT = "text/plain"
SCRAP_BMP = "image/bmp"
SCRAP_CLIPBOARD = 0
SCRAP_SELECTION = 1


def init():
    return True


def quit():
    return None


def get(type=None):
    return b"" if type else b""


def put(data, type=None):
    return None


def get_types():
    return []


def contains(type):
    return False


def lost():
    return False


def set_mode(mode):
    return None


def put_data(data_dict):
    """Accept multi-MIME clipboard dict; no system clipboard on host."""
    return None


def get_data(mime_type):
    return b""


def get_mime_types():
    return []
