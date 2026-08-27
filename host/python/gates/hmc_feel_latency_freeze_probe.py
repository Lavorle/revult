"""HuangmeiC feel latency + freeze probe (WP0 baseline).

Gate: RENPY_HOST_GATE=hmc_feel_latency_freeze_probe
"""

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from host.python.gates._harness import gate_harness, parametrized_gate

def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(msg):
    try:
        sys.__stdout__.write("[hmc_feel_latency] " + str(msg))
        sys.__stdout__.write(chr(10))
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        open("/tmp/hmc_feel_latency_freeze_probe.log", "a").write(str(msg) + chr(10))  # noqa: SIM115
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

def _product_presents():
    import renpy_host
    if hasattr(renpy_host, "product_presents"):
        return int(renpy_host.product_presents())
    return -1

def _frame_count():
    import renpy_host
    if hasattr(renpy_host, "frame_count"):
        return int(renpy_host.frame_count())
    return -1

def _screen_live(name):
    import renpy
    try:
        return renpy.display.screen.get_screen(name) is not None
    except Exception:
        return False

def _target_ready(kind):
    import renpy
    if kind == "prefs":
        return _screen_live("preferences")
    if kind == "main_menu":
        mm = bool(getattr(renpy.store, "main_menu", False))
        return mm and (not _screen_live("preferences"))
    if kind == "confirm":
        return _screen_live("confirm")
    if kind == "no_confirm":
        return not _screen_live("confirm")
    return False

def _wait_first_interactive(target_kind, t_action, timeout_s=5.0, stall_s=2.0):
    t0 = t_action
    p0 = _product_presents()
    f0 = _frame_count()
    deadline = t0 + timeout_s
    last_p = p0
    last_progress_t = t0
    first_ms = None
    stall = False
    hang_suspect = False
    crash_exc = None
    while time.monotonic() < deadline:
        try:
            p = _product_presents()
            now = time.monotonic()
            if p > last_p:
                last_p = p
                last_progress_t = now
                if first_ms is None and _target_ready(target_kind):
                    first_ms = (now - t0) * 1000.0
                    break
            if (now - last_progress_t) >= stall_s:
                stall = True
            time.sleep(0.002)
        except Exception as e:
            crash_exc = f"{type(e).__name__}: {e}"
            break
    elapsed = (time.monotonic() - t0) * 1000.0
    if first_ms is None and elapsed >= timeout_s * 1000.0:
        hang_suspect = True
    p1 = _product_presents()
    return {
        "first_interactive_ms": round(first_ms, 3) if first_ms is not None else None,
        "elapsed_ms": round(elapsed, 3),
        "product_presents_delta": (p1 - p0) if p0 >= 0 and p1 >= 0 else -1,
        "frames_delta": (_frame_count() - f0) if f0 >= 0 else -1,
        "stall_ge_2s": bool(stall),
        "hang_suspect": bool(hang_suspect),
        "crash_exc": crash_exc,
        "target_ready_end": _target_ready(target_kind),
    }

def _take_host_gaps():
    """Host-side inter-product-present gaps (ms). SSOT for AC-F p99."""
    try:
        import renpy_host
        take = getattr(renpy_host, "take_inter_present_gaps_ms", None)
        if take is not None:
            return [float(x) for x in list(take())]
        peek = getattr(renpy_host, "inter_present_gaps_ms", None)
        if peek is not None:
            return [float(x) for x in list(peek())]
    except Exception:
        pass
    return []

def _sample_continuous(label, seconds=3.0):
    # Prefer host-recorded present gaps (wall clock at end_frame_present).
    # Legacy 50ms probe-thread polling floors p99 near the poll period and
    # false-fails AC-F while product_fps stays high.
    # Drain residual gaps and start a clean host gap epoch (does not zero
    # product_presents). take_inter_present_gaps_ms clears last_present_at.
    _take_host_gaps()
    t0 = time.monotonic()
    p0 = _product_presents()
    f0 = _frame_count()
    last_p = p0
    last_t = t0
    poll_gaps = []
    while time.monotonic() - t0 < seconds:
        time.sleep(0.01)
        now = time.monotonic()
        p = _product_presents()
        if p > last_p:
            gap = (now - last_t) * 1000.0
            poll_gaps.append(gap)
            last_t = now
            last_p = p
    p1 = _product_presents()
    f1 = _frame_count()
    dt = time.monotonic() - t0
    product_fps = (p1 - p0) / dt if dt > 0 and p0 >= 0 and p1 >= p0 else -1.0
    host_gaps = _take_host_gaps()
    source = "host_present"
    gaps = host_gaps
    if len(gaps) < 2:
        # Fallback only when host FFI is unavailable.
        source = "probe_poll_fallback"
        gaps = poll_gaps
    p99 = None
    max_gap_ms = 0.0
    if gaps:
        sg = sorted(gaps)
        max_gap_ms = float(sg[-1])
        idx = min(len(sg) - 1, round(0.99 * (len(sg) - 1)))
        p99 = float(sg[idx])
    return {
        "label": label,
        "seconds": round(dt, 3),
        "product_fps": round(product_fps, 2),
        "product_presents": (p1 - p0) if p0 >= 0 else -1,
        "host_frames": (f1 - f0) if f0 >= 0 else -1,
        "max_inter_present_ms": round(max_gap_ms, 3),
        "p99_inter_present_ms": round(p99, 3) if p99 is not None else None,
        "gap_count": len(gaps),
        "gap_source": source,
        "host_gap_count": len(host_gaps),
        "poll_gap_count": len(poll_gaps),
    }

