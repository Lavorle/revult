"""
draw_debug — debug / signal helpers extracted from draw.py.

Contains:
- _DRAW_SCREEN_LOCK / _draw_screen_lock
- _HOST_DRAW_FAIL_LOGGED / _UI_TRACE_LOGGED
- _PHASE0_* throttling state + _phase0_* helpers
- _safe_print, _ui_trace_once, _host_draw_fail
- Generic _phase0_due(key, now, interval) with wrappers for compat.

Re-exported from draw.py so `from renpy.wgpu.draw import ...` remains valid.
"""
from __future__ import annotations

import os
import sys
import threading
import time as _time

# Centralized env helper — use host_bridge.host_env_bool when available so
# all RENPY_HOST_* bool reads go through one site (bridge). Fallback keeps
# draw_debug importable outside the wgpu package (hermetic gates / lint).
try:
    from .host_bridge import host_env_bool  # type: ignore
except Exception:  # pragma: no cover - import fallback for bare import  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed

    def host_env_bool(name: str) -> bool:  # type: ignore[no-redef]
        return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")

# Product present lock — created lazily so renpy.import_all module backup
# (pickle of module attrs) does not see a non-picklable RLock at import time.
_DRAW_SCREEN_LOCK = None  # type: Optional[threading.RLock]


def _draw_screen_lock() -> threading.RLock:
    global _DRAW_SCREEN_LOCK
    lock = _DRAW_SCREEN_LOCK
    if lock is None:
        lock = threading.RLock()
        _DRAW_SCREEN_LOCK = lock
    return lock


# Per-frame fail collector flag (module import time; env rarely changes at runtime).
_HOST_FRAME_FAILS_ENABLED: bool = os.environ.get(
    "RENPY_HOST_WALKSTAT", ""
).strip() in ("1", "true", "yes")


# Per-frame walk skip trace (same env flag as _HOST_FRAME_FAILS): records silent
# subtree-skipping decisions of the walker (clip-empty push, cache-hit
# short-circuit, cached-texture leaf, alpha-0 effect container, single-tex multi
# break) so a "frame lost a subtree prefix" flake can point at the exact branch.
_FRAME_SKIPS: list = []  # list[tuple[tag, note]]

# Lint-fast alias so hot call sites can gate arg construction themselves
# (`if _WALKSTAT_ON: _walkstat_skip(tag, note)` instead of building the
# f-string per node when the walkstat flag is off).
_WALKSTAT_ON = _HOST_FRAME_FAILS_ENABLED


def _walkstat_skip(tag: str, note: str) -> None:
    """Record one silent walk-skip decision of the current frame (capped)."""
    if not _HOST_FRAME_FAILS_ENABLED or len(_FRAME_SKIPS) >= 16:
        return
    try:
        _FRAME_SKIPS.append((tag, note[:110]))
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
        pass


def _walkstat_depth(self) -> int:
    """Best-effort walk depth for skip breadcrumb context (0 when unknown)."""
    d = getattr(self, "_frame_depth", None)
    if isinstance(d, int):
        return d
    return 0


# Log-once keys: (where, exception_type_name)
_HOST_DRAW_FAIL_LOGGED: set[tuple[str, str]] = set()

# Env-gated once-log keys for RENPY_HOST_UI_TRACE=1 (Phase 1 evidence matrix).
# Fixed keys only: alpha_zero, draw_text_exc, empty_upload, dead_present,
# reverse_branch, drop_bake_residual, arena_count, face_fallback.
_UI_TRACE_LOGGED: set[str] = set()

# Phase 0 dissolve / write_texture sample throttle (RENPY_HOST_PHASE0_SIGNALS).
_PHASE0_LAST_DISSOLVE_T: float = 0.0
_PHASE0_LAST_WRITE_T: float = 0.0
_PHASE0_LAST_FRAME_T: float = 0.0
_PHASE0_DISSOLVE_INTERVAL = 0.25  # seconds during mid-dissolve window
_PHASE0_WRITE_INTERVAL = 1.0  # seconds for write_texture_ms
_PHASE0_FRAME_INTERVAL = 0.5  # seconds for prepare/draw/present samples

# Generic fallback for arbitrary keys (used by _phase0_due beyond the 3 built-ins).
_PHASE0_LAST_GENERIC: dict[str, float] = {}


