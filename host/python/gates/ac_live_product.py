"""
AC-LIVE product mid-complete instrument — W3 verification runner.

Gate name: ac_live_product  (RENPY_HOST_GATE=ac_live_product)

Records real Dissolve.render (st, complete) during product Start → first
with fade / with dissolve path. Does NOT set prefs.transitions=0.
Sets transitions=2 if prefs exist (AC-L0: enable transitions).

Pass: >=2 mid completes in (0.15, 0.85) from real Dissolve.render within ~30s.
Writes host/target/gate-ac_live_product.txt and .omc/handoffs/evidence/ac-live-attempt.log

Note: no from __future__; host run_file prepends imports.
"""

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
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path("/mnt/nvme1n1p2/revult")


def _log(msg, lines=None):
    try:
        sys.__stdout__.write(f"[ac_live_product] {msg}\n")
        sys.__stdout__.flush()
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        open("/tmp/ac_live_product.log", "a").write(msg + "\n")  # noqa: SIM115
    except Exception:  # noqa: BLE001, S110
        pass
    if lines is not None:
        lines.append(msg)


def _request_quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:  # noqa: BLE001, S110
        pass


def _pre_main_host_stubs(lines):
    try:
        import renpy.audio as _ra
        import renpy.audio.renpysound_host as _rs_host

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        _log("renpysound rebound", lines)
    except Exception as e:  # noqa: BLE001
        _log(f"renpysound soft-fail: {e}", lines)
    try:
        import renpy_uguu_host as _uguu

        sys.modules["renpy.uguu"] = _uguu
        sys.modules["renpy.uguu.uguu"] = _uguu
        try:
            import renpy

            renpy.uguu = _uguu
        except Exception:  # noqa: BLE001, S110
            pass
        _log("uguu stub", lines)
    except Exception as e:  # noqa: BLE001
        _log(f"uguu soft-fail: {e}", lines)
    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        _log("ecsign stub", lines)
    except Exception as e:  # noqa: BLE001
        _log(f"ecsign soft-fail: {e}", lines)


