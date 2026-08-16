"""Host event module — host-fed queue via renpy_host; wait() is banned."""

from __future__ import annotations

from collections import deque

import renpy_host  # type: ignore

from .locals import NOEVENT, USEREVENT

_event_names: dict[int, str] = {}
_pushback = None
_wait_guard_armed = True
# Python-posted events (pygame.event.post / renpy.queue_event EVENTNAME).
# Held here so EVENTNAME payloads (eventnames lists) are not lost — Rust queue
# only carries Int/Float/Bool/Str and host inject_* helpers do not cover EVENTNAME.
_posted: deque = deque()
# Slice 1 (D2 nested-pump starvation): throttle side-pumps on the busy
# event_poll path so live winit events reach EVENT_QUEUE without spinning
# nested pump_app_events on every empty poll.
# Count-based throttle (not wall-clock): product busy-polls tens of thousands of
# times per second; wall-clock throttle was observed to stall after a few fires
# while poll kept spinning, so use poll cadence instead.
_polls_since_pump = 0
_NESTED_PUMP_EVERY_POLLS = 64
# Busy-poll side-pump must NOT block for 16ms: with ~600 pumps/s (INPUT_TRACE b
# ~9–13k / 15–20s) a 16ms wait would stack into multi-second freezes (Task #24
# F1). Zero-timeout drains pending winit events and returns immediately.
_NESTED_PUMP_BUSY_MS = 0
# Explicit pygame.event.pump() may use a short wait so waiters can observe
# a frame of OS events without the busy-poll tax.
_NESTED_PUMP_EXPLICIT_MS = 1
# Phase 0 dual-signal: count _nested_pump_slice fires (env-gated).
_phase0_pump_slice_count = 0
_phase0_pump_slice_logged = False
_phase0_first_poll_logged = False


class Event:
    def __init__(self, type_id=NOEVENT, dict=None, **kwargs):
        self.type = type_id
        self.dict = dict or {}
        self.dict.update(kwargs)
        # Coerce host-fed fields that stock SDL events expose as tuples.
        for key in ("pos", "rel"):
            if key in self.dict:
                self.dict[key] = _coerce_xy(self.dict[key])
        if "buttons" in self.dict:
            self.dict["buttons"] = _coerce_buttons(self.dict["buttons"])
        # KEYDOWN/KEYUP always expose the stock SDL keyboard fields so
        # renpy.display.behavior.map_event can read ev.unicode without
        # AttributeError when the host path omitted them.
        from . import locals as L

        if self.type in (L.KEYDOWN, L.KEYUP):
            self.dict.setdefault("unicode", "")
            self.dict.setdefault("key", 0)
            self.dict.setdefault("scancode", 0)
            self.dict.setdefault("mod", 0)
            self.dict.setdefault("repeat", False)
        # Mouse types also expose mod (map_event applies mod checks to mouse
        # keysyms too). core.py stamps ev.mod after poll, but belt-and-suspenders
        # for any path that reads Event before that stamp.
        if self.type in (L.MOUSEMOTION, L.MOUSEBUTTONDOWN, L.MOUSEBUTTONUP, L.MOUSEWHEEL):
            self.dict.setdefault("mod", 0)
        for k, v in self.dict.items():
            setattr(self, k, v)

    def __repr__(self):
        return f"<Event({self.type} {self.dict})>"


def _coerce_xy(v, default=(0, 0)):
    """Normalize pos/rel to a 2-int tuple (host may send 'x,y' strings)."""
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        try:
            return (int(v[0]), int(v[1]))
        except Exception:
            return default
    if isinstance(v, str) and "," in v:
        a, b = v.split(",", 1)
        try:
            return (int(float(a.strip())), int(float(b.strip())))
        except Exception:
            return default
    return default


