"""Phase 2 leaf residual probe: dead-at-present HostTexture revive.

1. load_texture solid → draw
2. kill_textures (destroys GPU, leaves HostTexture handle dead on surftree)
3. draw same HostTexture without re-load_texture
4. Expect present-path recovery from pixel stash (class b)

Also checks non-transient empty-pad is not permanently cached (class a residual).

Note: host run_file prepends imports, so no __future__ here.
"""
import os
from pathlib import Path

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

import renpy_host
from renpy.pygame.surface import Surface

from renpy.wgpu.draw import WgpuDraw

base = Path(os.environ.get("RENPY_HOST_BASE", "."))
out = base / "host" / "target" / "gate-dead_present_recover_probe.txt"
VW, VH = 320, 180
lines: list[str] = []
ok = True


class R:
    def __init__(self, w, h):
        self.width = w
        self.height = h
        self.children = []
        self.mesh = None
        self.reverse = None
        self.forward = None
        self.cached_texture = None
        self.cached_model = None
        self.loaded = False
        self.blits = None
        self.ndc = None
        self.texture = None
        self.textures = None
        self.shaders = None
        self.uniforms = None
        self.color = None
        self.vertices = None
        self.indices = None
        self.pipeline = None

    def blit(self, c, xo=0, yo=0):
        self.children.append((c, float(xo), float(yo), False, True))


def mean_rgb(rgba, w, h):
    if not rgba or w <= 0 or h <= 0:
        return 0.0
    rs = gs = bs = n = 0
    step = max(1, min(w, h) // 32)
    for y in range(0, h, step):
        for x in range(0, w, step):
            i = (y * w + x) * 4
            if i + 2 >= len(rgba):
                continue
            rs += rgba[i]
            gs += rgba[i + 1]
            bs += rgba[i + 2]
            n += 1
    if n <= 0:
        return 0.0
    return (rs / n + gs / n + bs / n) / 3.0


try:
    draw = WgpuDraw()
    draw.init((VW, VH))

    # --- class b: dead present recovery ---
    s = Surface((64, 64))
    s.fill((200, 40, 40, 255))
    ht = draw.load_texture(s)
    h0 = int(ht.handle)
    lines.append(f"upload handle={h0} stash={h0 in draw._handle_pixels}")
    if h0 <= 0 or h0 not in draw._handle_pixels:
        ok = False
        lines.append("FAIL no pixel stash after load_texture")

    root = R(VW, VH)
    root.blit(ht, 10, 10)
    draw.draw_screen(root, flip=True)
    w, h, rgba = renpy_host.read_game_rt_rgba()
    m0 = mean_rgb(rgba, w, h)
    lines.append(f"before_kill mean={m0:.1f} alive={renpy_host.texture_alive(h0)}")
    if m0 < 20:
        ok = False
        lines.append("FAIL before_kill too dark (draw failed)")

    # Destroy GPU without clearing pixel stash (kill_textures keeps stash).
    draw.kill_textures()
    alive_after = bool(renpy_host.texture_alive(h0))
    lines.append(
        f"after_kill alive={alive_after} stash_keys={len(draw._handle_pixels)} ht.handle={ht.handle}"
    )
    if alive_after:
        ok = False
        lines.append("FAIL handle still alive after kill_textures")
    if h0 not in draw._handle_pixels and int(ht.handle) not in draw._handle_pixels:
        # stash keyed by original handle
        ok = False
        lines.append("FAIL pixel stash cleared on kill (cannot recover)")

    # Draw the same HostTexture object without load_texture — recovery path.
    root2 = R(VW, VH)
    root2.blit(ht, 10, 10)
    draw.draw_screen(root2, flip=True)
    w, h, rgba = renpy_host.read_game_rt_rgba()
    m1 = mean_rgb(rgba, w, h)
    h1 = int(ht.handle)
    alive1 = bool(renpy_host.texture_alive(h1)) if h1 > 0 else False
    lines.append(
        f"after_recover mean={m1:.1f} handle={h0}->{h1} alive={alive1} remap={draw._handle_remap.get(h0)}"
    )
    if m1 < 20:
        ok = False
        lines.append("FAIL after_recover still dark (dead_present residual)")
    else:
        lines.append("PASS class_b dead_present recover")
    if not alive1:
        ok = False
        lines.append("FAIL recovered handle not alive")

    # --- class a residual: empty pad not permanently cached ---
    empty = Surface((32, 32))
    # Force empty pixel buffer path: wipe _pixels if present
    if hasattr(empty, "_pixels"):
        try:
            empty._pixels = b""
        except Exception:
            pass
    # Surface with no fill may still have buffer; create via size and zero length path
    # by using a surface and then load with empty pad simulation.
    # Prefer a real empty: Surface without fill still has zeros of correct size —
    # that is legitimate transparent content, NOT empty-pad. To hit empty-pad we
    # need len(pixels)==0 before pad. Simulate with a duck surface.
    class EmptySurf:
        def get_size(self):
            return (32, 32)

        @property
        def _pixels(self):
            return b""

    ht_empty = draw.load_texture(EmptySurf())  # type: ignore[arg-type]
    key_empty = id(EmptySurf())  # not the same id — check cache by scanning values
    # Re-load same EmptySurf instance
    es = EmptySurf()
    ht_e1 = draw.load_texture(es)  # type: ignore[arg-type]
    in_cache = any(h == ht_e1.handle for _fp, h in draw.texture_cache.values())
    # Because empty_pad_input=True non-transient, must NOT be in texture_cache
    lines.append(
        f"empty_pad handle={ht_e1.handle} in_texture_cache={in_cache} empty_flag_path=ok"
    )
    if in_cache:
        ok = False
        lines.append("FAIL empty-pad permanently cached (class a residual)")
    else:
        lines.append("PASS class_a empty_pad not permanently cached")

    # Content surface still caches normally
    filled = Surface((16, 16))
    filled.fill((10, 200, 10, 255))
    ht_f = draw.load_texture(filled)
    in_cache_f = any(h == ht_f.handle for _fp, h in draw.texture_cache.values())
    lines.append(f"content_fill handle={ht_f.handle} in_texture_cache={in_cache_f}")
    if not in_cache_f:
        ok = False
        lines.append("FAIL content fill not cached")
    else:
        lines.append("PASS content fill still cached")

except Exception as e:
    ok = False
    import traceback

    lines.append(f"EXC {e}")
    lines.append(traceback.format_exc())

lines.append(f"ok={ok}")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
if not ok:
    raise SystemExit(1)

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
