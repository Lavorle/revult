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

# --- harness (thin wrapper, original logic preserved) ---
from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore


def _base():
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path("/mnt/nvme1n1p2/revult")


def _log(msg):
    try:
        sys.__stdout__.write(f"[product-prefs-inject] {msg}\n")
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        open("/tmp/product-prefs-inject.log", "a").write(msg + "\n")  # noqa: SIM115
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
        import renpy.audio as _ra
        import renpy.audio.renpysound_host as _rs_host

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        _log("renpysound rebound")
    except Exception as e:
        _log(f"renpysound soft-fail: {e}")
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
        _log(f"uguu soft-fail: {e}")
    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        _log("ecsign stub")
    except Exception as e:
        _log(f"ecsign soft-fail: {e}")


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

    import bootstrap as boot
    import renpy_host  # type: ignore

    for name, call in (
        ("import_renpy", boot.stage_import_renpy),
        ("import_all", boot.stage_import_all),
        ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ):
        good, _miss, err, _extra = call()
        rec(f"stage {name} good={good} err={err!r}")
        if not good:
            out.write_text(f"ok=False\nerror={err}\n")
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
        rec(f"main_host: {e}")

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
        rec("args command={}".format(getattr(args, "command", None)))
    except Exception as e:
        rec(f"args fail: {e}")
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
                        rec("pulse#%d prefs_screen=%r main_menu=%r" % (i, scr is not None, mm))  # noqa: UP031
                    if scr is not None:
                        rec("Preferences opened at pulse#%d" % i)  # noqa: UP031
                        state["prefs"] = True
                        break
                except Exception as e:
                    rec(f"status: {e}")
                time.sleep(0.2)
        except Exception as e:
            rec(f"inject exc: {e}")
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
        rec(f"main exit {type(e).__name__}: {e}")

    prefs_ok = bool(state.get("prefs"))
    try:
        scr = renpy.display.screen.get_screen("preferences")
        if scr is not None:
            prefs_ok = True
        rec(f"prefs_screen={scr is not None!r} prefs_ok={prefs_ok}")
    except Exception as e:
        rec(f"post-check: {e}")

    ok = bool(prefs_ok)
    body = [f"ok={ok}", "injects={}".format(state["injects"]), "phase={}".format(state["phase"]), f"prefs={prefs_ok}"]
    body.extend(lines)
    out.write_text("\n".join(body) + "\n")
    rec(f"wrote {out} ok={ok}")
    _request_quit()


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
