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
except Exception:  # pragma: no cover - import fallback for bare import

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
    except Exception:
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
    except Exception:
        out = sys.__stdout__
    try:
        out.write(msg + "\n")
        out.flush()
    except Exception:
        try:
            sys.__stdout__.write(msg + "\n")
            sys.__stdout__.flush()
        except Exception:
            pass


def _ui_trace_once(key: str, msg: str) -> None:
    """Once-log under RENPY_HOST_UI_TRACE=1; keys fixed by plan (no spam)."""
    if not host_env_bool("RENPY_HOST_UI_TRACE"):
        return
    if key in _UI_TRACE_LOGGED:
        return
    _UI_TRACE_LOGGED.add(key)
    _safe_print(f"[UI_TRACE {key}] {msg}")


def _host_draw_fail(where: str, exc: BaseException) -> None:
    """Log a host-draw failure once per (where, type); optionally re-raise.

    When ``RENPY_HOST_DRAW_RAISE=1``, re-raises so CI / debug sessions surface
    the original traceback. Otherwise prints once and returns so the frame can
    continue with a typed placeholder.

    Always uses :func:`_safe_print` — never routes through ``renpy.log`` (needs
    full ``renpy.config`` and blows up bare host gates / early init).
    """
    key = (where, type(exc).__name__)
    if key not in _HOST_DRAW_FAIL_LOGGED:
        _HOST_DRAW_FAIL_LOGGED.add(key)
        msg = f"WgpuDraw.{where}: {type(exc).__name__}: {exc}"
        _safe_print(msg)
    if host_env_bool("RENPY_HOST_DRAW_RAISE"):
        raise exc


__all__ = [
    "_DRAW_SCREEN_LOCK",
    "_HOST_DRAW_FAIL_LOGGED",
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
