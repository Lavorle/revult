"""
HuangmeiC AC-Nav product ShowMenu chrome probe.

Gate name: hmc_nav_chrome_product  (RENPY_HOST_GATE=hmc_nav_chrome_product)

Boots product HuangmeiC path, waits for main_menu, then forces ShowMenu /
confirm for reachable screens and samples the game RT for non-featureless
chrome. Does NOT depend on dock hover/selection (AC-Sel is separate).

Authority: human full-screen interact still required for AC-Nav1–2 pass.
This gate only proves engine can present non-blank chrome after ShowMenu.

Stop rule: game_config_2 / mouse_config stubs ≠ engine fail.

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
        sys.__stdout__.write(f"[hmc_nav_chrome] {msg}\n")
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        open("/tmp/hmc_nav_chrome_product.log", "a").write(msg + "\n")  # noqa: SIM115
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
    """Mirror product._pre_main_host_stubs (sound/pygame/uguu/ecsign)."""
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
    """Rebuild scene root after ShowMenu and draw it (stale surftree is wrong)."""
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
        info["root"] = type(root).__name__
        info["surftree"] = type(surftree).__name__
        return info
    except Exception as e:
        info["error"] = f"{type(e).__name__}:{e}"
        return info


def _sample_rt():
    """Return dict with mean rgb, pure black frac, variance, size.

    Forces a product scene rebuild present first so we do not sample a stale
    clear-only RT (nested product often shows frames=1 without redraw).
    """
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
    # sparse grid sample
    step_x = max(1, rw // 32)
    step_y = max(1, rh // 18)
    samples = []
    for y in range(step_y // 2, rh, step_y):
        for x in range(step_x // 2, rw, step_x):
            o = (y * rw + x) * 4
            r, g, b, a = rt[o], rt[o + 1], rt[o + 2], rt[o + 3]
            samples.append((r, g, b, a))
            rs += r
            gs += g
            bs += b
            n += 1
            if r + g + b < 20 and a > 200:
                pure += 1
    if n == 0:
        return {"ok": False, "error": "no_samples", "w": rw, "h": rh, "present": pres}
    mean = (rs / n, gs / n, bs / n)
    pure_frac = pure / float(n)
    # variance
    mr, mg, mb = mean
    var = sum((s[0] - mr) ** 2 + (s[1] - mg) ** 2 + (s[2] - mb) ** 2 for s in samples) / n
    # featureless black: high pure frac OR very dark low variance
    featureless_black = pure_frac > 0.85 or (mean[0] + mean[1] + mean[2] < 40 and var < 80)
    # near-clear / empty: almost all transparent-ish or uniform near-zero
    emptyish = mean[0] + mean[1] + mean[2] < 15 and pure_frac > 0.5
    # clear-color residual (~13,13,20) with near-zero variance = never drew chrome
    clearish = (
        abs(mean[0] - 13) < 8
        and abs(mean[1] - 13) < 8
        and abs(mean[2] - 20) < 12
        and var < 5.0
    )
    ok = (not featureless_black) and (not emptyish) and (not clearish) and var >= 5.0
    return {
        "ok": ok,
        "w": rw,
        "h": rh,
        "mean": mean,
        "pure_frac": pure_frac,
        "var": var,
        "featureless_black": featureless_black,
        "emptyish": emptyish,
        "clearish": clearish,
        "n": n,
        "present": pres,
    }


def _get_screen(name):
    try:
        import renpy

        return renpy.display.screen.get_screen(name)
    except Exception:
        return None


def _force_show_menu(name):
    import renpy

    # Prefer Action call (product path) then display.screen.show_screen.
    try:
        action = renpy.store.ShowMenu(name)
        action()
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        return True, "ShowMenu()"
    except Exception as e1:
        try:
            renpy.display.screen.show_screen(name)
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            return True, f"display.screen.show_screen:{e1}"
        except Exception as e2:
            try:
                # Last resort: exports if bound
                show = getattr(renpy, "show_screen", None) or getattr(
                    renpy.exports, "show_screen", None
                )
                if show is None:
                    raise RuntimeError("no show_screen")
                show(name)
                return True, f"exports.show_screen:{e1}|{e2}"
            except Exception as e3:
                return False, f"fail:{e1}|{e2}|{e3}"


def _force_return():
    import renpy

    try:
        renpy.store.Return()()
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        return "Return()"
    except Exception:
        pass
    for n in ("load", "preferences", "appreciation", "flowchart", "confirm", "save"):
        try:
            renpy.display.screen.hide_screen(n)
        except Exception:
            pass
    try:
        renpy.restart_interaction()
    except Exception:
        pass
    return "hide_screens"


def _force_confirm_quit():
    import renpy

    # Force confirm chrome regardless of persistent confirm-requirement flag.
    try:
        # Ensure quit requires confirm for this probe only
        try:
            m = getattr(renpy.store, "persistent", None)
            if m is not None:
                mapping = getattr(m, "preferences_confirm_requirement_mapping", None)
                if isinstance(mapping, dict):
                    mapping["quit"] = True
        except Exception:
            pass
        renpy.display.screen.show_screen(
            "confirm",
            message="确认要退出游戏吗",
            yes_action=[renpy.store.Hide("confirm")],
            no_action=[renpy.store.Hide("confirm")],
            confirm_type="quit",
        )
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        return True, "show_screen_confirm"
    except Exception as e1:
        try:
            renpy.store.ConfirmAction("quit")()
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            return True, f"ConfirmAction():{e1}"
        except Exception as e2:
            return False, f"fail:{e1}|{e2}"


def run():
    base = _base()
    out = base / "host" / "target" / "gate-hmc_nav_chrome_product.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    state = {
        "phase": "boot",
        "main_menu": False,
        "results": [],
        "errors": [],
    }

    def rec(m):
        lines.append(m)
        _log(m)

    # Product HuangmeiC path
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

    for name, call in (
        ("import_renpy", boot.stage_import_renpy),
        ("import_all", boot.stage_import_all),
        ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ):
        good, _miss, err, _extra = call()
        rec(f"stage {name} good={good} err={err!r}")
        if not good:
            out.write_text(f"gate=hmc_nav_chrome_product\nok=False\nerror={err}\n")
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

    targets = [
        ("load", "load", "menu"),
        ("preferences", "preferences", "menu"),
        ("appreciation", "appreciation", "menu"),
        ("flowchart", "flowchart", "menu"),
        ("confirm", "confirm", "overlay"),
    ]

    def injector():
        # Wait for main menu
        rec("waiting main_menu")
        for i in range(400):
            try:
                if bool(getattr(renpy.store, "main_menu", False)):
                    state["main_menu"] = True
                    rec("main_menu at tick=%d" % i)  # noqa: UP031
                    break
            except Exception:
                pass
            time.sleep(0.05)
        if not state["main_menu"]:
            state["errors"].append("main_menu_timeout")
            rec("main_menu timeout")
            _request_quit()
            return

        # Let a few frames present (Movie decode + main menu chrome)
        time.sleep(2.5)
        try:
            base_rt = _sample_rt()
            rec(
                "main_menu_rt ok={} mean=({:.0f},{:.0f},{:.0f}) var={:.1f} pure={:.3f}".format(
                    base_rt.get("ok"),
                    base_rt.get("mean", (0, 0, 0))[0],
                    base_rt.get("mean", (0, 0, 0))[1],
                    base_rt.get("mean", (0, 0, 0))[2],
                    base_rt.get("var", 0),
                    base_rt.get("pure_frac", 0),
                )
            )
        except Exception as e:
            rec(f"main_menu_rt fail: {e}")

        for tname, screen_name, kind in targets:
            state["phase"] = f"nav_{tname}"
            rec(f"--- target {tname} ---")
            entry = {
                "name": tname,
                "screen": screen_name,
                "opened": False,
                "open_via": None,
                "rt_ok": False,
                "mean": (0, 0, 0),
                "var": 0.0,
                "pure_frac": 1.0,
                "featureless_black": True,
                "error": None,
            }
            try:
                if tname == "confirm":
                    ok_open, via = _force_confirm_quit()
                else:
                    ok_open, via = _force_show_menu(screen_name)
                entry["open_via"] = via
                if not ok_open:
                    entry["error"] = via
                    state["results"].append(entry)
                    rec(f"open FAIL {via}")
                    continue

                # Wait for screen + frames
                opened = False
                for j in range(40):
                    scr = _get_screen(screen_name)
                    if scr is not None:
                        opened = True
                        break
                    # confirm may be transient if preference disables
                    time.sleep(0.1)
                entry["opened"] = opened
                rec(f"opened={opened} via={via}")
                time.sleep(0.4)
                pinfo = _force_product_redraw()
                rec("present path={} err={} root={}".format(
                    pinfo.get("path"), pinfo.get("error"), pinfo.get("root")))
                time.sleep(0.1)
                rt = _sample_rt()
                entry["rt_ok"] = bool(rt.get("ok"))
                entry["mean"] = rt.get("mean", (0, 0, 0))
                entry["var"] = float(rt.get("var", 0))
                entry["pure_frac"] = float(rt.get("pure_frac", 1))
                entry["featureless_black"] = bool(rt.get("featureless_black"))
                entry["present_path"] = (rt.get("present") or {}).get("path") or pinfo.get("path")
                if rt.get("error"):
                    entry["error"] = rt["error"]
                rec(
                    "rt ok={} mean=({:.0f},{:.0f},{:.0f}) var={:.1f} pure={:.3f} fb={} clear={} present={} err={}".format(
                        entry["rt_ok"],
                        entry["mean"][0],
                        entry["mean"][1],
                        entry["mean"][2],
                        entry["var"],
                        entry["pure_frac"],
                        entry["featureless_black"],
                        rt.get("clearish"),
                        entry.get("present_path"),
                        entry.get("error"),
                    )
                )
            except Exception as e:
                entry["error"] = f"{e}"
                rec(f"exc {tname}: {e}")
                rec(traceback.format_exc())
            state["results"].append(entry)

            # Return to main menu chrome
            try:
                _force_return()
                time.sleep(0.4)
            except Exception as e:
                rec(f"return fail: {e}")

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
        rec(f"main exit {type(e).__name__}: {e}")

    # Summarize
    results = state["results"]
    per_ok = []
    for r in results:
        ok = bool(r.get("rt_ok")) and not bool(r.get("featureless_black"))
        if r.get("error") and str(r.get("error", "")).startswith("fail:"):
            ok = False
        # confirm: isolated hmc_chrome_residual already covers confirm Image path;
        # product overlay may still clear if layer rebuild misses screens layer —
        # treat opened+via as soft engine open, but keep rt_ok as hard chrome truth.
        per_ok.append(ok)
        r["ok"] = ok

    # Primary AC-Nav product screens (ShowMenu tag menu)
    primary = [r for r in results if r["name"] in ("load", "preferences", "appreciation", "flowchart")]
    primary_ok = all(r.get("ok") for r in primary) if primary else False
    confirm_r = next((r for r in results if r["name"] == "confirm"), None)
    confirm_ok = bool(confirm_r and confirm_r.get("ok"))
    # Overall panel_ok: all primary required; confirm preferred but soft if opened
    # and primary green (confirm assets already in hmc_chrome_residual).
    panel_ok = primary_ok and (confirm_ok or (confirm_r is not None and confirm_r.get("opened")))
    main_ok = bool(state["main_menu"])
    ok = main_ok and primary_ok  # hard bar: main + 4 ShowMenu screens non-clear

    body = [
        "gate=hmc_nav_chrome_product",
        f"ok={ok}",
        "ac=Nav_product_showmenu",
        f"main_menu={main_ok}",
        f"primary_ok={primary_ok}",
        f"confirm_ok={confirm_ok}",
        f"panel_ok={panel_ok}",
        "phase={}".format(state["phase"]),
        "errors=%s" % (";".join(state["errors"]) if state["errors"] else "none"),
        "notes=human_full_interact_still_authority_for_AC-Nav1-2;confirm_assets_in_hmc_chrome_residual",
    ]
    for r in results:
        body.append(
            "screen.{} ok={} opened={} rt_ok={} mean=({:.1f},{:.1f},{:.1f}) var={:.1f} "
            "pure_frac={:.3f} featureless_black={} via={} err={}".format(
                r["name"],
                r.get("ok"),
                r.get("opened"),
                r.get("rt_ok"),
                r.get("mean", (0, 0, 0))[0],
                r.get("mean", (0, 0, 0))[1],
                r.get("mean", (0, 0, 0))[2],
                r.get("var", 0),
                r.get("pure_frac", 1),
                r.get("featureless_black"),
                r.get("open_via"),
                r.get("error"),
            )
        )
    body.append(
        "matrix AC-Nav1=engine_partial_see_panels AC-Nav2=human_only "
        "AC-Nav3=pass_process_stubs_excluded"
    )
    body.extend([f"log.{ln}" for ln in lines[-40:]])
    out.write_text("\n".join(body) + "\n")
    rec(f"wrote {out} ok={ok} panel_ok={panel_ok}")
    _request_quit()
    if not ok:
        # Soft: still write artifact; raise only if main_menu never reached
        if not main_ok:
            raise RuntimeError("hmc_nav_chrome_product: main_menu never reached")


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
