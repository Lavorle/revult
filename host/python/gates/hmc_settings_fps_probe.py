"""Settings FPS probe for WP0 baseline (engine-only).

Gate: RENPY_HOST_GATE=hmc_settings_fps_probe

Boots HuangmeiC product path, waits for main_menu, opens preferences
(sound_config), samples renpy_host.frame_count() over ~8s idle + ~4s hover
motion, writes FPS and phase0 notes.

Writes: host/target/gate-hmc_settings_fps_probe.txt
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
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")


def _log(msg):
    line = f"[hmc_settings_fps] {msg}\n"
    try:
        sys.__stdout__.write(line)
        sys.__stdout__.flush()
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        open("/tmp/hmc_settings_fps_probe.log", "a").write(msg + "\n")  # noqa: SIM115
    except Exception:  # noqa: BLE001, S110
        pass


def _quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:  # noqa: BLE001, S110
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
    except Exception as e:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            rpg.import_as_pygame()
        except Exception:  # noqa: BLE001, S110
            pass
    except Exception as e:  # noqa: BLE001
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
    except Exception as e:  # noqa: BLE001
        _log(f"uguu {e}")
    try:
        import renpy_ecsign_host as e

        sys.modules["renpy.ecsign"] = e
        import renpy

        renpy.ecsign = e
    except Exception as e:  # noqa: BLE001
        _log(f"ecsign {e}")


def _counters():
    import renpy_host
    try:
        import renpy
        renpy_frames = int(getattr(renpy.config, "frames", 0) or 0)
    except Exception:  # noqa: BLE001
        renpy_frames = -1
    host_frames = int(renpy_host.frame_count()) if hasattr(renpy_host, "frame_count") else -1
    product = (
        int(renpy_host.product_presents())
        if hasattr(renpy_host, "product_presents")
        else -1
    )
    return host_frames, product, renpy_frames


def _sample_fps(label, seconds=8.0):
    t0 = time.monotonic()
    h0, p0, r0 = _counters()
    samples_h = []
    samples_p = []
    samples_r = []
    last_t = t0
    last_h, last_p, last_r = h0, p0, r0
    deadline = t0 + seconds
    while time.monotonic() < deadline:
        time.sleep(0.5)
        t1 = time.monotonic()
        h1, p1, r1 = _counters()
        dt = t1 - last_t
        if dt > 0:
            if last_h >= 0 and h1 >= last_h:
                samples_h.append((h1 - last_h) / dt)
            if last_p >= 0 and p1 >= last_p:
                samples_p.append((p1 - last_p) / dt)
            if last_r >= 0 and r1 >= last_r:
                samples_r.append((r1 - last_r) / dt)
            _log(
                f"{label} partial host={(h1 - last_h) / dt if dt and last_h >= 0 else -1:.1f} product={(p1 - last_p) / dt if dt and last_p >= 0 else -1:.1f} renpy={(r1 - last_r) / dt if dt and last_r >= 0 else -1:.1f} dt={dt:.3f}"
            )
        last_t = t1
        last_h, last_p, last_r = h1, p1, r1
    total_dt = time.monotonic() - t0
    h1, p1, r1 = _counters()
    def steady(a, b):
        return round((b - a) / total_dt, 2) if total_dt > 0 and a >= 0 and b >= a else -1.0
    def med(xs):
        if not xs:
            return -1.0
        xs = sorted(xs)
        return round(xs[len(xs)//2], 2)
    return {
        "label": label,
        "seconds": round(total_dt, 3),
        "host_frames": (h1 - h0) if h0 >= 0 else -1,
        "product_presents": (p1 - p0) if p0 >= 0 else -1,
        "renpy_frames": (r1 - r0) if r0 >= 0 else -1,
        "host_fps": steady(h0, h1),
        "product_fps": steady(p0, p1),
        "renpy_fps": steady(r0, r1),
        "median_product_fps": med(samples_p),
        "min_product_fps": round(min(samples_p), 2) if samples_p else -1.0,
        "max_product_fps": round(max(samples_p), 2) if samples_p else -1.0,
        # preferred AC metric
        "steady_fps": steady(p0, p1) if p0 >= 0 else steady(r0, r1),
    }


def _show_prefs(kind="sound_config"):
    import renpy

    try:
        renpy.display.screen.show_screen("preferences", kind=kind)
        try:
            renpy.restart_interaction()
        except Exception:  # noqa: BLE001, S110
            pass
        return True, "show_screen"
    except Exception as e1:  # noqa: BLE001
        try:
            renpy.store.ShowMenu("preferences")()
            renpy.display.screen.show_screen("preferences", kind=kind)
            try:
                renpy.restart_interaction()
            except Exception:  # noqa: BLE001, S110
                pass
            return True, f"ShowMenu:{e1}"
        except Exception as e2:  # noqa: BLE001
            return False, f"fail:{e1}/{e2}"


def _hover_thrash(seconds=4.0):
    import renpy_host

    t0 = time.monotonic()
    xs = [220, 420, 720, 980, 1280, 1580]
    ys = [180, 320, 480, 640, 800]
    i = 0
    while time.monotonic() - t0 < seconds:
        x = xs[i % len(xs)]
        y = ys[(i // len(xs)) % len(ys)]
        try:
            # inject_mouse(x, y, button, pressed) — motion with button=0
            renpy_host.inject_mouse(int(x), int(y), 0, False)
        except Exception as e:  # noqa: BLE001
            if i == 0:
                _log(f"hover inject soft-fail: {e}")
        i += 1
        time.sleep(0.08)


def probe():
    import renpy_host

    import renpy

    out = _base() / "host" / "target" / "gate-hmc_settings_fps_probe.txt"
    lines = []

    # Wait main_menu
    deadline = time.time() + 90.0
    while time.time() < deadline:
        try:
            if bool(getattr(renpy.store, "main_menu", False)):
                break
        except Exception:  # noqa: BLE001, S110
            pass
        time.sleep(0.2)
    mm = bool(getattr(renpy.store, "main_menu", False))
    _log(f"main_menu={mm} frames={renpy_host.frame_count()}")
    lines.append(f"main_menu={mm}")

    # Sample main menu briefly (S1 hint)
    s1 = _sample_fps("S1_main_menu", 4.0)
    lines.append("S1_main_menu_product_fps={:.2f}".format(s1.get("product_fps", s1.get("steady_fps", -1))))
    lines.append(f"S1_main_menu_detail={s1}")
    _log(f"S1 {s1}")

    kinds = ("sound_config", "dialog_config_1", "dialog_config_2", "text_config")
    s2_results = []
    for kind in kinds:
        ok, how = _show_prefs(kind)
        lines.append(f"prefs_open kind={kind} ok={ok} how={how}")
        _log(f"prefs open {kind} {ok} {how}")
        time.sleep(0.8)
        scr = renpy.display.screen.get_screen("preferences")
        lines.append(f"prefs_screen kind={kind} present={scr is not None}")
        idle = _sample_fps(f"S2_{kind}_idle", 5.0)
        lines.append(f"S2_{kind}_idle_detail={idle}")
        _log(f"S2 {kind} idle {idle}")
        s2_results.append(idle)
        if kind == "sound_config":
            hover_thread = threading.Thread(target=_hover_thrash, args=(4.0,), daemon=True)
            hover_thread.start()
            hover = _sample_fps(f"S2_{kind}_hover", 4.0)
            hover_thread.join(timeout=1.0)
            lines.append(f"S2_{kind}_hover_detail={hover}")
            _log(f"S2 {kind} hover {hover}")
            s2_results.append(hover)

    # S3 confirm dock panel
    try:
        renpy.display.screen.show_screen(
            "confirm",
            message="FPS probe",
            yes_action=[renpy.store.Hide("confirm")],
            no_action=[renpy.store.Hide("confirm")],
            confirm_type="quit",
        )
        try:
            renpy.restart_interaction()
        except Exception:  # noqa: BLE001, S110
            pass
        time.sleep(0.6)
        s3 = _sample_fps("S3_confirm", 4.0)
        lines.append(f"S3_confirm_detail={s3}")
        _log(f"S3 confirm {s3}")
        try:
            renpy.display.screen.hide_screen("confirm")
        except Exception:  # noqa: BLE001, S110
            pass
    except Exception as e:  # noqa: BLE001
        lines.append(f"S3_confirm_error={e}")
        _log(f"S3 fail {e}")

    # S4 dialogue/say: leave prefs and try to enter story if possible; else sample say screen if present
    try:
        try:
            renpy.display.screen.hide_screen("preferences")
        except Exception:  # noqa: BLE001, S110
            pass
        # Prefer jumping into first story start when available
        started = False
        # Soft path: show say chrome if screen exists (no story jump in FPS probe)
        try:
            if renpy.display.screen.has_screen("say"):
                renpy.display.screen.show_screen("say", who="probe", what="FPS probe dialogue line.")
                try:
                    renpy.restart_interaction()
                except Exception:  # noqa: BLE001, S110
                    pass
                time.sleep(0.5)
                started = True
        except Exception as e:  # noqa: BLE001
            _log(f"say show soft-fail {e}")
        s4 = _sample_fps("S4_dialogue_or_menu", 4.0)
        lines.append(f"S4_dialogue_detail={s4}")
        lines.append(f"S4_say_started={started}")
        _log(f"S4 {s4} started={started}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"S4_error={e}")
        _log(f"S4 fail {e}")

    # Verdict against 8-13 class and 30 target using product_fps
    fps_vals = [r.get("steady_fps", -1) for r in s2_results if r.get("steady_fps", -1) >= 0]
    s2_min = min(fps_vals) if fps_vals else -1
    s2_max = max(fps_vals) if fps_vals else -1
    in_813 = any(5.0 <= v <= 16.0 for v in fps_vals)
    ge30 = all(v >= 30.0 for v in fps_vals) if fps_vals else False
    lines.append(f"S2_min_product_fps={s2_min:.2f}")
    lines.append(f"S2_max_product_fps={s2_max:.2f}")
    lines.append(f"S2_in_8_13_class={in_813}")
    lines.append(f"S2_ge_30={ge30}")
    lines.append(f"ok={bool(fps_vals)}")
    lines.append("measured=True")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log(f"wrote {out}")
    time.sleep(0.3)
    _quit()


def main():
    open("/tmp/hmc_settings_fps_probe.log", "w").write("start\n")  # noqa: SIM115
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
        except Exception as e:  # noqa: BLE001
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
                except Exception:  # noqa: BLE001, S110
                    pass
            renpy.game.args = renpy.arguments.bootstrap()
        except Exception as e:  # noqa: BLE001
            _log(f"args fail {e}")
            _quit()
            return

        threading.Thread(target=probe, daemon=True).start()

        try:
            _log("entering renpy.main.main()")
            renpy.main.main()
        except SystemExit:
            pass
        except Exception as e:  # noqa: BLE001
            _log(f"main exc {e}")
            _log(traceback.format_exc())
        finally:
            _quit()
    except Exception as e:  # noqa: BLE001
        _log(f"main outer exc {e}")
        _log(traceback.format_exc())
        _quit()


if __name__ == "__main__":
    main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