def _coerce_buttons(v, default=(0, 0, 0)):
    """Normalize MOUSEMOTION.buttons to a 3-tuple (stock SDL / pygame)."""
    if isinstance(v, (tuple, list)):
        vals = list(v) + [0, 0, 0]
        try:
            return (int(vals[0]), int(vals[1]), int(vals[2]))
        except Exception:
            return default
    if isinstance(v, int) and not isinstance(v, bool):
        # Bitmask or zero from host Rust path — expand left/middle/right.
        return (1 if v & 1 else 0, 1 if v & 2 else 0, 1 if v & 4 else 0)
    if isinstance(v, str) and "," in v:
        parts = v.split(",")
        try:
            nums = [int(float(p.strip())) for p in parts] + [0, 0, 0]
            return (nums[0], nums[1], nums[2])
        except Exception:
            return default
    return default


def event_name(type_id: int) -> str:
    return _event_names.get(type_id, f"Unknown({type_id})")


def register(name: str) -> int:
    type_id = renpy_host.register_event_type(name)
    _event_names[type_id] = name
    return type_id


def get_standard_events():
    from . import locals as L

    return {
        L.QUIT,
        L.KEYDOWN,
        L.KEYUP,
        L.TEXTEDITING,
        L.TEXTINPUT,
        L.MOUSEMOTION,
        L.MOUSEBUTTONDOWN,
        L.MOUSEBUTTONUP,
        L.MOUSEWHEEL,
        L.WINDOWRESIZED,
        L.WINDOWEXPOSED,
        L.WINDOWFOCUSGAINED,
        L.WINDOWFOCUSLOST,
    }


def _sync_mouse_from_event(ev: Event) -> None:
    """Keep host_pygame.mouse._pos / pressed in sync with live polled events.

    winit CursorMoved / MouseInput only push the host queue; without this,
    pygame.mouse.get_pos() stays at (0,0) and hover/focus miss click targets
    even when event.pos is correct (Slice 2 residual H4).
    """
    from . import locals as L
    from . import mouse as mouse_mod

    t = ev.type
    if t == L.MOUSEMOTION:
        pos = getattr(ev, "pos", None)
        if isinstance(pos, (tuple, list)) and len(pos) >= 2:
            try:
                mouse_mod.set_pos((int(pos[0]), int(pos[1])))
            except Exception:
                pass
        buttons = getattr(ev, "buttons", None)
        if isinstance(buttons, (tuple, list)) and len(buttons) >= 3:
            try:
                mouse_mod.set_pressed(
                    (
                        bool(buttons[0]),
                        bool(buttons[1]),
                        bool(buttons[2]),
                    )
                )
            except Exception:
                pass
        return
    if t in (L.MOUSEBUTTONDOWN, L.MOUSEBUTTONUP):
        pos = getattr(ev, "pos", None)
        if isinstance(pos, (tuple, list)) and len(pos) >= 2:
            try:
                mouse_mod.set_pos((int(pos[0]), int(pos[1])))
            except Exception:
                pass
        btn = int(getattr(ev, "button", 0) or 0)
        if 1 <= btn <= 3:
            try:
                pressed = list(mouse_mod.get_pressed())
                # pad to 3
                while len(pressed) < 3:
                    pressed.append(False)
                pressed[btn - 1] = t == L.MOUSEBUTTONDOWN
                mouse_mod.set_pressed(
                    (bool(pressed[0]), bool(pressed[1]), bool(pressed[2]))
                )
            except Exception:
                pass


def _from_host(d) -> Event:
    if d is None:
        return Event(NOEVENT)
    type_id = d.get("type", NOEVENT)
    payload = {k: v for k, v in d.items() if k != "type"}
    return Event(type_id, payload)


def _phase0_signals_enabled() -> bool:
    import os

    return os.environ.get("RENPY_HOST_PHASE0_SIGNALS", "").strip() in ("1", "true", "yes")


