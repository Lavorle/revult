"""Screenshot clipboard + reverse dest sizing for overlay/typewriter."""
import os, sys, traceback
from pathlib import Path
import renpy_host
from renpy.wgpu.draw import WgpuDraw, HostTexture

base = Path(os.environ.get("RENPY_HOST_BASE", "."))
out = base / "host" / "target" / "gate-screenshot_host_probe.txt"
lines = []
ok = True

try:
    import host_pygame.scrap as scrap
    assert hasattr(scrap, "put_data")
    scrap.put_data({"image/png": b"x"})
    lines.append("PASS: host scrap.put_data")
except Exception as e:
    ok = False
    lines.append("FAIL scrap %r" % e)

try:
    class Mat2:
        def __init__(self, xdx, ydy):
            self.xdx = xdx
            self.ydy = ydy
            self.xdy = 0
            self.ydx = 0

    class R:
        def __init__(self, w, h, rev=None):
            self.width = w
            self.height = h
            self.children = []
            self.mesh = None
            self.reverse = rev

        def blit(self, c, xo=0, yo=0):
            self.children.append((c, xo, yo, False, True))

    d = WgpuDraw()
    d.init((1280, 720))
    # overlay: full oversampled tex, reverse 1/1.5, parent virtual box
    parent = R(420, 720, Mat2(1 / 1.5, 1 / 1.5))
    child = HostTexture(1, 630, 1080, 0, 0, 630, 1080)
    parent.blit(child, 0, 0)
    dest = d._reverse_dest_size(parent, child, (420, 720))
    lines.append("overlay dest=%s" % (dest,))
    if dest != (420, 720):
        ok = False
        lines.append("FAIL overlay")
    else:
        lines.append("PASS overlay fills parent")

    # typewriter partial
    parent2 = R(400, 48, Mat2(1 / 1.5, 1 / 1.5))
    child2 = HostTexture(1, 600, 72, 0, 0, 150, 72)
    parent2.blit(child2, 0, 0)
    dest2 = d._reverse_dest_size(parent2, child2, (400, 48))
    lines.append("tw dest=%s" % (dest2,))
    if abs(dest2[0] - 100) > 2 or abs(dest2[1] - 48) > 2:
        ok = False
        lines.append("FAIL tw")
    else:
        lines.append("PASS tw partial")
except Exception as e:
    ok = False
    lines.append("EXCEPTION %r" % e)
    lines.append(traceback.format_exc())

body = ("ok=%s\n" % ok) + "\n".join(lines) + "\n"
out.write_text(body)
print(body)
renpy_host.request_quit()
if not ok:
    raise SystemExit(1)
