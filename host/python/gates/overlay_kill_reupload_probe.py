"""After kill_textures, re-drawing overlay-like full image must re-upload (not blank)."""
import os
from pathlib import Path
import renpy_host
from renpy.pygame.surface import Surface
from renpy.wgpu.draw import WgpuDraw, HostTexture

base = Path(os.environ.get("RENPY_HOST_BASE", "."))
out = base / "host" / "target" / "gate-overlay_kill_reupload_probe.txt"
VW, VH = 1280, 720
lines = []
ok = True

class R:
    def __init__(self, w, h):
        self.width=w; self.height=h; self.children=[]; self.mesh=None
        self.reverse=None; self.forward=None; self.cached_texture=None
        self.cached_model=None; self.loaded=False; self.blits=None
        self.ndc=None; self.texture=None; self.textures=None
        self.shaders=None; self.uniforms=None; self.color=None
        self.vertices=None; self.indices=None; self.pipeline=None
    def blit(self,c,xo=0,yo=0):
        self.children.append((c,float(xo),float(yo),False,True))

def left_dark(rgba,w,h):
    rs=gs=bs=n=0
    for y in range(h//4, 3*h//4, 4):
        for x in range(20, 100, 2):
            i=(y*w+x)*4
            rs+=rgba[i]; gs+=rgba[i+1]; bs+=rgba[i+2]; n+=1
    m=(rs/n+gs/n+bs/n)/3
    return m

try:
    draw = WgpuDraw(); draw.init((VW,VH))
    bg = Surface((VW,VH)); bg.fill((40,120,40,255))
    ov = Surface((VW,VH)); ov.fill((0,0,0,0))
    for y in range(VH):
        for x in range(280):
            ov.set_at((x,y),(0,0,0,204))

    def present(surf):
        tex = draw.load_texture(surf)  # uses texture_cache by id(surf)
        root = R(VW,VH)
        root.blit(draw.load_texture(bg),0,0)
        root.blit(tex,0,0)
        draw.draw_screen(root, flip=True)
        return renpy_host.read_game_rt_rgba()

    w,h,rgba = present(ov)
    m0 = left_dark(rgba,w,h)
    lines.append("before kill left_mean=%.1f" % m0)
    if m0 > 50:
        ok=False; lines.append("FAIL before not dark")

    # Simulate before_resize kill
    draw.kill_textures()
    # Old handle must not be reusable from Python cache alone — re-upload
    w,h,rgba = present(ov)
    m1 = left_dark(rgba,w,h)
    lines.append("after kill+reupload left_mean=%.1f" % m1)
    if m1 > 50:
        ok=False; lines.append("FAIL after not dark (dead handle?)")
    else:
        lines.append("PASS reupload")

    # reverse oversample full tex still dark full height after kill
    class Mat2:
        def __init__(self,xdx,ydy):
            self.xdx=xdx; self.ydy=ydy; self.xdy=0; self.ydx=0
    class RR(R):
        pass
    draw.kill_textures()
    tex = draw.load_texture(ov)
    root = R(VW,VH)
    root.blit(draw.load_texture(bg),0,0)
    img = RR(VW,VH)
    img.reverse = Mat2(1/1.5, 1/1.5)
    img.blit(tex,0,0)
    root.blit(img,0,0)
    dest = draw._reverse_dest_size(img, tex, (VW,VH))
    lines.append("reverse dest=%s" % (dest,))
    draw.draw_screen(root, flip=True)
    w,h,rgba = renpy_host.read_game_rt_rgba()
    m2 = left_dark(rgba,w,h)
    # also top of left strip
    rs=n=0
    for y in range(10, 40, 2):
        for x in range(20,80,2):
            i=(y*w+x)*4; rs+=rgba[i]+rgba[i+1]+rgba[i+2]; n+=1
    top = rs/(3*n)
    lines.append("after reverse left_mean=%.1f top_mean=%.1f" % (m2, top))
    if m2 > 50 or top > 60:
        ok=False; lines.append("FAIL reverse overlay incomplete")
    else:
        lines.append("PASS reverse full")

except Exception as e:
    ok=False
    import traceback
    lines.append("EXC %r" % e)
    lines.append(traceback.format_exc())

body=("ok=%s\n"%ok)+"\n".join(lines)+"\n"
out.write_text(body)
print(body)
renpy_host.request_quit()
if not ok: raise SystemExit(1)
