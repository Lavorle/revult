"""Product-path Start probe without importing product.py (it auto-runs).

Supporting only (V3): delayed inject_key/mouse during renpy.main.main.
Writes host/target/gate-product-prefs-inject.txt
"""
import os
import sys
import threading
import time
import traceback
from pathlib import Path


def _base():
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path("/mnt/nvme1n1p2/revult")


def _log(msg):
    try:
        sys.__stdout__.write("[product-prefs-inject] %s\n" % msg)
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        open("/tmp/product-prefs-inject.log", "a").write(msg + "\n")
    except Exception:
        pass


def _request_quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:
        pass


def _pre_main_host_stubs():
    """Minimal copy of product._pre_main_host_stubs (avoid importing product.py)."""
    try:
        import renpy.audio.renpysound_host as _rs_host
        import renpy.audio as _ra

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        _log("renpysound rebound")
    except Exception as e:
        _log("renpysound soft-fail: %s" % e)
    try:
        import renpy_uguu_host as _uguu

        sys.modules["renpy.uguu"] = _uguu
        sys.modules["renpy.uguu.uguu"] = _uguu
        try:
            import renpy

            renpy.uguu = _uguu
        except Exception:
            pass
        _log("uguu stub")
    except Exception as e:
        _log("uguu soft-fail: %s" % e)
    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        _log("ecsign stub")
    except Exception as e:
        _log("ecsign soft-fail: %s" % e)


def run():
    base = _base()
    out = base / "host" / "target" / "gate-product-prefs-inject.txt"
    lines = []

    def rec(m):
        lines.append(m)
        _log(m)

    os.environ.setdefault("RENPY_HOST_BASE", str(base))
    os.environ.setdefault("RENPY_HOST_BUILD", "1")
    os.environ.setdefault("RENPY_HOST_GAME", str(base / "the_question"))
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    os.environ.setdefault("RENPY_SKIP_SPLASHSCREEN", "0")
    os.environ.pop("RENPY_SKIP_MAIN_MENU", None)

    gates = str(base / "host" / "python" / "gates")
    if gates not in sys.path:
        sys.path.insert(0, gates)

    import renpy_host  # type: ignore
    import bootstrap as boot

    for name, call in (
        ("import_renpy", boot.stage_import_renpy),
        ("import_all", boot.stage_import_all),
        ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ):
        good, miss, err, extra = call()
        rec("stage %s good=%s err=%r" % (name, good, err))
        if not good:
            out.write_text("ok=False\nerror=%s\n" % err)
            _request_quit()
            return

    import renpy

    renpy.host_build = True
    try:
        renpy.config.performance_test = False
    except Exception:
        pass

    try:
        import renpy_main_host

        renpy_main_host.install(renpy)
    except Exception as e:
        rec("main_host: %s" % e)

    try:
        import renpy.arguments

        basedir = getattr(renpy.config, "basedir", None) or str(base / "the_question")
        argv0 = sys.argv[0] if sys.argv else "renpy-host"
        sys.argv = [argv0, basedir, "run"]
        if not getattr(renpy.arguments, "commands", None):
            try:
                renpy.arguments.register_command("run", renpy.arguments.run, True)
                renpy.arguments.register_command("quit", renpy.arguments.quit)
            except Exception:
                pass
        args = renpy.arguments.bootstrap()
        renpy.game.args = args
        rec("args command=%s" % getattr(args, "command", None))
    except Exception as e:
        rec("args fail: %s" % e)
        rec(traceback.format_exc())

    _pre_main_host_stubs()

    state = {"injects": 0, "phase": "boot"}

    def injector():
        time.sleep(5.0)
        state["phase"] = "injecting"
        rec("begin Downx2+Enter for Preferences")
        try:
            import renpy
            K_DOWN = 1073741905
            K_RETURN = 13
            # Navigate Start -> Load -> Preferences (2 Downs), then Enter
            for step in range(2):
                renpy_host.inject_key(K_DOWN, True, "")
                renpy_host.inject_key(K_DOWN, False, "")
                time.sleep(0.2)
            for i in range(20):
                renpy_host.inject_key(K_RETURN, True, "\r")
                renpy_host.inject_key(K_RETURN, False, "\r")
                state["injects"] += 2
                try:
                    # Preferences is a screen; detect via get_screen
                    scr = None
                    try:
                        scr = renpy.display.screen.get_screen("preferences")
                    except Exception:
                        pass
                    mm = getattr(renpy.store, "main_menu", None)
                    if i % 3 == 0:
                        rec("pulse#%d prefs_screen=%r main_menu=%r" % (i, scr is not None, mm))
                    if scr is not None:
                        rec("Preferences opened at pulse#%d" % i)
                        state["prefs"] = True
                        break
                except Exception as e:
                    rec("status: %s" % e)
                time.sleep(0.2)
        except Exception as e:
            rec("inject exc: %s" % e)
            rec(traceback.format_exc())
        time.sleep(1.5)
        state["phase"] = "quitting"
        rec("request_quit")
        _request_quit()

    threading.Thread(target=injector, daemon=True).start()

    import renpy.main as renpy_main

    rec("entering renpy.main.main()")
    try:
        renpy_main.main()
        rec("main returned")
    except BaseException as e:
        rec("main exit %s: %s" % (type(e).__name__, e))

    prefs_ok = bool(state.get("prefs"))
    try:
        scr = renpy.display.screen.get_screen("preferences")
        if scr is not None:
            prefs_ok = True
        rec("prefs_screen=%r prefs_ok=%s" % (scr is not None, prefs_ok))
    except Exception as e:
        rec("post-check: %s" % e)

    ok = bool(prefs_ok)
    body = ["ok=%s" % ok, "injects=%s" % state["injects"], "phase=%s" % state["phase"], "prefs=%s" % prefs_ok]
    body.extend(lines)
    out.write_text("\n".join(body) + "\n")
    rec("wrote %s ok=%s" % (out, ok))
    _request_quit()


run()
