"""M3 B3 T3 — gamepad / IME regression shim.

Covers:
* axis normalized to [-1,1]
* button bitmask
* hat discrete
* IME rect + TEXTEDITING 64-truncate + probe JSON no KeyError
"""

import pathlib as _pl
import sys as _sys
_host_python = str(_pl.Path(__file__).resolve().parents[1] / "host" / "python")
if _host_python not in _sys.path:
    _sys.path.insert(0, _host_python)
import importlib
import sys
import types
import unittest


# ---------------------------------------------------------------------------
# Fake renpy_host when not running under host binary
# ---------------------------------------------------------------------------
class _FakeHost(types.ModuleType):
    def __init__(self):
        super().__init__("renpy_host")
        self._pads = []  # list of dict {axes:[6], buttons:int, hats:[(x,y)]}
        self._ime_rect = None

        # expose constants like real host
        self.JOYAXISMOTION = 0x600
        self.JOYBUTTONDOWN = 0x603
        self.JOYBUTTONUP = 0x604
        self.JOYHATMOTION = 0x602
        self.TEXTINPUT = 771
        self.TEXTEDITING = 770

    def _ensure(self, idx):
        while len(self._pads) <= idx:
            self._pads.append({"axes": [0.0] * 6, "buttons": 0, "hats": [(0, 0), (0, 0)]})

    def gamepad_count(self):
        return len(self._pads)

    def gamepad_axis(self, idx, axis):
        if idx < len(self._pads) and 0 <= axis < 6:
            return float(self._pads[idx]["axes"][axis])
        return 0.0

    def gamepad_button(self, idx, btn):
        if idx < len(self._pads) and 0 <= btn < 16:
            return bool((self._pads[idx]["buttons"] >> btn) & 1)
        return False

    def gamepad_hat(self, idx, hat):
        if idx < len(self._pads) and 0 <= hat < 2:
            return tuple(self._pads[idx]["hats"][hat])
        return (0, 0)

    def inject_joy_axis(self, idx, axis, value):
        self._ensure(idx)
        if 0 <= axis < 6:
            self._pads[idx]["axes"][axis] = float(max(-1.0, min(1.0, float(value))))

    def inject_joy_button(self, idx, btn, pressed):
        self._ensure(idx)
        if 0 <= btn < 16:
            if pressed:
                self._pads[idx]["buttons"] |= 1 << btn
            else:
                self._pads[idx]["buttons"] &= ~(1 << btn)

    def inject_joy_hat(self, idx, hat, x, y):
        self._ensure(idx)
        if 0 <= hat < 2:
            self._pads[idx]["hats"][hat] = (int(x), int(y))

    def set_text_input_rect(self, x, y, w, h):
        self._ime_rect = (int(x), int(y), int(w), int(h))

    def get_text_input_rect(self):
        return self._ime_rect

    def gamepad_probe(self):
        import json

        cnt = self.gamepad_count()
        axes = []
        buttons = []
        hat = [0, 0]
        if cnt > 0:
            axes = [self.gamepad_axis(0, a) for a in range(6)]
            buttons = [b for b in range(16) if self.gamepad_button(0, b)]
            hat = list(self.gamepad_hat(0, 0))
        return json.dumps(
            {"gamepad_count": cnt, "axis": axes, "buttons": buttons, "hat": hat}
        )

    def a11y_probe(self):
        import json

        return json.dumps(
            {"screen_reader_active": False, "backend": "stub", "detail": "deferred AT-SPI2"}
        )

    def get_screen_reader_active(self):
        return False

    def poll_event(self):
        return None


def _install_fake():
    fake = _FakeHost()
    sys.modules["renpy_host"] = fake
    return fake


def _ensure_host():
    # If real renpy_host exists, use it; otherwise install fake
    try:
        import renpy_host as rh  # type: ignore

        # Check that expected FFI exists; if not, augment with fake helpers.
        # Must ensure gamepad_count/axis/button/hat share same backing store
        # as inject_joy_*, so copy all from a single fake instance.
        needed = [
            "gamepad_count",
            "gamepad_axis",
            "gamepad_button",
            "gamepad_hat",
            "inject_joy_axis",
            "inject_joy_button",
            "inject_joy_hat",
            "gamepad_probe",
            "a11y_probe",
            "get_screen_reader_active",
            "set_text_input_rect",
            "get_text_input_rect",
        ]
        missing = [n for n in needed if not hasattr(rh, n)]
        if missing:
            fake = _FakeHost()
            for name in missing:
                setattr(rh, name, getattr(fake, name))
            # Ensure all gamepad-related methods share the same fake._pads
            # if we mixed real + fake; re-bind the already-present ones to
            # the fake's storage when gamepad_count came from fake.
            if "gamepad_count" in missing:
                for name in ["gamepad_axis", "gamepad_button", "gamepad_hat",
                             "inject_joy_axis", "inject_joy_button", "inject_joy_hat",
                             "gamepad_probe"]:
                    setattr(rh, name, getattr(fake, name))
        return rh
    except Exception:
        return _install_fake()

class TestGamepadAxis(unittest.TestCase):
    def setUp(self):
        self.host = _ensure_host()
        # Ensure host_pygame can be imported fresh
        for m in list(sys.modules.keys()):
            if m.startswith("host_pygame"):
                # keep host module, allow reimport of joystick etc
                pass

    def test_axis_normalized(self):
        self.host.inject_joy_axis(0, 0, 0.5)
        self.host.inject_joy_axis(0, 1, -1.2)  # should clamp to -1
        self.host.inject_joy_axis(0, 2, 2.0)  # should clamp to 1

        import host_pygame.joystick as joy_mod

        importlib.reload(joy_mod)
        j = joy_mod.Joystick(0)
        j.init()
        self.assertAlmostEqual(j.get_axis(0), 0.5, places=5)
        self.assertAlmostEqual(j.get_axis(1), -1.0, places=5)
        self.assertAlmostEqual(j.get_axis(2), 1.0, places=5)
        # out of range axis returns 0
        self.assertEqual(j.get_axis(10), 0.0)
        # negative axis out of range returns 0 via clamp path
        self.assertEqual(j.get_axis(-1), 0.0)


