#!/usr/bin/env python3
"""uinput-based REAL mouse injector (raw kernel, works on Wayland).

Emits EV_REL mouse events through /dev/uinput — the same path compositors
read a physical mouse from, so real clicks/motion drive the production
pipeline exactly like a human hand (this is the layer our pygame-level
synthetic events never reach).

uid must be in the ``input`` group (verified on this machine).

Usage:
  uinput_mouse.py move  <dx> <dy> [steps] [--dur ms]   # REL pointer move
  uinput_mouse.py click <l|r>                          # press+release
  uinput_mouse.py press|release <l|r>
  uinput_mouse.py stick --dx .. --dy .. --ms ..        # continuous motion
"""

from __future__ import annotations

import fcntl
import struct
import sys
import time

UI_PATH = "/dev/uinput"
UINPUT_MAX_NAME_SIZE = 80

# event types/codes
EV_FMT = "@llHHi"
EV_SYN, EV_KEY, EV_REL = 0x00, 0x01, 0x02
REL_X, REL_Y = 0x00, 0x01
BTN_LEFT, BTN_RIGHT = 0x110, 0x111
SYN_REPORT = 0


def _IOW(g, n, sz):
    return 0x40000000 | ((sz & 0x3FFF) << 16) | ((g & 0xFF) << 8) | (n & 0xFF)


# per /usr/include/linux/uinput.h (UI ioctl codes)
UI_SET_EVBIT = _IOW(0x55, 100, 4)
UI_SET_KEYBIT = _IOW(0x55, 101, 4)
UI_SET_RELBIT = _IOW(0x55, 102, 4)
_UI_DEV_CREATE = 0x5501  # _IO('U', 1)
_UI_DEV_DESTROY = 0x5502  # _IO('U', 2)


def _setup_blob():
    ident = struct.pack("=HHHH", 0x03, 0x1234, 0x0006, 1)  # BUS_USB vendor product version
    name = b"revult-synth-mouse"
    name = name + b"\0" * (UINPUT_MAX_NAME_SIZE - len(name))
    return ident + name + struct.pack("=I", 0)  # ff_effects_max = 0


_SETUP_BLOB = _setup_blob()
UI_DEV_SETUP = _IOW(0x55, 3, len(_SETUP_BLOB))


def _emit(fd, dx=0, dy=0, keys=()):
    now = time.time()
    t = int(now)
    u = int((now % 1) * 1e6)
    if dx or dy:
        for (ty, code, val) in ((EV_REL, REL_X, dx), (EV_REL, REL_Y, dy)):
            fd.write(struct.pack(EV_FMT, t, u, ty, code, int(val)))
        fd.write(struct.pack(EV_FMT, t, u, EV_SYN, SYN_REPORT, 0))
    for k in keys:
        fd.write(struct.pack(EV_FMT, int(time.time()), 0, EV_KEY, int(k), 1))
        fd.write(struct.pack(EV_FMT, int(time.time()), 0, EV_SYN, SYN_REPORT, 0))
        fd.write(struct.pack(EV_FMT, int(time.time()), 0, EV_KEY, int(k), 0))
        fd.write(struct.pack(EV_FMT, int(time.time()), 0, EV_SYN, SYN_REPORT, 0))


def main(argv):
    try:
        fd = open(UI_PATH, "wb", buffering=0)
    except OSError as e:
        print(f"cannot open {UI_PATH}: {e}; uid must be in the input group", file=sys.stderr)
        return 2

    def create():
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_REL)
        fcntl.ioctl(fd, UI_SET_RELBIT, REL_X)
        fcntl.ioctl(fd, UI_SET_RELBIT, REL_Y)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_LEFT)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_RIGHT)
        fcntl.ioctl(fd, UI_DEV_SETUP, _SETUP_BLOB)
        fcntl.ioctl(fd, _UI_DEV_CREATE, 0)

    try:
        create()
        cmd = argv[0]
        if cmd == "move":
            dx, dy = int(argv[1]), int(argv[2])
            steps = 1
            if len(argv) > 3 and argv[3] != "--dur":
                steps = int(argv[3])
            dur = 0.0
            if "--dur" in argv:
                dur = float(argv[argv.index("--dur") + 1]) / 1000.0 / max(1, steps)
            px = (dx / steps, dy / steps)
            for _ in range(steps):
                _emit(fd, px[0], px[1])
                if dur:
                    time.sleep(dur)
            rem = (dx - int(px[0]) * steps, dy - int(px[1]) * steps)
            _emit(fd, rem[0], rem[1])
        elif cmd == "stick":
            dx = int(argv[argv.index("--dx") + 1])
            dy = int(argv[argv.index("--dy") + 1])
            ms = int(argv[argv.index("--ms") + 1])
            steps = max(2, ms // 8)
            px = (dx / steps, dy / steps)
            for _ in range(steps):
                _emit(fd, px[0], px[1])
                time.sleep(ms / 1000.0 / steps)
            rem = (dx - int(px[0]) * steps, dy - int(px[1]) * steps)
            _emit(fd, rem[0], rem[1])
        elif cmd == "click":
            btn = BTN_LEFT if argv[1].startswith("l") else BTN_RIGHT
            _emit(fd, 0, 0, keys=(btn,))
        elif cmd == "press" or cmd == "down":
            btn = BTN_LEFT if argv[1].startswith("l") else BTN_RIGHT
            _emit(fd, 0, 0, keys=())
            fd.write(struct.pack(EV_FMT, int(time.time()), 0, EV_KEY, btn, 1))
            fd.write(struct.pack(EV_FMT, int(time.time()), 0, EV_SYN, SYN_REPORT, 0))
        elif cmd == "release" or cmd == "up":
            btn = BTN_LEFT if argv[1].startswith("l") else BTN_RIGHT
            fd.write(struct.pack(EV_FMT, int(time.time()), 0, EV_KEY, btn, 0))
            fd.write(struct.pack(EV_FMT, int(time.time()), 0, EV_SYN, SYN_REPORT, 0))
        else:
            print(__doc__)
            return 1
        return 0
    finally:
        try:
            fcntl.ioctl(fd, _UI_DEV_DESTROY, 0)
            fd.close()
        except Exception:  # noqa: BLE001 -- process exit cleanup only
            pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1:]))
