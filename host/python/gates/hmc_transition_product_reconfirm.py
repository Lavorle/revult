"""
HuangmeiC product transition reconfirm (AC-X1–X5 entry helper).

Gate name: hmc_transition_product_reconfirm
  (RENPY_HOST_GATE=hmc_transition_product_reconfirm)

Boots product HuangmeiC path, leaves main_menu via Start inject, enables
prefs.transitions=2, hooks Dissolve / Fade / ImageDissolve.render, and
records mid-completes + RT samples.

Authority: human full-screen interact remains AC authority for AC-X1–X5.
This gate is ENTRY reconfirm evidence before transition surgery (reopen Step 4).

Writes:
  host/target/gate-hmc_transition_product_reconfirm.txt
  .omc/artifacts/huangmeic-visual-residual-reopen-20260718e/gates/hmc_transition_product_reconfirm.txt

Note: no from __future__; host run_file prepends imports.
"""

import os
import sys
import threading
import time
import traceback
from pathlib import Path

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


def _base():
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path("/mnt/nvme1n1p2/revult")


def _log(msg):
    try:
        sys.__stdout__.write(f"[hmc_tx_reconfirm] {msg}\n")
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        open("/tmp/hmc_transition_product_reconfirm.log", "a").write(msg + "\n")  # noqa: SIM115
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
        import renpy.audio as _ra
        import renpy.audio.renpysound_host as _rs_host

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        _log("renpysound rebound")
    except Exception as e:
        _log(f"renpysound soft-fail: {e}")

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
            _log(f"import_as_pygame soft-fail: {e}")
        _log("pygame host shim ok")
    except Exception as e:
        _log(f"pygame soft-fail: {e}")

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
        pkg.uguu = _uguu
        pkg.gl = _uguu
        try:
            import renpy

            renpy.uguu = pkg
        except Exception:
            pass
        _log("uguu host stub installed")
    except Exception as e:
        _log(f"uguu soft-fail: {e}")

    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        try:
            import renpy as _renpy_pkg

            _renpy_pkg.ecsign = _ecsign
        except Exception:
            pass
        _log("ecsign host stub installed")
    except Exception as e:
        _log(f"ecsign soft-fail: {e}")


def _force_product_redraw():
    import interact_helpers as ih

    import renpy

    info = {"path": None, "error": None}
    try:
        ready, why, iface = ih.interface_ready()
        if not ready or iface is None:
            info["error"] = f"iface:{why}"
            return info
        root = ih._rebuild_product_root(iface)
        if root is None:
            info["error"] = "root_absent"
            return info
        w = int(getattr(renpy.config, "screen_width", 1920) or 1920)
        h = int(getattr(renpy.config, "screen_height", 1080) or 1080)
        surftree = renpy.display.render.render_screen(root, w, h)
        draw = getattr(renpy.display, "draw", None)
        if draw is None or not hasattr(draw, "draw_screen"):
            info["error"] = "no_draw"
            return info
        draw.draw_screen(surftree, flip=True)
        try:
            iface.surftree = surftree
        except Exception:
            pass
        info["path"] = "rebuild_render_screen"
        return info
    except Exception as e:
        info["error"] = f"{type(e).__name__}:{e}"
        return info


def _sample_rt():
    import renpy_host

    pres = _force_product_redraw()
    if pres.get("error"):
        try:
            import interact_helpers as ih

            pres2 = ih.ensure_frame_present(force=True)
            pres = {
                "path": "fallback:{}".format(pres2.get("path")),
                "error": pres.get("error"),
                "fallback_error": pres2.get("error"),
            }
        except Exception as e:
            pres = {"path": None, "error": "{}|fallback:{}".format(pres.get("error"), e)}

    try:
        rw, rh, rt = renpy_host.read_game_rt_rgba()
    except Exception as e:
        return {"ok": False, "error": f"read_rt:{e}", "present": pres}
    if not rw or not rh or not rt:
        return {"ok": False, "error": "empty_rt", "present": pres}

    rs = gs = bs = n = pure = 0
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
            if r < 8 and g < 8 and b < 8:
                pure += 1
    if n == 0:
        return {"ok": False, "error": "no_samples", "present": pres}
    mean = (rs / n, gs / n, bs / n)
    pure_frac = pure / float(n)
    featureless = pure_frac > 0.92 and (mean[0] + mean[1] + mean[2]) < 24
    return {
        "ok": True,
        "w": rw,
        "h": rh,
        "mean": mean,
        "pure_frac": pure_frac,
        "featureless_black": featureless,
        "present": pres,
    }