def _phase0_log(msg: str) -> None:
    if not _phase0_signals_enabled():
        return
    try:
        import sys
        import time as _time

        print(
            f"PHASE0_SIGNAL t={_time.monotonic():.3f} {msg}",
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        pass


def _nested_pump_slice(ms: int | None = None) -> None:
    """Re-enter the host EventLoop briefly so live winit events reach the queue.

    Product interact often stays on ``event_poll`` (no nested pump) when
    ``needs_redraw`` is set. The only live path was
    ``event_wait`` → ``wait_until`` → ``try_nested_pump``. Without an occasional
    pump on the busy-poll path, INPUT_TRACE (b) starves after the first wait.

    Use ``renpy_host.pump_once`` (not ``wait_until``): ``wait_until`` early-exits
    when EVENT_QUEUE is non-empty (PERIODIC/TIMEEVENT), which is the common
    busy-poll case and was the residual after the first side-pump attempts.

    Default slice is 0 ms (drain pending only). A 16 ms default was measured to
    stack with ~600 pumps/s into multi-second main-menu freezes (F1 residual).
    """
    global _phase0_pump_slice_count, _phase0_pump_slice_logged
    try:
        if ms is None:
            slice_ms = _NESTED_PUMP_BUSY_MS
        else:
            slice_ms = max(0, min(16, int(ms)))
        pump = getattr(renpy_host, "pump_once", None)
        if pump is not None:
            pumped = pump(slice_ms)
        else:
            # Old binary: wait_until needs a positive deadline delta.
            renpy_host.wait_until(int(renpy_host.get_ticks_ms()) + max(1, slice_ms))
            pumped = None
        if _phase0_signals_enabled():
            _phase0_pump_slice_count += 1
            # First fire + every 256 thereafter (avoid log flood on busy-poll).
            if not _phase0_pump_slice_logged or (_phase0_pump_slice_count % 256) == 0:
                _phase0_pump_slice_logged = True
                _phase0_log(
                    f"nested_pump_slice n={_phase0_pump_slice_count} "
                    f"slice_ms={slice_ms} pumped={pumped!r} "
                    f"has_pump_once={pump is not None}"
                )
    except Exception:
        # Never let pump cadence restore crash the interact loop.
        pass


def poll() -> Event:
    global _pushback, _polls_since_pump, _phase0_first_poll_logged
    if not _phase0_first_poll_logged and _phase0_signals_enabled():
        _phase0_first_poll_logged = True
        has_pump = getattr(renpy_host, "pump_once", None) is not None
        _phase0_log(
            f"event_poll_first has_pump_once={has_pump} "
            f"every_polls={_NESTED_PUMP_EVERY_POLLS} busy_ms={_NESTED_PUMP_BUSY_MS}"
        )
    # Count-throttled nested pump on EVERY poll path (including Python-posted
    # TIMEEVENT/REDRAW/_pushback). Otherwise busy-poll stays on _posted forever
    # after the first PERIODIC flood and never re-enters pump_once (observed:
    # 400k polls with since=1 after a single pump).
    _polls_since_pump += 1
    if _polls_since_pump >= _NESTED_PUMP_EVERY_POLLS:
        _polls_since_pump = 0
        _nested_pump_slice()
    if _pushback is not None:
        d = _pushback
        _pushback = None
        ev = _from_host(d) if isinstance(d, dict) else d
        if isinstance(ev, Event):
            _sync_mouse_from_event(ev)
        return ev
    # Prefer Python-posted events (queue_event / EVENTNAME) over host queue.
    if _posted:
        ev = _posted.popleft()
        if isinstance(ev, Event):
            _sync_mouse_from_event(ev)
        return ev
    host_ev = renpy_host.poll_event()
    ev = _from_host(host_ev)
    if isinstance(ev, Event) and ev.type != NOEVENT:
        _sync_mouse_from_event(ev)
    return ev


def get(eventtype=None, pump=True, exclude=None):
    """Drain matching events. Non-matching events are preserved (pygame parity).

    Residual post-pump: ``interact_core`` does ``pygame.event.get([PERIODIC])``
    (and similar) on every PERIODIC. Stock pygame leaves non-matching events on
    the queue; the prior host shim discarded them, so injected/live KEY and
    MOUSE events never reached ``Button.event`` / ``key_handler`` while the
    timer wheel kept PERIODIC flowing. That looked like total-dead input even
    after nested-pump cadence was restored.
    """
    out = []
    kept = []
    for _ in range(256):
        ev = poll()
        if ev.type == NOEVENT:
            break
        if eventtype is None:
            if exclude and ev.type in exclude:
                kept.append(ev)
                continue
            out.append(ev)
            continue
        types = eventtype if isinstance(eventtype, (list, tuple, set)) else (eventtype,)
        if ev.type in types:
            if exclude and ev.type in exclude:
                kept.append(ev)
                continue
            out.append(ev)
        else:
            # Preserve non-matching (KEY/MOUSE/WINDOW…) for later poll/get.
            kept.append(ev)
    if kept:
        # Restore original order: unmatched first, then any still-pending posted.
        kept.reverse()
        for ev in kept:
            _posted.appendleft(ev)
    return out


def peek(eventtype=None) -> bool:
    global _pushback
    if _posted:
        ev0 = _posted[0]
        if eventtype is None:
            return True
        types = eventtype if isinstance(eventtype, (list, tuple, set)) else (eventtype,)
        return ev0.type in types
    ev = renpy_host.poll_event()
    if ev is None:
        return False
    _pushback = ev
    if eventtype is None:
        return True
    types = eventtype if isinstance(eventtype, (list, tuple, set)) else (eventtype,)
    return ev.get("type") in types


def wait(timeout=None) -> Event:
    """Banned on host product path (plan Phase 1 assert)."""
    if _wait_guard_armed:
        raise RuntimeError(
            "pygame.event.wait is forbidden on renpy-host builds "
            "(use poll + renpy_host.wait_until / Mechanism 1)"
        )
    while True:
        ev = poll()
        if ev.type != NOEVENT:
            return ev
        renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)


