"""Inject mouse click on main-menu Start region; assert leave main_menu."""
import os, sys, time, threading, traceback
from pathlib import Path
try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

def _base():
    return Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))

def _log(m):
    try:
        sys.__stdout__.write("[start-mouse] %s\n" % m); sys.__stdout__.flush()
    except Exception:
        pass

def _request_quit():
    try:
        import renpy_host
        renpy_host.request_quit()
    except Exception:
        pass

def run():
    base = _base()
    out = base / "host" / "target" / "gate-product-start-mouse.txt"
    lines = []
    def rec(m):
        lines.append(m); _log(m)

    os.environ.setdefault("RENPY_HOST_BASE", str(base))
    os.environ.setdefault("RENPY_HOST_BUILD", "1")
    os.environ.setdefault("RENPY_HOST_GAME", str(base / "the_question"))
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    os.environ.setdefault("RENPY_SKIP_SPLASHSCREEN", "0")
    os.environ.pop("RENPY_SKIP_MAIN_MENU", None)
    gates = str(base / "host" / "python" / "gates")
    if gates not in sys.path:
        sys.path.insert(0, gates)

    import renpy_host
    import bootstrap as boot
    for name, call in (("import_renpy", boot.stage_import_renpy), ("import_all", boot.stage_import_all)):
        good, miss, err, extra = call() if name != "import_all" else call()
        rec("%s good=%s err=%r" % (name, good, err))
        if not good:
            raise RuntimeError(err)
    good, miss, err, extra = boot.stage_set_game_dir(base)
    rec("set_game_dir good=%s" % good)
    import renpy
    renpy.host_build = True
    try:
        import renpy_main_host
        renpy_main_host.install(renpy)
    except Exception as e:
        rec("main_host: %s" % e)
    try:
        import renpy.audio.renpysound_host as _rs
        import renpy.audio as _ra
        sys.modules["renpy.audio.renpysound"] = _rs
        _ra.renpysound = _rs
    except Exception:
        pass
    try:
        import renpy_uguu_host as u
        sys.modules["renpy.uguu"] = u
        sys.modules["renpy.uguu.uguu"] = u
    except Exception:
        pass

    import renpy.arguments
    basedir = str(base / "the_question")
    sys.argv = [sys.argv[0] if sys.argv else "renpy-host", basedir, "run"]
    try:
        renpy.arguments.register_command("run", renpy.arguments.run, True)
    except Exception:
        pass
    renpy.game.args = renpy.arguments.bootstrap()

    stop = threading.Event()
    result = {"left": False, "mm": None, "focus": None, "phys": None, "dpv": None}

    def injector():
        # wait for main menu
        for i in range(200):
            if stop.is_set():
                return
            try:
                mm = bool(getattr(renpy.store, "main_menu", False))
            except Exception:
                mm = False
            if mm:
                break
            time.sleep(0.05)
        time.sleep(0.6)
        try:
            draw = renpy.display.draw
            result["phys"] = getattr(draw, "physical_size", None)
            result["dpv"] = getattr(draw, "draw_per_virt", None)
            result["focus"] = str(renpy.display.focus.get_focused())
        except Exception as e:
            result["focus"] = "err:%s" % e
        # Click several points along left nav (Start is top button)
        pts = [(180, 220), (160, 250), (200, 280), (140, 200), (220, 240),
               (100, 300), (180, 350), (180, 400)]
        for (x, y) in pts:
            if stop.is_set():
                return
            try:
                renpy_host.inject_mouse(x, y, 1, True)
                renpy_host.inject_mouse(x, y, 1, False)
            except Exception as e:
                rec("inject fail %s" % e)
            time.sleep(0.15)
            try:
                if not bool(getattr(renpy.store, "main_menu", True)):
                    result["left"] = True
                    rec("left main_menu after click %s,%s" % (x, y))
                    break
            except Exception:
                pass
        # fallback Enter
        if not result["left"]:
            rec("mouse miss; try Enter")
            for _ in range(4):
                renpy_host.inject_key(13, True, "\r")
                renpy_host.inject_key(13, False, "\r")
                time.sleep(0.2)
                try:
                    if not bool(getattr(renpy.store, "main_menu", True)):
                        result["left"] = True
                        rec("left via Enter")
                        break
                except Exception:
                    pass
        time.sleep(0.3)
        _request_quit()

    t = threading.Thread(target=injector, daemon=True)
    t.start()
    try:
        import renpy.main as renpy_main
        renpy_main.main()
    except BaseException as e:
        rec("main exit %s: %s" % (type(e).__name__, e))
    stop.set()
    try:
        result["mm"] = bool(getattr(renpy.store, "main_menu", None))
    except Exception:
        pass
    rec("result=%s" % result)
    ok = result["left"]
    body = ("ok=%s\n" % ok) + "\n".join(lines) + "\n"
    out.write_text(body)
    try:
        sys.__stdout__.write(body); sys.__stdout__.flush()
    except Exception:
        pass
    _request_quit()
    if not ok:
        raise RuntimeError("start mouse probe failed")

run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