def run():
    base = _base()
    out = base / "host" / "target" / "gate-hmc_transition_product_reconfirm.txt"
    art = (
        base
        / ".omc"
        / "artifacts"
        / "huangmeic-visual-residual-reopen-20260718e"
        / "gates"
        / "hmc_transition_product_reconfirm.txt"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    art.parent.mkdir(parents=True, exist_ok=True)
    lines = []

    def rec(m):
        lines.append(m)
        _log(m)

    game = os.environ.get("RENPY_HOST_GAME") or str(base / "host" / "playtests" / "HuangmeiC")
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
            body = f"gate=hmc_transition_product_reconfirm\nok=False\nerror={err}\n"
            out.write_text(body)
            art.write_text(body)
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
        rec(f"main_host: {e}")

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
        rec("args command={} basedir={}".format(getattr(args, "command", None), basedir))
    except Exception as e:
        rec(f"args fail: {e}")
        rec(traceback.format_exc())

    _pre_main_host_stubs()

    state = {
        "phase": "boot",
        "main_menu": False,
        "left_main_menu": False,
        "started": False,
        "injects": 0,
        "transitions_pref": None,
        "dissolve_completes": [],
        "fade_completes": [],
        "imagedissolve_completes": [],
        "dissolve_mid": 0,
        "fade_mid": 0,
        "imagedissolve_mid": 0,
        "hook_dissolve": False,
        "hook_fade": False,
        "hook_imagedissolve": False,
        "rt_samples": [],
        "error": "",
    }

    def _record(kind, st, complete, time_attr, u=None):
        entry = {
            "kind": kind,
            "st": float(st),
            "complete": float(complete) if complete is not None else None,
            "time": float(time_attr) if time_attr is not None else None,
            "u": float(u) if u is not None else None,
            "t": time.time(),
        }
        key = {
            "dissolve": "dissolve_completes",
            "fade": "fade_completes",
            "imagedissolve": "imagedissolve_completes",
        }[kind]
        state[key].append(entry)
        c = entry["complete"]
        if c is not None and 0.15 < c < 0.85:
            mid_key = {
                "dissolve": "dissolve_mid",
                "fade": "fade_mid",
                "imagedissolve": "imagedissolve_mid",
            }[kind]
            state[mid_key] = int(state[mid_key]) + 1
        if len(state[key]) <= 40:
            rec(
                "{}.render st={:.4f} complete={} u={} T={}".format(kind, entry["st"], entry["complete"], entry["u"], entry["time"])
            )

    try:
        import renpy.display.transition as tr

        # Dissolve / ImageDissolve are Transition subclasses with .render.
        # Fade is a factory function returning MultipleTransition — hook factory.
        if hasattr(tr, "Dissolve") and hasattr(tr.Dissolve, "render"):
            _orig_d = tr.Dissolve.render

            def _hook_d(self, width, height, st, at):
                rv = _orig_d(self, width, height, st, at)
                try:
                    complete = getattr(rv, "operation_complete", None)
                    if complete is None and getattr(self, "time", 0):
                        complete = min(1.0, float(st) / float(self.time))
                    uniforms = getattr(rv, "uniforms", None) or {}
                    u = uniforms.get("u_renpy_dissolve") if isinstance(uniforms, dict) else None
                    _record("dissolve", st, complete, getattr(self, "time", None), u)
                except Exception as e:
                    rec(f"dissolve hook soft: {e}")
                return rv

            tr.Dissolve.render = _hook_d  # type: ignore
            state["hook_dissolve"] = True
            rec("Dissolve.render hook installed")

        if hasattr(tr, "Fade") and callable(tr.Fade):
            _orig_fade_factory = tr.Fade

            def _hook_fade_factory(*args, **kwargs):
                tx = _orig_fade_factory(*args, **kwargs)
                try:
                    # MultipleTransition / composed: mark for mid tracking via Dissolve hooks
                    # also wrap .render if present on returned object
                    if hasattr(tx, "render"):
                        _orig_tx_render = tx.render

                        def _hook_fade_render(self, width, height, st, at, _orig=_orig_tx_render):
                            rv = _orig(self, width, height, st, at)
                            try:
                                complete = getattr(rv, "operation_complete", None)
                                if complete is None:
                                    # estimate from out+hold+in times if available
                                    total = 0.0
                                    for a in args[:3]:
                                        try:
                                            total += float(a)
                                        except Exception:
                                            pass
                                    if total > 0:
                                        complete = min(1.0, float(st) / total)
                                _record("fade", st, complete, None, None)
                            except Exception as e:
                                rec(f"fade render soft: {e}")
                            return rv

                        # bind as method-like on instance
                        import types

                        tx.render = types.MethodType(_hook_fade_render, tx)  # type: ignore
                    state["hook_fade"] = True
                except Exception as e:
                    rec(f"fade factory wrap soft: {e}")
                return tx

            tr.Fade = _hook_fade_factory  # type: ignore
            rec("Fade factory hook installed")

        if hasattr(tr, "ImageDissolve") and hasattr(tr.ImageDissolve, "render"):
            _orig_i = tr.ImageDissolve.render

            def _hook_i(self, width, height, st, at):
                rv = _orig_i(self, width, height, st, at)
                try:
                    complete = getattr(rv, "operation_complete", None)
                    if complete is None and getattr(self, "time", 0):
                        complete = min(1.0, float(st) / float(self.time))
                    uniforms = getattr(rv, "uniforms", None) or {}
                    u = None
                    if isinstance(uniforms, dict):
                        u = uniforms.get("u_renpy_dissolve") or uniforms.get(
                            "u_renpy_dissolve_offset"
                        )
                    _record("imagedissolve", st, complete, getattr(self, "time", None), u)
                except Exception as e:
                    rec(f"imagedissolve hook soft: {e}")
                return rv

            tr.ImageDissolve.render = _hook_i  # type: ignore
            state["hook_imagedissolve"] = True
            rec("ImageDissolve.render hook installed")
    except Exception as e:
        state["error"] = f"hook_fail:{e}"
        rec(state["error"])
        rec(traceback.format_exc())

    def injector():
        try:
            rec(f"waiting main_menu game={game}")
            for i in range(500):
                try:
                    if bool(getattr(renpy.store, "main_menu", False)):
                        state["main_menu"] = True
                        rec("main_menu at tick=%d" % i)  # noqa: UP031
                        break
                except Exception:
                    pass
                time.sleep(0.05)
            if not state["main_menu"]:
                state["error"] = (state["error"] + "|main_menu_timeout").strip("|")
                rec("main_menu timeout")
                _request_quit()
                return

            time.sleep(2.5)
            try:
                prefs = getattr(renpy.game, "preferences", None)
                if prefs is not None and hasattr(prefs, "transitions"):
                    cur = int(getattr(prefs, "transitions", -1) or -1)
                    if cur < 2:
                        prefs.transitions = 2
                        rec(f"set prefs.transitions {cur} -> 2")
                    state["transitions_pref"] = int(prefs.transitions)
                # Do not force text_cps=0 — leave product timing
                try:
                    renpy.game.less_updates = False
                except Exception:
                    pass
                try:
                    import renpy.display.render as rm

                    rm.models = True
                except Exception:
                    pass
            except Exception as e:
                rec(f"prefs soft: {e}")

            state["phase"] = "injecting_start"
            rec("begin Start inject (Enter only)")
            K_RETURN = 13
            for i in range(40):
                renpy_host.inject_key(K_RETURN, True, "\r")
                renpy_host.inject_key(K_RETURN, False, "\r")
                state["injects"] = int(state["injects"]) + 2
                try:
                    mm = getattr(renpy.store, "main_menu", None)
                    if i % 5 == 0:
                        rec(
                            "pulse#%d main_menu=%r d_mid=%s f_mid=%s i_mid=%s"  # noqa: UP031
                            % (
                                i,
                                mm,
                                state["dissolve_mid"],
                                state["fade_mid"],
                                state["imagedissolve_mid"],
                            )
                        )
                    if mm is False:
                        state["left_main_menu"] = True
                        state["started"] = True
                        rec("left main_menu at pulse#%d" % i)  # noqa: UP031
                        break
                except Exception as e:
                    rec(f"status: {e}")
                time.sleep(0.25)

            if not state["left_main_menu"]:
                state["phase"] = "start_fallback"
                rec("Enter failed; trying behavior.run(Start) / activate_main_menu_start")
                try:
                    import interact_helpers as ih

                    try:
                        ih.activate_main_menu_start("start")
                    except Exception as e:
                        rec(f"activate_main_menu_start: {type(e).__name__}:{e}")
                    time.sleep(1.0)
                    mm = getattr(renpy.store, "main_menu", None)
                    if mm is False:
                        state["left_main_menu"] = True
                        state["started"] = True
                        rec("left main_menu via activate_main_menu_start")
                except Exception as e:
                    rec(f"start fallback activate: {e}")
                if not state["left_main_menu"]:
                    try:
                        import renpy.game as rgame
                        from renpy.display import behavior

                        Start = getattr(renpy.store, "Start", None)
                        if Start is not None:
                            try:
                                behavior.run(Start())
                                rec("behavior.run(Start()) invoked")
                            except rgame.CONTROL_EXCEPTIONS:
                                rec("behavior.run(Start) raised CONTROL (expected)")
                                raise
                        else:
                            raise rgame.JumpOutException("start")
                    except Exception as e:
                        rec(f"start fallback behavior: {e}")
                    time.sleep(1.0)
                    try:
                        mm = getattr(renpy.store, "main_menu", None)
                        if mm is False:
                            state["left_main_menu"] = True
                            state["started"] = True
                            rec("left main_menu via behavior.run fallback")
                    except Exception as e:
                        rec(f"post-fallback status: {e}")

            state["phase"] = "observing_tx"
            observe_until = time.time() + 20.0
            while time.time() < observe_until:
                renpy_host.inject_key(K_RETURN, True, "\r")
                renpy_host.inject_key(K_RETURN, False, "\r")
                state["injects"] = int(state["injects"]) + 2
                try:
                    mm = getattr(renpy.store, "main_menu", None)
                    if mm is False:
                        state["left_main_menu"] = True
                        state["started"] = True
                except Exception:
                    pass
                total_mid = (
                    int(state["dissolve_mid"])
                    + int(state["fade_mid"])
                    + int(state["imagedissolve_mid"])
                )
                if total_mid >= 2 and state["left_main_menu"]:
                    rec(f"early stop total_mid={total_mid}")
                    break
                time.sleep(0.35)

            try:
                rt = _sample_rt()
                state["rt_samples"].append(
                    {
                        "mean": rt.get("mean"),
                        "pure_frac": rt.get("pure_frac"),
                        "featureless_black": rt.get("featureless_black"),
                        "ok": rt.get("ok"),
                        "error": rt.get("error"),
                    }
                )
                rec(
                    "rt ok={} mean={} pure={:.3f} featureless={}".format(
                        rt.get("ok"),
                        tuple(round(x, 1) for x in (rt.get("mean") or (0, 0, 0))),
                        float(rt.get("pure_frac") or 0),
                        rt.get("featureless_black"),
                    )
                )
            except Exception as e:
                rec(f"rt soft: {e}")

            state["phase"] = "quitting"
            rec(
                "request_quit left_mm={} d_mid={} f_mid={} i_mid={}".format(
                    state["left_main_menu"],
                    state["dissolve_mid"],
                    state["fade_mid"],
                    state["imagedissolve_mid"],
                )
            )
        except Exception as e:
            state["error"] = f"{type(e).__name__}: {e}"
            rec("injector exc: {}".format(state["error"]))
            rec(traceback.format_exc())
        finally:
            _request_quit()

    threading.Thread(target=injector, daemon=True).start()

    import renpy.main as renpy_main

    rec("entering renpy.main.main()")
    try:
        renpy_main.main()
        rec("main returned")
    except BaseException as e:
        rec(f"main exit {type(e).__name__}: {e}")

    total_mid = (
        int(state["dissolve_mid"])
        + int(state["fade_mid"])
        + int(state["imagedissolve_mid"])
    )
    any_family = (
        len(state["dissolve_completes"])
        + len(state["fade_completes"])
        + len(state["imagedissolve_completes"])
    )
    last_rt = state["rt_samples"][-1] if state["rt_samples"] else {}
    rt_ok = bool(last_rt.get("ok")) and not bool(last_rt.get("featureless_black"))

    # engine_path_ok: left menu + at least one transition family rendered mid
    engine_path_ok = (
        bool(state["left_main_menu"])
        and total_mid >= 1
        and (state["transitions_pref"] is None or int(state["transitions_pref"]) >= 2)
        and rt_ok
    )
    # Entry: live_fail if no mid transition observed on product path
    live_fail = not engine_path_ok
    ok = True  # harness completed

    reason_parts = []
    if not state["left_main_menu"]:
        reason_parts.append("never_left_main_menu")
    if total_mid < 1:
        reason_parts.append(f"no_mid_transition (any={any_family})")
    if state["transitions_pref"] is not None and int(state["transitions_pref"]) < 2:
        reason_parts.append("transitions_pref={}<2".format(state["transitions_pref"]))
    if not rt_ok:
        reason_parts.append("rt_fail_or_featureless")
    if state["error"]:
        reason_parts.append("error=" + state["error"])
    reason = (
        "product_transition_usable"
        if engine_path_ok
        else ("|".join(reason_parts) if reason_parts else "fail")
    )

    body = [
        "gate=hmc_transition_product_reconfirm",
        f"ok={ok}",
        f"engine_path_ok={engine_path_ok}",
        f"live_fail_reconfirm={live_fail}",
        "path_kind=product_huangmeic_start_transition",
        f"game={game}",
        "left_main_menu={}".format(state["left_main_menu"]),
        "started={}".format(state["started"]),
        "transitions_pref={}".format(state["transitions_pref"]),
        "hook_dissolve={}".format(state["hook_dissolve"]),
        "hook_fade={}".format(state["hook_fade"]),
        "hook_imagedissolve={}".format(state["hook_imagedissolve"]),
        "dissolve_samples={}".format(len(state["dissolve_completes"])),
        "fade_samples={}".format(len(state["fade_completes"])),
        "imagedissolve_samples={}".format(len(state["imagedissolve_completes"])),
        "dissolve_mid={}".format(state["dissolve_mid"]),
        "fade_mid={}".format(state["fade_mid"]),
        "imagedissolve_mid={}".format(state["imagedissolve_mid"]),
        f"total_mid={total_mid}",
        "injects={}".format(state["injects"]),
        "last_rt_mean={}".format(last_rt.get("mean")),
        "last_rt_pure_frac={}".format(last_rt.get("pure_frac")),
        "last_rt_featureless={}".format(last_rt.get("featureless_black")),
        "phase={}".format(state["phase"]),
        f"reason={reason}",
        "notes=ENTRY helper for reopen Step 4. live_fail_reconfirm=True means surgery allowed; False means do not thrash green dissolve gates. Human remains AC authority.",
    ]
    body.extend(lines[-120:])
    text = "\n".join(body) + "\n"
    out.write_text(text)
    art.write_text(text)
    _log(
        f"WROTE {out} engine_path_ok={engine_path_ok} live_fail={live_fail} reason={reason}"
    )
    _request_quit()


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
