"""
Diagnose product main-menu surftree + RT after first draw_screen.

Gate: tq_draw_diag

Dumps post-prepare tree (cached_model / HostTexture on menu path) for AC-P2.
Boot path mirrors tq_main_menu_frame (bootstrap + host stubs + path helpers).

Note: host run_file prepends import preamble — do not use from __future__ or __file__.
"""
import os
import sys
import traceback
import types
from pathlib import Path

import bootstrap as boot

# --- harness (thin wrapper, original logic preserved) ---
from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore

base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
game_env = os.environ.get("RENPY_HOST_GAME")
game = Path(game_env) if game_env else (base / "the_question")
outp = base / "host" / "target" / "gate-tq_draw_diag.txt"
log_lines = []


def log(m):
    log_lines.append(str(m))
    print(f"[tq_draw_diag] {m}", flush=True)


def _summarize(node, depth=0, acc=None, budget=120):
    if acc is None:
        acc = []
    if len(acc) >= budget or node is None:
        return acc
    indent = "  " * depth
    cls = type(node).__name__
    mesh = getattr(node, "mesh", None)
    tex = getattr(node, "texture", None)
    tw = getattr(node, "width", None) or getattr(node, "w", None)
    th = getattr(node, "height", None) or getattr(node, "h", None)
    kids = getattr(node, "children", None) or []
    blits = getattr(node, "blits", None) or []
    nkid = len(kids) if hasattr(kids, "__len__") else "?"
    nblit = len(blits) if hasattr(blits, "__len__") else "?"
    loaded = getattr(node, "loaded", None)
    cached = getattr(node, "cached_model", None)
    cached_tex = None
    if cached is not None:
        ct = getattr(cached, "texture", None)
        cts = getattr(cached, "textures", None)
        if ct is not None and hasattr(ct, "handle"):
            cached_tex = "HostTexture(h={},{}x{})".format(
                ct.handle,
                getattr(ct, "w", 0),
                getattr(ct, "h", 0),
            )
        elif cts:
            slots = []
            for t in list(cts)[:4]:
                if t is not None and hasattr(t, "handle"):
                    slots.append("h={}:{}x{}".format(t.handle, getattr(t, "w", 0), getattr(t, "h", 0)))
                else:
                    slots.append(type(t).__name__ if t is not None else "None")
            cached_tex = "textures[{}]".format(",".join(slots))
        else:
            cached_tex = f"model({type(cached).__name__})"
    tex_s = None
    if tex is not None:
        if hasattr(tex, "handle"):
            tex_s = "HostTexture(h={},{}x{}@{},{})".format(
                tex.handle,
                getattr(tex, "w", 0),
                getattr(tex, "h", 0),
                getattr(tex, "x", 0),
                getattr(tex, "y", 0),
            )
        elif isinstance(tex, int):
            tex_s = f"int({tex})"
        else:
            tex_s = type(tex).__name__
    acc.append(
        f"{indent}{cls} size={tw}x{th} mesh={mesh!r} tex={tex_s} loaded={loaded} cached_model={cached_tex} children={nkid} blits={nblit}"
    )
    for entry in list(kids)[:8]:
        child = entry[0] if isinstance(entry, (tuple, list)) else entry
        _summarize(child, depth + 1, acc, budget)
    for entry in list(blits)[:4]:
        child = entry[0] if isinstance(entry, (tuple, list)) else entry
        _summarize(child, depth + 1, acc, budget)
    return acc


def _count_prepared(node, stats=None, budget=400, seen=None):
    if stats is None:
        stats = {
            "nodes": 0,
            "mesh_true": 0,
            "cached_model": 0,
            "cached_with_ht": 0,
            "host_tex_leaves": 0,
        }
    if seen is None:
        seen = set()
    if node is None or stats["nodes"] >= budget:
        return stats
    try:
        nid = id(node)
        if nid in seen:
            return stats
        seen.add(nid)
    except Exception:
        pass
    stats["nodes"] += 1
    if getattr(node, "mesh", None):
        stats["mesh_true"] += 1
    cached = getattr(node, "cached_model", None)
    if cached is not None:
        stats["cached_model"] += 1
        ct = getattr(cached, "texture", None)
        cts = getattr(cached, "textures", None) or []
        if (ct is not None and hasattr(ct, "handle") and ct.handle > 0) or any(
            t is not None and hasattr(t, "handle") and t.handle > 0 for t in cts
        ):
            stats["cached_with_ht"] += 1
    if type(node).__name__ == "HostTexture" or (
        hasattr(node, "handle") and hasattr(node, "w") and hasattr(node, "h")
    ):
        try:
            if int(getattr(node, "handle", 0) or 0) > 0:
                stats["host_tex_leaves"] += 1
        except Exception:
            pass
    kids = getattr(node, "children", None) or []
    blits = getattr(node, "blits", None) or []
    for entry in list(kids)[:16]:
        child = entry[0] if isinstance(entry, (tuple, list)) else entry
        _count_prepared(child, stats, budget, seen)
    for entry in list(blits)[:8]:
        child = entry[0] if isinstance(entry, (tuple, list)) else entry
        _count_prepared(child, stats, budget, seen)
    return stats


