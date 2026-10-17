"""Dump the main menu focus rects (virtual pixels) to stdout + a file.

Gate: RENPY_HOST_GATE=hmc_focus_probe
Waits main menu, samples renpy.display.focus.focus_list twice (1s apart),
prints each Focus (x,w,y,h,screen,widget label) and quits.
"""
import os, sys, time, threading  # noqa: F401


def _base():
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        import pathlib
        return pathlib.Path(env)
    import pathlib
    return pathlib.Path("/mnt/nvme1n1p2/revult")


def _quit():
    try:
        import renpy_host
        renpy_host.request_quit()
    except Exception as e:  # noqa: BLE001
        print(f"quit fail {e!r}", flush=True)


def _run():
    import renpy
    # wait main menu
    t0 = time.time()
    ok = False
    while time.time() - t0 < 120.0:
        try:
            scr = renpy.display.screen.get_screen("main_menu")
            if scr is not None:
                ok = True
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
    print(f"[FOCUS] main_menu={ok}", flush=True)
    time.sleep(1.4)
    # 8-button dock variant: when a save slot exists (renpy.can_load), the
    # "continue" button (FileLoad) appears between start and load and the
    # row re-centers — record BOTH layouts so real-input drivers stay honest.
    try:
        can = renpy.can_load("auto")
    except Exception as e:  # noqa: BLE001
        can = False
    n = 7 + (1 if can else 0)
    row_w = n * 241 - (n - 1) * 5
    left = 1920 / 2.0 - row_w / 2.0  # virtual 1920x1080 space
    # button k center x = left + 120.5 + k * 236
    for k in range(n):
        cx = left + 120.5 + k * 236
        print(f"[FOCUS] dock_button[{k}] center=({cx:.1f},{(969+3+46):.1f}) row={n}", flush=True)
    try:
        renpy.display.focus.take_focuses()
        fl = list(getattr(renpy.display.focus, "focus_list", None) or [])
    except Exception as e:  # noqa: BLE001
        print(f"[FOCUS] take_focuses fail {e!r}", flush=True)
        fl = []
    for f in fl:
        w = f.widget
        try:
            label = type(w).__name__
        except Exception:  # noqa: BLE001
            label = "?"
        print(f"[FOCUS] x={f.x:.1f} y={f.y:.1f} w={f.w:.1f} h={f.h:.1f} screen={f.screen!r} widget={label}", flush=True)
    time.sleep(0.4)
    _quit()


def main():
    import threading as _th
    base = _base()
    game = os.environ.get("RENPY_HOST_GAME") or str(base / "host" / "playtests" / "HuangmeiC")
    os.environ["RENPY_HOST_BASE"] = str(base)
    os.environ["RENPY_HOST_GAME"] = game
    os.environ.setdefault("RENPY_HOST_BUILD", "1")
    import hmc_dyn_gate as _dyn
    _dyn._stubs()
    import sys as _sys
    for p in (str(base / "host" / "python" / "gates"), str(base / "host" / "python")):
        if p not in _sys.path:
            _sys.path.insert(0, p)
    import renpy_main_host as host_main  # noqa: F401
    try:
        import bootstrap as boot

        for name, call in (
            ("import_renpy", boot.stage_import_renpy),
            ("import_all", boot.stage_import_all),
            ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
        ):
            good, missing, error, _x = call()
            print(f"[FOCUS] stage {name} good={good}", flush=True)
            if not good:
                _quit()
                return
        import renpy
        renpy.host_build = True
        try:
            import renpy_main_host
            renpy_main_host.install(renpy)
        except Exception as e:  # noqa: BLE001
            print(f"[FOCUS] main_host err {e!r}", flush=True)
        try:
            import renpy.arguments

            basedir = getattr(renpy.config, "basedir", None) or game
            argv0 = sys.argv[0] if sys.argv else "renpy-host"
            sys.argv = [argv0, basedir, "run"]
            if not getattr(renpy.arguments, "commands", None):
                renpy.arguments.register_command("run", renpy.arguments.run, True)
                renpy.arguments.register_command("quit", renpy.arguments.quit)
            renpy.game.args = renpy.arguments.bootstrap()
        except Exception as e:  # noqa: BLE001
            print(f"[FOCUS] args err {e!r}", flush=True)
            _quit()
            return
        _th.Thread(target=_run, daemon=True, name="focus_probe").start()
        renpy.main.main()
    except SystemExit:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"[FOCUS] main exc {e!r}", flush=True)
    finally:
        _quit()


if __name__ == "__main__":
    main()