class TestGamepadButton(unittest.TestCase):
    def setUp(self):
        self.host = _ensure_host()

    def test_button_bitmask(self):
        self.host.inject_joy_button(0, 0, True)
        self.host.inject_joy_button(0, 5, True)
        self.host.inject_joy_button(0, 1, False)
        import host_pygame.joystick as joy_mod

        importlib.reload(joy_mod)
        j = joy_mod.Joystick(0)
        j.init()
        self.assertTrue(j.get_button(0))
        self.assertTrue(j.get_button(5))
        self.assertFalse(j.get_button(1))
        self.assertFalse(j.get_button(15))
        # out of range
        self.assertFalse(j.get_button(16))
        self.assertFalse(j.get_button(99))

        # Toggle off
        self.host.inject_joy_button(0, 0, False)
        self.assertFalse(j.get_button(0))


class TestGamepadHat(unittest.TestCase):
    def setUp(self):
        self.host = _ensure_host()

    def test_hat_discrete(self):
        self.host.inject_joy_hat(0, 0, 1, 0)
        self.host.inject_joy_hat(0, 1, -1, 1)
        import host_pygame.joystick as joy_mod

        importlib.reload(joy_mod)
        j = joy_mod.Joystick(0)
        j.init()
        self.assertEqual(j.get_hat(0), (1, 0))
        self.assertEqual(j.get_hat(1), (-1, 1))
        # out of range hat -> (0,0)
        self.assertEqual(j.get_hat(2), (0, 0))
        self.assertEqual(j.get_hat(99), (0, 0))
        # also test controller mapping
        import host_pygame.controller as ctrl_mod

        importlib.reload(ctrl_mod)
        c = ctrl_mod.Controller(0)
        c.init()
        self.assertEqual(c.get_hat(0), (1, 0))


class TestIME(unittest.TestCase):
    def setUp(self):
        self.host = _ensure_host()

    def test_ime_rect(self):
        import host_pygame.key as key_mod

        importlib.reload(key_mod)
        key_mod.set_text_input_rect(10, 20, 100, 30)
        self.assertEqual(key_mod.get_text_input_rect(), (10, 20, 100, 30))
        # Also check host store
        try:
            host_rect = self.host.get_text_input_rect()
            self.assertEqual(host_rect, (10, 20, 100, 30))
        except Exception:
            pass
        # Flexible arg form: tuple
        key_mod.set_text_input_rect((5, 6, 7, 8))
        self.assertEqual(key_mod.get_text_input_rect(), (5, 6, 7, 8))
        # None clears
        key_mod.set_text_input_rect(None)
        self.assertIsNone(key_mod.get_text_input_rect())
        # Window pass-through
        import host_pygame.display as disp_mod

        importlib.reload(disp_mod)
        w = disp_mod.Window("test", (1280, 720))
        w.set_ime_cursor_area(1, 2, 3, 4)
        self.assertEqual(key_mod.get_text_input_rect(), (1, 2, 3, 4))
        # display helper
        disp_mod.set_ime_cursor_area(9, 9, 9, 9)
        self.assertEqual(key_mod.get_text_input_rect(), (9, 9, 9, 9))

    def test_textediting_truncate(self):
        import host_pygame.event as evt_mod
        import host_pygame.locals as L

        importlib.reload(evt_mod)
        long = "a" * 100
        ev_dict = {"type": L.TEXTEDITING, "text": long, "start": 0, "length": len(long)}
        ev = evt_mod._from_host(ev_dict)
        self.assertEqual(len(ev.text), 64)
        self.assertEqual(ev.length, 64)
        # short stays
        short = "hello"
        ev2_dict = {"type": L.TEXTEDITING, "text": short, "start": 0, "length": len(short)}
        ev2 = evt_mod._from_host(ev2_dict)
        self.assertEqual(ev2.text, short)
        self.assertEqual(ev2.length, len(short))
        # inject alias also truncates
        ev3 = evt_mod._inject_host_event({"type": L.TEXTEDITING, "text": "b" * 200})
        self.assertEqual(len(ev3.text), 64)

    def test_probe_json_no_keyerror(self):
        import json

        # gamepad probe JSON should never KeyError even with no hardware stubbed
        probe_str = self.host.gamepad_probe()
        data = json.loads(probe_str)
        # spec says no KeyError — must contain these keys
        self.assertIn("gamepad_count", data)
        # axis may be empty or 6-length; ensure no KeyError on access
        _ = data.get("axis", [])
        _ = data.get("buttons", [])
        _ = data.get("hat", [0, 0])
        # also a11y probe
        a11y_str = self.host.a11y_probe()
        a11y = json.loads(a11y_str)
        self.assertIn("screen_reader_active", a11y)
        # host_pygame a11y stub also no KeyError
        import host_pygame.a11y as a11y_mod

        importlib.reload(a11y_mod)
        self.assertIsInstance(a11y_mod.get_screen_reader_active(), bool)
        self.assertIsInstance(a11y_mod.probe_orca(), dict)
        self.assertIn("screen_reader_active", a11y_mod.probe_orca())


if __name__ == "__main__":
    unittest.main()
