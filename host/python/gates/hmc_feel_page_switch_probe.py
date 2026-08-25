"""Micro probe: prefs page switch cold/warm first_interactive.

Gate: RENPY_HOST_GATE=hmc_feel_page_switch_probe
"""

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback


def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")


def _log(msg):
    try:
        open("/tmp/hmc_feel_page_switch_probe.log", "a").write(str(msg) + "\n")  # noqa: SIM115
    except Exception:
        pass
    try:
        sys.__stdout__.write(f"[page_switch] {msg}\n")
        sys.__stdout__.flush()
    except Exception:
        pass


def _quit():
    try:
        import renpy_host
        renpy_host.request_quit()
    except Exception:
        pass


def _clear_falsey(name):
    val = os.environ.get(name)
    if val is not None and str(val).strip().lower() in ("", "0", "false", "no", "off", "n"):
        os.environ.pop(name, None)


def _stubs():
    import types
    try:
        import renpy.audio as a
        import renpy.audio.renpysound_host as h
        sys.modules["renpy.audio.renpysound"] = h
        a.renpysound = h
    except Exception as e:
        _log(f"sound {e}")
    try:
        import host_pygame
        import host_pygame.locals as loc
        from host_pygame import scrap
        if not hasattr(host_pygame, "constants"):
            host_pygame.constants = loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules["renpy.pygame.scrap"] = scrap
        sys.modules["pygame.scrap"] = scrap
        import renpy.pygame as rpg
        if not hasattr(rpg, "constants"):
            rpg.constants = host_pygame.constants
        try:
            rpg.scrap = scrap
        except Exception:
            pass
        try:
            rpg.import_as_pygame()
        except Exception:
            pass
    except Exception as e:
        _log(f"pygame {e}")
    try:
        import renpy_uguu_host as u
        sys.modules["renpy.uguu.uguu"] = u
        sys.modules["renpy.uguu.gl"] = u
        pkg = sys.modules.get("renpy.uguu") or types.ModuleType("renpy.uguu")
        pkg.__path__ = []
        sys.modules["renpy.uguu"] = pkg
        for n in dir(u):
            if n.startswith("GL_") or n in ("clear_errors", "get_error"):
                setattr(pkg, n, getattr(u, n))
        pkg.uguu = u
        pkg.gl = u
        import renpy
        renpy.uguu = pkg
    except Exception as e:
        _log(f"uguu {e}")
    try:
        import renpy_ecsign_host as e
        sys.modules["renpy.ecsign"] = e
        import renpy
        renpy.ecsign = e
    except Exception as e:
        _log(f"ecsign {e}")


def _pp():
    import renpy_host
    if hasattr(renpy_host, "product_presents"):
        return int(renpy_host.product_presents())
    return -1


def _show(kind):
    import renpy
    t0 = time.monotonic()
    renpy.display.screen.show_screen("preferences", kind=kind)
    try:
        renpy.restart_interaction()
    except Exception:
        pass
    try:
        iface = renpy.game.interface
        if iface is not None:
            iface.force_redraw = True
            iface.restart_interaction = True
    except Exception:
        pass
    return (time.monotonic() - t0) * 1000.0


def _wait_present_after(p0, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        p = _pp()
        if p > p0:
            return (time.monotonic() - t0) * 1000.0, p - p0
        time.sleep(0.002)
    return None, 0


def _measure(kind, label):
    import renpy
    p0 = _pp()
    t_action = time.monotonic()
    show_ms = _show(kind)
    wait_ms, dlt = _wait_present_after(p0)
    total = (time.monotonic() - t_action) * 1000.0
    live = renpy.display.screen.get_screen("preferences") is not None
    return {
        "label": label,
        "kind": kind,
        "show_screen_ms": round(show_ms, 3),
        "wait_present_ms": None if wait_ms is None else round(wait_ms, 3),
        "total_ms": round(total, 3),
        "presents_delta": dlt,
        "prefs_live": live,
    }


def probe():
    import renpy
    out = _base() / "host" / "target" / "gate-hmc_feel_page_switch_probe.json"
    report = {"gate": "hmc_feel_page_switch_probe", "runs": []}
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            if bool(getattr(renpy.store, "main_menu", False)):
                break
        except Exception:
            pass
        time.sleep(0.2)
    report["runs"].append(_measure("sound_config", "open_sound"))
    time.sleep(0.3)
    report["runs"].append(_measure("text_config", "to_text"))
    time.sleep(0.2)
    report["runs"].append(_measure("dialog_config_1", "to_dialog_cold"))
    time.sleep(0.2)
    report["runs"].append(_measure("sound_config", "back_sound"))
    time.sleep(0.2)
    report["runs"].append(_measure("dialog_config_1", "to_dialog_warm"))
    time.sleep(0.2)
    report["runs"].append(_measure("keyboard_config", "to_keyboard"))
    time.sleep(0.2)
    report["runs"].append(_measure("dialog_config_1", "to_dialog_again"))
    time.sleep(0.2)
    report["runs"].append(_measure("image_config", "to_image"))
    for r in report["runs"]:
        _log(r)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _log(f"wrote {out}")
    time.sleep(0.2)
    _quit()


def main():
    open("/tmp/hmc_feel_page_switch_probe.log", "w").write("start\n")  # noqa: SIM115
    base = _base()
    game = os.environ.get("RENPY_HOST_GAME") or str(base / "host" / "playtests" / "HuangmeiC")
    os.environ["RENPY_HOST_BASE"] = str(base)
    os.environ["RENPY_HOST_BUILD"] = "1"
    os.environ["RENPY_HOST_GAME"] = game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    os.environ.setdefault("RENPY_HOST_PHASE0_SIGNALS", "1")
    _clear_falsey("RENPY_SKIP_MAIN_MENU")
    _clear_falsey("RENPY_SKIP_SPLASHSCREEN")
    for path in (str(base / "host" / "python" / "gates"), str(base / "host" / "python")):
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        _stubs()
        import bootstrap as boot
        for name, call in (
            ("import_renpy", boot.stage_import_renpy),
            ("import_all", boot.stage_import_all),
            ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
        ):
            good, _missing, _error, _extra = call()
            _log(f"stage {name} good={good}")
            if not good:
                _quit()
                return
        import renpy
        renpy.host_build = True
        try:
            import renpy_main_host
            renpy_main_host.install(renpy)
        except Exception as e:
            _log(f"main_host {e}")
        try:
            import renpy.arguments
            basedir = getattr(renpy.config, "basedir", None) or game
            argv0 = sys.argv[0] if sys.argv else "renpy-host"
            sys.argv = [argv0, basedir, "run"]
            if not getattr(renpy.arguments, "commands", None):
                try:
                    renpy.arguments.register_command("run", renpy.arguments.run, True)
                    renpy.arguments.register_command("quit", renpy.arguments.quit)
                except Exception:
                    pass
            renpy.game.args = renpy.arguments.bootstrap()
        except Exception as e:
            _log(f"args {e}")
            _quit()
            return
        threading.Thread(target=probe, daemon=True).start()
        try:
            renpy.main.main()
        except SystemExit:
            pass
        except Exception as e:
            _log(f"main {e}")
            _log(traceback.format_exc())
        finally:
            _quit()
    except Exception as e:
        _log(f"outer {e}")
        _log(traceback.format_exc())
        _quit()


if __name__ == "__main__":
    main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
