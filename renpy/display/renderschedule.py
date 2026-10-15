"""
Host-only render schedule lock (`renpy.display.renderschedule`).

Upstream Ren'Py performs the entire visit of `Interface.draw_screen` —
``per_frame`` mutations drain → ``process_redraws`` → ``render_screen`` →
``draw.draw_screen`` walk → ``mark_sweep`` — on ONE thread, so renpy's
render-system GC may freely assume nothing outside that body still
dereferences the killed Render trees. ``Render.mark_sweep`` kills every
render not reachable from the current ``screen_render`` and
``Render.kill`` then does ``del self.children[:]`` on the (still
Python-live) Render objects; a foreign walker thread that holds a freshly
rendered tree between its own ``render_screen`` and ``draw.draw_screen``
can have those same Render objects' ``children`` lists cleared by the
product thread's mark_sweep while it walks → the walk loses a whole
subtree prefix for that one frame (whole / partial-chrome black sample in
the prefs hover-thrash gate; kill-mid-walk, not a walker bug).

Introduce one cross-thread schedule mutex so exactly one thread is ever
between "build a render tree" and "sweep it", reproducing the upstream
single-thread invariant for any number of walker threads. Reentrant: the
product body itself nests (walk inner `_draw_screen_lock` etc.).

Import everywhere with a graceful fallback (no-op context manager) so
bare host gates / early renpy import paths cannot break.
"""

from __future__ import annotations

import contextlib
import threading

_LOCK = None  # type: ignore[var-annotated]


def _schedule_lock():
    """Create-on-first-use reentrant schedule lock (thread-safe)."""
    global _LOCK
    lock = _LOCK
    if lock is None:
        lock = threading.RLock()
        _LOCK = lock
    return lock


@contextlib.contextmanager
def render_schedule():
    """Context manager holding the host render schedule lock."""
    lock = _schedule_lock()
    acquired = False
    try:
        try:
            lock.acquire()
            acquired = True
        except Exception:  # noqa: BLE001 — lock-must-not-abort-frame fallback
            acquired = False
        yield
    finally:
        if acquired:
            try:
                lock.release()
            except Exception:  # noqa: BLE001, S110 — lock-must-not-abort-frame fallback
                pass


def schedule_lock():
    """Direct context-manager alias (same object, no fallback change)."""
    return _schedule_lock()


def schedule_lock_obj():
    """Raw lock object (RLock), for `with lock:` when a CM import is undesirable."""
    return _schedule_lock()

