"""Synthetic: many unique quads under low mesh cache cap; early panel must survive present.

Regression for dialog_config black panel: mid-frame destroy_mesh of mesh-cache
evictions left early HostTexture draw cmds with dead mesh ids so encode_pass
skipped them. Deferred destroy after end_frame_present keeps them alive.
"""
import array
import os
import sys
import traceback
from pathlib import Path

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback


def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")


def _log(m):
    try:
        sys.__stdout__.write(f"[mesh_thrash] {m}\n")
        sys.__stdout__.flush()
    except Exception:  # noqa: BLE001, S110
        pass
    open("/tmp/hmc_dc_mesh_thrash.log", "a").write(m + "\n")  # noqa: SIM115


def _quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:  # noqa: BLE001, S110
        pass


def main():
    open("/tmp/hmc_dc_mesh_thrash.log", "w").write("start\n")  # noqa: SIM115
    base = _base()
    out = base / "host" / "target" / "gate-hmc_dc_mesh_thrash.txt"
    lines = []
    try:
        import renpy_host

        from renpy.wgpu.draw import HostTexture, WgpuDraw

        draw = WgpuDraw()
        # init(None) would null virtual_size; set sizes and skip full init.
        draw.virtual_size = (1920, 1080)
        draw.physical_size = (1920, 1080)
        draw.drawable_size = (1920, 1080)
        draw._refresh_scale()
        draw._ensure_pipes()
        # Force tiny cache so eviction happens mid-frame during the walk.
        draw._mesh_cache_cap = 32

        def solid(w, h, rgba):
            r, g, b, a = rgba
            buf = array.array("B", [r, g, b, a] * (w * h))
            hnd = renpy_host.create_texture_rgba(w, h, bytes(buf))
            return HostTexture(hnd, w, h)

        # White panel on the left; thrash quads only on the right so sample stays pure.
        bg = solid(400, 300, (246, 246, 247, 255))
        smalls = [solid(16, 16, (i % 200 + 20, 40, 60, 255)) for i in range(160)]

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
            # Right half only — unique offsets → unique mesh keys.
            kids.append((s, float(1000 + (i % 20) * 20), float(40 + (i // 20) * 20)))
        root = R(1920, 1080, kids)

        # Spy: after draw walk but before flush, deferred should be non-empty if cap hit.
        orig_end = renpy_host.end_frame_present

        def _spy_end():
            n_def = len(getattr(draw, "_mesh_deferred_destroy", []) or [])
            n_cache = len(draw._mesh_cache)
            lines.append("pre_present deferred=%d mesh_cache=%d" % (n_def, n_cache))  # noqa: UP031
            _log(lines[-1])
            return orig_end()

        renpy_host.end_frame_present = _spy_end
        try:
            draw.draw_screen(root, flip=True)
        finally:
            renpy_host.end_frame_present = orig_end

        rw, _rh, rt = renpy_host.read_game_rt_rgba()
        # Sample interior of left white panel (40+200, 40+150) = (240, 190)
        x, y = 240, 190
        o = (y * rw + x) * 4
        r, g, b = rt[o], rt[o + 1], rt[o + 2]
        line = (
            "panel_px=(%d,%d,%d) mesh_cache=%d deferred_after=%d cap=%d"  # noqa: UP031
            % (
                r,
                g,
                b,
                len(draw._mesh_cache),
                len(getattr(draw, "_mesh_deferred_destroy", []) or []),
                draw._mesh_cache_cap,
            )
        )
        lines.append(line)
        _log(line)
        # Pass: panel sample near white, not arena clear (~13,13,20) or thrash RGB.
        ok = r > 200 and g > 200 and b > 200
        lines.append(f"ok={ok}")
        out.write_text("\n".join(lines) + "\n")
        _log(f"wrote {out} ok={ok}")
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        lines.append(tb)
        out.write_text("\n".join(lines) + "\n")
        _log(tb)
    finally:
        _quit()


if __name__ == "__main__":
    main()
else:
    try:
        main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
