"""Wrap tq_main_menu_frame with surftree dump. Gate: tq_tree_dump"""
import os
import sys
import traceback
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---

base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
gates = base / "host" / "python" / "gates"
if str(gates) not in sys.path:
    sys.path.insert(0, str(gates))

outp = base / "host" / "target" / "gate-tq-tree-dump.txt"
lines = []

def L(m):
    lines.append(str(m))
    print(f"[tq_tree_dump] {m}", flush=True)

import renpy_host

import renpy.wgpu.draw as wdraw

stats = {"nodes":0,"mesh_true":0,"ht_tex":0,"ht_child":0,"cached_model":0,"size0":0}

def texinfo(t):
    if t is None: return None
    if isinstance(t, wdraw.HostTexture):
        stats["ht_tex"] += 1
        return f"HT({t.handle},{t.w}x{t.h}@{t.x},{t.y})"
    if isinstance(t, int) and not isinstance(t, bool):
        return f"int({t})"
    return type(t).__name__

def summarize(node, depth=0, acc=None, budget=150):
    if acc is None: acc=[]
    if node is None or len(acc)>=budget: return acc
    stats["nodes"] += 1
    cls = type(node).__name__
    mesh = getattr(node, "mesh", None)
    if mesh is True: stats["mesh_true"] += 1
    tw = getattr(node, "width", None) or getattr(node, "w", None)
    th = getattr(node, "height", None) or getattr(node, "h", None)
    try:
        if (not tw or not th) and hasattr(node, "get_size"):
            tw, th = node.get_size()
    except Exception:
        pass
    if not tw or not th: stats["size0"] += 1
    tex = getattr(node, "texture", None)
    ct = getattr(node, "cached_texture", None)
    cm = getattr(node, "cached_model", None)
    kids = getattr(node, "children", None) or []
    cm_s = None
    if cm is not None:
        stats["cached_model"] += 1
        t0 = texinfo(getattr(cm, "texture", None))
        cm_s = f"MODEL(tex={t0} {getattr(cm,'width',None)}x{getattr(cm,'height',None)})"
    ctypes=[]
    for entry in list(kids)[:6]:
        ch = entry[0] if isinstance(entry,(tuple,list)) else entry
        if isinstance(ch, wdraw.HostTexture):
            stats["ht_child"] += 1
            ctypes.append(f"HT({ch.handle},{ch.w}x{ch.h})")
        else:
            ctypes.append(type(ch).__name__)
    acc.append(f"{'  '*depth}{cls} {tw}x{th} mesh={mesh!r} tex={texinfo(tex)} ctex={texinfo(ct)} cm={cm_s} kids={len(kids)} {ctypes}")
    for entry in list(kids)[:8]:
        ch = entry[0] if isinstance(entry,(tuple,list)) else entry
        summarize(ch, depth+1, acc, budget)
    return acc

_orig = wdraw.WgpuDraw.draw_screen
_n = {"i":0}

def hooked(self, surftree, flip=True):
    _n["i"] += 1
    n = _n["i"]
    if n <= 2 and surftree is not None:
        try:
            self._ensure_pipes()
            self.load_all_textures(surftree)
            L(f"=== prepare draw#{n} {type(surftree).__name__} ===")
            for s in summarize(surftree):
                L(s)
            L(f"STATS {stats} cache={len(self.texture_cache)}")
            _la = self.load_all_textures
            self.load_all_textures = lambda *a, **k: None
            try:
                rv = _orig(self, surftree, flip=flip)
            finally:
                self.load_all_textures = _la
            w,h,rgba = renpy_host.read_game_rt_rgba()
            if w and h and rgba:
                npx=w*h
                mr=sum(rgba[i] for i in range(0,npx*4,4))/npx
                mg=sum(rgba[i+1] for i in range(0,npx*4,4))/npx
                mb=sum(rgba[i+2] for i in range(0,npx*4,4))/npx
                thr=30; non=0
                for i in range(0,npx*4,64):
                    if abs(rgba[i]-13)>thr or abs(rgba[i+1]-13)>thr or abs(rgba[i+2]-20)>thr:
                        non += 1
                L(f"RT #{n} mean=({mr:.1f},{mg:.1f},{mb:.1f}) nonclear={non}")
            return rv
        except Exception as e:
            L(f"hook {type(e).__name__}: {e}")
            L(traceback.format_exc()[-1200:])
            return _orig(self, surftree, flip=flip)
    return _orig(self, surftree, flip=flip)

wdraw.WgpuDraw.draw_screen = hooked
L("hook installed")

import tq_main_menu_frame as tq

try:
    tq.run()
except SystemExit:
    pass
except Exception as e:
    L(f"tq.run fail {e}")
    L(traceback.format_exc()[-1000:])

outp.write_text("\n".join(lines)+"\n")
L(f"wrote {outp} draws={_n['i']}")
print("ok=diag", flush=True)
try:
    renpy_host.request_quit()
except Exception:
    pass

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
