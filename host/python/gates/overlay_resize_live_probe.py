"""Live product: left overlay darkness full-height before/after enlarge."""
import os
import sys
import threading
import time
import traceback
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---
from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore


base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
sys.path.insert(0, str(base / "host" / "python" / "gates"))
os.environ.setdefault("RENPY_HOST_BASE", str(base))
os.environ.setdefault("RENPY_HOST_BUILD", "1")
os.environ.setdefault("RENPY_HOST_GAME", str(base / "the_question"))
os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
os.environ.pop("RENPY_SKIP_MAIN_MENU", None)
os.environ.pop("RENPY_SKIP_SPLASHSCREEN", None)

import bootstrap as boot
import product as prod
import renpy_host

lines = []
ok = True
state = {"done": False}

def sample_profile():
    w, h, rgba = renpy_host.read_game_rt_rgba()
    # left strip x=40 virtual mapped to physical
    # use physical x proportional to 40/1280
    xs = [max(1, int(w * x / 1280)) for x in (30, 60, 100, 140, 200)]
    ys = [int(h * f) for f in (0.08, 0.25, 0.5, 0.75, 0.92)]
    rows = []
    for y in ys:
        row = []
        for x in xs:
            i = (y * w + x) * 4
            row.append((rgba[i], rgba[i+1], rgba[i+2]))
        # mean of first 3 x samples (deep left)
        m = tuple(sum(c[k] for c in row[:3]) / 3 for k in range(3))
        rows.append((y, m))
    return w, h, rows

def worker():
    global ok
    try:
        for _ in range(500):
            try:
                if bool(getattr(__import__('renpy').store, 'main_menu', False)):
                    break
            except Exception:
                pass
            time.sleep(0.05)
        time.sleep(0.5)
        # force a few presents
        for _ in range(10):
            renpy_host.pump_once(16)
            time.sleep(0.03)
        w0, h0, rows0 = sample_profile()
        lines.append("before size=%dx%d" % (w0, h0))  # noqa: UP031
        for y, m in rows0:
            lines.append("  y=%d mean=%s" % (y, tuple(round(v,1) for v in m)))  # noqa: UP031
        dark0 = all(sum(m)/3 < 100 for _, m in rows0)
        lines.append(f"before_dark_all={dark0}")

        renpy_host.request_window_size(1920, 1080)
        for _ in range(40):
            renpy_host.pump_once(16)
            time.sleep(0.02)
        try:
            import renpy
            renpy.game.interface.force_redraw = True
            renpy.exports.restart_interaction()
        except Exception as e:
            lines.append(f"restart {e!r}")
        time.sleep(1.0)
        for _ in range(30):
            renpy_host.pump_once(16)
            time.sleep(0.02)

        w1, h1, rows1 = sample_profile()
        lines.append("after size=%dx%d" % (w1, h1))  # noqa: UP031
        for y, m in rows1:
            lines.append("  y=%d mean=%s" % (y, tuple(round(v,1) for v in m)))  # noqa: UP031
        dark1 = all(sum(m)/3 < 110 for _, m in rows1)
        lines.append(f"after_dark_all={dark1}")
        size_ok = (w1, h1) != (w0, h0)
        lines.append(f"size_changed={size_ok}")
        ok = bool(dark0 and dark1 and size_ok)
        if not dark1:
            lines.append("FAIL overlay not dark full-height after resize")
    except Exception as e:
        ok = False
        lines.append(f"EXCEPTION {e!r}")
        lines.append(traceback.format_exc())
    finally:
        state["done"] = True
        prod._request_quit()

# Run product gate body
try:
    # product.run() is module level - call stages ourselves like product.run
    good, miss, err, extra = boot.stage_import_renpy()
    assert good, err
    good, miss, err, extra = boot.stage_import_all()
    assert good, err
    good, miss, err, extra = boot.stage_set_game_dir(base)
    assert good, err
    import renpy
    renpy.host_build = True
    prod._ensure_renpy_main(base)
    prod._pre_main_host_stubs()
    prod._prepare_run_args(base)
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    import renpy.main as m
    try:
        m.main()
    except BaseException as e:
        lines.append(f"main {type(e).__name__}")
except Exception as e:
    ok = False
    lines.append(f"boot EXC {e!r}")
    lines.append(traceback.format_exc())
    prod._request_quit()

body = (f"ok={ok}\n") + "\n".join(lines) + "\n"
outp = base / "host" / "target" / "gate-overlay_resize_live_probe.txt"
outp.write_text(body)
print(body)
renpy_host.request_quit()
if not ok:
    raise SystemExit(1)

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

