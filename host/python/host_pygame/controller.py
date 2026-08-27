"""Host controller — SDL_GameController mapping over renpy_host gamepad.

Thin alias over joystick GamepadState with controller semantics:
* axis/button string mapping (pygame.controller.* helpers)
* rumble → soft true stub
* get_count mirrors joystick count (shared evdev/winit source)
* add_mapping / add_mappings parse SDL_GameControllerDB strings

Keeps import compatibility: ``import renpy.pygame.controller`` and
``from renpy.display.controller import *`` both work.
"""

from __future__ import annotations

try:
    import renpy_host  # type: ignore
except Exception:  # pragma: no cover
    renpy_host = None  # type: ignore

from .joystick import Joystick as _JoystickBase

# Re-use joystick counts/capabilities — single GamepadState pool
_NUM_AXES = 6
_NUM_BUTTONS = 16

# SDL_GameController axis names (subset)
_AXIS_NAMES = [
    "leftx",
    "lefty",
    "rightx",
    "righty",
    "lefttrigger",
    "righttrigger",
]
_AXIS_MAP = {name: i for i, name in enumerate(_AXIS_NAMES)}
# Alternative names some DB files use
_AXIS_ALIAS = {
    "leftstick.x": 0,
    "leftstick.y": 1,
    "rightstick.x": 2,
    "rightstick.y": 3,
    "leftshoulder": 4,
    "rightshoulder": 5,
}
_AXIS_MAP.update(_AXIS_ALIAS)

_BUTTON_NAMES = [
    "a",
    "b",
    "x",
    "y",
    "back",
    "guide",
    "start",
    "leftstick",
    "rightstick",
    "leftshoulder",
    "rightshoulder",
    "dpad_up",
    "dpad_down",
    "dpad_left",
    "dpad_right",
    "misc1",
]
_BUTTON_MAP = {name: i for i, name in enumerate(_BUTTON_NAMES)}

_initialized = True
_mappings: list[str] = []


def _host():
    return renpy_host


def init() -> None:
    global _initialized
    _initialized = True
    return None


def quit() -> None:
    global _initialized
    _initialized = False
    return None


def get_init() -> bool:
    return bool(_initialized)


def get_count() -> int:
    h = _host()
    if h is None:
        return 0
    try:
        return int(h.gamepad_count())
    except Exception:
        return 0


def add_mapping(mapping: str) -> int:
    """Add a single SDL_GameControllerDB mapping line (stub parser)."""
    if isinstance(mapping, (bytes, bytearray)):
        mapping = mapping.decode("utf-8", "replace")
    s = str(mapping).strip()
    if not s or s.startswith("#"):
        return 0
    _mappings.append(s)
    return 1


def add_mappings(mapping_file: str) -> None:
    if isinstance(mapping_file, (bytes, bytearray)):
        mapping_file = mapping_file.decode("utf-8", "replace")
    # mapping_file may be path or raw DB content; handle both
    try:
        import os

        if os.path.isfile(str(mapping_file)):
            with open(str(mapping_file), "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    add_mapping(line)
            return
    except Exception:
        pass
    # Treat as content blob with newlines
    try:
        for line in str(mapping_file).splitlines():
            add_mapping(line)
    except Exception:
        pass
    return None


def get_axis_from_string(name: str) -> int:
    if name is None:
        return -1
    s = str(name).strip().lower()
    return int(_AXIS_MAP.get(s, -1))


def get_button_from_string(name: str) -> int:
    if name is None:
        return -1
    s = str(name).strip().lower()
    return int(_BUTTON_MAP.get(s, -1))


def get_string_for_axis(axis: int) -> str:
    axis = int(axis)
    if 0 <= axis < len(_AXIS_NAMES):
        return _AXIS_NAMES[axis]
    return f"axis{axis}"


def get_string_for_button(button: int) -> str:
    button = int(button)
    if 0 <= button < len(_BUTTON_NAMES):
        return _BUTTON_NAMES[button]
    return f"button{button}"


class Controller:
    """SDL_GameController wrapper over host GamepadState.

    Delegates axis/button reads to ``renpy_host.gamepad_*`` but exposes
    controller naming (``get_string_for_axis`` etc.) and rumble.
    """

    def __init__(self, id: int):
        self.id: int = int(id)
        self.instance_id: int = int(id)
        self._joy = _JoystickBase(int(id))
        self._initialized: bool = False

    def init(self) -> None:
        self._initialized = True
        try:
            self._joy.init()
        except Exception:
            pass
        return None

    def quit(self) -> None:
        self._initialized = False
        try:
            self._joy.quit()
        except Exception:
            pass
        return None

    def get_init(self) -> bool:
        return bool(self._initialized)

    def get_axis(self, axis: int) -> float:
        axis = int(axis)
        # Allow string-named axis as well
        if isinstance(axis, str):  # type: ignore
            mapped = get_axis_from_string(axis)  # type: ignore
            if mapped >= 0:
                axis = mapped
        return float(self._joy.get_axis(axis))

    def get_button(self, button) -> bool:
        if isinstance(button, str):
            mapped = get_button_from_string(button)
            if mapped >= 0:
                button = mapped
        return bool(self._joy.get_button(int(button)))

    def get_name(self) -> str:
        try:
            return self._joy.get_name()
        except Exception:
            return f"Controller {self.id}"

    def is_controller(self) -> bool:
        # All host pads are considered controllers when count>0
        return get_count() > self.id

    def get_guid(self) -> str:
        try:
            return self._joy.get_guid()
        except Exception:
            return "03000000000000000000000000000000"

    def get_guid_string(self) -> str:
        return self.get_guid()

    def rumble(self, low_frequency: float = 0.0, high_frequency: float = 0.0, duration: int = 0) -> bool:
        try:
            return bool(self._joy.rumble(low_frequency, high_frequency, duration))
        except Exception:
            return True

    def stop_rumble(self) -> None:
        try:
            self._joy.stop_rumble()
        except Exception:
            pass
        return None

    def set_rumble(self, *args, **kwargs) -> bool:
        return self.rumble(*args, **kwargs)

    # Compatibility shims for old pygame.controller API
    def get_numaxes(self) -> int:
        return int(_NUM_AXES)

    def get_numbuttons(self) -> int:
        return int(_NUM_BUTTONS)

    def get_hat(self, hat: int):
        return self._joy.get_hat(hat)

    def __repr__(self) -> str:
        return f"<Controller id={self.id} name={self.get_name()!r}>"