def _phase0_signals_enabled() -> bool:
    # Delegates to centralized host_bridge.host_env_bool so all
    # RENPY_HOST_PHASE0_SIGNALS reads share one bool parser.
    return host_env_bool("RENPY_HOST_PHASE0_SIGNALS")


def _phase0_log(msg: str) -> None:
    """stderr PHASE0_SIGNAL line; same format as renpysound_host._phase0_log."""
    if not _phase0_signals_enabled():
        return
    try:
        import sys

        print(
            f"PHASE0_SIGNAL t={_time.monotonic():.3f} {msg}",
            file=sys.stderr,
            flush=True,
        )
    except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
        pass


def _phase0_due(key: str, now: float, interval: float) -> bool:
    """Generic throttle: True once per `interval` for `key`.

    Consolidates duplicated _phase0_due_dissolve/_write/_frame logic (DRY).
    Legacy per-key globals are kept for backward inspection; generic keys
    use _PHASE0_LAST_GENERIC dict.
    """
    if not _phase0_signals_enabled():
        return False
    global _PHASE0_LAST_DISSOLVE_T, _PHASE0_LAST_WRITE_T, _PHASE0_LAST_FRAME_T
    if key == "dissolve":
        if (now - _PHASE0_LAST_DISSOLVE_T) < interval:
            return False
        _PHASE0_LAST_DISSOLVE_T = now
        return True
    if key == "write":
        if (now - _PHASE0_LAST_WRITE_T) < interval:
            return False
        _PHASE0_LAST_WRITE_T = now
        return True
    if key == "frame":
        if (now - _PHASE0_LAST_FRAME_T) < interval:
            return False
        _PHASE0_LAST_FRAME_T = now
        return True
    # generic key path
    last = _PHASE0_LAST_GENERIC.get(key, 0.0)
    if (now - last) < interval:
        return False
    _PHASE0_LAST_GENERIC[key] = now
    return True


def _phase0_due_dissolve() -> bool:
    """True once per _PHASE0_DISSOLVE_INTERVAL while mid-dissolve samples."""
    return _phase0_due("dissolve", _time.monotonic(), _PHASE0_DISSOLVE_INTERVAL)


def _phase0_due_write() -> bool:
    """True once per _PHASE0_WRITE_INTERVAL for write_texture_ms samples."""
    return _phase0_due("write", _time.monotonic(), _PHASE0_WRITE_INTERVAL)


def _phase0_due_frame() -> bool:
    """True once per _PHASE0_FRAME_INTERVAL for prepare/draw frame samples."""
    return _phase0_due("frame", _time.monotonic(), _PHASE0_FRAME_INTERVAL)


def _safe_print(msg: str) -> None:
    """Write to real stdout — never ``sys.stdout`` after ``renpy.log`` redirect.

    Bare hermetic gates import ``WgpuDraw`` before ``renpy.config`` exists.
    Redirected ``print`` → ``renpy.log`` then raises ``AttributeError`` and
    aborts the frame (solid/frame gates read pure arena clear).
    """
    try:
        import renpy.log as _rlog  # type: ignore

        out = getattr(_rlog, "real_stdout", None) or sys.__stdout__
    except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
        out = sys.__stdout__
    try:
        out.write(msg + "\n")
        out.flush()
    except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
        try:
            sys.__stdout__.write(msg + "\n")
            sys.__stdout__.flush()
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass


def _ui_trace_once(key: str, msg: str) -> None:
    """Once-log under RENPY_HOST_UI_TRACE=1; keys fixed by plan (no spam)."""
    if not host_env_bool("RENPY_HOST_UI_TRACE"):
        return
    if key in _UI_TRACE_LOGGED:
        return
    _UI_TRACE_LOGGED.add(key)
    _safe_print(f"[UI_TRACE {key}] {msg}")


# Per-frame fail collector (RENPY_HOST_WALKSTAT=1): unlike _HOST_DRAW_FAIL_LOGGED
# (log-once dedup), this records EVERY fail of the current product frame so a
# flaky broken frame cannot be masked by an earlier benign failure of the same
# (where, type) pair. Reset at each product present (draw_screen body).
_HOST_FRAME_FAILS: list = []  # list[tuple[where, exc_type, msg]]


