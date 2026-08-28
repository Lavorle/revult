import os
from pathlib import Path

import renpy_host

from renpy.wgpu.draw import WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---

base = Path(os.environ.get("RENPY_HOST_BASE","/mnt/nvme1n1p2/revult"))
GUI = base/"host/playtests/HuangmeiC/game/gui"
VW,VH=1280,720
BG=(40,80,120,255)

class Mat2:
    def __init__(self,a,b,c,d):
        self.xdx=float(a); self.xdy=float(b); self.ydx=float(c); self.ydy=float(d)

class Surf:
    def __init__(self,w,h,p):
        self._w=int(w); self._h=int(h)
        need=self._w*self._h*4
        raw=bytes(p)
        self._pixels=raw if len(raw)>=need else raw+bytes(need-len(raw))
    def get_size(self): return self._w,self._h

class R:
    def __init__(self,w,h):
        self.width=int(w); self.height=int(h); self.children=[]
        self.mesh=None; self.texture=None; self.textures=None; self.color=None
        self.shaders=None; self.pipeline=None; self.vertices=None; self.indices=None
        self.cached_model=None; self.blits=None; self.ndc=None; self.uniforms=None
        self.loaded=False; self.forward=None; self.reverse=None
    def blit(self,c,x=0,y=0):
        self.children.append((c,float(x),float(y),False,True))
    def get_size(self): return self.width,self.height

import struct
import zlib


def png_rgba(path):
    data=path.read_bytes(); pos=8; w=h=None; raw=b""; ct=None
    while pos < len(data):
        ln=struct.unpack(">I", data[pos:pos+4])[0]; pos+=4
        typ=data[pos:pos+4]; pos+=4; chunk=data[pos:pos+ln]; pos+=ln+4
        if typ==b'IHDR':
            w,h=struct.unpack(">II", chunk[:8]); ct=chunk[9]
        elif typ==b'IDAT':
            raw+=chunk
        elif typ==b'IEND':
            break
    zlib.decompress(raw); {2:3,6:4,0:1,4:2}.get(ct,4)
    # only handle RGBA
    bytearray()
    w*4
    # re-decode properly via simple path if already RGBA
    # use gate's decoder by import
    return w,h,None

# import decoder from gate
import sys

sys.path.insert(0,str(base/"host/python/gates"))
from hmc_chrome_residual import near_bg, samp
from hmc_chrome_residual import png_rgba as pr

draw=WgpuDraw(); draw.init((VW,VH))
rel="flowchart/common/preview_background.png"
w,h,px=pr(GUI/rel)
print("src",w,h)
root=R(VW,VH)
root.blit(Surf(VW,VH,bytes([BG[0],BG[1],BG[2],BG[3]])*(VW*VH)),0,0)
t=draw.load_texture(Surf(w,h,px))
print("ht",t, getattr(t,"handle",None), getattr(t,"w",None), getattr(t,"h",None))
if w>600 or h>200:
    dw,dh=min(w,600),min(h,200)
    node=R(dw,dh)
    node.reverse=Mat2(dw/float(w),0,0,dh/float(h))
    node.forward=Mat2(w/float(dw),0,0,h/float(dh))
    node.blit(t,0,0)
    root.blit(node,50,50)
    size=(dw,dh)
    cx,cy=50+dw//2,50+dh//2
else:
    root.blit(t,50,50); size=(w,h); cx,cy=50+w//2,50+h//2
print("size",size,"center",cx,cy)
draw.draw_screen(root, flip=True)
rw,rh,rt=renpy_host.read_game_rt_rgba()
c=samp(rt,rw,rh,cx,cy)
print("center_rgb",c,"near_bg",near_bg(c),"mean_sample",c)
# also sample a few points
for (x,y) in [(50,50),(cx,cy),(50+size[0]-1,50+size[1]-1),(10,10)]:
    print("samp",x,y,samp(rt,rw,rh,x,y))
print("handle_pixels",len(getattr(draw,"_handle_pixels",{})))
print("alive", renpy_host.texture_alive(int(t.handle)))

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
