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


def log_host(where: str, exc: BaseException) -> None:
    """Single-point host-draw failure logger (converges scattered except blocks).

    Delegates to ``draw_debug._host_draw_fail`` when available so all
    ``except Exception`` paths that must not abort the frame share one
    throttle/one-print site. Falls back to stderr when draw_debug is not yet
    importable (early lint).
    """
    try:
        from .draw_debug import _host_draw_fail

        _host_draw_fail(where, exc)
    except Exception:
        try:
            import sys

            sys.__stdout__.write(f"WgpuDraw.{where}: {type(exc).__name__}: {exc}\n")
            sys.__stdout__.flush()
        except Exception:
            pass


def get_host():
    """Single-point factory for the optional ``renpy_host`` extension.

    Returns ``renpy_host`` when the extension is importable (product run),
    or ``None`` when linting without the native host (hermetic gates).
    """
    import sys

    h = sys.modules.get("renpy_host")
    if h is not None:
        return h
    return renpy_host


class _InstanceGroup:
    """Grouped instance batch for textured/solid quads (M1 T3 + ordered-stream drain).

    map key = (pipeline, texture, texture1, texture2) -> list[float] of packed 12-float instances
    layout per instance: rect_off(2), rect_size(2), uv_off(2), uv_size(2), color(4)

    Painter-order contract (ordered-stream integration, batched `_dm`):

    ``ScreenMixin._dm`` no longer commits each uniform draw immediately; it
    appends a ``("GRP", snapshot)`` boundary element to the draw stream
    whenever the group still holds quads at that tree position. The snapshot
    records, per key, the *cumulative* instance count queued into this epoch's
    bucket at that exact boundary. Consumption is offset-sliced:

    - each key owns a consumption offset (instances already drained from its
      bucket in the current epoch);
    - ``drain_upto(snapshot)`` emits exactly ``total[offset : snapshot_count]``
      per key (>= one ``draw_instances`` per live key), so nothing queued
      *after* the boundary is touched;
    - ``drain_all()`` drains each key's remainder; the bucket+offset view is
      reset ("drained full") only when every key is fully consumed, which
      makes every drain idempotent-by-offset — the follow-up
      ``_end_frame_present`` seam flush cannot double-submit;
    - ``self.gen`` is an epoch counter bumped on every reset. A boundary
      snapshot is valid only in its own epoch: after a full reset (or a
      ``flush`` full drain at an RTT push seam) the epoch moves and stale
      boundaries are skipped by the gen check instead of re-emitting already
      committed instances (no double draw).

    push/pop (nested RTT) stack the whole (map, offsets, gen) view so child
    bakes start a fresh epoch and the parent resumes byte-identical offsets.
    """

    def __init__(self):
        self.map: dict[tuple, list[float]] = {}
        self.offsets: dict[tuple, int] = {}
        self.gen: int = 0
        self.stack: list = []

    def clear(self):
        self.map.clear()
        self.offsets.clear()
        # Epoch moves: any outstanding boundary snapshot must not re-emit or
        # drain against cleared buckets.
        self.gen += 1

    def empty(self) -> bool:
        return not bool(self.map)

    def push(self):
        """Save current batch for nested frame (RTT) and start fresh.

        The entire consumption view (buckets, per-key offsets, epoch) is
        stacked; the child bake runs in a fresh epoch so its boundaries can
        never alias parent data.
        """
        self.stack.append((self.map, self.offsets, self.gen))
        self.map = {}
        self.offsets = {}
        self.gen += 1

    def pop(self):
        """Restore parent batch after nested frame."""
        if self.stack:
            self.map, self.offsets, self.gen = self.stack.pop()
        else:
            self.map = {}
            self.offsets = {}

    def snapshot(self):
        """Per-key cumulative instance counts of the current epoch, or None.

        Snapshot counts are cumulative-from-bucket-start because bucket data
        is never removed mid-epoch (only offset-advanced). Two boundaries
        B1/B2 (counts c1<c2) therefore drain [0,c1) and [c1,c2) in sequence.
        """
        out = {}
        for k, v in self.map.items():
            n = len(v) // 12
            if n > 0:
                out[k] = n
        if not out:
            return None
        return (self.gen, out)

    def drain_upto(self, snap, draw_obj=None):
        """Consume the boundary slice fixated by ``snap`` (snapshot tuple).

        For each key, emit instances from the current offset up to the
        snapshot's cumulative count (>= one draw_instances per live key).
        Data queued after the boundary (snapshot count exceeded) stays put
        for a later boundary or the tail drain. Stale-epoch snapshots
        (already fully drained elsewhere) are skipped — idempotency guard.
        """
        if not snap or not isinstance(snap, tuple):
            return
        try:
            gen, counts = snap
        except Exception:
            return
        if gen != self.gen:
            return
        for key, cnt in counts.items():
            bucket = self.map.get(key)
            if not bucket:
                continue
            total = len(bucket) // 12
            if total <= 0:
                continue
            start = self.offsets.get(key, 0)
            end = cnt if cnt < total else total
            if end <= start:
                continue
            data = bucket[start * 12:end * 12]
            # Advance BEFORE emission (old flush's clear-before-emit contract):
            # the hermetic fallback path may recursively commit appended quads
            # (`_flush_instance_fallback` -> `_dm` -> `_flush_draw_batch`), and
            # that nested runner's tail drain must not re-slice this same
            # unconsumed window.
            self.offsets[key] = end
            self._emit(key, data, draw_obj)
        self._reap()

    def drain_all(self, draw_obj=None):
        """Drain every key's unconsumed remainder (stream-runner tail)."""
        for key, bucket in list(self.map.items()):
            if not bucket:
                continue
            total = len(bucket) // 12
            start = self.offsets.get(key, 0)
            if total <= start:
                continue
            data = bucket[start * 12:]
            # Advance BEFORE emission (fallback re-entrancy guard, see
            # drain_upto): nested fallback commits must not re-slice this
            # window.
            self.offsets[key] = total
            self._emit(key, data, draw_obj)
        self._reap()

    def _reap(self):
        """Reset the epoch when every key is fully consumed (drain 满 reset)."""
        for k, v in self.map.items():
            if (len(v) // 12) > self.offsets.get(k, 0):
                return
        if self.map:
            self.map.clear()
            self.offsets.clear()
            self.gen += 1

    def _emit(self, key, datas, draw_obj=None):
        """Emit one instance slice (>= 12 floats) via host draw_instances."""
        if not datas:
            return
        pipe, tex, tex1, tex2 = key
        h = get_host()
        if h is not None and hasattr(h, "draw_instances"):
            try:
                h.draw_instances(int(pipe), tex, tex1, tex2, instances=list(datas))
                return
            except Exception:
                pass
        # fallback: per-quad via draw_obj if available (hermetic/lint)
        if draw_obj is not None:
            try:
                fallback = getattr(draw_obj, "_flush_instance_fallback", None)
                if fallback is not None:
                    # The quads appended by the fallback land in
                    # draw_obj._draw_batch; the stream runner commits them at
                    # the current tree position right after the drain
                    # (residue commit). Do NOT commit here: a nested full
                    # runner run would also drain the group's tail remainder,
                    # pulling post-boundary group quads ahead of later stream
                    # elements (order breach).
                    fallback(int(pipe), tex, tex1, tex2, list(datas))
            except Exception:
                pass

    def flush(self, draw_obj=None):
        """Seam commit of pending grouped quads (kept contract: full drain).

        When ``draw_obj`` carries the ordered draw stream (ScreenMixin), the
        painter-order-correct commit is the full stream run: boundaries in
        stream order slice the group at their exact tree positions, and the
        tail drain_all commits the remaining group quads after the last
        stream element. Delegating to ``_flush_draw_batch`` therefore keeps
        interleaved [batch, group quads, batch] pending content in exact tree
        order even when a legacy RTT-push seam calls ``_grp.flush()`` first.
        Without a stream carrier (hermetic/lint), this is the full local
        drain via draw_instances (legacy semantics).
        """
        runner = getattr(draw_obj, "_flush_draw_batch", None) if draw_obj is not None else None
        if runner is not None:
            try:
                runner()
                return
            except Exception:
                pass
        self.drain_all(draw_obj)

    def add(self, pipeline, texture, texture1, texture2, x0, y0, x1, y1, u0, v0, u1, v1, color):
        """Add one quad instance for grouping. Computes 12 floats and appends."""
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
        key = (int(pipeline) if pipeline is not None else 0, texture, texture1, texture2)
        lst = self.map.get(key)
        if lst is None:
            lst = []
            self.map[key] = lst
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
__all__ = ["get_host", "host_env_bool", "log_host", "renpy_host", "get_frame_stats", "_InstanceGroup"]
