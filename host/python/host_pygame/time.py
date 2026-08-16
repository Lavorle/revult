"""Host time shim — TimerWheel via renpy_host."""

from __future__ import annotations

import renpy_host  # type: ignore


def get_ticks() -> int:
    return int(renpy_host.get_ticks_ms())


def wait(milliseconds: int) -> int:
    start = get_ticks()
    renpy_host.wait_until(start + max(0, int(milliseconds)))
    return get_ticks() - start


def delay(milliseconds: int) -> int:
    return wait(milliseconds)


def set_timer(eventid: int, milliseconds: int, loops: int = 0, once: bool = False) -> None:
    """Match pygame.time.set_timer; honor once= for Ren'Py TIMEEVENT/REDRAW."""
    if milliseconds <= 0:
        renpy_host.clear_timer(int(eventid))
    else:
        # loops==1 is also one-shot in some pygame versions.
        one_shot = bool(once) or (int(loops) == 1)
        renpy_host.set_timer(int(eventid), int(milliseconds), once=one_shot)


class Clock:
    def __init__(self):
        self.last = get_ticks()

    def tick(self, framerate=0):
        now = get_ticks()
        dt = now - self.last
        self.last = now
        if framerate > 0:
            target = int(1000 / framerate)
            if dt < target:
                wait(target - dt)
                now = get_ticks()
                dt = now - self.last
                self.last = now
        return dt

    def get_time(self):
        return 0

    def get_fps(self):
        return 0.0
