"""Settings dwell degradation probe (single instance, RENPY_HOST_GATE=hmc_dwell_gate).

Reproduces the "settings page gets slower over time" signature seen in the
user repro log (nodes constant @115, per-frame cost 3.7 -> 15.6ms over 250s):
- wait main menu, open preferences(image_config), click text_config tab,
- dwell 360s on text_config with a 2s hover-thrash every 30s (real-user-like
  pointer motion), sampling process RSS every second;
- cProfile two 15s slices: slice A at t=45..60s (healthy), slice B at
  t=330..345s (degraded); dumps .pstats to docs logs for before/after diff.

Writes:
- /tmp/hmc_dwell.log       probe log lines ([hmc_dwell] ...);
- /tmp/hmc_dwell_scen.log  scenario markers;
- RSS samples inline via _log (DWELLRSS ...);
- .pstats dumps: host/target/dwell_sliceA.pstats, .../dwell_sliceB.pstats
"""

import os
import sys
import time

import hmc_dyn_gate as dyn


def _rss_kb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except Exception:
        return -1
    return -1


def _pstats_dump(pr, path):
    try:
        import io
        import pstats

        pr.disable()
        pr.dump_stats(path)
        buf = io.StringIO()
        pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(25)
        dyn._log(f"pstats dump {path}\n{buf.getvalue()[:4000]}")
    except Exception as e:  # noqa: BLE001
        dyn._log(f"pstats dump fail {path}: {e}")


def probe():
    import renpy

    dyn._force_window()
    mm_wait = dyn._wait_main_menu(90.0)
    dyn._scen(f"main_menu_ready waited={mm_wait}")
    time.sleep(1.0)

    opened = False
    try:
        renpy.display.screen.show_screen("preferences", kind="image_config")
        renpy.restart_interaction()
        opened = True
    except Exception as e:  # noqa: BLE001
        dyn._log(f"prefs open fail: {e}")
    dyn._scen(f"prefs_opened={opened}")
    time.sleep(1.2)

    i = dyn.TAB_NAMES.index("text_config")
    x, y = dyn.TAB_X[i], dyn.TAB_Y
    try:
        dyn._click(x, y)
    except Exception as e:  # noqa: BLE001
        dyn._log(f"tab click err {e}")
    dyn._scen(f"text_config_clicked x={x} y={y}")
    time.sleep(1.5)

    dwell0 = time.time()
    DUR = 220.0
    sliceA = sliceB = None
    profiler = None
    prof_phase = 0
    next_thrash = dwell0 + 20.0
    while True:
        el = time.time() - dwell0
        if el >= DUR:
            break
        # periodic 2s hover thrash with the same _hover paths (pointer motion)
        # DISABLED for the no-input control run: synthetic thrash is far more
        # aggressive than a real user; measure the bare dwell first.
        if False and time.time() >= next_thrash:
            dyn._scen(f"DWELL thrash at t={el:.1f}")
            try:
                dyn._hover_thrash(2.0)
            except Exception as e:  # noqa: BLE001
                dyn._log(f"thrash fail {e}")
            next_thrash = time.time() + 28.0
        try:
            import renpy_host

            renpy_host.request_quit() if False else None
        except Exception:
            pass
        dyn._log(
            f"DWELLRSS t={el:.1f} rss_kb={_rss_kb()} "
            f"drawn_recent={getattr(renpy.game.interface, 'frames', '?')}"
        )
        try:
            from renpy.audio import audio as _aa

            _np = getattr(_aa, "all_channels", None)
            nchan = len(_np) if _np is not None else -1
            _mcd = renpy.game.context().music
            nmcd = len(_mcd) if hasattr(_mcd, "keys") else -1
            try:
                from renpy.audio import renpysound_host as _rs

                nrs = len(_rs._channels)
            except Exception:  # noqa: BLE001
                nrs = -1
            dyn._log(
                f"AUDCOUNT t={el:.1f} all_channels={nchan} music_ctx={nmcd} rs_channels={nrs} "
                f"names={list(_np[-8:]) if _np else '-'}"
            )
        except Exception as e:  # noqa: BLE001
            dyn._log(f"AUDCOUNT fail {e}")
        # profiler slices
        if 45.0 <= el < 52.0 and prof_phase < 1:
            import cProfile as _cpf

            profiler = _cpf.Profile()
            profiler.enable()
            prof_phase = 1
        if prof_phase == 1 and el >= 60.0:
            if profiler is not None:
                _pstats_dump(profiler, str(dyn._base() / "host/target/dwell_sliceA.pstats"))
            profiler = None
            prof_phase = 2
        if 180.0 <= el < 182.0 and prof_phase == 2:
            import cProfile as _cpf

            profiler = _cpf.Profile()
            profiler.enable()
            prof_phase = 3
        if prof_phase == 3 and el >= 196.0:
            if profiler is not None:
                _pstats_dump(profiler, str(dyn._base() / "host/target/dwell_sliceB.pstats"))
            profiler = None
        time.sleep(1.0)
    dyn._log(f"dwell done rss_kb={_rss_kb()}")
    dyn._quit()


