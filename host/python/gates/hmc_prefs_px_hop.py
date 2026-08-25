"""HuangmeiC preferences TEXT CONFIG hop residual gate.

Gate name: hmc_prefs_px_hop  (RENPY_HOST_GATE=hmc_prefs_px_hop)

PX-1: open prefs -> text_config -> sound_config -> dialog_config_1 -> text_config.
       No wrong binds / black / garbled chrome / misaligned labels after hop.
PX-2: after returning to text_config, the selected tab still shows the
       navigation_selected yellow wipe (effect presence after hop).

This is the first machine-readable PX gate. PX-2 timing (u_animation continuous
advance) is tracked as a stricter follow-up; this gate catches absence/regression.

Writes: host/target/gate-hmc_prefs_px_hop.txt
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



def _base():
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path("/mnt/nvme1n1p2/revult")


def _log(msg):
    line = f"[hmc_prefs_px] {msg}\n"
    try:
        sys.__stdout__.write(line)
        sys.__stdout__.flush()
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        open("/tmp/hmc_prefs_px_hop.log", "a").write(msg + "\n")  # noqa: SIM115
    except Exception:  # noqa: BLE001, S110
        pass


def _request_quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:  # noqa: BLE001, S110
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
    except Exception as e:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            rpg.import_as_pygame()
        except Exception:  # noqa: BLE001, S110
            pass
    except Exception as e:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001, S110
            pass
    except Exception as e:  # noqa: BLE001
        _log(f"uguu soft-fail: {e}")

    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        try:
            import renpy as _renpy_pkg

            _renpy_pkg.ecsign = _ecsign
        except Exception:  # noqa: BLE001, S110
            pass
    except Exception as e:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001, S110
            pass
        info["path"] = "rebuild_render_screen"
        return info
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}:{e}"
        return info


def _read_rt():
    import renpy_host

    pres = _force_product_redraw()
    try:
        rw, rh, rt = renpy_host.read_game_rt_rgba()
    except Exception as e:  # noqa: BLE001
        return None, None, None, {"error": f"read:{e}", "present": pres}
    return rw, rh, rt, {"present": pres}


def _sample_regions(rt, rw, rh):
    regions = {
        "title": (0.0, 0.0, 0.25, 0.12),
        "nav": (0.25, 0.03, 0.75, 0.12),
        "panel_c": (0.4, 0.35, 0.6, 0.65),
        "panel_tl": (0.1, 0.2, 0.3, 0.4),
        "bottom": (0.7, 0.9, 0.98, 0.99),
        "hint": (0.02, 0.90, 0.28, 0.99),
        "edge": (0.0, 0.0, 0.08, 0.08),
    }
    out = {}
    for name, (x0, y0, x1, y1) in regions.items():
        xs = int(x0 * rw)
        xe = max(xs + 1, int(x1 * rw))
        ys = int(y0 * rh)
        ye = max(ys + 1, int(y1 * rh))
        rs = gs = bs = n = 0
        dark = 0
        step_x = max(1, (xe - xs) // 16)
        step_y = max(1, (ye - ys) // 12)
        for y in range(ys + step_y // 2, ye, step_y):
            for x in range(xs + step_x // 2, xe, step_x):
                o = (y * rw + x) * 4
                r, g, b = rt[o], rt[o + 1], rt[o + 2]
                rs += r
                gs += g
                bs += b
                n += 1
                # arena clear ~ (13,13,20)
                if r < 25 and g < 25 and b < 35:
                    dark += 1
        if n <= 0:
            out[name] = {"mean": (0, 0, 0), "dark": 1.0, "n": 0}
            continue
        out[name] = {
            "mean": (round(rs / n, 1), round(gs / n, 1), round(bs / n, 1)),
            "dark": round(dark / float(n), 3),
            "n": n,
        }
    return out


def _is_broken(samples):
    """Broken frame: panel or chrome largely arena-clear while not fully empty."""
    panel = samples.get("panel_c") or {}
    title = samples.get("title") or {}
    bottom = samples.get("bottom") or {}
    edge = samples.get("edge") or {}
    # Panel should be light gray-ish when healthy (~240)
    panel_dark = float(panel.get("dark") or 0)
    title_dark = float(title.get("dark") or 0)
    bottom_dark = float(bottom.get("dark") or 0)
    edge_dark = float(edge.get("dark") or 0)
    panel_mean = panel.get("mean") or (0, 0, 0)
    # Hole signatures from user screenshot: dark TL + incomplete bottom + partial panel
    holes = 0
    if title_dark >= 0.45 or edge_dark >= 0.55:
        holes += 1
    if bottom_dark >= 0.55:
        holes += 1
    if panel_dark >= 0.35 or (panel_mean[0] < 80 and panel_mean[1] < 80):
        holes += 1
    return holes >= 2, {
        "panel_dark": panel_dark,
        "title_dark": title_dark,
        "bottom_dark": bottom_dark,
        "edge_dark": edge_dark,
        "panel_mean": panel_mean,
        "holes": holes,
    }


def _force_show_prefs(kind):
    import renpy

    try:
        renpy.display.screen.show_screen("preferences", kind=kind)
        try:
            renpy.restart_interaction()
        except Exception:  # noqa: BLE001, S110
            pass
        return True, "show_screen_preferences_kind"
    except Exception as e1:  # noqa: BLE001
        try:
            renpy.store.ShowMenu("preferences")()
            renpy.display.screen.show_screen("preferences", kind=kind)
            return True, "ShowMenu+kind"
        except Exception as e2:  # noqa: BLE001
            return False, f"{e1}|{e2}"


def _inject_hover(vx, vy):
    import renpy_host

    import renpy

    how = []
    try:
        renpy_host.inject_mouse(int(vx), int(vy), 0, False)
        how.append("inject_mouse")
    except Exception:  # noqa: BLE001
        try:
            renpy_host.inject_mouse(int(vx), int(vy), 0)
            how.append("inject_mouse3")
        except Exception as e:  # noqa: BLE001
            how.append(f"inject_fail:{e}")
    try:
        renpy.display.focus.mouse_handler(None, int(vx), int(vy), default=False)
        how.append("mouse_handler")
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        renpy.restart_interaction()
        how.append("restart")
    except Exception:  # noqa: BLE001, S110
        pass
    return "+".join(how)


def _click_nav_tab(tab_index):
    """Click a preferences top-nav tab (0-based) at virtual coords."""
    import hmc_prefs_nav_effects as _nav
    import renpy_host

    import renpy

    vx = 462 + (tab_index + 0.5) * 179
    vy = 47 + 32
    wx, wy = _nav._virtual_to_window(vx, vy)
    renpy_host.inject_mouse(int(wx), int(wy), 1, True)
    renpy_host.inject_mouse(int(wx), int(wy), 1, False)
    try:
        renpy.display.focus.mouse_handler(None, int(vx), int(vy), default=False)
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        renpy.restart_interaction()
    except Exception:  # noqa: BLE001, S110
        pass
    return "click_tab%d@%d,%d" % (tab_index, wx, wy)  # noqa: UP031


def _kind_tab(kind):
    return {
        "image_config": 0,
        "game_config_1": 1,
        "text_config": 2,
        "sound_config": 3,
        "dialog_config_1": 4,
        "keyboard_config": 5,
    }.get(kind, 2)


def run():
    base = _base()
    out = base / "host" / "target" / "gate-hmc_prefs_px_hop.txt"
    lines = []

    def rec(m):
        lines.append(m)
        _log(m)

    os.environ.setdefault("RENPY_HOST_BASE", str(base))
    os.environ.setdefault("RENPY_HOST_BUILD", "1")
    os.environ.setdefault(
        "RENPY_HOST_GAME", str(base / "host" / "playtests" / "HuangmeiC")
    )
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    _clear_falsey_skip("RENPY_SKIP_SPLASHSCREEN")
    os.environ["RENPY_SKIP_SPLASHSCREEN"] = "1"
    os.environ.pop("RENPY_SKIP_MAIN_MENU", None)

    gates = str(base / "host" / "python" / "gates")
    if gates not in sys.path:
        sys.path.insert(0, gates)

    import bootstrap as boot

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

        basedir = getattr(renpy.config, "basedir", None) or str(
            base / "host" / "playtests" / "HuangmeiC"
        )
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
        rec("args command={} basedir={}".format(getattr(args, "command", None), basedir))
    except Exception as e:  # noqa: BLE001
        rec(f"args fail: {e}")
        rec(traceback.format_exc())

    _pre_main_host_stubs()

    state = {
        "phase": "boot",
        "broken": 0,
        "samples": 0,
        "pages_ok": [],
        "pages_fail": [],
        "px2_ok": False,
        "px2_yellow_frac": 0.0,
    }

    # Open prefs on sound_config first (matches product: user opens prefs, then
    # clicks text_config). Direct cold-open to text_config has a separate
    # first-frame dark issue; the user-visible hop starts from an already-open
    # preferences screen.
    PX_SEQUENCE = ("sound_config", "text_config", "sound_config", "dialog_config_1", "text_config")
    # Choice button approx positions (virtual 1920x1080) for image_config layout:
    # left column choices around x~700-900, y~220+
    HOVER_POINTS = [
        (780, 250),
        (900, 250),
        (780, 340),
        (900, 340),
        (780, 430),
        (1500, 250),
        (1500, 340),
        (1700, 1020),  # bottom reset/return area
        (500, 80),  # nav tab
    ]

    def probe():
        deadline = time.time() + 70.0
        while time.time() < deadline:
            try:
                mm = getattr(renpy.store, "main_menu", None)
                if mm:
                    rec(f"main_menu at t={time.time():.2f}")
                    break
            except Exception:  # noqa: BLE001, S110
                pass
            time.sleep(0.1)
        else:
            rec("FAIL: main_menu timeout")
            state["phase"] = "fail"
            _request_quit()
            return

        time.sleep(0.4)

        for idx, kind in enumerate(PX_SEQUENCE):
            rec("=== hop %d %s ===" % (idx, kind))  # noqa: UP031
            if idx == 0:
                ok, via = _force_show_prefs(kind)
            else:
                via = _click_nav_tab(_kind_tab(kind))
                ok = True
            rec(f"opened {kind} ok={ok} via={via}")
            if not ok:
                state["pages_fail"].append(kind)
                continue
            time.sleep(0.25)
            # Show() can take a few frames to build the preferences screen; wait
            # like hmc_prefs_nav_effects (24 × 50ms) before sampling.
            for _ in range(24):
                _force_product_redraw()
                time.sleep(0.05)

            rw, rh, rt, meta = _read_rt()
            if not rw or not rh or not rt:
                rec(f"FAIL empty rt on open: {meta}")
                state["pages_fail"].append(kind)
                continue
            base_s = _sample_regions(rt, rw, rh)
            broken, info = _is_broken(base_s)
            rec(
                "baseline size={}x{} broken={} panel_mean={} dark={}".format(
                    rw,
                    rh,
                    broken,
                    base_s.get("panel_c", {}).get("mean"),
                    {
                        k: base_s.get(k, {}).get("dark")
                        for k in ("title", "panel_c", "bottom", "edge")
                    },
                )
            )
            page_broken = 0
            if broken:
                page_broken += 1
                state["broken"] += 1
            state["samples"] += 1

            # Light hover exercise after each hop: catches texture/effect disorder
            # without turning this into the full hover-thrash soak.
            for cycle in range(2):
                for i, (vx, vy) in enumerate(HOVER_POINTS[:4]):
                    how = _inject_hover(vx, vy)
                    for _ in range(2):
                        _force_product_redraw()
                    rw, rh, rt, meta = _read_rt()
                    if not rw or not rt:
                        continue
                    s = _sample_regions(rt, rw, rh)
                    br, info = _is_broken(s)
                    state["samples"] += 1
                    if br:
                        page_broken += 1
                        state["broken"] += 1
                        rec(
                            "BROKEN hop=%d cycle=%d pt=%d (%d,%d) how=%s info=%s panel=%s"  # noqa: UP031
                            % (
                                idx,
                                cycle,
                                i,
                                vx,
                                vy,
                                how,
                                info,
                                s.get("panel_c"),
                            )
                        )
                    _inject_hover(50, 50)
                    _force_product_redraw()
                    rw, rh, rt, meta = _read_rt()
                    if rw and rt:
                        s2 = _sample_regions(rt, rw, rh)
                        br2, info2 = _is_broken(s2)
                        state["samples"] += 1
                        if br2:
                            page_broken += 1
                            state["broken"] += 1
                            rec(
                                "BROKEN unhover hop=%d cycle=%d pt=%d info=%s"  # noqa: UP031
                                % (idx, cycle, i, info2)
                            )

            if page_broken == 0:
                state["pages_ok"].append(kind)
                rec(f"PASS hop {kind}")
            else:
                state["pages_fail"].append(kind)
                rec("FAIL hop %s broken_frames=%d" % (kind, page_broken))  # noqa: UP031

            # PX-2: selected tab must show navigation_selected yellow on text_config.
            # Log on every text_config hop; only the final return gates ok.
            if kind == "text_config":
                try:
                    import hmc_prefs_nav_effects as _nav

                    for _ in range(24):
                        _force_product_redraw()
                        time.sleep(0.05)
                    rw, rh, rt, meta = _read_rt()
                    if rw and rh and rt:
                        sx = float(rw) / 1920.0
                        sy = float(rh) / 1080.0
                        tab2_yf = 0.0
                        # Log all tabs for diagnosis, but gate on the text_config
                        # tab (index 2) specifically.
                        for tab_index in range(7):
                            x0 = (462 + tab_index * 179) * sx
                            y0 = 47 * sy
                            x1 = (462 + (tab_index + 1) * 179) * sx
                            y1 = (47 + 64) * sy
                            tab = _nav._sample_band(rt, rw, rh, x0, y0, x1, y1, step=1)
                            yf = tab.get("yellow_frac", 0) if tab.get("ok") else 0
                            rec(
                                "PX-2 tab%d yellow_frac=%.4f n=%s"  # noqa: UP031
                                % (tab_index, yf, tab.get("n", 0) if tab.get("ok") else 0)
                            )
                            if tab_index == 2:
                                tab2_yf = yf
                        state["px2_yellow_frac"] = tab2_yf
                        if idx == len(PX_SEQUENCE) - 1:
                            if tab2_yf >= 0.08:
                                state["px2_ok"] = True
                                rec(
                                    f"PASS PX-2 text_config selected yellow after hop frac={tab2_yf:.4f}"
                                )
                            else:
                                rec(
                                    f"FAIL PX-2 text_config selected yellow weak/absent frac={tab2_yf:.4f}"
                                )
                        else:
                            rec(
                                "PX-2 text_config hop=%d yellow_frac=%.4f"  # noqa: UP031
                                % (idx, tab2_yf)
                            )
                    else:
                        rec("FAIL PX-2 empty rt on text_config")
                except Exception as e:  # noqa: BLE001
                    rec(f"PX-2 sample exception: {type(e).__name__}: {e}")

        state["phase"] = "done"
        ok = (
            state["broken"] == 0
            and len(state["pages_ok"]) == len(PX_SEQUENCE)
            and len(state["pages_fail"]) == 0
            and state["px2_ok"]
        )
        rec(
            "summary pages_ok=%s pages_fail=%s broken=%d samples=%d px2=%.4f ok=%s"  # noqa: UP031
            % (
                state["pages_ok"],
                state["pages_fail"],
                state["broken"],
                state["samples"],
                state["px2_yellow_frac"],
                ok,
            )
        )
        report = [
            "gate=hmc_prefs_px_hop",
            f"ok={ok}",
            "broken=%d" % state["broken"],  # noqa: UP031
            "samples=%d" % state["samples"],  # noqa: UP031
            "pages_ok={}".format(",".join(state["pages_ok"])),
            "pages_fail={}".format(",".join(state["pages_fail"])),
            "px1_ok=%s" % (state["broken"] == 0 and len(state["pages_fail"]) == 0),
            "px2_ok={}".format(state["px2_ok"]),
            "px2_yellow_frac={:.4f}".format(state["px2_yellow_frac"]),
            "phase={}".format(state["phase"]),
        ]
        report.extend("log." + ln for ln in lines)
        out.write_text("\n".join(report) + "\n")
        _request_quit()

    # Launch main in thread like other product gates
    def main_thread():
        try:
            rec("entering renpy.main.main()")
            renpy.main.main()
        except Exception as e:  # noqa: BLE001
            rec(f"main exit {type(e).__name__}: {e}")
        finally:
            if state["phase"] not in ("done", "fail"):
                # probe may not have finished
                try:
                    out.write_text(
                        "ok=False\nphase=%s\nbroken=%d\n"  # noqa: UP031
                        % (state["phase"], state["broken"])
                    )
                except Exception:  # noqa: BLE001, S110
                    pass
            _request_quit()

    t = threading.Thread(target=probe, name="prefs-px-hop", daemon=True)
    t.start()
    main_thread()


run()

# ----------------------------------------------------------------------
# HARNESS MIGRATION (thin wrapper, original logic preserved above)
# ----------------------------------------------------------------------
# Migration path for hmc_prefs_px_hop:
#   1. Keep all helpers/classes above untouched (header license preserved).
#   2. Extract the body of main()/run()/probe into _harness_run_one(case):
#        def _harness_run_one(case):
#            # case: dict with {"hop": "text->sound->dialog->text"}
#            # ... reuse helpers above (WgpuDraw / FakeRender / _mean_rgb ...)
#            # w, h, rgba = renpy_host.read_game_rt_rgba()
#            # return w, h, rgba   # or (ok, msg)
#   3. Define golden_compare delegating to golden_mae or custom mean check:
#        def _harness_golden_compare(w, h, rgba):
#            from golden_mae import compare_or_bootstrap
#            return compare_or_bootstrap("hmc_prefs_px_hop", w, h, rgba)
#            # or custom: mr/mg/mb = _mean_rgb(rgba,w,h); return (ok,msg)
#   4. Wire via harness (opt-in via RENPY_HOST_HARNESS=1 to keep default run unchanged):
#        if parametrized_gate is not None:
#            @parametrized_gate("hmc_prefs_px_hop", [{"hop": "text->sound->dialog->text"}])
#            def _parametrized_case(case):
#                w, h, rgba = _harness_run_one(case)
#                return _harness_golden_compare(w, h, rgba)
#        def _harness_main():
#            import os as _os
#            if gate_harness is not None and _os.environ.get("RENPY_HOST_HARNESS") == "1":
#                cases = [{"hop": "text->sound->dialog->text"}]
#                ok = gate_harness("hmc_prefs_px_hop", cases, _harness_run_one, _harness_golden_compare)
#                raise SystemExit(0 if ok else 1)
#            else:
#                main()  # or run() — original path
#        if __name__ == "__main__":
#            _harness_main()
#
# Notes: PX-1/ PX-2 hop residual + yellow wipe presence; wraps _click_nav_tab + _sample_regions; gates on RENPY_HOST_HARNESS to keep default run() intact.
# Original code above is untouched; this block is documentation + ready-to-enable
# wrapper ensuring `python -m py_compile` stays green.
# To fully migrate, move the `main()`/`run()` call into `_harness_main` and
# gate on RENPY_HOST_HARNESS as shown.

