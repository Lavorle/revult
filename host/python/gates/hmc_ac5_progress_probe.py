"""
HuangmeiC AC5 progress probe — Start / Continue / Load reachable.

Gate name: hmc_ac5_progress_probe  (RENPY_HOST_GATE=hmc_ac5_progress_probe)

Product path:
  1. Wait main_menu
  2. Open Load via ShowMenu → assert screen open + nonclear RT structure floor
  3. Return to main_menu
  4. Activate Start() → assert leave main_menu without hang
  5. Document Continue presence (quit-slot conditional)

Pass: Load reachable; Start leaves main_menu without hang/wipe.
Continue absent without quit slot is documented, not FAIL.

Writes:
  host/target/gate-hmc_ac5_progress_probe.txt
  /tmp/huangmeic-ab/ac5-progress-probe.log

Note: no from __future__; host run_file prepends imports.
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
    line = "[hmc_ac5] %s\n" % msg
    try:
        sys.__stdout__.write(line)
        sys.__stdout__.flush()
    except Exception:
        pass
    for p in (
        "/tmp/hmc_ac5_progress_probe.log",
        "/tmp/huangmeic-ab/ac5-progress-probe.log",
    ):
        try:
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            open(p, "a").write(msg + "\n")
        except Exception:
            pass


def _request_quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:
        pass


def _clear_falsey_skip(name):
    val = os.environ.get(name)
    if val is None:
        return
    if str(val).strip().lower() in ("", "0", "false", "no", "off", "n"):
        os.environ.pop(name, None)


def _pre_main_host_stubs():
    import types

    try:
        import renpy.audio.renpysound_host as _rs_host
        import renpy.audio as _ra

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        _log("renpysound rebound")
    except Exception as e:
        _log("renpysound soft-fail: %s" % e)

    try:
        import host_pygame
        import host_pygame.locals as _loc
        import host_pygame.scrap as _host_scrap

        if not hasattr(host_pygame, "constants"):
            host_pygame.constants = _loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules.setdefault("pygame.constants", host_pygame.constants)
        sys.modules["renpy.pygame.scrap"] = _host_scrap
        sys.modules["pygame.scrap"] = _host_scrap
        import renpy.pygame as rpg

        if not hasattr(rpg, "constants"):
            rpg.constants = host_pygame.constants
        try:
            rpg.scrap = _host_scrap
        except Exception:
            pass
        try:
            rpg.import_as_pygame()
        except Exception as e:
            _log("import_as_pygame soft-fail: %s" % e)
        _log("pygame host shim ok")
    except Exception as e:
        _log("pygame soft-fail: %s" % e)

    try:
        import renpy_uguu_host as _uguu

        sys.modules["renpy.uguu.uguu"] = _uguu
        sys.modules["renpy.uguu.gl"] = _uguu
        pkg = sys.modules.get("renpy.uguu")
        if pkg is None:
            pkg = types.ModuleType("renpy.uguu")
            pkg.__path__ = []  # type: ignore[attr-defined]
            sys.modules["renpy.uguu"] = pkg
        for _name in dir(_uguu):
            if _name.startswith("GL_") or _name in ("clear_errors", "get_error"):
                setattr(pkg, _name, getattr(_uguu, _name))
        setattr(pkg, "uguu", _uguu)
        setattr(pkg, "gl", _uguu)
        try:
            import renpy

            renpy.uguu = pkg
        except Exception:
            pass
        _log("uguu host stub installed")
    except Exception as e:
        _log("uguu soft-fail: %s" % e)

    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        try:
            import renpy as _renpy_pkg

            setattr(_renpy_pkg, "ecsign", _ecsign)
        except Exception:
            pass
        _log("ecsign host stub installed")
    except Exception as e:
        _log("ecsign soft-fail: %s" % e)


def _sample_rt():
    import renpy_host

    try:
        rw, rh, rt = renpy_host.read_game_rt_rgba()
    except Exception as e:
        return {"ok": False, "error": "read_rt:%s" % e}
    if not rw or not rh or not rt:
        return {"ok": False, "error": "empty_rt"}
    rs = gs = bs = n = 0
    step_x = max(1, rw // 32)
    step_y = max(1, rh // 18)
    for y in range(step_y // 2, rh, step_y):
        for x in range(step_x // 2, rw, step_x):
            o = (y * rw + x) * 4
            r, g, b = rt[o], rt[o + 1], rt[o + 2]
            rs += r
            gs += g
            bs += b
            n += 1
    if n == 0:
        return {"ok": False, "error": "no_samples"}
    mean = (rs / n, gs / n, bs / n)
    var = 0.0
    for y in range(step_y // 2, rh, step_y):
        for x in range(step_x // 2, rw, step_x):
            o = (y * rw + x) * 4
            var += (
                (rt[o] - mean[0]) ** 2
                + (rt[o + 1] - mean[1]) ** 2
                + (rt[o + 2] - mean[2]) ** 2
            )
    var /= float(n)
    clearish = (
        abs(mean[0] - 13) < 8
        and abs(mean[1] - 13) < 8
        and abs(mean[2] - 20) < 12
        and var < 5.0
    )
    featureless = (mean[0] + mean[1] + mean[2] < 40 and var < 80)
    return {
        "ok": (not clearish) and (not featureless) and var >= 5.0,
        "mean": mean,
        "var": var,
        "w": rw,
        "h": rh,
        "clearish": clearish,
        "featureless": featureless,
    }


def _product_present():
    """Soft product present after ShowMenu (nested frames=1)."""
    try:
        import renpy
        import interact_helpers as ih

        try:
            renpy.restart_interaction()
        except Exception:
            pass
        ready, why, iface = ih.interface_ready()
        if not ready or iface is None:
            return {"error": "iface:%s" % why}
        root = ih._rebuild_product_root(iface)
        if root is None:
            return {"error": "root_absent"}
        w = int(getattr(renpy.config, "screen_width", 1920) or 1920)
        h = int(getattr(renpy.config, "screen_height", 1080) or 1080)
        st = renpy.display.render.render_screen(root, w, h)
        draw = getattr(renpy.display, "draw", None)
        if draw is None:
            return {"error": "no_draw"}
        try:
            if hasattr(draw, "load_all_textures"):
                draw.load_all_textures(st)
        except Exception as e:
            return {"error": "prepare:%s" % e}
        draw.draw_screen(st, flip=True)
        try:
            iface.surftree = st
        except Exception:
            pass
        return {"path": "rebuild_present"}
    except Exception as e:
        return {"error": "%s:%s" % (type(e).__name__, e)}


def _get_screen(name):
    try:
        import renpy

        return renpy.display.screen.get_screen(name)
    except Exception:
        return None


def run():
    base = _base()
    out = base / "host" / "target" / "gate-hmc_ac5_progress_probe.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    state = {
        "phase": "boot",
        "main_menu": False,
        "load_opened": False,
        "load_rt_ok": False,
        "load_mean": None,
        "start_left": False,
        "start_via": None,
        "continue_present": None,
        "continue_can_load": None,
        "errors": [],
        "t0": time.time(),
        "first_focus_s": None,
        "t_decode": None,
    }

    def rec(m):
        lines.append(m)
        _log(m)

    game = os.environ.get("RENPY_HOST_GAME") or str(
        base / "host" / "playtests" / "HuangmeiC"
    )
    os.environ["RENPY_HOST_BASE"] = str(base)
    os.environ["RENPY_HOST_BUILD"] = "1"
    os.environ["RENPY_HOST_GAME"] = game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    _clear_falsey_skip("RENPY_SKIP_MAIN_MENU")
    _clear_falsey_skip("RENPY_SKIP_SPLASHSCREEN")

    gates = str(base / "host" / "python" / "gates")
    host_py = str(base / "host" / "python")
    if gates not in sys.path:
        sys.path.insert(0, gates)
    if host_py not in sys.path:
        sys.path.insert(0, host_py)

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
            out.write_text("gate=hmc_ac5_progress_probe\nok=False\nerror=%s\n" % err)
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
        rec("main_host installed")
    except Exception as e:
        rec("main_host: %s" % e)

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
        args = renpy.arguments.bootstrap()
        renpy.game.args = args
        rec("args command=%s basedir=%s" % (getattr(args, "command", None), basedir))
    except Exception as e:
        rec("args fail: %s" % e)
        rec(traceback.format_exc())

    _pre_main_host_stubs()

    def injector():
        rec("waiting main_menu")
        t_wait0 = time.time()
        for i in range(400):
            try:
                if bool(getattr(renpy.store, "main_menu", False)):
                    state["main_menu"] = True
                    state["first_focus_s"] = time.time() - t_wait0
                    rec("main_menu at tick=%d first_focus_s=%.3f" % (i, state["first_focus_s"]))
                    break
            except Exception:
                pass
            time.sleep(0.05)
        if not state["main_menu"]:
            state["errors"].append("main_menu_timeout")
            rec("main_menu timeout")
            _request_quit()
            return

        # Capture T_decode if phase0 signals exposed it.
        try:
            import renpy.audio.renpysound_host as rsh

            td = getattr(rsh, "_last_t_decode", None) or getattr(
                rsh, "LAST_T_DECODE", None
            )
            if td is not None:
                state["t_decode"] = float(td)
                rec("t_decode=%s" % td)
        except Exception as e:
            rec("t_decode soft: %s" % e)

        time.sleep(1.5)

        # --- Continue presence (document only) ---
        state["phase"] = "continue_check"
        try:
            quit_slot = getattr(renpy.store, "_quit_slot", None)
            can = False
            if quit_slot is not None:
                try:
                    # renpy.exports.can_load (not module attr on some host builds)
                    can_load = getattr(renpy, "can_load", None)
                    if can_load is None:
                        import renpy.exports as _ex

                        can_load = getattr(_ex, "can_load", None)
                    if can_load is not None:
                        can = bool(can_load(quit_slot))
                    else:
                        rec("can_load unavailable; continue treated absent")
                except Exception as e:
                    rec("can_load err: %s" % e)
            state["continue_can_load"] = can
            state["continue_present"] = can
            rec("continue present=%s quit_slot=%r" % (can, quit_slot))
        except Exception as e:
            rec("continue check: %s" % e)
            state["continue_present"] = False

        # --- Load path ---
        state["phase"] = "load"
        rec("=== AC5 Load ===")
        try:
            renpy.store.ShowMenu("load")()
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            opened = False
            for j in range(40):
                if _get_screen("load") is not None:
                    opened = True
                    break
                time.sleep(0.1)
            state["load_opened"] = opened
            rec("load opened=%s" % opened)
            time.sleep(0.4)
            pinfo = _product_present()
            rec("load present %s" % pinfo)
            time.sleep(0.15)
            rt = _sample_rt()
            state["load_rt_ok"] = bool(rt.get("ok"))
            state["load_mean"] = rt.get("mean")
            rec(
                "load rt ok=%s mean=%s var=%.1f"
                % (rt.get("ok"), tuple(round(x, 1) for x in (rt.get("mean") or (0, 0, 0))), float(rt.get("var") or 0))
            )
            # Return
            try:
                renpy.store.Return()()
            except Exception:
                try:
                    renpy.display.screen.hide_screen("load")
                except Exception:
                    pass
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            time.sleep(0.6)
            # Wait main_menu again
            for j in range(40):
                if bool(getattr(renpy.store, "main_menu", False)):
                    rec("back to main_menu after load")
                    break
                time.sleep(0.1)
        except Exception as e:
            state["errors"].append("load:%s" % e)
            rec("load exc: %s" % e)
            rec(traceback.format_exc())

        # --- Start path ---
        state["phase"] = "start"
        rec("=== AC5 Start ===")
        try:
            # Prefer product Start() action; fallback Enter on seeded focus.
            left = False
            via = None
            try:
                renpy.store.Start()()
                via = "Start()"
                rec("invoked Start()")
            except Exception as e1:
                rec("Start() fail: %s — try inject Enter" % e1)
                try:
                    K_RETURN = 13
                    for i in range(20):
                        renpy_host.inject_key(K_RETURN, True, "\r")
                        renpy_host.inject_key(K_RETURN, False, "\r")
                        time.sleep(0.15)
                        if not bool(getattr(renpy.store, "main_menu", True)):
                            left = True
                            via = "inject_key_Enter"
                            break
                    if not left:
                        via = "inject_key_Enter_no_leave"
                except Exception as e2:
                    via = "fail:%s|%s" % (e1, e2)
                    state["errors"].append("start:%s" % via)

            # Poll leave main_menu
            for j in range(60):
                try:
                    if not bool(getattr(renpy.store, "main_menu", True)):
                        left = True
                        break
                except Exception:
                    pass
                time.sleep(0.1)
            state["start_left"] = left
            state["start_via"] = via
            rec("start left_main_menu=%s via=%s" % (left, via))
            if left:
                # Brief settle — no hang means we can still quit cleanly.
                time.sleep(1.0)
                try:
                    ctx = renpy.game.context()
                    rec("context.current=%r" % (getattr(ctx, "current", None),))
                except Exception as e:
                    rec("context soft: %s" % e)
                # Sample RT is not featureless black (no wipe)
                rt = _sample_rt()
                rec(
                    "post_start rt ok=%s mean=%s var=%.1f"
                    % (
                        rt.get("ok"),
                        tuple(round(x, 1) for x in (rt.get("mean") or (0, 0, 0))),
                        float(rt.get("var") or 0),
                    )
                )
                state["post_start_rt_ok"] = bool(rt.get("ok"))
            else:
                state["errors"].append("start_did_not_leave_main_menu")
        except Exception as e:
            state["errors"].append("start:%s" % e)
            rec("start exc: %s" % e)
            rec(traceback.format_exc())

        state["phase"] = "done"
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

    main_ok = bool(state["main_menu"])
    load_ok = bool(state["load_opened"] and state["load_rt_ok"])
    start_ok = bool(state["start_left"])
    ok = main_ok and load_ok and start_ok

    mean_s = "None"
    if state["load_mean"]:
        mean_s = str(tuple(round(x, 1) for x in state["load_mean"]))
    ffs = (
        "{:.3f}".format(state["first_focus_s"])
        if state["first_focus_s"] is not None
        else "None"
    )
    body = [
        "gate=hmc_ac5_progress_probe",
        "ok={}".format(ok),
        "ac=AC5_progress",
        "main_menu={}".format(main_ok),
        "load_opened={}".format(state["load_opened"]),
        "load_rt_ok={}".format(state["load_rt_ok"]),
        "load_mean={}".format(mean_s),
        "start_left={}".format(state["start_left"]),
        "start_via={}".format(state["start_via"]),
        "post_start_rt_ok={}".format(state.get("post_start_rt_ok")),
        "continue_present={}".format(state["continue_present"]),
        "continue_can_load={}".format(state["continue_can_load"]),
        "first_focus_s={}".format(ffs),
        "t_decode={}".format(state["t_decode"]),
        "phase={}".format(state["phase"]),
        "errors={}".format(
            ";".join(state["errors"]) if state["errors"] else "none"
        ),
        "notes=Load_ShowMenu_structure;Start_leaves_main_menu;Continue_conditional_documented",
    ]
    body.extend(["log.{}".format(ln) for ln in lines[-80:]])
    text = "\n".join(body) + "\n"
    try:
        out.write_text(text)
    except Exception as e:
        rec("gate write fail: {}".format(e))
    rec(
        "wrote {} ok={} load={} start={}".format(
            str(out), ok, load_ok, start_ok
        )
    )
    try:
        Path("/tmp/huangmeic-ab/ac5-progress-gate.txt").write_text(text)
    except Exception:
        pass
    try:
        _request_quit()
    except Exception:
        pass


run()
