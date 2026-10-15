"""Per-frame FFI submission stats at 2560x1440 (prefs dense page).

Gate: RENPY_HOST_GATE=hmc_drawstats_1440

Boots HuangmeiC product path, waits for main_menu, forces the drawable to
2560x1440 (parity with the user playtest fullscreen), opens preferences
(sound_config), hovers hard for ~8s while a background thread samples:
  - renpy_host.frame_cmd_count()  (per-frame command-list length)
  - get_frame_stats()             (draw_calls / instances / ms)
  - PHASE0 draw_frame_ms lines are counted when the host prints them
Writes: host/target/gate-hmc_drawstats_1440.txt
"""

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path


def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")


def _log(msg):
    line = f"[hmc_drawstats_1440] {msg}\n"
    try:
        sys.__stdout__.write(line)
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        open("/tmp/hmc_drawstats_1440.log", "a").write(msg + "\n")
    except Exception:
        pass


def _quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:
        pass


def _force_window():
    try:
        import renpy_host

        rws = getattr(renpy_host, "request_window_size", None)
        if rws is not None:
            rws(2560, 1440)
            _log("window forced 2560x1440")
    except Exception as e:
        _log(f"window force fail: {e}")


_SAMPLING = threading.Event()
_STATS = {"cmd": [], "dc": [], "inst": [], "ms": []}


def _sampler():
    while not _SAMPLING.is_set():
        try:
            import renpy_host

            fid = getattr(renpy_host, "frame_cmd_count", None)
            if fid is not None:
                _STATS["cmd"].append(int(fid()))
            gfs = getattr(renpy_host, "get_frame_stats", None)
            if gfs is not None:
                s = gfs()
                _STATS["dc"].append(int(s.get("draw_calls", 0)))
                _STATS["inst"].append(int(s.get("instances", 0)))
                _STATS["ms"].append(float(s.get("ms", 0.0)))
        except Exception as e:
            _log(f"sampler soft-fail: {e}")
            break
        time.sleep(0.5)


def _pctl(xs, p):
    if not xs:
        return -1
    xs = sorted(xs)
    i = min(len(xs) - 1, int(len(xs) * p))
    return xs[i]


def _hover_thrash(seconds=8.0):
    import renpy_host

    t0 = time.monotonic()
    xs = [220, 640, 1280, 1900, 2400, 1500]
    ys = [180, 400, 720, 1040, 1300]
    i = 0
    while time.monotonic() - t0 < seconds:
        x = xs[i % len(xs)]
        y = ys[(i // len(xs)) % len(ys)]
        try:
            renpy_host.inject_mouse(int(x), int(y), 0, False)
        except Exception as e:
            if i == 0:
                _log(f"hover inject soft-fail: {e}")
        i += 0 if False else 1
        time.sleep(0.06)
    return i


def probe():
    import renpy_host

    import renpy

    _force_window()
    out = _base() / "host" / "target" / "gate-hmc_drawstats_1440.txt"
    lines = []

    deadline = time.time() + 180.0
    while time.time() < deadline:
        try:
            if bool(getattr(renpy.store, "main_menu", False)):
                break
        except Exception:
            pass
        time.sleep(0.2)
    mm = bool(getattr(renpy.store, "main_menu", False))
    _log(f"main_menu={mm}")
    lines.append(f"main_menu={mm}")

    try:
        try:
            renpy.restart_interaction()
        except Exception:
            pass
    except Exception as e:
        _log(f"show prefs fail: {e}")

    t = threading.Thread(target=_sampler, daemon=True)
    hovers = _hover_thrash(8.0)
    _SAMPLING.set()
    t.join(timeout=1.0)

    def p(name, xs, unit=""):
        lines.append(
            f"{name} p50={_pctl(xs, 0.5)} p95={_pctl(xs, 0.95)} max={_pctl(xs, 1.0)} n={len(xs)} {unit}"
        )
        _log(f"{name} p50={_pctl(xs,0.5)} p95={_pctl(xs,0.95)} max={_pctl(xs,1.0)} n={len(xs)} {unit}")

    p("frame_cmd_count", _STATS["cmd"])
    p("draw_calls", _STATS["dc"])
    p("instances", _STATS["inst"])
    p("frame_ms", [round(x, 2) for x in _STATS["ms"]], "ms")
    lines.append(f"hovers={hovers}")
    _log(f"hovers={hovers}")

    txt = "\n".join(lines)
    out.write_text(txt + "\n")
    _log("wrote " + str(out))
    _quit()


if os.environ.get("RENPY_HOST_GATE", "") == "hmc_drawstats_1440":
    try:
        probe()
    except Exception:
        _log("probe fail: " + traceback.format_exc()[:2000])
        try:
            _quit()
        except Exception:
            pass
