"""After request_window_size, main_menu left overlay must stay dark (not scenic)."""
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


base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
sys.path.insert(0, str(base / "host" / "python" / "gates"))
os.environ.setdefault("RENPY_HOST_BASE", str(base))
os.environ.setdefault("RENPY_HOST_BUILD", "1")
os.environ.setdefault("RENPY_HOST_GAME", str(base / "the_question"))
os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
os.environ.pop("RENPY_SKIP_MAIN_MENU", None)
os.environ.pop("RENPY_SKIP_SPLASHSCREEN", None)

import bootstrap as boot
import renpy_host
from product import _ensure_renpy_main, _pre_main_host_stubs, _prepare_run_args, _request_quit

# Reuse product stages lightly
good,_,_,_ = boot.stage_import_renpy()
assert good
good,_,_,_ = boot.stage_import_all()
assert good
good,_,_,_ = boot.stage_set_game_dir(base)
assert good
import renpy

renpy.host_build = True
_ensure_renpy_main(base)
_pre_main_host_stubs()
_prepare_run_args(base)

result = {"ok": False, "notes": []}
stop = threading.Event()

def work():
    try:
        # wait main menu
        for _ in range(400):
            if stop.is_set(): return
            try:
                if bool(getattr(renpy.store, "main_menu", False)):
                    break
            except Exception:  # noqa: BLE001, S110
                pass
            time.sleep(0.05)
        time.sleep(0.4)
        # baseline left strip
        def left_stats():
            w,h,rgba = renpy_host.read_game_rt_rgba()
            # sample x=40..120, y mid band
            rs=gs=bs=n=0
            for y in range(h//3, 2*h//3, 4):
                for x in range(max(1,w//40), max(2,w//10), 2):
                    i=(y*w+x)*4
                    rs+=rgba[i]; gs+=rgba[i+1]; bs+=rgba[i+2]; n+=1
            return w,h, (rs/n, gs/n, bs/n) if n else (0,0,0)

        w0,h0,m0 = left_stats()
        result["notes"].append("baseline size=%dx%d left_mean=%s" % (w0,h0,tuple(round(x,1) for x in m0)))  # noqa: UP031
        # enlarge
        renpy_host.request_window_size(1920, 1080)
        for _ in range(30):
            renpy_host.pump_once(16)
            time.sleep(0.02)
        # force redraws
        try:
            renpy.game.interface.force_redraw = True
            renpy.exports.restart_interaction()
        except Exception as e:  # noqa: BLE001
            result["notes"].append(f"restart soft {e!r}")
        time.sleep(0.8)
        for _ in range(20):
            renpy_host.pump_once(16)
            time.sleep(0.02)
        w1,h1,m1 = left_stats()
        result["notes"].append("after size=%dx%d left_mean=%s" % (w1,h1,tuple(round(x,1) for x in m1)))  # noqa: UP031
        # Overlay dark strip is near-black-ish (mean low); scenic is green/blue higher
        # baseline left should be dark; after should still be dark not scenic green
        dark0 = (m0[0]+m0[1]+m0[2])/3 < 80
        dark1 = (m1[0]+m1[1]+m1[2])/3 < 90
        size_ok = w1 > w0 or h1 > h0
        result["notes"].append(f"dark0={dark0} dark1={dark1} size_ok={size_ok}")
        result["ok"] = bool(dark0 and dark1 and size_ok)
    except Exception as e:  # noqa: BLE001
        result["notes"].append(f"EXCEPTION {e!r}")
        result["notes"].append(traceback.format_exc())
    finally:
        _request_quit()

t = threading.Thread(target=work, daemon=True)
t.start()
try:
    import renpy.main as m
    m.main()
except BaseException as e:  # noqa: BLE001
    result["notes"].append(f"main exit {type(e).__name__}")
stop.set()
out = base/"host"/"target"/"gate-overlay_after_resize_probe.txt"
body = ("ok={}\n".format(result["ok"])) + "\n".join(result["notes"]) + "\n"
out.write_text(body)
print(body)
_request_quit()
if not result["ok"]:
    raise SystemExit(1)

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

