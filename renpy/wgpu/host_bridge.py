"""
host_bridge — single-point renpy_host import for wgpu package.

Centralizes the optional host extension import so call sites can use:

    from .host_bridge import renpy_host

instead of repeated `import renpy_host  # type: ignore` with fallback.

If the extension is unavailable (lint / hermetic gate), renpy_host is None.

Also centralizes RENPY_HOST_* env reads (bool-typed) so Python call-sites
do not scatter `os.environ.get(...) == "1"` checks.
"""

from __future__ import annotations

import os

try:
    import renpy_host  # type: ignore
except Exception:  # ImportError or extension load error  # noqa: BLE001 -- host import probe — Extension load may fail; fallback is imported host absent
    renpy_host = None  # type: ignore


def host_env_bool(name: str) -> bool:
    """Typed bool read for ``RENPY_HOST_*`` env vars.

    Accepts ``1`` / ``true`` / ``yes`` (case-insensitive, whitespace-trimmed),
    matching ``host/renpy-host/src/config.rs::env_bool``.
    Keeps compatibility with prior direct ``os.environ.get`` call-sites; only
    centralizes the parsing.
    """
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def get_host():
    """Single-point factory for the optional ``renpy_host`` extension.

    Returns the imported ``renpy_host`` module or ``None`` when unavailable
    (lint / hermetic gate). Call-sites should do ``if get_host() is None: ...``.
    """
    return renpy_host


def get_frame_stats():
    """Thin wrapper for ``renpy_host.get_frame_stats`` with hasattr fallback.

    Returns a dict with 5 keys: draw_calls, quads, instances, overdraw_est, ms.
    Falls back to zeros when the host is unavailable, lacks the symbol, or
    raises (perf gate not enabled / no frame yet). Never raises.
    """
    try:
        h = get_host()
        if h is not None and hasattr(h, "get_frame_stats"):
            return h.get_frame_stats()  # type: ignore[union-attr]
    except Exception:
        pass
    return {
        "draw_calls": 0,
        "quads": 0,
        "instances": 0,
        "overdraw_est": 0.0,
        "ms": 0.0,
    }


__all__ = ["get_host", "host_env_bool", "renpy_host", "get_frame_stats"]
