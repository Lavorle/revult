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


class _InstanceGroup:
    """Grouped instance batch for textured/solid quads (M1 T3).

    map key = (pipeline, texture, texture1, texture2) -> list[float] of packed 12-float instances
    layout per instance: rect_off(2), rect_size(2), uv_off(2), uv_size(2), color(4)
    """
    def __init__(self):
        self.map: dict[tuple, list[float]] = {}
        self.stack: list[dict] = []

    def clear(self):
        self.map.clear()

    def empty(self) -> bool:
        return not bool(self.map)

    def push(self):
        """Save current batch for nested frame (RTT) and start fresh."""
        self.stack.append(self.map)
        self.map = {}

    def pop(self):
        """Restore parent batch after nested frame."""
        if self.stack:
            self.map = self.stack.pop()
        else:
            self.map = {}

    def add(self, pipeline, texture, texture1, texture2, x0, y0, x1, y1, u0, v0, u1, v1, color):
        """Add one quad instance for grouping. Computes 12 floats and appends."""
        key = (int(pipeline) if pipeline is not None else 0, texture, texture1, texture2)
        lst = self.map.get(key)
        if lst is None:
            lst = []
            self.map[key] = lst
        # rect off/size in NDC
        try:
            rox = float(x0)
            roy = float(y0)
            rsx = float(x1) - float(x0)
            rsy = float(y1) - float(y0)
            uox = float(u0)
            voy = float(v0)
            usx = float(u1) - float(u0)
            vsy = float(v1) - float(v0)
            # color 4 floats
            c = color or (1.0, 1.0, 1.0, 1.0)
            cr = float(c[0]) if len(c) > 0 else 1.0
            cg = float(c[1]) if len(c) > 1 else 1.0
            cb = float(c[2]) if len(c) > 2 else 1.0
            ca = float(c[3]) if len(c) > 3 else 1.0
        except Exception:
            return
        lst.extend([rox, roy, rsx, rsy, uox, voy, usx, vsy, cr, cg, cb, ca])

    def add_packed(self, key, rect_off, rect_size, uv_off, uv_size, color):
        """Alternative add with precomputed tuples (compat with spec sketch)."""
        pipeline, texture, texture1, texture2 = key
        x0, y0 = rect_off
        sx, sy = rect_size
        u0, v0 = uv_off
        usx, vsy = uv_size
        # reconstruct x1,y1,u1,v1
        self.add(pipeline, texture, texture1, texture2, x0, y0, x0 + sx, y0 + sy, u0, v0, u0 + usx, v0 + vsy, color)

    def flush(self, draw_obj=None):
        """Emit grouped draws via host draw_instances; fallback per-quad if unavailable."""
        if not self.map:
            return
        # snapshot and clear before emission to avoid re-entrancy
        pending = list(self.map.items())
        self.map.clear()
        h = get_host()
        # path: host draw_instances batch
        for (pipe, tex, tex1, tex2), datas in pending:
            if not datas:
                continue
            # Filter non-groupable that may have slipped in (should be solid/textured only)
            # Still emit; host will validate pipeline
            if h is not None and hasattr(h, "draw_instances"):
                try:
                    t = tex if tex is not None else None
                    t1 = tex1 if tex1 is not None else None
                    t2 = tex2 if tex2 is not None else None
                    h.draw_instances(int(pipe), t, t1, t2, instances=list(datas))
                    continue
                except Exception:
                    pass
            # fallback: per-quad via draw_obj._dm if available (hermetic/lint)
            if draw_obj is not None:
                try:
                    # need to reconstruct individual draws: iterate 12-float chunks
                    # Fallback requires creating meshes; delegate to draw_obj fallback helper if exists
                    fallback = getattr(draw_obj, "_flush_instance_fallback", None)
                    if fallback is not None:
                        fallback(int(pipe), tex, tex1, tex2, list(datas))
                    else:
                        # generic fallback: emit via _dm per quad using unit quad mesh placeholder
                        # We don't have rect, so skip? In lint we just no-op
                        pass
                except Exception:
                    pass



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
__all__ = ["get_host", "host_env_bool", "renpy_host", "get_frame_stats", "_InstanceGroup"]
