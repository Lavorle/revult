
"""Count draw_model during product present. Gate: tq_draw_count"""
import atexit
import os
import sys
import traceback
from pathlib import Path

base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
gates = base / "host" / "python" / "gates"
if str(gates) not in sys.path:
    sys.path.insert(0, str(gates))

import renpy_host

import renpy.wgpu.draw as wdraw

# --- harness (thin wrapper, original logic preserved) ---

counts = {"draw_model": 0, "begin": 0, "end": 0, "ht": 0, "create_mesh": 0, "ds": 0}
samples = []
out = base / "host" / "target" / "gate-tq-draw-count.txt"

def flush():
    out.write_text(f"counts={counts}\nsamples={samples!r}\n")
atexit.register(flush)

_orig_dm = renpy_host.draw_model
def dm(*a, **k):
    counts["draw_model"] += 1
    if counts["draw_model"] <= 10:
        samples.append(("dm", a[:5] if a else a, k))
    return _orig_dm(*a, **k)
renpy_host.draw_model = dm

_orig_bf = renpy_host.begin_frame
def bf(*a, **k):
    counts["begin"] += 1
    return _orig_bf(*a, **k)
renpy_host.begin_frame = bf

_orig_ef = renpy_host.end_frame_present
def ef(*a, **k):
    counts["end"] += 1
    # read RT immediately after each present
    try:
        w,h,rgba = renpy_host.read_game_rt_rgba()
        n=w*h
        mr=sum(rgba[i] for i in range(0,n*4,4))/n
        mg=sum(rgba[i+1] for i in range(0,n*4,4))/n
        mb=sum(rgba[i+2] for i in range(0,n*4,4))/n
        thr=30; non=0
        for i in range(0,n*4,64):
            if abs(rgba[i]-13)>thr or abs(rgba[i+1]-13)>thr or abs(rgba[i+2]-20)>thr:
                non += 1
        samples.append(("rt_after_end", counts["end"], round(mr,1), round(mg,1), round(mb,1), non, counts["draw_model"]))
        print(f"[tq_draw_count] after end#{counts['end']} mean=({mr:.1f},{mg:.1f},{mb:.1f}) nonclear={non} dm_total={counts['draw_model']}", flush=True)
    except Exception as e:
        samples.append(("rt_fail", str(e)))
    return _orig_ef(*a, **k)
renpy_host.end_frame_present = ef

_orig_cm = renpy_host.create_mesh
def cm(verts, indices=None):
    counts["create_mesh"] += 1
    # sample first mesh verts
    if counts["create_mesh"] <= 5 and verts:
        samples.append(("mesh", counts["create_mesh"], list(verts)[:16], indices[:6] if indices else None))
    return _orig_cm(verts, indices)
renpy_host.create_mesh = cm

_orig_dml = wdraw.WgpuDraw._draw_model_like
def dml(self, node, ox=0.0, oy=0.0):
    if isinstance(node, wdraw.HostTexture):
        counts["ht"] += 1
        if counts["ht"] <= 15:
            x0,y0,x1,y1 = self._virt_rect_to_ndc(ox, oy, node.w, node.h)
            samples.append(("ht", node.handle, node.w, node.h, node.x, node.y, ox, oy, (x0,y0,x1,y1), self.virtual_size))
    return _orig_dml(self, node, ox, oy)
wdraw.WgpuDraw._draw_model_like = dml

_orig_ds = wdraw.WgpuDraw.draw_screen
def ds(self, surftree, flip=True):
    counts["ds"] += 1
    before_dm = counts["draw_model"]
    rv = _orig_ds(self, surftree, flip=flip)
    delta_dm = counts["draw_model"] - before_dm
    print(f"[tq_draw_count] ds#{counts['ds']} dm_delta={delta_dm} vs={self.virtual_size}", flush=True)
    flush()
    return rv
wdraw.WgpuDraw.draw_screen = ds

print("[tq_draw_count] hooks ready", flush=True)

# prevent tq_main_menu_frame from raising
import tq_main_menu_frame as tq

_orig_run = tq.run
def soft_run():
    try:
        _orig_run()
    except RuntimeError as e:
        print(f"[tq_draw_count] soft RuntimeError: {e}", flush=True)
    except SystemExit:
        pass
    except Exception as e:
        print(f"[tq_draw_count] other {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
tq.run = soft_run

# tq_main_menu_frame auto-runs at import bottom - import triggers run
# Actually the module calls run() at bottom when __name__... host runs the file as script.
# Our gate imports tq which may auto-run. Call explicitly.
if hasattr(tq, 'run'):
    soft_run()

flush()
print(f"[tq_draw_count] TOTAL {counts}", flush=True)
for s in samples[:40]:
    print(f"[tq_draw_count] {s}", flush=True)
try:
    renpy_host.request_quit()
except Exception:
    pass

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