def _frame_fail_record(where: str, exc: BaseException) -> None:
    """Append to the per-frame fail list when walk-stat instrumentation is on."""
    if not _HOST_FRAME_FAILS_ENABLED:
        return
    if len(_HOST_FRAME_FAILS) < 32:
        try:
            _HOST_FRAME_FAILS.append((where, type(exc).__name__, str(exc)[:120]))
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass


# =============================================================================
# Bake-attribution counters (Phase 0d, pure instrumentation, zero draw
# semantics). Every nested-RTT bake trigger site calls bake_attn() with its
# where_tag + the node being baked + the RTT size; mixin call-sites may push a
# caller cookie (bake_caller_push/_pop) so nested render_to_texture bakes are
# attributed to the mixin path (dissolve_mid / imd_fallback) instead of the
# generic rtt_pass tag. bake_frame_flush() emits ONE throttled (0.5 s)
# PHASE0_SIGNAL summary at the end of each product frame in _draw_screen_body.
# Collection is enabled only under RENPY_HOST_WALKSTAT=1 or
# RENPY_HOST_PHASE0_SIGNALS=1 (_BAKE_ENABLED); all call sites are guarded and
# must never abort frames. Emission piggybacks on _phase0_log; when only
# RENPY_HOST_WALKSTAT=1 is set it prints the same line via _safe_print.
# =============================================================================
_BAKE_ENABLED: bool = _HOST_FRAME_FAILS_ENABLED or _phase0_signals_enabled()

_BAKE_TAGS: dict[str, int] = {}  # where_tag -> bake count (current window)
_BAKE_SIZES: dict[str, int] = {}  # "WxH" -> bake count
_BAKE_NODECLS: dict[str, int] = {}  # node class short name -> bake count
_BAKE_DETAIL: dict[str, int] = {}  # "tag/class/WxH" -> bake count
_BAKE_LAST_EMIT_T: float = 0.0
_BAKE_EMIT_INTERVAL = 0.5  # seconds; matches _PHASE0_FRAME_INTERVAL cadence


def bake_caller_push(self, tag: str):
    """Push a bake-attribution caller cookie on the draw object.

    Returns the stack depth before the push (for bake_caller_pop), or None
    when instrumentation is disabled (call sites no-op then).
    """
    if not _BAKE_ENABLED:
        return None
    try:
        stack = getattr(self, "_bake_caller_stack", None)
        if stack is None:
            stack = []
            self._bake_caller_stack = stack
        depth = len(stack)  # restore convention: pop(depth) ⇒ del stack[depth:]
        stack.append(tag)
        return depth
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
        return None


def bake_caller_pop(self, depth) -> None:
    """Restore the caller-cookie stack to ``depth`` (see bake_caller_push)."""
    if not _BAKE_ENABLED or depth is None:
        return
    try:
        stack = getattr(self, "_bake_caller_stack", None)
        if stack is not None and isinstance(depth, int) and depth >= 0:
            del stack[depth:]
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
        pass


def _bake_caller(self):
    try:
        stack = getattr(self, "_bake_caller_stack", None)
        if stack:
            return stack[-1]
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
        pass
    return None


def bake_attn(self, tag: str, node, w=0, h=0) -> None:
    """Record one nested-RTT bake trigger: (where_tag, node class, w×h).

    Effective tag is the innermost caller cookie when one is on the draw
    object's stack, else ``tag`` itself. Counts land in the frame-collector
    dicts and are summarized by bake_frame_flush() at the frame boundary.
    """
    if not _BAKE_ENABLED:
        return
    try:
        eff = _bake_caller(self) or tag
        if node is None:
            cls = "-"
        elif isinstance(node, int):
            cls = "tex"
        else:
            cls = type(node).__name__[:24] or "-"
        sk = "%dx%d" % (max(0, int(w or 0)), max(0, int(h or 0)))
        dk = "%s/%s/%s" % (eff, cls, sk)
        _BAKE_TAGS[eff] = _BAKE_TAGS.get(eff, 0) + 1
        _BAKE_SIZES[sk] = _BAKE_SIZES.get(sk, 0) + 1
        _BAKE_NODECLS[cls] = _BAKE_NODECLS.get(cls, 0) + 1
        _BAKE_DETAIL[dk] = _BAKE_DETAIL.get(dk, 0) + 1
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
        pass