def main():
    import hmc_dyn_gate  # noqa: F401 -- module-level patching when needed

    open("/tmp/hmc_dwell.log", "w").write("start\n")
    try:
        os.remove("/tmp/hmc_dwell_scen.log")
    except FileNotFoundError:
        pass
    base = dyn._base()
    game = os.environ.get("RENPY_HOST_GAME") or str(base / "host" / "playtests" / "HuangmeiC")
    os.environ["RENPY_HOST_BASE"] = str(base)
    os.environ["RENPY_HOST_BUILD"] = "1"
    os.environ["RENPY_HOST_GAME"] = game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    dyn._clear_falsey("RENPY_SKIP_MAIN_MENU")
    dyn._clear_falsey("RENPY_SKIP_SPLASHSCREEN")
    for path in (str(base / "host" / "python" / "gates"), str(base / "host" / "python")):
        if path not in sys.path:
            sys.path.insert(0, path)

    try:
        dyn._stubs()
        import bootstrap as boot

        for name, call in (
            ("import_renpy", boot.stage_import_renpy),
            ("import_all", boot.stage_import_all),
            ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
        ):
            good, missing, error, _extra = call()
            dyn._log(f"stage {name} good={good} missing={missing} error={error!r}")
            if not good:
                dyn._log(f"bootstrap fail {name}")
                dyn._quit()
                return

        import renpy

        renpy.host_build = True
        try:
            import renpy_main_host

            renpy_main_host.install(renpy)
        except Exception as e:  # noqa: BLE001
            dyn._log(f"main_host: {e}")
        try:
            import renpy.arguments

            basedir = getattr(renpy.config, "basedir", None) or game
            argv0 = sys.argv[0] if sys.argv else "renpy-host"
            sys.argv = [argv0, basedir, "run"]
            if not getattr(renpy.arguments, "commands", None):
                try:
                    renpy.arguments.register_command("run", renpy.arguments.run, True)
                    renpy.arguments.register_command("quit", renpy.arguments.quit)
                except Exception:  # noqa: BLE001
                    pass
            renpy.game.args = renpy.arguments.bootstrap()
        except Exception as e:  # noqa: BLE001
            dyn._log(f"args fail {e}")
            dyn._quit()
            return

        import threading

        threading.Thread(target=probe, daemon=True, name="hmc_dwell_probe").start()

        try:
            dyn._log("entering renpy.main.main()")
            renpy.main.main()
        except SystemExit:
            pass
        except Exception as e:  # noqa: BLE001
            dyn._log(f"main exc {e}")
            import traceback as _tb

            dyn._log(_tb.format_exc())
        finally:
            dyn._quit()
    except Exception as e:  # noqa: BLE001
        dyn._log(f"main outer exc {e}")
        import traceback as _tb

        dyn._log(_tb.format_exc())
        dyn._quit()


if __name__ == "__main__":
    main()