class HostStop(BaseException):
    def __init__(self, stage, detail=""):
        self.stage = stage
        self.detail = detail
        super().__init__(
            f"HostStop@{stage}: {detail}" if detail else f"HostStop@{stage}"
        )


def _pre_main_host_stubs():
    try:
        import renpy.audio as _ra
        import renpy.audio.renpysound_host as _rs_host

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        log("renpysound rebound to host")
    except Exception as e:
        log(f"renpysound rebound soft-fail: {e}")

    try:
        import host_pygame
        import host_pygame.locals as _loc

        if not hasattr(host_pygame, "constants"):
            host_pygame.constants = _loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules.setdefault("pygame.constants", host_pygame.constants)
        import renpy.pygame as rpg

        if not hasattr(rpg, "constants"):
            rpg.constants = host_pygame.constants
        try:
            rpg.import_as_pygame()
        except Exception as e:
            log(f"import_as_pygame soft-fail: {e}")
    except Exception as e:
        log(f"pygame.constants soft-fail: {e}")

    try:
        import renpy_uguu_host as _uguu

        sys.modules["renpy.uguu.uguu"] = _uguu
        sys.modules["renpy.uguu.gl"] = _uguu
        pkg = sys.modules.get("renpy.uguu")
        if pkg is None:
            pkg = types.ModuleType("renpy.uguu")
            pkg.__path__ = []  # type: ignore[attr-defined]
            sys.modules["renpy.uguu"] = pkg
        for _name in dir(_uguu):
            if _name.startswith("GL_") or _name in ("clear_errors", "get_error"):
                setattr(pkg, _name, getattr(_uguu, _name))
        pkg.uguu = _uguu
        pkg.gl = _uguu
        log("uguu host stub installed")
    except Exception as e:
        log(f"uguu stub soft-fail: {type(e).__name__}: {e}")

    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        try:
            import renpy as _renpy_pkg

            _renpy_pkg.ecsign = _ecsign
        except Exception:
            pass
        log("ecsign host stub installed")
    except Exception as e:
        log(f"ecsign soft-fail: {e}")


def _ensure_renpy_main(repo_base):
    import renpy

    main_mod = getattr(renpy, "__main__", None)
    helpers = (
        "path_to_common",
        "path_to_gamedir",
        "path_to_saves",
        "predefined_searchpath",
        "path_to_logdir",
    )
    have = {
        name: callable(getattr(main_mod, name, None)) if main_mod is not None else False
        for name in helpers
    }
    if all(have.values()):
        return main_mod, have, "present"
    import renpy_main_host  # type: ignore

    main_mod = renpy_main_host.install(renpy)
    have = {
        name: callable(getattr(main_mod, name, None)) if main_mod is not None else False
        for name in helpers
    }
    return main_mod, have, "installed"


# --- bootstrap ---
os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
os.environ.setdefault("RENPY_SKIP_SPLASHSCREEN", "0")

good, miss, err, extra = boot.stage_import_renpy()
log(f"import_renpy good={good} err={err}")
if not good:
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    raise SystemExit(1)

good, miss, err, extra = boot.stage_import_all()
log(f"import_all good={good} err={err}")

# stage_set_game_dir expects repo root (resolves the_question / RENPY_HOST_GAME)
good, miss, err, extra = boot.stage_set_game_dir(base)
log(
    "set_game_dir good={} basedir={} err={}".format(good, (extra or {}).get("basedir") if isinstance(extra, dict) else extra, err)
)

import renpy_host

import renpy

if not getattr(renpy, "host_build", False):
    renpy.host_build = True

main_mod, have, how = _ensure_renpy_main(base)
log(f"path helpers {how} have={have}")

basedir = getattr(renpy.config, "basedir", None) or str(game)
renpy.config.renpy_base = getattr(renpy.config, "renpy_base", None) or str(base)
try:
    logdir = main_mod.path_to_logdir(basedir)
    renpy.config.logdir = logdir
    os.makedirs(logdir, 0o777, exist_ok=True)
except Exception as e:
    log(f"logdir soft-fail: {e}")

try:
    renpy.importer.init_importer()
except Exception as e:
    log(f"importer soft-fail: {e}")

_pre_main_host_stubs()