def run():
    base = _base()
    out = base / "host" / "target" / "gate-ac_live_product.txt"
    evid_dir = base / ".omc" / "handoffs" / "evidence"
    evid_dir.mkdir(parents=True, exist_ok=True)
    evid = evid_dir / "ac-live-attempt.log"
    lines = []
    out.parent.mkdir(parents=True, exist_ok=True)

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
            body = f"ok=False\nerror={err}\nphase=bootstrap\n"
            out.write_text(body)
            evid.write_text(body)
            _request_quit()
            return

    import renpy

    renpy.host_build = True
    try:
        renpy.config.performance_test = False
    except Exception:  # noqa: BLE001, S110
        pass

    try:
        import renpy_main_host

        renpy_main_host.install(renpy)
        rec("main_host installed")
    except Exception as e:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001, S110
                pass
        args = renpy.arguments.bootstrap()
        renpy.game.args = args
        rec("args command={}".format(getattr(args, "command", None)))
    except Exception as e:  # noqa: BLE001
        rec(f"args fail: {e}")
        rec(traceback.format_exc())

    _pre_main_host_stubs(lines)

    state = {
        "completes": [],
        "mid_count": 0,
        "injects": 0,
        "phase": "boot",
        "started": False,
        "transitions_pref": None,
        "transitions_forced_zero": False,
        "less_updates": None,
        "models": None,
        "hook_installed": False,
        "error": "",
        "left_main_menu": False,
    }

    try:
        import renpy.display.render as render_mod

        import renpy.display.transition as tr

        _orig_dissolve_render = tr.Dissolve.render

        def _hooked_dissolve_render(self, width, height, st, at):
            rv = _orig_dissolve_render(self, width, height, st, at)
            try:
                complete = getattr(rv, "operation_complete", None)
                if complete is None and getattr(self, "time", 0):
                    complete = min(1.0, float(st) / float(self.time))
                uniforms = getattr(rv, "uniforms", None) or {}
                u = None
                if isinstance(uniforms, dict):
                    u = uniforms.get("u_renpy_dissolve")
                entry = {
                    "t": time.time(),
                    "st": float(st),
                    "complete": float(complete) if complete is not None else None,
                    "time": float(getattr(self, "time", -1)),
                    "u": float(u) if u is not None else None,
                }
                state["completes"].append(entry)
                c = entry["complete"]
                if c is not None and 0.15 < c < 0.85:
                    state["mid_count"] = int(state["mid_count"]) + 1
                if len(state["completes"]) <= 50:
                    rec(
                        "Dissolve.render st={:.4f} complete={} u={} T={}".format(entry["st"], entry["complete"], entry["u"], entry["time"])
                    )
            except Exception as e:  # noqa: BLE001
                rec(f"hook log soft: {e}")
            return rv

        tr.Dissolve.render = _hooked_dissolve_render  # type: ignore
        state["hook_installed"] = True
        rec("Dissolve.render hook installed")
        try:
            render_mod.models = True
        except Exception:  # noqa: BLE001, S110
            pass
    except Exception as e:  # noqa: BLE001
        state["error"] = f"hook_fail: {e}"
        rec(state["error"])
        rec(traceback.format_exc())

    def injector():
        try:
            # Wait for main menu paint + focus seed
            time.sleep(5.0)
            state["phase"] = "prefs"
            import renpy as _renpy

            try:
                prefs = getattr(_renpy.game, "preferences", None)
                # AC-L0: enable transitions (>=2). NEVER set to 0.
                if prefs is not None and hasattr(prefs, "transitions"):
                    cur = int(getattr(prefs, "transitions", -1))
                    if cur < 2:
                        prefs.transitions = 2
                        rec(f"set prefs.transitions {cur} -> 2 (enable, not zero)")
                    state["transitions_pref"] = int(prefs.transitions)
                    state["transitions_forced_zero"] = False
                else:
                    rec("prefs missing or no transitions attr")
                try:
                    _renpy.game.less_updates = False
                    state["less_updates"] = bool(_renpy.game.less_updates)
                except Exception as e:  # noqa: BLE001
                    rec(f"less_updates soft: {e}")
                try:
                    import renpy.display.render as rm

                    rm.models = True
                    state["models"] = bool(rm.models)
                except Exception as e:  # noqa: BLE001
                    rec(f"models soft: {e}")
                try:
                    _renpy.config.performance_test = False
                    _renpy.config.has_music = False
                    _renpy.config.main_menu_music = None
                    if prefs is not None:
                        if hasattr(prefs, "performance_test"):
                            prefs.performance_test = False
                        if hasattr(prefs, "text_cps"):
                            prefs.text_cps = 0
                except Exception as e:  # noqa: BLE001
                    rec(f"prefs soft: {e}")
            except Exception as e:  # noqa: BLE001
                rec(f"prefs block: {e}")

            state["phase"] = "injecting"
            rec("begin Start inject (Enter only)")
            K_RETURN = 13
            for i in range(40):
                renpy_host.inject_key(K_RETURN, True, "\r")
                renpy_host.inject_key(K_RETURN, False, "\r")
                state["injects"] = int(state["injects"]) + 2
                try:
                    mm = getattr(_renpy.store, "main_menu", None)
                    if i % 5 == 0:
                        rec(
                            "pulse#%d main_menu=%r mid_count=%s completes=%s"  # noqa: UP031
                            % (i, mm, state["mid_count"], len(state["completes"]))
                        )
                    if mm is False:
                        state["left_main_menu"] = True
                        state["started"] = True
                        rec("left main_menu at pulse#%d" % i)  # noqa: UP031
                        break
                except Exception as e:  # noqa: BLE001
                    rec(f"status: {e}")
                time.sleep(0.25)

            state["phase"] = "observing"
            observe_until = time.time() + 12.0
            while time.time() < observe_until:
                renpy_host.inject_key(K_RETURN, True, "\r")
                renpy_host.inject_key(K_RETURN, False, "\r")
                state["injects"] = int(state["injects"]) + 2
                if int(state["mid_count"]) >= 2:
                    rec("mid_count>=2 early stop")
                    break
                time.sleep(0.35)
                try:
                    mm = getattr(_renpy.store, "main_menu", None)
                    if mm is False:
                        state["left_main_menu"] = True
                        state["started"] = True
                except Exception:  # noqa: BLE001, S110
                    pass

            try:
                prefs = getattr(_renpy.game, "preferences", None)
                if prefs is not None and hasattr(prefs, "transitions"):
                    state["transitions_pref"] = int(prefs.transitions)
                state["less_updates"] = bool(getattr(_renpy.game, "less_updates", None))
            except Exception:  # noqa: BLE001, S110
                pass

            state["phase"] = "quitting"
            rec(
                "request_quit mid_count={} completes={} started={} transitions_pref={}".format(
                    state["mid_count"],
                    len(state["completes"]),
                    state["started"],
                    state["transitions_pref"],
                )
            )
        except Exception as e:  # noqa: BLE001
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
    except BaseException as e:  # noqa: BLE001
        rec(f"main exit {type(e).__name__}: {e}")

    mids = [
        e
        for e in state["completes"]
        if e.get("complete") is not None and 0.15 < float(e["complete"]) < 0.85
    ]
    state["mid_count"] = len(mids)
    ok = (
        state["hook_installed"]
        and state["transitions_forced_zero"] is False
        and len(mids) >= 2
        and (state["transitions_pref"] is None or int(state["transitions_pref"]) >= 2)
    )
    reason_parts = []
    if not state["hook_installed"]:
        reason_parts.append("hook_not_installed")
    if len(mids) < 2:
        reason_parts.append(f"mid_count={len(mids)}<2")
    if state["transitions_pref"] is not None and int(state["transitions_pref"]) < 2:
        reason_parts.append("transitions_pref={}<2".format(state["transitions_pref"]))
    if state["transitions_forced_zero"]:
        reason_parts.append("transitions_forced_zero")
    if not state["started"] and not state["left_main_menu"]:
        reason_parts.append("never_left_main_menu")
    if state["error"]:
        reason_parts.append("error=" + state["error"])
    reason = "pass" if ok else ("|".join(reason_parts) if reason_parts else "fail")

    body = [
        "gate=ac_live_product",
        f"ok={ok}",
        "path_kind=product_interact_dissolve_hook",
        "hook_installed={}".format(state["hook_installed"]),
        "transitions_pref={}".format(state["transitions_pref"]),
        "transitions_forced_zero={}".format(state["transitions_forced_zero"]),
        "less_updates={}".format(state["less_updates"]),
        "models={}".format(state["models"]),
        "started={}".format(state["started"]),
        "left_main_menu={}".format(state["left_main_menu"]),
        "injects={}".format(state["injects"]),
        "complete_samples={}".format(len(state["completes"])),
        f"mid_count={len(mids)}",
        "mid_completes=%s" % [round(float(e["complete"]), 4) for e in mids[:20]],
        "all_completes=%s"
        % [
            round(float(e["complete"]), 4)
            for e in state["completes"][:40]
            if e.get("complete") is not None
        ],
        "phase={}".format(state["phase"]),
        f"reason={reason}",
        "notes=AC-LIVE requires >=2 Dissolve.render completes in (0.15,0.85) under product path without transitions=0",
    ]
    body.extend(lines[-100:])
    text = "\n".join(body) + "\n"
    out.write_text(text)
    evid.write_text(text)
    _log(f"WROTE {out} ok={ok} reason={reason}")
    _request_quit()


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
