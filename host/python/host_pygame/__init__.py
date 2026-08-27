# Host-only pure-Python pygame shims (no libSDL).
# Inserted on sys.path ahead of the tree so `import renpy.pygame` resolves here
# when running under renpy-host (see host/README §4.9 / plan §4.5).

from . import color as color
from . import controller as controller
from . import display as display
from . import draw as draw
from . import error as error
from . import event as event
from . import image as image
from . import iostream as iostream
from . import joystick as joystick
from . import key as key
from . import a11y as a11y

# pygame.constants is an alias of locals in real renpy.pygame
# (see renpy/pygame/__init__.py: locals as constants).
from . import locals as constants
from . import locals as locals
from . import mouse as mouse
from . import power as power
from . import rect as rect
from . import scrap as scrap
from . import surface as surface
from . import sysfont as sysfont
from . import time as time
from . import transform as transform
from .color import Color  # noqa: F401
from .error import error  # noqa: F401
from .event import Event, event_name  # noqa: F401

# Re-export common names expected by renpy.display.core / pgrender
# (pygame.Surface, pygame.Rect, pygame.Color, pygame.SRCALPHA, …).
from .locals import *
from .rect import Rect  # noqa: F401
from .surface import Surface  # noqa: F401

# Surface flag used by pgrender.surface() / convert paths.
try:
    SRCALPHA
except NameError:
    SRCALPHA = 0x00010000  # SDL_SRCALPHA-compatible bit

init_functions = []
quit_functions = []


def init():
    for i in init_functions:
        i()
    return len(init_functions), 0


def quit():
    for i in quit_functions:
        try:
            i()
        except Exception:
            pass


def import_as_pygame():
    """
    Mirror renpy.pygame.import_as_pygame for host:
    register pygame / pygame.constants aliases without SDL.
    """
    import sys

    sys.modules.setdefault("pygame", sys.modules[__name__])
    sys.modules.setdefault("pygame.constants", constants)
    sys.modules.setdefault("pygame_sdl2", sys.modules[__name__])
    sys.modules.setdefault("pygame_sdl2.constants", constants)
    # Submodule aliases used by product code.
    for name in (
        "event",
        "display",
        "time",
        "key",
        "mouse",
        "surface",
        "color",
        "rect",
        "locals",
        "error",
        "joystick",
        "controller",
        "scrap",
        "power",
        "a11y",
        "iostream",
        "transform",
        "draw",
        "image",
        "sysfont",
        "constants",
    ):
        mod = globals().get(name)
        if mod is None and name == "constants":
            mod = locals
            globals()["constants"] = mod
        if mod is not None:
            sys.modules.setdefault(f"pygame.{name}", mod)
            sys.modules.setdefault(f"pygame_sdl2.{name}", mod)
            sys.modules.setdefault(f"renpy.pygame.{name}", mod)