def _force_redraw():
    try:
        import renpy
        iface = renpy.game.interface
        if iface is not None:
            iface.force_redraw = True
            iface.restart_interaction = True
    except Exception:
        pass

def _show_prefs(kind="sound_config"):
    import renpy
    try:
        renpy.display.screen.show_screen("preferences", kind=kind)
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        _force_redraw()
        return True, "show_screen"
    except Exception as e1:
        try:
            renpy.store.ShowMenu("preferences")()
            renpy.display.screen.show_screen("preferences", kind=kind)
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            _force_redraw()
            return True, "ShowMenu"
        except Exception as e2:
            return False, f"fail:{e1}/{e2}"

def _hide_prefs():
    import renpy
    try:
        renpy.display.screen.hide_screen("preferences")
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        _force_redraw()
        return True, "hide_screen"
    except Exception as e:
        return False, str(e)

def _show_confirm():
    import renpy
    try:
        renpy.display.screen.show_screen(
            "confirm",
            message="feel latency probe",
            yes_action=[renpy.store.Hide("confirm")],
            no_action=[renpy.store.Hide("confirm")],
            confirm_type="quit",
        )
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        _force_redraw()
        return True, "show_screen"
    except Exception as e:
        return False, str(e)

def _hide_confirm():
    import renpy
    try:
        renpy.display.screen.hide_screen("confirm")
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        _force_redraw()
        return True, "hide_screen"
    except Exception as e:
        return False, str(e)

def _attempt_take_focuses_repro():
    import renpy
    result = {"attempted": True, "crash": False, "error": None, "steps": []}
    try:
        ok, how = _show_prefs("sound_config")
        result["steps"].append(f"open_prefs:{ok}:{how}")
        time.sleep(0.4)
        ok, how = _hide_prefs()
        result["steps"].append(f"hide_prefs:{ok}:{how}")
        time.sleep(0.3)
        try:
            renpy.display.focus.take_focuses()
            result["steps"].append("take_focuses:ok")
        except Exception as e:
            result["crash"] = True
            result["error"] = f"{type(e).__name__}: {e}"
            result["steps"].append("take_focuses:CRASH")
            result["traceback"] = traceback.format_exc()
            return result
        for i in range(3):
            _show_prefs("text_config" if i % 2 else "sound_config")
            time.sleep(0.15)
            _hide_prefs()
            time.sleep(0.15)
            try:
                renpy.display.focus.take_focuses()
                result["steps"].append("cycle%d_take_focuses:ok" % i)  # noqa: UP031
            except Exception as e:
                result["crash"] = True
                result["error"] = f"{type(e).__name__}: {e}"
                result["steps"].append("cycle%d_take_focuses:CRASH" % i)  # noqa: UP031
                result["traceback"] = traceback.format_exc()
                return result
    except Exception as e:
        result["crash"] = True
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()
    return result

