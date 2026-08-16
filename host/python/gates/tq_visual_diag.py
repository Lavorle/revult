"""Dump product surftree after load_all_textures + RT stats. Gate: tq_visual_diag"""
import os, sys, time, types, traceback
from pathlib import Path

base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
game = Path(os.environ.get("RENPY_HOST_GAME", str(base / "the_question")))
outp = base / "host" / "target" / "gate-tq-visual-diag.txt"
lines = []

def log(m):
    lines.append(str(m))
    print(f"[tq_visual_diag] {m}", flush=True)

# bootstrap like tq_main_menu_frame
import bootstrap as boot
good, miss, err, extra = boot.stage_import_renpy()
log(f"import_renpy good={good} err={err}")
good, miss, err, extra = boot.stage_import_all()
log(f"import_all good={good} err={err}")
good, miss, err, extra = boot.stage_set_game_dir(game if (game / "game").is_dir() else base)
log(f"set_game_dir good={good}")

import renpy, renpy_host
from renpy.wgpu import draw as wdraw

# uguu stubs
try:
    import renpy_uguu_host as _uguu
    sys.modules["renpy.uguu.uguu"] = _uguu
    sys.modules["renpy.uguu.gl"] = _uguu
    pkg = sys.modules.get("renpy.uguu")
    if pkg is None:
        pkg = types.ModuleType("renpy.uguu")
        pkg.__path__ = []
        sys.modules["renpy.uguu"] = pkg
    for _name in dir(_uguu):
        if not _name.startswith("_"):
            setattr(pkg, _name, getattr(_uguu, _name))
    setattr(pkg, "uguu", _uguu)
    setattr(pkg, "gl", _uguu)
    log("uguu ok")
except Exception as e:
    log(f"uguu fail {e}")

try:
    import renpy_ecsign_host as _ec
    sys.modules["renpy.common.ecsign"] = _ec
    log("ecsign ok")
except Exception as e:
    log(f"ecsign fail {e}")

class HostStop(BaseException):
    def __init__(self, stage, detail=""):
        self.stage, self.detail = stage, detail
        super().__init__(stage)

_stats = {"draws": 0, "prepare_models": 0, "ht": 0, "mesh_true": 0, "nodes": 0}

def summarize(node, depth=0, acc=None, budget=80):
    if acc is None:
        acc = []
    if node is None or len(acc) >= budget:
        return acc
    _stats["nodes"] += 1
    cls = type(node).__name__
    mesh = getattr(node, "mesh", None)
    if mesh is True:
        _stats["mesh_true"] += 1
    tex = getattr(node, "texture", None)
    ct = getattr(node, "cached_texture", None)
    cm = getattr(node, "cached_model", None)
    kids = getattr(node, "children", None) or []
    tw = getattr(node, "width", None) or getattr(node, "w", None)
    th = getattr(node, "height", None) or getattr(node, "h", None)
    def texinfo(t):
        if t is None:
            return None
        if isinstance(t, wdraw.HostTexture):
            _stats["ht"] += 1
            return f"HT(h={t.handle},{t.w}x{t.h}@{t.x},{t.y} full={t.width}x{t.height})"
        if isinstance(t, int):
            return f"int({t})"
        return type(t).__name__
    cm_s = None
    if cm is not None:
        _stats["prepare_models"] += 1
        cm_s = f"model(tex={texinfo(getattr(cm,'texture',None))}, size={getattr(cm,'width',None)}x{getattr(cm,'height',None)})"
    acc.append(f"{'  '*depth}{cls} {tw}x{th} mesh={mesh!r} tex={texinfo(tex)} cached_tex={texinfo(ct)} cached_model={cm_s} kids={len(kids) if hasattr(kids,'__len__') else '?'}")
    for entry in list(kids)[:10]:
        child = entry[0] if isinstance(entry, (tuple, list)) else entry
        summarize(child, depth+1, acc, budget)
    return acc

_orig = wdraw.WgpuDraw.draw_screen
_n = {"i": 0}

def hooked(self, surftree, flip=True):
    _n["i"] += 1
    n = _n["i"]
    # Call prepare ourselves to inspect mid-state: use original but wrap load
    try:
        if n <= 2 and surftree is not None:
            self._ensure_pipes()
            self.load_all_textures(surftree)
            log(f"--- after prepare #{n} ---")
            for s in summarize(surftree):
                log(s)
            log(f"stats={_stats} texture_cache={len(self.texture_cache)}")
            # don't double-prepare: temporarily no-op load
            _la = self.load_all_textures
            self.load_all_textures = lambda *a, **k: None
            try:
                rv = _orig(self, surftree, flip=flip)
            finally:
                self.load_all_textures = _la
            try:
                w, h, rgba = renpy_host.read_game_rt_rgba()
                if w and h and rgba and len(rgba) >= w*h*4:
                    npx = w*h
                    mr = sum(rgba[i] for i in range(0, npx*4, 4))/npx
                    mg = sum(rgba[i+1] for i in range(0, npx*4, 4))/npx
                    mb = sum(rgba[i+2] for i in range(0, npx*4, 4))/npx
                    # count non-near-clear pixels
                    thr = 30
                    non = 0
                    for i in range(0, npx*4, 16):  # sample every 4th
                        if abs(rgba[i]-13)>thr or abs(rgba[i+1]-13)>thr or abs(rgba[i+2]-20)>thr:
                            non += 1
                    log(f"RT #{n}: {w}x{h} mean=({mr:.1f},{mg:.1f},{mb:.1f}) nonclear_samples={non}")
                else:
                    log(f"RT empty #{n}")
            except Exception as e:
                log(f"RT fail {e}")
            return rv
        return _orig(self, surftree, flip=flip)
    except Exception as e:
        log(f"draw_screen fail {type(e).__name__}: {e}")
        raise

wdraw.WgpuDraw.draw_screen = hooked

import renpy.display.core as core
_orig_i = core.Interface.interact
_ic = {"n": 0}

def interact_hook(self, *a, **k):
    _ic["n"] += 1
    log(f"interact #{_ic['n']}")
    if _ic["n"] >= 2 and _n["i"] >= 1:
        raise HostStop("done", f"interacts={_ic['n']} draws={_n['i']}")
    try:
        return _orig_i(self, *a, **k)
    except HostStop:
        raise
    except Exception as e:
        log(f"interact err {e}")
        raise

core.Interface.interact = interact_hook
renpy.display.core.Interface.interact = interact_hook

os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
os.environ.pop("RENPY_SKIP_MAIN_MENU", None)

try:
    log("entering main")
    renpy.main.main()
except HostStop as hs:
    log(f"HostStop {hs.stage} {hs.detail}")
except SystemExit as se:
    log(f"SystemExit {se}")
except Exception as e:
    log(f"main fail {type(e).__name__}: {e}")
    log(traceback.format_exc()[-1500:])

outp.write_text("\n".join(lines) + "\n")
log(f"wrote {outp}")
print("ok=diag")