try:
    renpy.config.performance_test = False
    renpy.config.has_music = False
    renpy.config.main_menu_music = None
except Exception:
    pass

from renpy.wgpu import draw as wdraw

_orig_draw = wdraw.WgpuDraw.draw_screen
_n = {"i": 0}
_diag = {
    "prepare_stats": None,
    "rt_mean": None,
    "has_cached_ht": False,
}


def draw_screen_hook(self, surftree, flip=True):
    _n["i"] += 1
    n = _n["i"]
    # Explicit prepare so we can dump cached_model before draw_screen invalidate.
    if n <= 3 and surftree is not None:
        try:
            log(f"--- draw_screen #{n} type={type(surftree).__name__} PRE-prepare ---")
            for s in _summarize(surftree):
                log(s)
            try:
                self._ensure_pipes()
                self.load_all_textures(surftree)
            except Exception as e:
                log(f"prepare fail: {type(e).__name__}: {e}")
            log(f"--- draw_screen #{n} POST-prepare ---")
            for s in _summarize(surftree):
                log(s)
            stats = _count_prepared(surftree)
            _diag["prepare_stats"] = stats
            _diag["has_cached_ht"] = bool(stats.get("cached_with_ht"))
            log(
                "prepare_stats nodes={} mesh={} cached_model={} cached_with_ht={} texture_cache_len={}".format(
                    stats["nodes"],
                    stats["mesh_true"],
                    stats["cached_model"],
                    stats["cached_with_ht"],
                    len(getattr(self, "texture_cache", {})),
                )
            )
        except Exception as e:
            log(f"summarize fail: {type(e).__name__}: {e}")
    try:
        rv = _orig_draw(self, surftree, flip=flip)
    except Exception as e:
        log(f"draw_screen raised: {type(e).__name__}: {e}")
        raise
    if n <= 3:
        try:
            w, h, rgba = renpy_host.read_game_rt_rgba()
            if w and h and rgba and len(rgba) >= w * h * 4:
                n_pix = w * h
                rs = sum(rgba[i] for i in range(0, n_pix * 4, 4)) / n_pix
                gs = sum(rgba[i + 1] for i in range(0, n_pix * 4, 4)) / n_pix
                bs = sum(rgba[i + 2] for i in range(0, n_pix * 4, 4)) / n_pix
                cx, cy = w // 2, h // 2
                i = (cy * w + cx) * 4
                _diag["rt_mean"] = (rs, gs, bs)
                log(
                    f"RT after #{n}: {w}x{h} mean=({rs:.1f},{gs:.1f},{bs:.1f}) center={tuple(rgba[i : i + 4])}"
                )
            else:
                log(
                    f"RT empty after #{n}: {w}x{h} bytes={0 if not rgba else len(rgba)}"
                )
        except Exception as e:
            log(f"readback fail: {e}")
    return rv


wdraw.WgpuDraw.draw_screen = draw_screen_hook

_interacts = {"n": 0}
from renpy.display import core

_orig_interact = core.Interface.interact


def interact_hook(self, *a, **k):
    _interacts["n"] += 1
    n = _interacts["n"]
    log(f"interact enter #{n}")
    try:
        return _orig_interact(self, *a, **k)
    finally:
        log("interact leave #{} draws={}".format(n, _n["i"]))
        if n >= 1 and _n["i"] >= 1:
            raise HostStop("diag", "interact={} draws={}".format(n, _n["i"]))


core.Interface.interact = interact_hook


class Args:
    command = "run"
    basedir = basedir
    savedir = None
    trace = 0
    profile_display = False
    debug_image_cache = False
    warp = None
    lint = False
    compile = False
    errors = None
    json_dump = None
    json_dump_private = False
    force_compile = False
    compile_python = False


renpy.game.args = Args()

try:
    log("entering renpy.main.main()")
    renpy.main.main()
    log("main returned normally")
except HostStop as e:
    log(f"HostStop: {e}")
except Exception as e:
    log(f"main exception: {type(e).__name__}: {e}")
    log(traceback.format_exc()[-2000:])

stats = _diag.get("prepare_stats") or {}
rt = _diag.get("rt_mean")
ac_p2 = bool(stats.get("cached_with_ht"))
log(
    "SUMMARY ac_p2_prepared_ht={} cached_model={} cached_with_ht={} mesh={} rt_mean={} draws={}".format(
        ac_p2,
        stats.get("cached_model"),
        stats.get("cached_with_ht"),
        stats.get("mesh_true"),
        rt,
        _n["i"],
    )
)

outp.parent.mkdir(parents=True, exist_ok=True)
outp.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
log(f"wrote {outp}")
try:
    renpy_host.request_quit()
except Exception:
    pass

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