def probe():

    import renpy
    out_txt = _base() / "host" / "target" / "gate-hmc_feel_latency_freeze_probe.txt"
    out_json = _base() / "host" / "target" / "gate-hmc_feel_latency_freeze_probe.json"
    lines = []
    report = {
        "gate": "hmc_feel_latency_freeze_probe",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "measurements": {},
        "continuous": {},
        "ac_z": {},
        "take_focuses_repro": {},
        "h_rank_hints": [],
    }
    deadline = time.time() + 90.0
    while time.time() < deadline:
        try:
            if bool(getattr(renpy.store, "main_menu", False)):
                break
        except Exception:
            pass
        time.sleep(0.2)
    mm = bool(getattr(renpy.store, "main_menu", False))
    _log(f"main_menu={mm} frames={_frame_count()} product={_product_presents()}")
    lines.append(f"main_menu={mm}")
    report["main_menu"] = mm
    cont_mm = _sample_continuous("main_menu", 3.0)
    report["continuous"]["main_menu"] = cont_mm
    lines.append(f"continuous_main_menu={cont_mm}")
    t_action = time.monotonic()
    ok, how = _show_prefs("sound_config")
    m_open = _wait_first_interactive("prefs", t_action)
    m_open["action_ok"] = ok
    m_open["action_how"] = how
    report["measurements"]["prefs_open"] = m_open
    lines.append(f"prefs_open={m_open}")
    time.sleep(0.3)
    cont_prefs = _sample_continuous("prefs_idle", 2.5)
    report["continuous"]["prefs_idle"] = cont_prefs
    lines.append(f"continuous_prefs_idle={cont_prefs}")
    t_action = time.monotonic()
    ok, how = _show_prefs("text_config")
    m_page = _wait_first_interactive("prefs", t_action)
    m_page["action_ok"] = ok
    m_page["action_how"] = how
    m_page["page"] = "text_config"
    report["measurements"]["prefs_page"] = m_page
    lines.append(f"prefs_page={m_page}")
    time.sleep(0.2)
    t_action = time.monotonic()
    ok, how = _show_prefs("dialog_config_1")
    m_page2 = _wait_first_interactive("prefs", t_action)
    m_page2["action_ok"] = ok
    m_page2["action_how"] = how
    m_page2["page"] = "dialog_config_1"
    report["measurements"]["prefs_page_2"] = m_page2
    lines.append(f"prefs_page_2={m_page2}")
    t_action = time.monotonic()
    ok, how = _hide_prefs()
    m_ret = _wait_first_interactive("main_menu", t_action)
    m_ret["action_ok"] = ok
    m_ret["action_how"] = how
    report["measurements"]["prefs_return"] = m_ret
    lines.append(f"prefs_return={m_ret}")
    time.sleep(0.3)
    t_action = time.monotonic()
    ok, how = _show_confirm()
    m_copen = _wait_first_interactive("confirm", t_action)
    m_copen["action_ok"] = ok
    m_copen["action_how"] = how
    report["measurements"]["confirm_open"] = m_copen
    lines.append(f"confirm_open={m_copen}")
    time.sleep(0.2)
    t_action = time.monotonic()
    ok, how = _hide_confirm()
    m_cclose = _wait_first_interactive("no_confirm", t_action)
    m_cclose["action_ok"] = ok
    m_cclose["action_how"] = how
    report["measurements"]["confirm_close"] = m_cclose
    lines.append(f"confirm_close={m_cclose}")
    time.sleep(0.2)
    repro = _attempt_take_focuses_repro()
    report["take_focuses_repro"] = repro
    lines.append(f"take_focuses_repro={repro}")
    stall_any = any(bool((report["measurements"].get(k) or {}).get("stall_ge_2s")) for k in report["measurements"])
    hang_any = any(bool((report["measurements"].get(k) or {}).get("hang_suspect")) for k in report["measurements"])
    crash_any = bool(repro.get("crash")) or any((report["measurements"].get(k) or {}).get("crash_exc") for k in report["measurements"])
    report["ac_z"] = {
        "hang": hang_any,
        "stall_ge_2s": stall_any,
        "crash": crash_any,
        "take_focuses_none": bool(repro.get("crash")) and "NoneType" in str(repro.get("error") or ""),
    }
    lines.append("ac_z={}".format(report["ac_z"]))
    fi_keys = ["prefs_open", "prefs_page", "prefs_page_2", "prefs_return", "confirm_open", "confirm_close"]
    fi_vals = []
    for k in fi_keys:
        v = (report["measurements"].get(k) or {}).get("first_interactive_ms")
        lines.append(f"first_interactive_ms_{k}={v}")
        if v is not None:
            fi_vals.append(float(v))
    report["first_interactive_max_ms"] = max(fi_vals) if fi_vals else None
    report["first_interactive_min_ms"] = min(fi_vals) if fi_vals else None
    report["ac_t_pass_lt_200ms"] = bool(fi_vals) and all(v < 200.0 for v in fi_vals)
    lines.append("ac_t_pass_lt_200ms={}".format(report["ac_t_pass_lt_200ms"]))
    lines.append("first_interactive_max_ms={}".format(report["first_interactive_max_ms"]))
    p99s = [c.get("p99_inter_present_ms") for c in report["continuous"].values() if c.get("p99_inter_present_ms") is not None]
    report["p99_inter_present_max_ms"] = max(p99s) if p99s else None
    report["ac_f_proxy_p99_le_66"] = bool(p99s) and all(v <= 66.0 for v in p99s)
    # AC-P99 mission success (wgpu-perf-stutter): both continuous surfaces ≤8.3ms.
    cont = report.get("continuous") or {}
    mm_p99 = (cont.get("main_menu") or {}).get("p99_inter_present_ms")
    prefs_p99 = (cont.get("prefs_idle") or {}).get("p99_inter_present_ms")
    report["ac_p99_main_menu_ms"] = mm_p99
    report["ac_p99_prefs_idle_ms"] = prefs_p99
    report["ac_p99_pass_le_8_3"] = (
        mm_p99 is not None
        and prefs_p99 is not None
        and float(mm_p99) <= 8.3
        and float(prefs_p99) <= 8.3
    )
    lines.append("p99_inter_present_max_ms={}".format(report["p99_inter_present_max_ms"]))
    lines.append("ac_f_proxy_p99_le_66={}".format(report["ac_f_proxy_p99_le_66"]))
    lines.append(f"ac_p99_main_menu_ms={mm_p99}")
    lines.append(f"ac_p99_prefs_idle_ms={prefs_p99}")
    lines.append("ac_p99_pass_le_8_3={}".format(report["ac_p99_pass_le_8_3"]))
    h_hints = []
    if report["ac_z"].get("take_focuses_none") or report["ac_z"].get("crash"):
        h_hints.append({"id": "H2", "title": "take_focuses / screen_render None crash", "evidence": repro.get("error") or "crash path", "severity_hint": "high"})
    high_lat = [k for k in fi_keys if ((report["measurements"].get(k) or {}).get("first_interactive_ms") or 0) >= 200]
    if high_lat:
        h_hints.append({"id": "H3", "title": "transition RT rebuild / full-tree invalidate", "evidence": f"high first_interactive on {high_lat}", "severity_hint": "high"})
    if stall_any or hang_any:
        h_hints.append({"id": "H4", "title": "event_wait / can_block / nested pump stalls", "evidence": f"stall={stall_any} hang={hang_any}", "severity_hint": "high"})
    h_hints.append({"id": "H1", "title": "about_to_wait always window.request_redraw busy-wake residual", "evidence": "host/renpy-host/src/main.rs about_to_wait", "severity_hint": "med"})
    max_p99 = report.get("p99_inter_present_max_ms")
    if max_p99 is not None and max_p99 > 40:
        h_hints.append({"id": "H5", "title": "PresentMode Fifo present-wait", "evidence": f"p99_inter_present_ms={max_p99}", "severity_hint": "low-med"})
    else:
        h_hints.append({"id": "H5", "title": "PresentMode Fifo present-wait", "evidence": f"not dominant p99={max_p99}", "severity_hint": "low"})
    order = {"H2": 0, "H4": 1, "H3": 2, "H1": 3, "H5": 4}
    h_hints.sort(key=lambda h: order.get(h["id"], 9))
    report["h_rank_hints"] = h_hints
    for i, h in enumerate(h_hints, 1):
        lines.append("H%d_rank=%s severity=%s evidence=%s" % (i, h["id"], h["severity_hint"], h["evidence"]))  # noqa: UP031
    report["ok"] = True
    report["measured"] = True
    lines.append("ok=True")
    lines.append("measured=True")
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    out_json.write_text(json.dumps(report, indent=2, default=str) + chr(10), encoding="utf-8")
    _log(f"wrote {out_txt}")
    time.sleep(0.3)
    _quit()

def main():
    open("/tmp/hmc_feel_latency_freeze_probe.log", "w").write("start" + chr(10))  # noqa: SIM115
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
            good, missing, error, _extra = call()
            _log(f"stage {name} good={good} missing={missing} error={error!r}")
            if not good:
                _log(f"bootstrap fail {name}")
                _quit()
                return
        import renpy
        renpy.host_build = True
        try:
            import renpy_main_host
            renpy_main_host.install(renpy)
        except Exception as e:
            _log(f"main_host: {e}")
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
            _log(f"args fail {e}")
            _quit()
            return
        threading.Thread(target=probe, daemon=True).start()
        try:
            _log("entering renpy.main.main()")
            renpy.main.main()
        except SystemExit:
            pass
        except Exception as e:
            _log(f"main exc {e}")
            _log(traceback.format_exc())
        finally:
            _quit()
    except Exception as e:
        _log(f"main outer exc {e}")
        _log(traceback.format_exc())
        _quit()

if __name__ == "__main__":
    main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
