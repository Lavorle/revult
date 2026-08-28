"""
is_pixel_opaque probe (autoresearch C2).

Gate name: is_pixel_opaque_probe  (RENPY_HOST_GATE=is_pixel_opaque_probe)

Verifies WgpuDraw.is_pixel_opaque samples real alpha via RTT readback:
  1) empty/clear RTT size tuple → opaque=False
  2) solid opaque surface → opaque=True
  3) fully transparent surface → opaque=False

Writes host/target/gate-is_pixel_opaque_probe.txt with ok=True/False.
"""

import os
from pathlib import Path


import renpy_host

from renpy.wgpu.draw import WgpuDraw


def _repo_root() -> Path:
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path.cwd()


def _result_path() -> Path:
    return _repo_root() / "host" / "target" / "gate-is_pixel_opaque_probe.txt"


class _PixelSurf:
    """Minimal surface-like object for load_texture / is_pixel_opaque."""

    def __init__(self, w, h, rgba_pixel):
        self.width = int(w)
        self.height = int(h)
        r, g, b, a = rgba_pixel
        self._pixels = bytes([r, g, b, a]) * (self.width * self.height)

    def get_size(self):
        return (self.width, self.height)


draw = WgpuDraw()
assert draw.init((1280, 720))

notes = []
ok = True

# Case A: empty size RTT (clear a=0)
empty = draw.render_to_texture((4, 4))
a_empty = draw.is_pixel_opaque(empty)
notes.append(f"empty_rtt_opaque={a_empty}")
if a_empty is not False:
    ok = False
    notes.append("FAIL: empty RTT should be transparent")

# Case B: solid opaque red surface (render_to_texture path via is_pixel_opaque)
opaque_surf = _PixelSurf(8, 8, (255, 0, 0, 255))
b_opaque = draw.is_pixel_opaque(opaque_surf)
notes.append(f"opaque_surf_opaque={b_opaque}")
if b_opaque is not True:
    ok = False
    notes.append("FAIL: opaque surface should be opaque")

# Case C: fully transparent surface
trans_surf = _PixelSurf(8, 8, (0, 0, 0, 0))
c_trans = draw.is_pixel_opaque(trans_surf)
notes.append(f"trans_surf_opaque={c_trans}")
if c_trans is not False:
    ok = False
    notes.append("FAIL: transparent surface should not be opaque")

# Signature: only `what` (Render path)
only_what = draw.is_pixel_opaque(empty)
notes.append(f"signature_only_what={only_what}")

msg = "gate=is_pixel_opaque_probe\nok={}\n{}\n".format(
    "True" if ok else "False",
    "\n".join(notes),
)
out = _result_path()
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg, encoding="utf-8")
print(msg, flush=True)
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