def clear(eventtype=None):
    """Clear matching events. Non-matching host events must be preserved.

    Residual post-pump: ``interact_core`` calls ``pygame.event.clear([TIMEEVENT])``
    / ``clear([REDRAW])`` often. The prior host clear drained *all* host-queue
    events, discarding injected/live KEY and MOUSE before they could be polled —
    same total-dead class as the non-preserving ``get()`` bug.
    """
    global _pushback
    if eventtype is None:
        _pushback = None
        _posted.clear()
        for _ in range(256):
            if renpy_host.poll_event() is None:
                break
        return

    types = eventtype if isinstance(eventtype, (list, tuple, set)) else (eventtype,)
    # Drop matching from pushback / posted.
    if _pushback is not None:
        pb = _pushback if isinstance(_pushback, dict) else None
        pb_type = pb.get("type") if pb is not None else getattr(_pushback, "type", None)
        if pb_type in types:
            _pushback = None
    kept = deque(e for e in _posted if e.type not in types)
    _posted.clear()
    _posted.extend(kept)
    # Drain host queue but re-queue non-matching events.
    for _ in range(256):
        host_ev = renpy_host.poll_event()
        if host_ev is None:
            break
        t = host_ev.get("type")
        if t not in types:
            _posted.append(_from_host(host_ev))


def pump():
    """Drain live winit events into the host queue (was a no-op; starved input)."""
    _nested_pump_slice(_NESTED_PUMP_EXPLICIT_MS)
    return None


def post(event: Event):
    """Post an Event into the host queue (supports EVENTNAME / queue_event)."""
    if event is None:
        return
    # Keep KEY/TEXT on Rust inject path for inject_key consumers; everything else
    # (especially EVENTNAME with eventnames lists) goes on the Python posted queue.
    t = getattr(event, "type", NOEVENT)
    try:
        if t == renpy_host.KEYDOWN:
            renpy_host.inject_key(
                int(getattr(event, "key", 0)),
                True,
                str(getattr(event, "unicode", "")),
            )
            return
        if t == renpy_host.KEYUP:
            renpy_host.inject_key(
                int(getattr(event, "key", 0)),
                False,
                str(getattr(event, "unicode", "")),
            )
            return
        if t == renpy_host.TEXTINPUT:
            renpy_host.inject_text(str(getattr(event, "text", "")))
            return
    except Exception:
        pass
    _posted.append(event)


def set_blocked(*args, **kwargs):
    return None


def set_allowed(*args, **kwargs):
    return None


def get_blocked(*args, **kwargs):
    return False


def set_grab(grab):
    return None


def get_grab():
    return False
