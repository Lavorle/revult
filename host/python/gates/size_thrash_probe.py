import renpy_host
from renpy.wgpu.draw import WgpuDraw

draw = WgpuDraw()
draw.init((1280,720))
lines=[]
for i in range(30):
    renpy_host.pump_once(16)
    w,h = renpy_host.window_size()
    before = draw.physical_size
    rv = draw.update(force=False)
    lines.append(f"i={i} win={w}x{h} phys_before={before} phys_after={draw.physical_size} update={rv} dpv={draw.draw_per_virt:.4f}")
print("\n".join(lines))
# also force path once
rv2 = draw.update(force=True)
print(f"force_true returned {rv2} phys={draw.physical_size}")
renpy_host.request_quit()
