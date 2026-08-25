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


__all__ = ["get_host", "host_env_bool", "renpy_host"]
