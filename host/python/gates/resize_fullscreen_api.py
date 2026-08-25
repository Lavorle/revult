"""Gate: host set_fullscreen / is_fullscreen / request_window_size + WgpuDraw.resize.

Proves:
  1. renpy_host exposes set_fullscreen / is_fullscreen / request_window_size.
  2. WgpuDraw.resize reads preferences-like state and calls host APIs without raising.
  3. request_window_size changes window_size (or forced drawable) away from create size.

Gate name: resize_fullscreen_api
"""

import os
from pathlib import Path

import renpy_host  # type: ignore
from renpy.wgpu.draw import WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---
try:
    from _harness import gate_harness, parametrized_gate  # type: ignore
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore
    except ImportError:
        gate_harness = None  # type: ignore
        parametrized_gate = None  # type: ignore
# fallback



def main():
    base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
    out = Path(base) / "host" / "target" / "gate-resize_fullscreen_api.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    ok = True

    has_set = hasattr(renpy_host, "set_fullscreen")
    has_is = hasattr(renpy_host, "is_fullscreen")
    has_req = hasattr(renpy_host, "request_window_size")
    lines.append(f"has_set_fullscreen={has_set}")
    lines.append(f"has_is_fullscreen={has_is}")
    lines.append(f"has_request_window_size={has_req}")
    if not (has_set and has_is and has_req):
        ok = False

    w0, h0 = renpy_host.window_size()
    lines.append(f"create_size={w0}x{h0}")

    # Enlarge via request_window_size (forced drawable path on Wayland).
    renpy_host.request_window_size(1600, 900)
    for _ in range(10):
        renpy_host.pump_once(16)
    w1, h1 = renpy_host.window_size()
    lines.append(f"after_request_1600x900={w1}x{h1}")
    size_ok = (w1, h1) == (1600, 900) or (w1 > w0 or h1 > h0)
    lines.append(f"size_ok={size_ok}")
    if not size_ok:
        ok = False

    # is_fullscreen should be callable; default is windowed.
    try:
        fs0 = bool(renpy_host.is_fullscreen())
        lines.append(f"is_fullscreen_initial={fs0}")
    except Exception as e:
        ok = False
        lines.append(f"is_fullscreen_fail={e!r}")
        fs0 = False

    # Toggle fullscreen on then off (borderless). Do not assert compositor applied
    # the mode (CI/headless may ignore), only that the call does not raise and
    # is_fullscreen remains callable.
    try:
        renpy_host.set_fullscreen(True)
        for _ in range(5):
            renpy_host.pump_once(16)
        fs1 = bool(renpy_host.is_fullscreen())
        lines.append(f"is_fullscreen_after_on={fs1}")
        renpy_host.set_fullscreen(False)
        for _ in range(5):
            renpy_host.pump_once(16)
        fs2 = bool(renpy_host.is_fullscreen())
        lines.append(f"is_fullscreen_after_off={fs2}")
        toggle_ok = True
    except Exception as e:
        ok = False
        toggle_ok = False
        lines.append(f"toggle_fail={e!r}")
    lines.append(f"toggle_ok={toggle_ok}")

    # WgpuDraw.resize path with a fake preferences object.
    draw = WgpuDraw()
    draw.init((1920, 1080))

    class _Prefs:
        fullscreen = False
        physical_size = (1280, 720)
        maximized = False

    class _Game:
        preferences = _Prefs()

    class _Iface:
        fullscreen = False

        def before_resize(self):
            return None

    class _Display:
        interface = _Iface()

    class _Renpy:
        game = _Game()
        display = _Display()
        config = type("C", (), {})()

    import sys
    import types

    # Inject a minimal renpy so resize() can import it.
    mod = types.ModuleType("renpy")
    mod.game = _Game()
    mod.display = _Display()
    mod.config = types.SimpleNamespace()
    sys.modules["renpy"] = mod
    # also renpy.display / renpy.game aliases
    sys.modules["renpy.display"] = types.ModuleType("renpy.display")
    sys.modules["renpy.display"].interface = _Display.interface if False else _Iface()
    # rebind
    mod.display = sys.modules["renpy.display"]
    mod.display.interface = _Iface()
    mod.game = _Game()

    try:
        # Windowed resize to 1280x720
        mod.game.preferences.fullscreen = False
        mod.game.preferences.physical_size = (1280, 720)
        draw.resize()
        lines.append("draw_resize_windowed=ok")
        # Fullscreen request
        mod.game.preferences.fullscreen = True
        draw.resize()
        lines.append("draw_resize_fullscreen=ok")
        # Back to windowed
        mod.game.preferences.fullscreen = False
        mod.game.preferences.physical_size = (1280, 720)
        draw.resize()
        lines.append("draw_resize_restore=ok")
        resize_ok = True
    except Exception as e:
        ok = False
        resize_ok = False
        lines.append(f"draw_resize_fail={e!r}")
    lines.append(f"resize_ok={resize_ok}")

    ok = ok and size_ok and toggle_ok and resize_ok
    lines.insert(0, f"ok={ok}")
    msg = "\n".join(lines) + "\n"
    out.write_text(msg, encoding="utf-8")
    print(msg, flush=True)
    if not ok:
        raise RuntimeError(msg)
    renpy_host.request_quit()


if __name__ == "__main__":
    main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

