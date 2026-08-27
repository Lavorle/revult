"""Accessibility probe stub — AT-SPI2 Orca deferred.

Provides ``get_screen_reader_active()`` so product code and gates can probe
without requiring a live AT-SPI2 session bus.  Future wiring will connect to
``renpy_host.a11y_probe`` (input.rs a11y::probe_orca) when the bus is
available; for now the probe JSON is a stable stub so JSON never KeyErrors.
"""

from __future__ import annotations

try:
    import renpy_host  # type: ignore
except Exception:  # pragma: no cover
    renpy_host = None  # type: ignore


def get_screen_reader_active() -> bool:
    """Return True if a screen reader (Orca) is active.

    Host path: ``renpy_host.get_screen_reader_active`` when available.
    Falls back to False so no KeyError on systems without AT-SPI2.
    """
    h = renpy_host
    if h is not None:
        try:
            # Prefer host FFI if present (input.rs a11y::screen_reader_active)
            if hasattr(h, "get_screen_reader_active"):
                return bool(h.get_screen_reader_active())
            if hasattr(h, "a11y_probe"):
                import json

                raw = h.a11y_probe()
                data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                if isinstance(data, dict):
                    return bool(data.get("screen_reader_active", False))
        except Exception:
            pass
    return False


def probe_orca() -> dict:
    """Return a11y probe dict (never raises, never KeyErrors)."""
    h = renpy_host
    if h is not None:
        try:
            if hasattr(h, "a11y_probe"):
                import json

                raw = h.a11y_probe()
                data = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
                if isinstance(data, dict) and "screen_reader_active" in data:
                    return dict(data)
        except Exception:
            pass
    return {"screen_reader_active": False, "backend": "stub", "detail": "deferred AT-SPI2"}