def _fmt_counts(d: dict, cap: int = 10) -> str:
    """Counts facet, count-descendant sorted, capped (``+N_more`` suffix)."""
    try:
        items = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:cap]
        parts = ["%s:%d" % (k, v) for k, v in items]
        if len(d) > cap:
            parts.append("+%d_more" % (len(d) - cap))
        return ",".join(parts) or "-"
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
        return "-"


def bake_frame_flush(now: float = 0.0) -> None:
    """Emit the throttled per-frame bake-attribution summary line.

    Called once per product frame at the end of ``_draw_screen_body`` (before
    next-frame work). Counters accumulate across frames that fall inside the
    0.5 s throttle window (they are only cleared on an emitted line), so no
    bake attribution is ever lost to throttling; each emitted line covers the
    accumulated window since the previous emit (≥ one present frame).
    """
    global _BAKE_LAST_EMIT_T
    global _TRANS_CACHE_HITS, _TRANS_CACHE_MISSES
    if not _BAKE_ENABLED:
        return
    try:
        if not _BAKE_TAGS:
            return
        _now = float(now) if now else _time.monotonic()
        if (_now - _BAKE_LAST_EMIT_T) < _BAKE_EMIT_INTERVAL:
            return
        tags = dict(_BAKE_TAGS)
        sizes = dict(_BAKE_SIZES)
        ncls = dict(_BAKE_NODECLS)
        detail = dict(_BAKE_DETAIL)
        _BAKE_TAGS.clear()
        _BAKE_SIZES.clear()
        _BAKE_NODECLS.clear()
        _BAKE_DETAIL.clear()
        _BAKE_LAST_EMIT_T = _now
        # Transition bake cache window counters ride the same throttled line
        # (cleared on emit so slow-band values aggregate like bake tags do).
        tch = _TRANS_CACHE_HITS
        tcm = _TRANS_CACHE_MISSES
        _TRANS_CACHE_HITS = 0
        _TRANS_CACHE_MISSES = 0
        total = sum(tags.values())
        top_det = sorted(detail.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        det_s = ",".join(
            "%s=%d" % (k.replace("/", "|"), v) for k, v in top_det
        ) or "-"
        msg = (
            f"bakes_in_frame={total} tags={_fmt_counts(tags)} "
            f"cls={_fmt_counts(ncls)} sizes={_fmt_counts(sizes)} detail={det_s} "
            f"cache_hit={tch} miss={tcm}"
        )
        if _phase0_signals_enabled():
            _phase0_log(msg)
        elif _safe_print is not None and _HOST_FRAME_FAILS_ENABLED:
            _safe_print("PHASE0_SIGNAL t=%.3f %s" % (_now, msg))
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
        pass


# =============================================================================
# Transition bake cache signals + one-shot env switch (Phase 0 walkstat).
# Lives beside the bake-attribution counters because dissolve_mid consumers
# read them through the same PHASE0 frame-flush throttled line.
# RENPY_HOST_TRANS_BAKE_CACHE: unset/"1"/true/yes/on → enabled (default);
# "0"/false/off/no → disabled (behavior identical to pre-cache product).
# Parsed exactly once (OnceLock pattern).
# =============================================================================
_TRANS_BAKE_CACHE_ON: bool | None = None
# Since-last-emit window counters (cleared by bake_frame_flush like _BAKE_TAGS).
_TRANS_CACHE_HITS = 0
_TRANS_CACHE_MISSES = 0
# Lifetime totals (hermetic gates / offline greps read these directly).
_TRANS_CACHE_HIT_TOTAL = 0
_TRANS_CACHE_MISS_TOTAL = 0
_TRANS_CACHE_KEY_FAILS = 0
_TRANS_CACHE_CLEAR_TOTAL = 0


def trans_bake_cache_enabled() -> bool:
    """RENPY_HOST_TRANS_BAKE_CACHE — OnceLock parse; default on ("1")."""
    global _TRANS_BAKE_CACHE_ON
    v = _TRANS_BAKE_CACHE_ON
    if v is None:
        raw = os.environ.get("RENPY_HOST_TRANS_BAKE_CACHE", "").strip().lower()
        v = raw not in ("0", "false", "off", "no")
        _TRANS_BAKE_CACHE_ON = v
    return v


def trans_cache_count(kind: str) -> None:
    """Record one transition-cache decision (hit / miss / key_fail / clear)."""
    global _TRANS_CACHE_HITS, _TRANS_CACHE_MISSES
    global _TRANS_CACHE_HIT_TOTAL, _TRANS_CACHE_MISS_TOTAL
    global _TRANS_CACHE_KEY_FAILS, _TRANS_CACHE_CLEAR_TOTAL
    try:
        if kind == "hit":
            _TRANS_CACHE_HITS += 1
            _TRANS_CACHE_HIT_TOTAL += 1
        elif kind == "miss":
            _TRANS_CACHE_MISSES += 1
            _TRANS_CACHE_MISS_TOTAL += 1
        elif kind == "key_fail":
            _TRANS_CACHE_KEY_FAILS += 1
        elif kind == "clear":
            _TRANS_CACHE_CLEAR_TOTAL += 1
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
        pass


def trans_cache_totals() -> tuple[int, int, int, int]:
    """Lifetime (hit, miss, key_fail, clear) totals for hermetic gates."""
    return (
        _TRANS_CACHE_HIT_TOTAL,
        _TRANS_CACHE_MISS_TOTAL,
        _TRANS_CACHE_KEY_FAILS,
        _TRANS_CACHE_CLEAR_TOTAL,
    )


def _host_draw_fail(where: str, exc: BaseException) -> None:
    """Log a host-draw failure once per (where, type); optionally re-raise.

    When ``RENPY_HOST_DRAW_RAISE=1``, re-raises so CI / debug sessions surface
    the original traceback. Otherwise prints once and returns so the frame can
    continue with a typed placeholder.

    Always uses :func:`_safe_print` — never routes through ``renpy.log`` (needs
    full ``renpy.config`` and blows up bare host gates / early init).
    """
    _frame_fail_record(where, exc)
    key = (where, type(exc).__name__)
    if key not in _HOST_DRAW_FAIL_LOGGED:
        _HOST_DRAW_FAIL_LOGGED.add(key)
        msg = f"WgpuDraw.{where}: {type(exc).__name__}: {exc}"
        _safe_print(msg)
    if host_env_bool("RENPY_HOST_DRAW_RAISE"):
        raise exc


__all__ = [
    "_BAKE_DETAIL",
    "_BAKE_ENABLED",
    "_BAKE_EMIT_INTERVAL",
    "_BAKE_LAST_EMIT_T",
    "_BAKE_NODECLS",
    "_BAKE_SIZES",
    "_BAKE_TAGS",
    "_bake_caller",
    "_TRANS_BAKE_CACHE_ON",
    "_TRANS_CACHE_HITS",
    "_TRANS_CACHE_MISSES",
    "_TRANS_CACHE_HIT_TOTAL",
    "_TRANS_CACHE_MISS_TOTAL",
    "_TRANS_CACHE_KEY_FAILS",
    "_TRANS_CACHE_CLEAR_TOTAL",
    "trans_bake_cache_enabled",
    "trans_cache_count",
    "trans_cache_totals",
    "_DRAW_SCREEN_LOCK",
    "_HOST_DRAW_FAIL_LOGGED",
    "_HOST_FRAME_FAILS",
    "_HOST_FRAME_FAILS_ENABLED",
    "_fmt_counts",
    "_frame_fail_record",
    "bake_attn",
    "bake_caller_pop",
    "bake_caller_push",
    "bake_frame_flush",
    "_PHASE0_DISSOLVE_INTERVAL",
    "_PHASE0_FRAME_INTERVAL",
    "_PHASE0_LAST_DISSOLVE_T",
    "_PHASE0_LAST_FRAME_T",
    "_PHASE0_LAST_GENERIC",
    "_PHASE0_LAST_WRITE_T",
    "_PHASE0_WRITE_INTERVAL",
    "_UI_TRACE_LOGGED",
    "_draw_screen_lock",
    "_host_draw_fail",
    "_phase0_due",
    "_phase0_due_dissolve",
    "_phase0_due_frame",
    "_phase0_due_write",
    "_phase0_log",
    "_phase0_signals_enabled",
    "_safe_print",
    "_ui_trace_once",
]
