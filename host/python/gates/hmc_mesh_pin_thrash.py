"""Synthetic: many unique mid-frame meshes; early panel mesh must survive present.

Regression for prefs hover flicker: host mesh FIFO eviction mid-frame destroyed
meshes already queued in frame_cmds so encode_pass skipped them after Clear.

Gate name: hmc_mesh_pin_thrash  (RENPY_HOST_GATE=hmc_mesh_pin_thrash)

Also exercises:
  - Python _mesh_cache dead-handle recovery (mesh_alive)
  - host touch_mesh / pin-until-present

Writes: host/target/gate-hmc_mesh_pin_thrash.txt
"""
import array
import os
import sys
import traceback
from pathlib import Path


def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")


def _log(m):
    try:
        sys.__stdout__.write("[mesh_pin] %s\n" % m)
        sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_mesh_pin_thrash.log", "a").write(m + "\n")


def _quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:
        pass


def main():
    open("/tmp/hmc_mesh_pin_thrash.log", "w").write("start\n")
    base = _base()
    out = base / "host" / "target" / "gate-hmc_mesh_pin_thrash.txt"
    lines = []
    try:
        import renpy_host
        from renpy.wgpu.draw import HostTexture, WgpuDraw

        # Probe new bindings exist.
        has_alive = hasattr(renpy_host, "mesh_alive")
        has_touch = hasattr(renpy_host, "touch_mesh")
        has_map = hasattr(renpy_host, "mesh_map_len")
        lines.append(
            "bindings mesh_alive=%s touch_mesh=%s mesh_map_len=%s"
            % (has_alive, has_touch, has_map)
        )
        _log(lines[-1])
        if not (has_alive and has_touch):
            lines.append("ok=False reason=missing_mesh_bindings")
            out.write_text("\n".join(lines) + "\n")
            _quit()
            return

        draw = WgpuDraw()
        draw.virtual_size = (1920, 1080)
        draw.physical_size = (1920, 1080)
        draw.drawable_size = (1920, 1080)
        draw._refresh_scale()
        draw._ensure_pipes()
        # Tiny Python cache so deferred path also engages.
        draw._mesh_cache_cap = 32

        def solid(w, h, rgba):
            r, g, b, a = rgba
            buf = array.array("B", [r, g, b, a] * (w * h))
            hnd = renpy_host.create_texture_rgba(w, h, bytes(buf))
            return HostTexture(hnd, w, h)

        # White panel on the left; thrash quads only on the right.
        bg = solid(400, 300, (246, 246, 247, 255))
        # More thrash than max_meshes pressure is hard without lowering host cap;
        # still create enough unique geometry to exercise cache eviction + pin.
        smalls = [solid(16, 16, (i % 200 + 20, 40, 60, 255)) for i in range(400)]

        class R:
            def __init__(self, w, h, children):
                self.width = w
                self.height = h
                self.children = children
                self.mesh = None
                self.cached_model = None
                self.cached_texture = None

        kids = [(bg, 40.0, 40.0)]
        for i, s in enumerate(smalls):
            kids.append((s, float(1000 + (i % 20) * 20), float(40 + (i // 20) * 20)))
        root = R(1920, 1080, kids)

        pre_map = int(renpy_host.mesh_map_len()) if has_map else -1
        lines.append("pre_draw mesh_map_len=%d" % pre_map)
        _log(lines[-1])

        orig_end = renpy_host.end_frame_present

        def _spy_end():
            n_def = len(getattr(draw, "_mesh_deferred_destroy", []) or [])
            n_cache = len(draw._mesh_cache)
            n_map = int(renpy_host.mesh_map_len()) if has_map else -1
            lines.append(
                "pre_present deferred=%d mesh_cache=%d mesh_map=%d"
                % (n_def, n_cache, n_map)
            )
            _log(lines[-1])
            return orig_end()

        renpy_host.end_frame_present = _spy_end
        try:
            draw.draw_screen(root, flip=True)
        finally:
            renpy_host.end_frame_present = orig_end

        # Second present reuses Python cache — must not hit dead host meshes.
        draw.draw_screen(root, flip=True)

        rw, rh, rt = renpy_host.read_game_rt_rgba()
        # Sample interior of left white panel (40+200, 40+150) = (240, 190)
        x, y = 240, 190
        o = (y * rw + x) * 4
        r, g, b = rt[o], rt[o + 1], rt[o + 2]
        # Arena clear is ~ (13,13,20) from (0.05,0.05,0.08)*255
        arenaish = r < 30 and g < 30 and b < 40
        panel_ok = r > 200 and g > 200 and b > 200 and not arenaish
        lines.append(
            "panel_px=(%d,%d,%d) mesh_cache=%d deferred_after=%d cap=%d"
            % (
                r,
                g,
                b,
                len(draw._mesh_cache),
                len(getattr(draw, "_mesh_deferred_destroy", []) or []),
                draw._mesh_cache_cap,
            )
        )
        _log(lines[-1])

        # Cache hit path: create one known mesh, touch, destroy host side via
        # many creates? We can't lower max_meshes from Python. Instead verify
        # mesh_alive true for a freshly created cached quad after draw.
        # Probe: first mesh in cache should be alive.
        alive_hits = 0
        dead_hits = 0
        for _k, h in list(draw._mesh_cache.items())[:20]:
            if renpy_host.mesh_alive(int(h)):
                alive_hits += 1
            else:
                dead_hits += 1
        lines.append("cache_probe alive=%d dead=%d" % (alive_hits, dead_hits))
        _log(lines[-1])

        ok = panel_ok and dead_hits == 0 and has_alive and has_touch
        lines.append("panel_ok=%s ok=%s" % (panel_ok, ok))
        lines.append("ok=%s" % ok)
        _log(lines[-1])
        out.write_text("\n".join(lines) + "\n")
    except Exception:
        tb = traceback.format_exc()
        lines.append("EXCEPTION\n" + tb)
        lines.append("ok=False")
        try:
            out.write_text("\n".join(lines) + "\n")
        except Exception:
            pass
        _log(tb)
    finally:
        _quit()


main()
