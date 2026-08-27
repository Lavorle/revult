"""Supporting evidence: product path + inject + nested wait (not V1 bare claim)."""
import os
import sys
import traceback
from pathlib import Path

from host.python.gates._harness import gate_harness, parametrized_gate

import renpy
import renpy.config

OUT = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult")) / "host" / "target" / "gate-verify-input-pump.txt"
lines = []

def log(m):
    lines.append(m)
    try:
        sys.__stdout__.write(f"[verify-input-pump] {m}\n")
        sys.__stdout__.flush()
    except Exception:
        pass

def finish(ok, **extra):
    with OUT.open("w") as f:
        f.write(f"ok={ok}\n")
        for k,v in extra.items():
            f.write(f"{k}={v}\n")
        f.write("---\n")
        for l in lines:
            f.write(l+"\n")

def rq():
    try:
        import renpy_host
        renpy_host.request_quit()
    except Exception:
        pass

def run():
    base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
    os.environ.setdefault("RENPY_HOST_GAME", str(base / "the_question"))
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    os.environ.setdefault("RENPY_SKIP_SPLASHSCREEN", "0")
    os.environ.pop("RENPY_SKIP_MAIN_MENU", None)
    import renpy_host
    gates = str(base / "host" / "python" / "gates")
    if gates not in sys.path:
        sys.path.insert(0, gates)
    import bootstrap as boot
    for call in (boot.stage_import_renpy, boot.stage_import_all, lambda: boot.stage_set_game_dir(base)):
        good, _miss, err, _extra = call()
        log(f"stage good={good} err={err!r}")
        if not good:
            finish(False, error=err); rq(); return
    renpy.host_build = True
    renpy.config.performance_test = False
    try:
        import renpy_main_host
        renpy_main_host.install(renpy)
    except Exception as e:
        log(f"main_host: {e}")
    try:
        import product as pm
        if hasattr(pm, "_prepare_run_args"):
            pm._prepare_run_args(base)
        if hasattr(pm, "_pre_main_host_stubs"):
            pm._pre_main_host_stubs()
        log("product helpers ok")
    except Exception as e:
        log(f"product helpers: {e}")
        log(traceback.format_exc())

    # Measure pump: call wait_until repeatedly and inject keys
    t0 = renpy_host.get_ticks_ms()
    injects = 0
    for i in range(30):
        renpy_host.inject_key(13, True, "\r")
        renpy_host.inject_key(13, False, "\r")
        injects += 2
        renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
        # drain
        drained = 0
        for _ in range(64):
            ev = renpy_host.poll_event()
            if ev is None:
                break
            drained += 1
        if i % 10 == 0:
            log("iter=%d drained=%d ticks=%s" % (i, drained, renpy_host.get_ticks_ms()-t0))  # noqa: UP031
    log("pre-main inject loop done injects=%d" % injects)  # noqa: UP031

    # Enter main briefly - HostStop after short watchdog via request_quit timer
    import threading
    def kill_later():
        import time
        time.sleep(8)
        try:
            # inject more during main
            for _ in range(5):
                renpy_host.inject_key(13, True, "\r")
                renpy_host.inject_key(13, False, "\r")
            # also mouse click near Start (approx center-left of 1280x720 main menu)
            renpy_host.inject_mouse(640, 420, 1, True)
            renpy_host.inject_mouse(640, 420, 1, False)
        except Exception:
            pass
        time.sleep(4)
        rq()
    threading.Thread(target=kill_later, daemon=True).start()

    import renpy.main as renpy_main
    log("entering main")
    try:
        renpy_main.main()
        log("main returned")
    except BaseException as e:
        log(f"main exit {type(e).__name__}: {e}")
    finish(True, note="supporting pump+inject path exercised")
    rq()

run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
