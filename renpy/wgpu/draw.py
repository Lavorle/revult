"""
WgpuDraw — host renderer (Phase 2–5, Phase 8 model mesh).

Primary path: begin_frame → draw_model → end_frame_present.
Phase 5: RTT (create_render_texture + begin/end_target), screenshot via
read_game_rt_rgba, dissolve/blur/matrixcolor/mask helpers.
Phase 8: create_mesh upload + draw_model_mesh for assimp/procedural models.
"""

from __future__ import annotations

import os
import sys
import threading
import time as _time
from typing import Any, Optional, Sequence

# --- P0 decomposition: re-exports keep pickle/import compat ---
# HostTexture + fingerprint moved to host_texture.py; debug helpers to
# draw_debug.py (with generic _phase0_due); RTT pool to rtt_pool.py.
from .draw_debug import (  # noqa: F401
    _DRAW_SCREEN_LOCK,
    _draw_screen_lock,
    _HOST_DRAW_FAIL_LOGGED,
    _UI_TRACE_LOGGED,
    _PHASE0_LAST_DISSOLVE_T,
    _PHASE0_LAST_WRITE_T,
    _PHASE0_LAST_FRAME_T,
    _PHASE0_DISSOLVE_INTERVAL,
    _PHASE0_WRITE_INTERVAL,
    _PHASE0_FRAME_INTERVAL,
    _PHASE0_LAST_GENERIC,
    _phase0_signals_enabled,
    _phase0_log,
    _phase0_due,
    _phase0_due_dissolve,
    _phase0_due_write,
    _phase0_due_frame,
    _safe_print,
    _ui_trace_once,
    _host_draw_fail,
)
from .host_texture import HostTexture, _surf_fingerprint  # noqa: F401
from .rtt_pool import RttPoolMixin
from .draw_surftree import SurftreeMixin  # noqa: F401
from .draw_traversal import TraversalMixin  # noqa: F401
from .draw_model import ModelMixin  # noqa: F401
from .draw_walk import WalkMixin  # noqa: F401

# host_bridge single-point import (optional; fallback to direct import)
try:
    from .host_bridge import renpy_host as _host_bridge  # noqa: F401
except Exception:  # pragma: no cover
    _host_bridge = None  # type: ignore


class WgpuDraw(RttPoolMixin, SurftreeMixin, TraversalMixin, ModelMixin, WalkMixin):
    def __init__(self):
        self.info = {
            "renderer": "wgpu",
            "resizable": True,
            "additive": True,
            "models": True,
            "gpu_vendor": "wgpu",
            "gpu_name": "Vulkan",
            "gpu_driver_version": "phase7",
        }
        self.virtual_size = (1280, 720)
        # Stable product layout size; mesh RTT bake may temporarily overwrite virtual_size.
        self.layout_virtual_size = (1280, 720)
        self.physical_size = (1280, 720)
        self.drawable_size = (1280, 720)
        self.drawable_viewport = (0, 0, 1280, 720)
        # Product text/im paths read draw_per_virt (GL2Draw/SWDraw parity).
        self.draw_per_virt = 1.0
        self.virt_to_draw = None
        self.draw_to_virt = None
        self.auto_mipmap = False
        self.full_redraw = False
        self.draw_per_sec = 60.0
        self.fullscreen = False
        self.texture_cache = {}
        # Movie / transient uploads: id(surf) → (handle, w, h, fingerprint).
        # Stock get_movie_texture always passes transient=True on a stable
        # channel Surface; without reuse, each frame allocates a full-size
        # sample texture and arena FIFO eviction kills dock chrome handles.
        self._transient_tex = {}
        # Present-path dead-handle recovery (class b): handle → (w, h, rgba).
        # load_texture already re-uploads on cache miss (~657–664) when callers
        # re-enter load_texture; surftree-held HostTextures after kill_textures
        # / FIFO eviction never re-enter that path. Stash full-texture pixels at
        # upload so _draw_texture_at / resolve can re-create and remap in place.
        # Cap count + skip large Movie frames to bound RAM.
        self._handle_pixels = {}  # handle -> (w, h, pixels)
        # Prefer keeping UI chrome (Solid 10×10 + prefs/confirm panels) under
        # flowchart thrash. 512 dropped mid-size panels before present revive.
        self._handle_pixels_cap = 2048
        self._handle_remap = {}  # dead_handle -> live_handle (subsurface share)
        # Nested product draw depth (reentrancy guard inside the process lock).
        self._draw_screen_depth = 0
        # Pending draw_model args for one product frame; flushed once under
        # renpy_host.draw_models (single host lock) before end_frame_present.
        self._draw_batch = []
        # Size-keyed freelist for offscreen render targets. mesh_bake /
        # render_to_texture re-create full-screen RTTs every frame; without
        # recycle, HuangmeiC splash/dissolve thrash OOMs the X server
        # (thousands of 1920×1080 RTTs in seconds).
        self._rtt_free = {}  # (w, h) -> [handle, ...]
        self._rtt_prev_frame = []  # [(handle, w, h), ...]
        self._rtt_curr_frame = []
        self._rtt_pool_cap = 8  # max free handles per size
        # Geometry-keyed mesh cache. Every textured draw used to call
        # create_mesh for an identical NDC quad; HuangmeiC main-menu trees
        # allocate thousands of mesh buffers per second and OOM the process.
        self._mesh_cache = {}  # key -> mesh handle
        # Dense preferences pages (dialog_config_1/2) walk 700+ unique HostTexture
        # quads in one product present. Cap 512 + mid-frame destroy_mesh killed
        # early layout meshes (1849×846 background) already queued in frame_cmds;
        # encode_pass then skipped them → pure arena-clear panel while image_config
        # (lighter tree) stayed white. Keep headroom for full prefs chrome.
        self._mesh_cache_cap = 4096
        # Handles popped from the Python cache but still referenced by the open
        # product frame_cmds list. Destroy only after end_frame_present so
        # encode_pass can still resolve mesh ids drawn earlier in the walk.
        self._mesh_deferred_destroy = []  # type: list[int]
        # Use white verts for geometry; when alpha/color is dynamic, still
        # cache by coarse alpha (see _mesh_quad_ndc).
        self._solid_pipe = None
        self._tex_pipe = None
        self._dissolve_pipe = None
        self._imagedissolve_pipe = None
        self._blur_pipe = None
        self._matrixcolor_pipe = None
        self._alpha_mask_pipe = None
        self._mask_pipe = None
        self._live2d_mask_pipe = None
        self._live2d_inverted_mask_pipe = None
        self._live2d_colors_pipe = None
        self._live2d_flip_pipe = None
        self._quad_mesh = None
        # GL2-parity axis-aligned clip stack (virtual-pixel absolute coords).
        # None = no clip. Pushed when Render.xclipping/yclipping is set; intersected
        # with parent; empty intersect skips the subtree. Mesh crop (not GPU scissor).
        # v1: axis-aligned only — reverse-transformed clips are residual (see
        # _clip_push_from_node docstring).
        self._clip_rect = None  # type: Optional[tuple[float, float, float, float]]

    def _ensure_pipes(self):
        import renpy_host  # type: ignore

        if self._solid_pipe is None:
            self._solid_pipe = renpy_host.solid_pipeline()
        if self._tex_pipe is None:
            self._tex_pipe = renpy_host.textured_pipeline()
        if self._dissolve_pipe is None:
            self._dissolve_pipe = renpy_host.dissolve_pipeline()
        if self._imagedissolve_pipe is None:
            self._imagedissolve_pipe = renpy_host.imagedissolve_pipeline()
        if self._blur_pipe is None:
            self._blur_pipe = renpy_host.blur_pipeline()
        if self._matrixcolor_pipe is None:
            self._matrixcolor_pipe = renpy_host.matrixcolor_pipeline()
        if self._alpha_mask_pipe is None:
            self._alpha_mask_pipe = renpy_host.alpha_mask_pipeline()
        if self._mask_pipe is None:
            self._mask_pipe = renpy_host.mask_pipeline()
        if self._live2d_mask_pipe is None:
            self._live2d_mask_pipe = renpy_host.live2d_mask_pipeline()
        if self._live2d_inverted_mask_pipe is None:
            self._live2d_inverted_mask_pipe = renpy_host.live2d_inverted_mask_pipeline()
        if self._live2d_colors_pipe is None:
            self._live2d_colors_pipe = renpy_host.live2d_colors_pipeline()
        if self._live2d_flip_pipe is None:
            self._live2d_flip_pipe = renpy_host.live2d_flip_pipeline()
        # Unit-quad singleton used by dissolve/blur/matrixcolor/Live2D paths.
        # Same residual class as geometry-keyed cache: host FIFO can kill it if
        # it ages out of mesh_order; recreate when mesh_alive says dead.
        need_quad = self._quad_mesh is None
        if not need_quad:
            try:
                probe = getattr(renpy_host, "mesh_alive", None)
                if probe is not None and not bool(probe(int(self._quad_mesh))):
                    need_quad = True
                    self._quad_mesh = None
            except Exception:
                pass
        if need_quad:
            # Unit quad NDC
            verts = [
                -1.0,
                -1.0,
                0.0,
                1.0,
                1,
                1,
                1,
                1,
                1.0,
                -1.0,
                1.0,
                1.0,
                1,
                1,
                1,
                1,
                1.0,
                1.0,
                1.0,
                0.0,
                1,
                1,
                1,
                1,
                -1.0,
                1.0,
                0.0,
                0.0,
                1,
                1,
                1,
                1,
            ]
            self._quad_mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
        else:
            try:
                touch = getattr(renpy_host, "touch_mesh", None)
                if touch is not None:
                    touch(int(self._quad_mesh))
            except Exception:
                pass


    def _refresh_scale(self):
        """Keep draw_per_virt / virt↔draw matrices in sync with window size."""
        vw = max(1, int(self.virtual_size[0]))
        _ = max(1, int(self.virtual_size[1]))
        dw = max(1, int(self.drawable_size[0]))
        dh = max(1, int(self.drawable_size[1]))
        self.drawable_viewport = (0, 0, dw, dh)
        self.draw_per_virt = float(dw) / float(vw)
        try:
            import renpy.display.render as render

            self.virt_to_draw = render.Matrix2D(self.draw_per_virt, 0, 0, self.draw_per_virt)
            self.draw_to_virt = render.Matrix2D(1.0 / self.draw_per_virt, 0, 0, 1.0 / self.draw_per_virt)
        except Exception:
            self.virt_to_draw = None
            self.draw_to_virt = None
        self.auto_mipmap = self.draw_per_virt < 0.75

    def update(self, force=False):
        """Detect physical size changes; kill textures and signal redraw (GL2 parity).

        core.interact uses a True return to set needs_redraw so the next
        draw_screen re-rasters bitmaps at the new ``draw_per_virt`` instead of
        soft-stretching 1× textures after maximize.

        Important: only call ``interface.before_resize()`` on a **real** size
        change. That path sets ``restart_interaction`` and would thrash the
        main menu if we invoked it for every ``force=display_reset`` with an
        unchanged window (cannot process Start/Prefs clicks).
        """
        size_changed = False
        fs_changed = False
        try:
            import renpy_host  # type: ignore

            w, h = renpy_host.window_size()
            new_size = (int(w), int(h))
            if new_size != tuple(int(x) for x in self.physical_size):
                size_changed = True
                self.physical_size = new_size
                self.drawable_size = new_size
                self._refresh_scale()
            elif force:
                # display_reset / explicit resize with same chrome: keep scale
                # numbers in sync but do not restart the interaction.
                self._refresh_scale()

            # Track live fullscreen so preferences.fullscreen can resync after
            # user/WM toggles (Alt+Enter / compositor) without a stuck flag.
            if hasattr(renpy_host, "is_fullscreen"):
                try:
                    live_fs = bool(renpy_host.is_fullscreen())
                    if live_fs != bool(self.fullscreen):
                        fs_changed = True
                        self.fullscreen = live_fs
                        try:
                            import renpy  # type: ignore

                            iface = getattr(renpy.display, "interface", None)
                            if iface is not None:
                                iface.fullscreen = live_fs
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            if force:
                self._refresh_scale()

        if not size_changed and not force and not fs_changed:
            return False

        if size_changed:
            # GL2 parity for maximize/resize: kill textures + restart interaction.
            try:
                import renpy

                iface = getattr(renpy.display, "interface", None)
                if iface is not None and hasattr(iface, "before_resize"):
                    iface.before_resize()
                else:
                    self.kill_textures()
            except Exception:
                self.kill_textures()
        elif force:
            # Force without size change: soft redraw only.
            # Do **not** kill_textures here — that cleared im.cache + destroyed
            # every sample handle while the product surftree / image cache still
            # held HostTexture ids. Under flowchart thrash + display_reset force
            # the next confirm present then drew dead handles (encode_pass skips
            # missing textures → pure arena clear). GL2 does not mass-destroy on
            # every force redraw; host matches that contract.
            pass

        return True

    def init(self, virtual_size):
        self.virtual_size = virtual_size
        self.layout_virtual_size = virtual_size
        # AC2: env-gated once-log so HuangmeiC playtest can confirm virtual is
        # product 1920×1080 after init (constructor default is 1280×720 only).
        # Do NOT hardcode constructor default for all games.
        try:
            if os.environ.get("RENPY_HOST_ASSERT_VIRTUAL", "").strip() in (
                "1",
                "true",
                "yes",
            ):
                import sys

                vw = int(virtual_size[0]) if virtual_size else 0
                vh = int(virtual_size[1]) if virtual_size else 0
                print(
                    f"AC2_VIRTUAL virtual_size=({vw}, {vh})",
                    file=sys.stderr,
                    flush=True,
                )
                if (vw, vh) != (1920, 1080):
                    print(
                        f"AC2_WARN virtual_size=({vw}, {vh}) expected=(1920, 1080) "
                        f"for HuangmeiC full-bleed",
                        file=sys.stderr,
                        flush=True,
                    )
        except Exception:
            pass
        # Host thrash (flowchart mesh / Movie) can exceed the stock image-cache
        # budget and kill const_size confirm/preferences panels while the screen
        # tree still expects them. Raise the MB budget early so im.cache.init
        # (called from bootstrap after draw init) sees a larger default. Games
        # may still override via config.image_cache_size_mb.
        try:
            import renpy

            cfg = getattr(renpy, "config", None)
            if cfg is not None:
                cur = getattr(cfg, "image_cache_size_mb", None)
                if cur is None or (isinstance(cur, (int, float)) and float(cur) < 512):
                    cfg.image_cache_size_mb = 512
        except Exception:
            pass
        try:
            import renpy_host  # type: ignore

            renpy_host.set_window_title("renpy-host / WgpuDraw")
            w, h = renpy_host.window_size()
            self.physical_size = (w, h)
            self.drawable_size = (w, h)
            self._refresh_scale()
            self._ensure_pipes()
            renpy_host.request_redraw()
        except Exception:
            self._refresh_scale()
            return False
        return True

    def quit(self):
        self.kill_textures()

    def resize(self):
        """Apply preferences.fullscreen / physical_size (GL2 GLDraw.resize parity).

        core.interact calls this when ``preferences.fullscreen != interface.fullscreen``
        (windowed ↔ fullscreen toggle from product image_config). Without host-side
        set_fullscreen / request_window_size, every form of size change is a no-op.

        Contract (mirrors gl2draw.pyx:643–681):
          1. Read ``preferences.fullscreen`` and ``preferences.physical_size``.
          2. Request borderless fullscreen or restore windowed size via renpy_host.
          3. Publish ``interface.fullscreen`` so the next interact does not re-enter.
          4. ``update(force=True)`` refreshes draw_per_virt when chrome already matches.
        """
        try:
            import renpy  # type: ignore
            import renpy_host  # type: ignore

            prefs = renpy.game.preferences
            want_fs = bool(getattr(prefs, "fullscreen", False))

            # Physical target for windowed mode.
            ps = getattr(prefs, "physical_size", None)
            if ps and len(ps) >= 2 and ps[0] and ps[1]:
                width = max(256, int(ps[0]))
                height = max(256, int(ps[1]))
            else:
                vw, vh = self.virtual_size
                width = max(256, int(vw or 1280))
                height = max(256, int(vh or 720))

            # Cap to a sane max (desktop-ish) when host exposes no monitor query.
            width = min(width, 7680)
            height = min(height, 4320)

            if want_fs:
                if hasattr(renpy_host, "set_fullscreen"):
                    renpy_host.set_fullscreen(True)
            else:
                # Leave fullscreen first so request_inner_size can apply.
                if hasattr(renpy_host, "set_fullscreen"):
                    renpy_host.set_fullscreen(False)
                if hasattr(renpy_host, "request_window_size"):
                    renpy_host.request_window_size(int(width), int(height))

            # Publish so interact does not loop on preferences.fullscreen != self.
            self.fullscreen = want_fs
            try:
                iface = getattr(renpy.display, "interface", None)
                if iface is not None:
                    iface.fullscreen = want_fs
            except Exception:
                pass

            # Keep preferences.fullscreen honest if host rejected (optional).
            try:
                if hasattr(renpy_host, "is_fullscreen"):
                    live = bool(renpy_host.is_fullscreen())
                    # Only trust live after a settle; do not force-clear want_fs
                    # immediately (Wayland may apply asynchronously).
                    self.fullscreen = live if live == want_fs else want_fs
            except Exception:
                pass

        except Exception as e:
            try:
                _host_draw_fail("resize", e)
            except Exception:
                pass

        return self.update(force=True)

    def can_block(self):
        return True

    def should_redraw(self, needs_redraw, first_pass, can_block):
        return needs_redraw or first_pass

    def mutated_surface(self, surf):
        """Invalidate the long-lived texture_cache entry for this surface.

        IMPORTANT: do **not** destroy the GPU handle here.

        Product image cache (``renpy.display.im``) often does:

            ce.texture = draw.load_texture(surf)
            # when config.cache_surfaces is False:
            draw.mutated_surface(ce.surf)
            ce.surf = None

        The returned ``HostTexture`` still references the uploaded handle. If we
        destroy it here, every subsequent draw samples a missing texture and the
        game RT stays at arena clear (the permanent ``tq_main_menu_frame``
        ``arena_rt_clear`` failure). GL2 keeps the GL texture alive via the
        Texture object; host HostTexture is the equivalent live reference.

        Cache pop forces the next non-transient ``load_texture`` of this surface
        id to re-upload. GPU reclaim is via ``kill_textures`` / fingerprint
        mismatch re-upload.

        Movie transient path uses ``_transient_tex`` (not this map).

        Do **not** force-dirty the transient fingerprint here: stock
        ``get_movie_texture`` calls ``mutated_surface`` every ready frame, but
        Phase-1 1b keeps the same decode-size pixels until ``frame_index``
        advances. Fingerprint / ``_host_frame_idx`` in ``load_texture`` already
        rewrite on real content change; dirtying every call forced an 8 MiB
        ``write_texture_rgba`` thrash (~1 FPS class).
        """
        key = id(surf)
        self.texture_cache.pop(key, None)
        return None

    def _stash_handle_pixels(self, handle, w, h, pixels, *, transient=False):
        """Remember full-texture RGBA for present-path dead-handle recovery.

        Does **not** replace load_texture dead-handle cache re-upload (~657–664);
        that path still runs when callers re-enter load_texture. This stash is for
        HostTexture leaves still held on the surftree after kill_textures / FIFO
        eviction that never re-enter load_texture (class b dead_present).

        Skip Movie-sized transient frames (rewrite every present) and 1×1 stubs.
        Cap entry count; prefer dropping large non-chrome entries first so
        Solid 10×10 / panel chrome survives flowchart thrash (confirm_alone_2).
        """
        try:
            handle = int(handle or 0)
        except Exception:
            return
        if handle <= 0:
            return
        try:
            w, h = int(w), int(h)
        except Exception:
            return
        if w <= 0 or h <= 0:
            return
        # Movie full-frame thrash: rewrite path already keeps the live handle.
        if transient and (w * h) >= (1280 * 720):
            return
        # Tiny placeholders rarely need revive; Solid 10×10 still kept (UI chrome).
        if (w * h) <= 1:
            return
        need = w * h * 4
        if not isinstance(pixels, (bytes, bytearray)) or len(pixels) < need:
            return
        # Bound RAM: drop preferred victims when over cap.
        store = getattr(self, "_handle_pixels", None)
        if store is None:
            self._handle_pixels = {}
            store = self._handle_pixels
        cap = int(getattr(self, "_handle_pixels_cap", 2048) or 2048)
        if len(store) >= cap and handle not in store:
            self._evict_handle_pixels(store, cap)
        store[handle] = (w, h, bytes(pixels[:need]))

    def _evict_handle_pixels(self, store, cap):
        """Drop stash entries under thrash, pinning UI chrome panels.

        Prefer drop order: very large Movie-sized textures first, then mid-size
        non-chrome, never drop small Solid chrome or prefs full-bleed panels
        (up to ~2k×1k) until nothing else remains.

        Previous pin_px=1920*400 treated 1849×846 preferences background as
        "non-pinned" and dropped it first under thrash — present-path revive
        then failed → prefs hover flicker residual (black panel holes).
        """
        if not store:
            return
        drop_n = max(1, int(cap) // 8)
        # Keep Solid 10×10 + prefs/confirm full-bleed panels (~1849×846).
        # Only force-drop textures larger than a full HD frame (Movie thrash).
        pin_px = 1920 * 1080  # keep panels ≤ full HD; drop > HD first
        chrome_px = 256 * 256  # always try to keep tiny Solid / icon chrome

        def _area(ent):
            try:
                return int(ent[0]) * int(ent[1])
            except Exception:
                return 0

        # Candidates: oversize (Movie / full-bleed thrash) first, largest first.
        items = list(store.items())
        oversize = [(k, _area(v)) for k, v in items if _area(v) > pin_px]
        oversize.sort(key=lambda kv: kv[1], reverse=True)
        dropped = 0
        for k, _a in oversize:
            if dropped >= drop_n:
                break
            store.pop(k, None)
            dropped += 1
        if dropped >= drop_n:
            return
        # Still need room: drop mid-size non-chrome (not tiny Solid).
        mid = [(k, _area(v)) for k, v in store.items() if _area(v) > chrome_px]
        mid.sort(key=lambda kv: kv[1], reverse=True)
        for k, _a in mid:
            if dropped >= drop_n:
                break
            store.pop(k, None)
            dropped += 1
        if dropped >= drop_n:
            return
        # Last resort: drop oldest arbitrary (including small chrome).
        for k in list(store.keys()):
            if dropped >= drop_n:
                break
            if k in store:
                store.pop(k, None)
                dropped += 1

    def _forget_handle_pixels(self, handle):
        """Drop pixel stash + remap for a destroyed handle."""
        try:
            handle = int(handle or 0)
        except Exception:
            return
        if handle <= 0:
            return
        store = getattr(self, "_handle_pixels", None)
        if store is not None:
            store.pop(handle, None)
        remap = getattr(self, "_handle_remap", None)
        if remap is not None:
            remap.pop(handle, None)
            # Also drop reverse mappings that pointed at this handle.
            dead = [k for k, v in remap.items() if int(v) == handle]
            for k in dead:
                remap.pop(k, None)

    def _ensure_host_texture_alive(self, ht):
        """Revive a dead HostTexture handle in place from pixel stash (class b).

        Returns the (possibly remapped) HostTexture, or the original when alive /
        unrecoverable. Mutates ``ht.handle`` so subsurface siblings that share the
        same Python object identity see the new id; also records ``_handle_remap``
        so other HostTexture instances that still hold the dead id can follow.

        Does not re-implement load_texture cache-miss re-upload.
        """
        if ht is None:
            return None
        try:
            old = int(getattr(ht, "handle", 0) or 0)
        except Exception:
            return ht
        if old <= 0:
            return ht
        # Follow prior remap chain (subsurface siblings / multi-draw same frame).
        remap = getattr(self, "_handle_remap", None)
        if remap is None:
            self._handle_remap = {}
            remap = self._handle_remap
        seen = set()
        cur = old
        while cur in remap and cur not in seen:
            seen.add(cur)
            try:
                cur = int(remap[cur])
            except Exception:
                break
        if cur != old and cur > 0:
            try:
                import renpy_host  # type: ignore

                alive_mapped = True
                if hasattr(renpy_host, "texture_alive"):
                    alive_mapped = bool(renpy_host.texture_alive(int(cur)))
                if alive_mapped:
                    ht.handle = int(cur)
                    ht.texture = int(cur)
                    return ht
            except Exception:
                pass

        try:
            import renpy_host  # type: ignore

            alive = True
            if hasattr(renpy_host, "texture_alive"):
                alive = bool(renpy_host.texture_alive(int(cur if cur > 0 else old)))
            if alive:
                # Keep LRU hot for chrome still on the surftree.
                if hasattr(renpy_host, "touch_texture"):
                    try:
                        renpy_host.touch_texture(int(ht.handle))
                    except Exception:
                        pass
                return ht

            # Dead at present — try pixel-stash re-create.
            store = getattr(self, "_handle_pixels", None) or {}
            ent = store.get(old) or store.get(cur)
            # Also follow reverse remap: if some other dead id remapped here earlier
            # and stash only lives under a sibling key, scan remap values (cheap).
            if ent is None and remap:
                for k, v in list(remap.items()):
                    try:
                        if int(v) in (old, cur) and k in store:
                            ent = store.get(k)
                            break
                    except Exception:
                        continue
            if ent is None:
                # Last-chance: re-upload from long-lived texture_cache surfaces /
                # image cache entries that still point at this dead handle. Prefer
                # fingerprint-keyed texture_cache (Surface id) over scanning im.
                ent = self._recover_pixels_for_dead_handle(old, cur, ht)
            if ent is None:
                # Once-log class (b) residual when we cannot revive.
                if (
                    os.environ.get("RENPY_HOST_UI_TRACE") == "1"
                    and "dead_present" not in _UI_TRACE_LOGGED
                ):
                    _ui_trace_once(
                        "dead_present",
                        f"handle={old} alive=0 size=({getattr(ht, 'w', '?')},"
                        f"{getattr(ht, 'h', '?')}) recover=no_pixels",
                    )
                return ht
            sw, sh, pixels = ent
            try:
                new_h = int(renpy_host.create_texture_rgba(int(sw), int(sh), pixels))
            except Exception as e:
                if (
                    os.environ.get("RENPY_HOST_UI_TRACE") == "1"
                    and "dead_present" not in _UI_TRACE_LOGGED
                ):
                    _ui_trace_once(
                        "dead_present",
                        f"handle={old} alive=0 recover=create_fail "
                        f"err={type(e).__name__}:{e}",
                    )
                return ht
            if new_h <= 0:
                return ht
            # Remap dead → live; move pixel stash to the new handle.
            remap[old] = int(new_h)
            if cur != old:
                remap[cur] = int(new_h)
            store.pop(old, None)
            store.pop(cur, None)
            store[int(new_h)] = (int(sw), int(sh), pixels)
            # Patch texture_cache entries that still point at the dead handle so
            # next load_texture hit returns the live id without re-upload thrash.
            try:
                for k, (fp, h, tw, th) in list(self.texture_cache.items()):
                    try:
                        if int(h) in (old, cur):
                            self.texture_cache[k] = (fp, int(new_h), tw, th)
                    except Exception:
                        continue
            except Exception:
                pass
            # Patch im.cache HostTexture objects in place (same Python identity
            # as surftree leaves when ce.texture was blitted).
            try:
                import renpy.display.im as im  # type: ignore

                cache = getattr(im, "cache", None)
                if cache is not None:
                    try:
                        _lock = getattr(cache, "lock", None)
                        it = list(getattr(cache, "cache", {}).values())
                    except Exception:
                        it = []
                    for ce in it:
                        tex = getattr(ce, "texture", None)
                        if isinstance(tex, HostTexture):
                            try:
                                if int(getattr(tex, "handle", 0) or 0) in (old, cur):
                                    tex.handle = int(new_h)
                                    tex.texture = int(new_h)
                            except Exception:
                                pass
                        elif isinstance(tex, int) and not isinstance(tex, bool):
                            try:
                                if int(tex) in (old, cur):
                                    ce.texture = HostTexture(
                                        int(new_h),
                                        int(getattr(ht, "width", sw) or sw),
                                        int(getattr(ht, "height", sh) or sh),
                                    )
                            except Exception:
                                pass
            except Exception:
                pass
            ht.handle = int(new_h)
            ht.texture = int(new_h)
            if hasattr(renpy_host, "touch_texture"):
                try:
                    renpy_host.touch_texture(int(new_h))
                except Exception:
                    pass
            # Soft once-log that recovery ran (still uses dead_present key once).
            if (
                os.environ.get("RENPY_HOST_UI_TRACE") == "1"
                and "dead_present" not in _UI_TRACE_LOGGED
            ):
                _ui_trace_once(
                    "dead_present",
                    f"handle={old} alive=0 size=({getattr(ht, 'w', '?')},"
                    f"{getattr(ht, 'h', '?')}) recover=ok new_handle={int(new_h)}",
                )
            return ht
        except Exception as e:
            if (
                os.environ.get("RENPY_HOST_UI_TRACE") == "1"
                and "dead_present" not in _UI_TRACE_LOGGED
            ):
                _ui_trace_once(
                    "dead_present",
                    f"texture_alive probe fail handle={old} err={type(e).__name__}:{e}",
                )
            return ht

    def _recover_pixels_for_dead_handle(self, old, cur, ht):
        """Best-effort pixel source for a dead handle with empty stash.

        1. ``texture_cache`` reverse lookup is not possible (values are handles
           only) — instead scan ``im.cache`` CacheEntry.texture HostTextures that
           share the dead handle and re-upload from ``ce.surf`` when present.
        2. If the HostTexture has matching ``w/h`` and we find a surface of that
           size in im.cache with dead handle, re-upload it.

        Returns ``(w, h, pixels)`` or None. Does not create GPU textures.
        """
        try:
            import renpy.display.im as im  # type: ignore
        except Exception:
            return None
        cache = getattr(im, "cache", None)
        if cache is None:
            return None
        entries = None
        try:
            lock = getattr(cache, "lock", None)
            if lock is not None:
                with lock:
                    entries = list(getattr(cache, "cache", {}).values())
            else:
                entries = list(getattr(cache, "cache", {}).values())
        except Exception:
            try:
                entries = list(getattr(cache, "cache", {}).values())
            except Exception:
                return None
        want = set()
        try:
            want.add(int(old))
            want.add(int(cur))
        except Exception:
            pass
        for ce in entries:
            try:
                tex = getattr(ce, "texture", None)
                th = None
                if isinstance(tex, HostTexture):
                    th = int(getattr(tex, "handle", 0) or 0)
                elif isinstance(tex, int) and not isinstance(tex, bool):
                    th = int(tex)
                if th is None or th not in want:
                    continue
                surf = getattr(ce, "surf", None)
                # cache_surfaces=False: surfaces are dropped after GPU upload.
                # Re-load from ImageBase when still registered on the entry.
                if surf is None:
                    what = getattr(ce, "what", None)
                    if what is not None and hasattr(what, "load"):
                        try:
                            surf = what.load()
                        except Exception:
                            surf = None
                if surf is None:
                    continue
                try:
                    sw, sh = surf.get_size()
                    sw, sh = int(sw), int(sh)
                except Exception:
                    continue
                if sw <= 0 or sh <= 0:
                    continue
                try:
                    bounds = getattr(ce, "bounds", None)
                    if (
                        bounds is not None
                        and len(bounds) >= 4
                        and tuple(bounds) != (0, 0, sw, sh)
                        and hasattr(surf, "subsurface")
                    ):
                        sub = surf.subsurface(tuple(bounds[:4]))
                        if sub is not None:
                            surf = sub
                            sw, sh = surf.get_size()
                            sw, sh = int(sw), int(sh)
                except Exception:
                    pass
                pixels = getattr(surf, "_pixels", None)
                if pixels is None and hasattr(surf, "get_buffer"):
                    try:
                        pixels = bytes(surf.get_buffer())
                    except Exception:
                        pixels = None
                if not isinstance(pixels, (bytes, bytearray)):
                    continue
                need = sw * sh * 4
                if len(pixels) < need:
                    continue
                return (sw, sh, bytes(pixels[:need]))
            except Exception:
                continue
        return None

    def load_texture(self, surf, transient=False, properties=None):
        """Upload RGBA surface to GpuArena; return HostTexture (handle + size).

        Cache is keyed by id(surf) but guarded by a cheap pixel fingerprint so
        in-place mutations and id reuse cannot serve the wrong GPU texture.

        ``transient=True`` (stock Movie ``get_movie_texture`` every frame) uses
        a separate ``_transient_tex`` map and **rewrites** the existing GPU
        handle via ``write_texture_rgba`` when the channel Surface is stable.
        Without that reuse, each present allocated a full 1920×1080 sample
        texture; arena FIFO eviction then destroyed dock chrome handles while
        the surftree still held dead HostTexture ids — product RT stayed at
        arena clear with draw_model silently skipping missing textures
        (AC-Idle1 inverted residual).

        `properties` is accepted for GL2Draw/SWDraw call-site parity (mipmap etc);
        host MVP ignores it. Product text paths require ``.subsurface``.

        On failure returns a typed transparent HostTexture placeholder so
        HostTexture-typed walkers never see a raw Surface.
        """
        try:
            import renpy_host  # type: ignore

            w, h = surf.get_size()
            w, h = max(1, int(w)), max(1, int(h))
            need = w * h * 4
            key = id(surf)

            # Phase-1 thrash kill: stock get_movie_texture calls load_texture every
            # frame with a stable frame_surf. When read_video stamped the same
            # ``_host_frame_idx`` and the transient GPU handle is alive, skip the
            # 8 MiB bytes() + fingerprint + write_texture entirely.
            if transient:
                frame_idx = getattr(surf, "_host_frame_idx", None)
                tent = self._transient_tex.get(key)
                if (
                    frame_idx is not None
                    and tent is not None
                    and len(tent) >= 4
                ):
                    thandle, tw, th, tfp = tent[0], tent[1], tent[2], tent[3]
                    last_idx = tent[4] if len(tent) >= 5 else None
                    if (
                        tw == w
                        and th == h
                        and thandle
                        and last_idx is not None
                        and int(last_idx) == int(frame_idx)
                    ):
                        alive = True
                        if hasattr(renpy_host, "texture_alive"):
                            try:
                                alive = bool(renpy_host.texture_alive(int(thandle)))
                            except Exception:
                                alive = True
                        if alive:
                            if hasattr(renpy_host, "touch_texture"):
                                try:
                                    renpy_host.touch_texture(int(thandle))
                                except Exception:
                                    pass
                            return HostTexture(thandle, w, h)

            if hasattr(surf, "_pixels"):
                raw = surf._pixels
                pixels = bytes(raw) if not isinstance(raw, (bytes, bytearray)) else bytes(raw)
            else:
                try:
                    pixels = bytes(surf.get_buffer())
                except Exception:
                    pixels = b""
            # Class (a): empty-pad residual — input had zero bytes before pad.
            # Non-transient empty pad must not permanently cache a blank HT.
            empty_pad_input = False
            if len(pixels) < need:
                # Pad / refuse empty uploads that would panic wgpu write_texture.
                if len(pixels) == 0:
                    empty_pad_input = True
                    pixels = bytes(need)  # transparent black
                else:
                    pixels = pixels + bytes(need - len(pixels))
            elif len(pixels) > need:
                pixels = pixels[:need]
            # Stash for long-lived path (cleared after use each call).
            self._load_empty_pad_input = empty_pad_input and (not transient)
            fp = _surf_fingerprint(pixels, w, h)
            # key already set above as id(surf)

            # Class (a) blank successful upload candidate: all-zero alpha or empty pad.
            # Ignore 1×1 / tiny placeholders (Movie/Solid stubs) so text-sized
            # blanks still get the once-log slot for the evidence matrix.
            if (
                os.environ.get("RENPY_HOST_UI_TRACE") == "1"
                and "empty_upload" not in _UI_TRACE_LOGGED
                and (w * h) > 4
            ):
                try:
                    # Sample alpha channel (every 4th byte starting at 3).
                    any_a = False
                    for i in range(3, len(pixels), 4):
                        if pixels[i]:
                            any_a = True
                            break
                    if not any_a:
                        # Defer final log until we know handle; stash flag on self.
                        self._ui_trace_blank_pixels = (w, h, bool(empty_pad_input))
                except Exception:
                    pass

            def _alive(handle):
                """True when arena still owns this sample handle (or API missing)."""
                if not handle:
                    return False
                if not hasattr(renpy_host, "texture_alive"):
                    return True
                try:
                    return bool(renpy_host.texture_alive(int(handle)))
                except Exception:
                    return True

            def _trace_empty_upload(handle, tag):
                """Once-log empty_upload class (a) blank success or (c) handle-0."""
                blank = getattr(self, "_ui_trace_blank_pixels", None)
                if blank is None and int(handle or 0) != 0:
                    return
                if int(handle or 0) == 0:
                    _ui_trace_once(
                        "empty_upload",
                        f"class=c handle=0 tag={tag} size=({w},{h}) pixels_len={len(pixels)} need={need}",
                    )
                    return
                if blank is not None:
                    bw, bh, was_empty = blank
                    _ui_trace_once(
                        "empty_upload",
                        f"class=a blank_success handle={int(handle)} tag={tag} "
                        f"size=({bw},{bh}) all_alpha_zero=1 was_empty_pad={int(bool(was_empty))}",
                    )
                    try:
                        del self._ui_trace_blank_pixels
                    except Exception:
                        self._ui_trace_blank_pixels = None

            def _touch(handle):
                if not handle or not hasattr(renpy_host, "touch_texture"):
                    return
                try:
                    renpy_host.touch_texture(int(handle))
                except Exception:
                    pass

            # --- Movie / transient path: reuse one GPU handle per surface id ---
            if transient:
                # AC2: log Movie-sized (or known half-bleed) transient sizes once
                # each. Early 1×1 placeholders are not Movie — ignore those.
                if os.environ.get("RENPY_HOST_MOVIE_ASSERT", "").strip() in (
                    "1",
                    "true",
                    "yes",
                ):
                    logged = getattr(self, "_ac2_tex_sizes_logged", None)
                    if logged is None:
                        logged = set()
                        self._ac2_tex_sizes_logged = logged
                    # Log layout Movie size and known half-bleed residuals only.
                    # Larger full images (e.g. 3840×2160 splash under reverse) are
                    # not Movie leaves — do not AC2_WARN them.
                    # Present 1b intentionally uses decode-size (960×540) HostTexture
                    # with Movie.render mesh-scale to layout — not a half-bleed bug.
                    present_1b = os.environ.get(
                        "RENPY_HOST_MOVIE_PRESENT", "1b"
                    ).strip().lower() not in ("1a", "layout", "s1")
                    half_bleed = (w, h) in ((960, 540), (1280, 720))
                    layout_ok = (w, h) == (1920, 1080)
                    if (layout_ok or half_bleed) and (w, h) not in logged:
                        logged.add((w, h))
                        try:
                            import sys

                            print(
                                f"AC2_HOSTTEX transient size=({w}, {h})"
                                f"{' present_1b=1' if (half_bleed and present_1b) else ''}",
                                file=sys.stderr,
                                flush=True,
                            )
                            if half_bleed and not present_1b:
                                print(
                                    f"AC2_WARN HostTexture size=({w}, {h}) "
                                    f"expected layout (1920, 1080) — half-bleed risk",
                                    file=sys.stderr,
                                    flush=True,
                                )
                        except Exception:
                            pass
                tent = self._transient_tex.get(key)
                if tent is not None:
                    # (handle, w, h, fp[, frame_idx]) — frame_idx optional (Phase 1).
                    thandle, tw, th, tfp = tent[0], tent[1], tent[2], tent[3]
                    if tw == w and th == h and thandle and _alive(thandle):
                        if tfp is not None and tfp == fp:
                            _touch(thandle)
                            return HostTexture(thandle, w, h)
                        # Dirty or new frame pixels: rewrite in place.
                        wrote = False
                        wt_ms = 0.0
                        if hasattr(renpy_host, "write_texture_rgba"):
                            try:
                                _wt0 = _time.monotonic() if _phase0_signals_enabled() else None
                                renpy_host.write_texture_rgba(int(thandle), pixels)
                                if _wt0 is not None:
                                    wt_ms = (_time.monotonic() - _wt0) * 1000.0
                                wrote = True
                            except Exception:
                                wrote = False
                        if wrote:
                            if _phase0_due_write():
                                _phase0_log(
                                    f"write_texture_ms={wt_ms:.3f} size={w}x{h} "
                                    f"path=transient_rewrite handle={int(thandle)}"
                                )
                            frame_idx = getattr(surf, "_host_frame_idx", None)
                            self._transient_tex[key] = (
                                thandle, w, h, fp, frame_idx
                            )
                            # Movie-sized transient skip inside _stash_handle_pixels.
                            self._stash_handle_pixels(
                                thandle, w, h, pixels, transient=True
                            )
                            return HostTexture(thandle, w, h)
                        # write failed (e.g. handle already evicted): re-create.
                        try:
                            renpy_host.destroy_texture(thandle)
                        except Exception:
                            pass
                        self._forget_handle_pixels(thandle)
                    elif thandle:
                        # Size changed or dead handle: drop old entry.
                        try:
                            renpy_host.destroy_texture(thandle)
                        except Exception:
                            pass
                        self._forget_handle_pixels(thandle)
                    self._transient_tex.pop(key, None)
                handle = renpy_host.create_texture_rgba(w, h, pixels)
                frame_idx = getattr(surf, "_host_frame_idx", None)
                self._transient_tex[key] = (handle, w, h, fp, frame_idx)
                # Cap transient map so a flood of one-shot surfaces cannot grow
                # unbounded (Movie is typically 1–2 entries; keep headroom).
                if len(self._transient_tex) > 64:
                    # Drop oldest arbitrary keys (insertion order on CPython 3.7+).
                    for old_k in list(self._transient_tex.keys())[
                        : max(0, len(self._transient_tex) - 32)
                    ]:
                        old = self._transient_tex.pop(old_k, None)
                        if old is not None:
                            try:
                                renpy_host.destroy_texture(int(old[0]))
                            except Exception:
                                pass
                            try:
                                self._forget_handle_pixels(int(old[0]))
                            except Exception:
                                pass
                self._stash_handle_pixels(handle, w, h, pixels, transient=True)
                _trace_empty_upload(handle, "transient_create")
                return HostTexture(handle, w, h)

            # --- Long-lived image-cache path ---
            # Class (a) residual: empty-pad into permanent blank HT. Movie uses
            # transient=True (allowed transparent placeholder). Non-transient
            # empty pad must not stick in texture_cache so a later content fill
            # of the same surface id re-enters create (text/icon residual).
            empty_input = bool(getattr(self, "_load_empty_pad_input", False))
            # Clear per-call flag so a nested load cannot inherit it.
            self._load_empty_pad_input = False

            if key in self.texture_cache:
                old_fp, handle, old_w, old_h = self.texture_cache[key]
                # FIFO thrash may have destroyed the GPU handle while this map
                # still points at it. Treat dead handles as cache miss so the
                # next prepare re-uploads (otherwise HostTexture leaves stay
                # dead forever and product RT stays arena clear).
                # NOTE: this is the existing dead-handle cache re-upload path —
                # do not duplicate it at present; present recovery is separate.
                if not _alive(handle):
                    # Leave pixel stash for present-path revive of surftree HTs
                    # that still hold this dead handle (class b). load_texture
                    # will re-create below for callers that re-enter.
                    self.texture_cache.pop(key, None)
                elif old_fp is not None and old_fp == fp and (old_w, old_h) == (w, h):
                    _touch(handle)
                    return HostTexture(handle, w, h)
                else:
                    # Fingerprint changed / dirty (None after mutated_surface):
                    # rewrite in place only when the GPU handle has the same
                    # dimensions. A different size must re-create; writing a
                    # new-sized buffer into an old-sized handle corrupts the
                    # texture and causes id-reuse chrome panels to read as BG.
                    wrote = False
                    wt_ms = 0.0
                    if (
                        handle
                        and (old_w, old_h) == (w, h)
                        and hasattr(renpy_host, "write_texture_rgba")
                    ):
                        try:
                            _wt0 = _time.monotonic() if _phase0_signals_enabled() else None
                            renpy_host.write_texture_rgba(int(handle), pixels)
                            if _wt0 is not None:
                                wt_ms = (_time.monotonic() - _wt0) * 1000.0
                            wrote = True
                        except Exception:
                            wrote = False
                    if wrote:
                        if _phase0_due_write():
                            _phase0_log(
                                f"write_texture_ms={wt_ms:.3f} size={w}x{h} "
                                f"path=cache_rewrite handle={int(handle)}"
                            )
                        self.texture_cache[key] = (fp, handle, w, h)
                        self._stash_handle_pixels(
                            handle, w, h, pixels, transient=False
                        )
                        _trace_empty_upload(handle, "cache_rewrite")
                        return HostTexture(handle, w, h)
                    try:
                        renpy_host.destroy_texture(handle)
                    except Exception:
                        pass
                    self._forget_handle_pixels(handle)
                    self.texture_cache.pop(key, None)
            handle = renpy_host.create_texture_rgba(w, h, pixels)
            # Permanent cache: skip only non-transient empty-pad residual so a
            # content-backed surface is not stuck as transparent forever. Movie
            # / legitimate transparent placeholders use transient or real zeros.
            if not empty_input:
                self.texture_cache[key] = (fp, handle, w, h)
                self._stash_handle_pixels(handle, w, h, pixels, transient=False)
            else:
                # Still stash nothing useful (all zero); return HT for this frame.
                _trace_empty_upload(handle, "cache_create_empty_pad_nocache")
            if not empty_input:
                _trace_empty_upload(handle, "cache_create")
            return HostTexture(handle, w, h)
        except Exception as e:
            _host_draw_fail("load_texture", e)
            # Best-effort transparent HostTexture placeholder (never raw Surface).
            try:
                w, h = surf.get_size()
                w, h = max(1, int(w)), max(1, int(h))
            except Exception:
                w, h = 1, 1
            try:
                import renpy_host  # type: ignore

                transparent_bytes = bytes(w * h * 4)
                handle = renpy_host.create_texture_rgba(w, h, transparent_bytes)
                _ui_trace_once(
                    "empty_upload",
                    f"class=a blank_success handle={int(handle)} tag=placeholder_exc "
                    f"size=({w},{h}) all_alpha_zero=1 was_empty_pad=1",
                )
                return HostTexture(handle, w, h)
            except Exception:
                _ui_trace_once(
                    "empty_upload",
                    f"class=c handle=0 tag=placeholder_fail size=({w},{h})",
                )
                return HostTexture(0, w, h)

    def ready_one_texture(self):
        return False

    def solid_texture(self, w, h, color):
        """Create a solid-color HostTexture (product Solid uses 10×10 + reverse).

        Returns a full-rect texture of size (w,h) filled with ``color``. Callers
        such as ``imagelike.Solid`` blit this into a larger Render and stamp
        ``reverse`` Matrix2D so the draw walk stretches it via
        ``_node_needs_axis_scale`` + ``_draw_texture_at``.
        """
        from renpy.pygame.surface import Surface

        s = Surface((max(1, int(w)), max(1, int(h))))
        if len(color) == 3:
            color = (*color, 255)
        s.fill(color)
        return self.load_texture(s)

    def _dm(self, pipeline, mesh, texture=None, texture1=None, uniforms=None, texture2=None):
        """Queue a draw_model for the current product frame (batched FFI)."""
        self._draw_batch.append(
            (int(pipeline), int(mesh), texture, texture1, uniforms, texture2)
        )

    def _flush_draw_batch(self):
        """Emit queued draw_model calls via draw_models (or fallback)."""
        batch = self._draw_batch
        if not batch:
            return
        self._draw_batch = []
        try:
            import renpy_host  # type: ignore

            dm = getattr(renpy_host, "draw_models", None)
            if dm is not None:
                dm(batch)
                return
            for item in batch:
                renpy_host.draw_model(*item)
        except Exception as e:
            _host_draw_fail("flush_draw_batch", e)

    def _end_frame_present(self):
        """Flush batched draw_model cmds then present (product + nested RTT)."""
        self._flush_draw_batch()
        import renpy_host  # type: ignore
        renpy_host.end_frame_present()

    def draw_screen(self, surftree, flip=True):


        """
        Walk a Render-like surftree and emit draw_model batches.

        Duck-types nodes rather than requiring Cython Render/GL2Model:

        - Model-like: ``mesh`` (handle / MeshData / True), optional ``texture`` /
          ``textures``, ``vertices``+``indices``, ``color``, ``shaders``
        - Render-like: ``children`` list of ``(child, xo, yo, …)`` or bare nodes;
          optional ``cached_model``, ``blits``, ``mesh``
        - Surface-like: ``get_size()`` + pixel buffer → textured quad
        - int: already-uploaded texture handle → full-window textured quad

        Offsets ``xo``/``yo`` are virtual-pixel (top-left origin) and map into NDC
        via ``virtual_size``. Clear is encoded in host ``end_frame_present``.

        Frame order matches GL2: prepare (load_all_textures) → begin_frame →
        draw → present → invalidate frame-local prepare marks.

        Critical: always pair begin_frame with end_frame_present. If a prior
        draw left the host ``in_frame`` (exception between begin/end), the next
        begin_frame nests and end_frame_present **drops** product cmds — the
        classic permanent ``arena_rt_clear`` failure mode.

        Also serializes concurrent force-redraw vs interact present (threading
        RLock) so two end_frame_present calls cannot interleave into pure
        arena-clear black after chrome thrash (confirm_alone_2 residual).
        """
        # Reentrant product present: nested calls (e.g. mid-draw side effect)
        # must not open a second host frame or wipe the outer cmds.
        lock = _draw_screen_lock()
        if not lock.acquire(blocking=True, timeout=30.0):
            _host_draw_fail("draw_screen", RuntimeError("draw_screen lock timeout"))
            return
        try:
            depth = int(getattr(self, "_draw_screen_depth", 0) or 0)
            if depth > 0:
                # Nested: only walk into the already-open host frame if any.
                try:
                    import renpy_host  # type: ignore

                    if surftree is not None and hasattr(renpy_host, "in_frame"):
                        try:
                            if renpy_host.in_frame():
                                self._draw_node(surftree, 0.0, 0.0)
                        except Exception as e:
                            _host_draw_fail("draw_screen.nested", e)
                except Exception as e:
                    _host_draw_fail("draw_screen.nested_import", e)
                return
            self._draw_screen_depth = depth + 1
            try:
                self._draw_screen_body(surftree, flip=flip)
            finally:
                self._draw_screen_depth = 0
        finally:
            try:
                lock.release()
            except Exception:
                pass

    def _draw_screen_body(self, surftree, flip=True):
        """Inner product present (caller holds ``_DRAW_SCREEN_LOCK`` + depth)."""
        try:
            import renpy_host  # type: ignore

            frame_t0 = _time.monotonic() if _phase0_signals_enabled() else None
            prepare_ms = 0.0
            draw_ms = 0.0
            present_ms = 0.0
            invalidate_ms = 0.0

            self._ensure_pipes()
            # Recover from a stuck nested/in_frame host state (no-op when clean).
            self._recover_frame_state()
            if surftree is not None:
                try:
                    _p0 = _time.monotonic() if frame_t0 is not None else None
                    self.load_all_textures(surftree)
                    if _p0 is not None:
                        prepare_ms = (_time.monotonic() - _p0) * 1000.0
                except Exception as e:
                    _host_draw_fail("load_all_textures", e)
            # Prepare may have nested RTT frames; ensure we are not nested before
            # the product present so cmds are not discarded as "nested non-target".
            self._recover_frame_state()
            renpy_host.begin_frame()
            self._draw_batch = []
            # Fresh clip stack each product present (axis-aligned mesh crop).
            self._clip_rect = None
            walk_ok = True
            try:
                if surftree is not None:
                    _d0 = _time.monotonic() if frame_t0 is not None else None
                    self._draw_node(surftree, 0.0, 0.0)
                    if _d0 is not None:
                        draw_ms = (_time.monotonic() - _d0) * 1000.0
            except Exception as e:
                walk_ok = False
                _host_draw_fail("draw_node", e)
            finally:
                self._clip_rect = None
                # Always close the host frame. Prefer NOT presenting a partial
                # cmd list after a walk exception: encode_pass Clears then draws
                # only what was queued → prefs chrome holes / hover flicker.
                # reset_frame_state drops cmds without encoding so the last good
                # game RT remains (empty-present no-op path).
                try:
                    _pr0 = _time.monotonic() if frame_t0 is not None else None
                    if walk_ok:
                        self._end_frame_present()
                    else:
                        reset = getattr(renpy_host, "reset_frame_state", None)
                        if reset is not None:
                            reset()
                        else:
                            # Fallback: still close the frame (may present partial).
                            self._end_frame_present()
                    if _pr0 is not None:
                        present_ms = (_time.monotonic() - _pr0) * 1000.0
                except Exception as e:
                    _host_draw_fail("end_frame_present", e)
                    try:
                        reset = getattr(renpy_host, "reset_frame_state", None)
                        if reset is not None:
                            reset()
                    except Exception:
                        pass
                # Flush meshes that were evicted from the Python cache while still
                # referenced by this frame's draw cmds (deferred in _mesh_quad_ndc).
                try:
                    self._flush_deferred_meshes()
                except Exception as e:
                    _host_draw_fail("flush_deferred_meshes", e)
                # Recycle previous-frame RTTs after present so live handles
                # from this frame remain valid for one more present.
                try:
                    self._recycle_frame_rtts()
                except Exception as e:
                    _host_draw_fail("recycle_frame_rtts", e)
            # Do NOT call renpy_host.request_redraw() after every product present.
            # GL2 flip() only swaps buffers; continuous request_redraw wakes the
            # host event loop every frame and pairs with about_to_wait's redraw
            # to create a busy present/wake storm. Product redraw is already
            # driven by interact needs_redraw / video.playing / REDRAW timers.
            # Frame-local prepare marks: style/hover must re-prepare next frame.
            if surftree is not None:
                try:
                    _i0 = _time.monotonic() if frame_t0 is not None else None
                    self._invalidate_prepared(surftree)
                    if _i0 is not None:
                        invalidate_ms = (_time.monotonic() - _i0) * 1000.0
                except Exception:
                    pass
            if frame_t0 is not None:
                total_ms = (_time.monotonic() - frame_t0) * 1000.0
                # Always emit stall frames; throttle normal samples to interval.
                if total_ms >= 50.0 or _phase0_due_frame():
                    try:
                        fc = int(renpy_host.frame_count()) if hasattr(renpy_host, "frame_count") else -1
                    except Exception:
                        fc = -1
                    tag = "STALL " if total_ms >= 50.0 else ""
                    _phase0_log(
                        f"{tag}draw_frame_ms={total_ms:.3f} prepare_ms={prepare_ms:.3f} "
                        f"draw_ms={draw_ms:.3f} present_ms={present_ms:.3f} "
                        f"invalidate_ms={invalidate_ms:.3f} flip={int(bool(flip))} "
                        f"host_frames={fc}"
                    )
            # Phase 1: arena thrash probe (once). Prefer existing Rust counters.
            if os.environ.get("RENPY_HOST_UI_TRACE") == "1" and "arena_count" not in _UI_TRACE_LOGGED:
                try:
                    sc = (
                        int(renpy_host.sample_texture_count())
                        if hasattr(renpy_host, "sample_texture_count")
                        else -1
                    )
                    ol = (
                        int(renpy_host.texture_order_len())
                        if hasattr(renpy_host, "texture_order_len")
                        else -1
                    )
                    _ui_trace_once(
                        "arena_count",
                        f"sample_texture_count={sc} texture_order_len={ol} cap=8192",
                    )
                except Exception as e:
                    _ui_trace_once(
                        "arena_count",
                        f"arena counter read fail err={type(e).__name__}:{e}",
                    )
        except Exception as e:
            _host_draw_fail("draw_screen", e)


    def _recover_frame_state(self):
        """Pop any stuck nested host frames so the next present is top-level.

        Host ``end_frame_present`` with a non-empty ``frame_cmd_stack`` and no
        ``active_target`` discards cmds (nested non-target path). Product
        draw_screen must therefore never start while nested. We best-effort
        close leftover frames; missing APIs are ignored.

        Prefer ``reset_frame_state`` when available — after flowchart mesh RTT
        thrash the stack can be half-popped with in_frame false while cmds still
        land outside begin_frame (confirm AC-Nav residual).
        """
        try:
            import renpy_host  # type: ignore

            # Preferred: explicit depth/reset if host exposes them.
            if hasattr(renpy_host, "reset_frame_state"):
                renpy_host.reset_frame_state()
                return
            depth = 0
            if hasattr(renpy_host, "frame_depth"):
                try:
                    depth = int(renpy_host.frame_depth())
                except Exception:
                    depth = 0
            # Fallback: attempt a few end_frame_present calls when in_frame-like
            # helpers exist, else a single defensive end (ignored if not in frame).
            n = max(depth, 0)
            if n == 0 and hasattr(renpy_host, "in_frame"):
                try:
                    if renpy_host.in_frame():
                        n = 1
                except Exception:
                    n = 0
            # Always try at least nothing; if we know we're nested, drain.
            for _ in range(min(n, 8)):
                try:
                    self._end_frame_present()
                except Exception:
                    break
        except Exception:
            pass

    def render_to_texture(self, what, alpha=True, properties=None, oversample=1.0):
        """
        Render `what` into an offscreen RTT and return its texture handle.

        `what` may be:
          - int / texture handle already on GPU → returned as-is
          - HostTexture → handle returned as-is
          - Render-like tree (children / mesh) → full tree walk into RTT
          - Surface-like with get_size() + pixels → uploaded and drawn into RTT
          - (width, height) size tuple → empty clear RTT of that size
          - object with .width/.height and optional .texture handle

        Uses create_render_texture + begin_target / end_target + draw_model.
        Nested begin_frame is safe under host frame_cmd_stack **only when**
        every begin_frame is paired with end_frame_present (try/finally below).
        `properties` / `oversample` accepted for GL2Draw call-site parity.
        """
        try:
            import renpy_host  # type: ignore

            self._ensure_pipes()

            # Already a GPU handle.
            if isinstance(what, int) and not isinstance(what, bool):
                return what
            if isinstance(what, HostTexture):
                return what.handle if what.handle > 0 else what

            def _rtt_pass(w, h, draw_fn):
                """begin_target → begin_frame → draw_fn → end_frame → end_target.

                RTTs are borrowed from the size-keyed freelist (see
                ``_acquire_rtt``) so mesh-bake / dissolve thrash does not
                allocate a new full-screen target every call.
                """
                rtt = self._acquire_rtt(w, h)
                renpy_host.begin_target(rtt)
                renpy_host.begin_frame()
                try:
                    draw_fn()
                finally:
                    try:
                        self._end_frame_present()
                    except Exception as e:
                        _host_draw_fail("render_to_texture.end_frame", e)
                    try:
                        renpy_host.end_target()
                    except Exception as e:
                        _host_draw_fail("render_to_texture.end_target", e)
                return rtt

            # Size-only → empty RTT.
            if isinstance(what, (tuple, list)) and len(what) >= 2 and all(
                isinstance(x, (int, float)) for x in what[:2]
            ):
                return _rtt_pass(what[0], what[1], lambda: None)

            # Object exposing a pre-built texture handle (leaf, no children needed).
            tex = getattr(what, "texture", None)
            children = list(self._iter_children(what)) if what is not None else []
            if isinstance(tex, HostTexture):
                tex = tex.handle if tex.handle > 0 else None
            if isinstance(tex, int) and not isinstance(tex, bool) and tex > 0 and not children:
                w = int(getattr(what, "width", 0) or getattr(what, "w", 0) or 0)
                h = int(getattr(what, "height", 0) or getattr(what, "h", 0) or 0)
                if w <= 0 or h <= 0:
                    try:
                        w, h = what.get_size()
                    except Exception:
                        w, h = self.virtual_size
                handle = tex
                return _rtt_pass(
                    w, h, lambda: self._dm(self._tex_pipe, self._quad_mesh, handle)
                )

            # Surface-like leaf (no children): upload + draw into RTT.
            if (hasattr(what, "get_size") or hasattr(what, "_pixels")) and not children:
                try:
                    w, h = what.get_size()
                except Exception:
                    w, h = self._node_size(what)
                w, h = max(1, int(w)), max(1, int(h))
                src = self.load_texture(what, transient=True)
                handle = src.handle if isinstance(src, HostTexture) else src
                return _rtt_pass(
                    w, h, lambda: self._dm(self._tex_pipe, self._quad_mesh, handle)
                )

            # Render/Model tree → walk into RTT (covers mesh=True + children).
            # GL2 always prepares before RTT (gl2draw.pyx:1173+). Without this,
            # nested scene Renders used as dissolve slots draw blank/black.
            try:
                self.load_all_textures(what)
            except Exception as e:
                _host_draw_fail("rtt.load_all_textures", e)
            # Nested prepare may leave host frame stack dirty; drain before RTT
            # ONLY when we are not already inside a product/parent frame.
            # Mid-draw _child_to_texture → render_to_texture is common for mesh
            # containers; calling reset_frame_state there wipes the outer
            # begin_frame so every subsequent draw_model is dropped
            # ("draw_model outside begin_frame") and the game RT stays arena-clear
            # despite prepared dock HostTextures (H-Idle-A′ / AC-Idle residual).
            try:
                in_f = False
                if hasattr(renpy_host, "in_frame"):
                    try:
                        in_f = bool(renpy_host.in_frame())
                    except Exception:
                        in_f = False
                if not in_f:
                    self._recover_frame_state()
            except Exception:
                pass

            w, h = self._node_size(what)
            if w <= 0 or h <= 0:
                w, h = self.virtual_size

            old_vs = self.virtual_size

            def _draw_tree():
                old_clip = self._clip_rect
                try:
                    self.virtual_size = (w, h)
                    # RTT-local coords: do not inherit product absolute clip AABB.
                    self._clip_rect = None
                    self._draw_node(what, 0.0, 0.0)
                finally:
                    self.virtual_size = old_vs
                    self._clip_rect = old_clip

            return _rtt_pass(w, h, _draw_tree)
        except Exception as e:
            _host_draw_fail("render_to_texture", e)
            return what

    # --- Shader / uniform packing (named-pipeline honesty) ---------------------

    def _pipeline_for_shaders(self, shaders, has_texture: bool) -> int:
        """Map renpy.* shader names to host pipeline handles.

        P1.5 routing:
        1. Strip composition-only parts (geometry / alpha) → effect_parts.
        2. Any atomic multi-tex (dissolve / imagedissolve) → prebaked prefer-list.
        3. Multiple mergeable effects → composer (WgslShaderCache); residual-log
           on fail, then fall through to prebaked prefer-list.
        4. Single (or zero) effect → prebaked prefer-list / solid-or-texture.
        Composition parts never select a pipeline by themselves; renpy.alpha is
        applied via the vertex-color fold elsewhere in the draw path.
        """
        self._ensure_pipes()
        try:
            from renpy.wgpu.shaders import (
                composition_mode,
                host_pipeline_key,
                is_atomic,
                is_mergeable,
            )
        except Exception:
            def composition_mode(_n):
                return None

            def host_pipeline_key(_n):
                return None

            def is_atomic(_n):
                return False

            def is_mergeable(_n):
                return False

        names = list(shaders or ())
        # Effect parts: strip composition-only (geometry / alpha).
        effect_parts = [n for n in names if not composition_mode(n)]

        # Multi-mergeable stack → try composer (product path: hard_fail=False).
        # Atomic multi-tex (dissolve/imagedissolve) stays on the prebaked path.
        if (
            len(effect_parts) > 1
            and not any(is_atomic(n) for n in effect_parts)
            and all(is_mergeable(n) for n in effect_parts)
        ):
            try:
                from renpy.wgpu.composer import get_shader_cache

                result = get_shader_cache().get(
                    effect_parts, hard_fail=False, has_texture=has_texture
                )
            except Exception as e:  # noqa: BLE001 — product residual, never crash
                result = None
                try:
                    print(
                        f"[wgpu composer residual] multi-effect compose failed "
                        f"for {effect_parts!r}: {e}",
                        flush=True,
                    )
                except Exception:
                    pass
            if result is not None:
                pipe = int(getattr(result, "pipeline", 0) or 0)
                residual = getattr(result, "residual", None)
                if residual:
                    # Soft-fail residual already logged by composer; restate for draw.
                    try:
                        print(
                            f"[wgpu composer residual] draw fallback for "
                            f"{effect_parts!r}: {residual}",
                            flush=True,
                        )
                    except Exception:
                        pass
                if pipe > 0:
                    return pipe
                # pipeline==0 → fall through to prebaked prefer-list
            else:
                # get() returned None (compose_wgsl illegal / hard residual).
                try:
                    print(
                        f"[wgpu composer residual] no compose for "
                        f"{effect_parts!r}; using prebaked prefer-list",
                        flush=True,
                    )
                except Exception:
                    pass

        # Priority: more specialized first. (single-effect, atomic, or residual)
        prefer = (
            "renpy.imagedissolve",
            "renpy.blur",
            "renpy.matrixcolor",
            "renpy.mask",
            "renpy.alpha_mask",
            "renpy.dissolve",
            "renpy.solid",
            "renpy.texture",
            "renpy.ftl",
            "live2d.mask",
            "live2d.inverted_mask",
            "live2d.colors",
            "live2d.flip_texture",
        )
        ordered = [n for n in prefer if n in names]
        ordered += [n for n in names if n not in ordered]

        key_to_pipe = {
            "textured_pipeline": self._tex_pipe,
            "solid_pipeline": self._solid_pipe,
            "dissolve_pipeline": self._dissolve_pipe,
            "imagedissolve_pipeline": self._imagedissolve_pipe,
            "blur_pipeline": self._blur_pipe,
            "matrixcolor_pipeline": self._matrixcolor_pipe,
            "alpha_mask_pipeline": self._alpha_mask_pipe,
            "mask_pipeline": self._mask_pipe,
            "live2d_mask_pipeline": self._live2d_mask_pipe,
            "live2d_inverted_mask_pipeline": self._live2d_inverted_mask_pipe,
            "live2d_colors_pipeline": self._live2d_colors_pipe,
            "live2d_flip_pipeline": self._live2d_flip_pipe,
        }

        for name in ordered:
            if composition_mode(name):
                continue
            key = host_pipeline_key(name)
            if key and key in key_to_pipe and key_to_pipe[key]:
                return int(key_to_pipe[key])
            # bare aliases
            if name in ("solid", "renpy.solid"):
                return int(self._solid_pipe)
            if name in ("texture", "renpy.texture", "ftl", "renpy.ftl"):
                return int(self._tex_pipe)

        return int(self._tex_pipe if has_texture else self._solid_pipe)

    def _matrix_to_floats(self, matrix) -> Optional[list]:
        """Extract 16 column-major floats from a Ren'Py Matrix or sequence."""
        if matrix is None:
            return None
        if isinstance(matrix, (list, tuple)):
            u = [float(x) for x in matrix[:16]]
            while len(u) < 16:
                u.append(0.0)
            return u
        # renpy.display.matrix.Matrix stores row-ish named fields; m is &xdx.
        try:
            # Column-major for WGSL mat4x4(col0..col3):
            # col0 = (xdx, ydx, zdx, wdx), ...
            return [
                float(matrix.xdx), float(matrix.ydx), float(matrix.zdx), float(matrix.wdx),
                float(matrix.xdy), float(matrix.ydy), float(matrix.zdy), float(matrix.wdy),
                float(matrix.xdz), float(matrix.ydz), float(matrix.zdz), float(matrix.wdz),
                float(matrix.xdw), float(matrix.ydw), float(matrix.zdw), float(matrix.wdw),
            ]
        except Exception:
            pass
        try:
            m = list(matrix.m)  # type: ignore[attr-defined]
            u = [float(x) for x in m[:16]]
            while len(u) < 16:
                u.append(0.0)
            return u
        except Exception:
            return None

    def _pack_uniforms(self, uniforms, shaders) -> Optional[list]:
        """Pack product dict uniforms into the host 16-float blob.

        Layouts match arena WGSL Params:
          - blur: data0.x = u_renpy_blur_log2
          - matrixcolor: col0..col3 = u_renpy_matrixcolor (16 floats)
          - mask: data0.xy = mult, offset
        Dissolve dict handled by dual-draw early-return (not packed here).
        """
        if uniforms is None:
            return None
        if not isinstance(uniforms, dict):
            u = list(uniforms)[:16]
            while len(u) < 16:
                u.append(0.0)
            return [float(x) for x in u]

        shaders = list(shaders or ())
        # matrixcolor takes full 16 floats
        if "u_renpy_matrixcolor" in uniforms or any(
            s in ("renpy.matrixcolor", "matrixcolor") for s in shaders
        ):
            mat = uniforms.get("u_renpy_matrixcolor")
            packed = self._matrix_to_floats(mat)
            if packed is not None:
                return packed

        u = [0.0] * 16
        packed_any = False

        # Plain dissolve amount (2-tex mix): data0.x = u_renpy_dissolve
        if "u_renpy_dissolve" in uniforms and "u_renpy_dissolve_offset" not in uniforms:
            try:
                u[0] = float(uniforms["u_renpy_dissolve"])
                packed_any = True
            except (TypeError, ValueError):
                u[0] = 1.0
            return u

        # ImageDissolve: data0.xy = offset, multiplier (arena IMAGEDISSOLVE_WGSL)
        if "u_renpy_dissolve_offset" in uniforms or "u_renpy_dissolve_multiplier" in uniforms:
            try:
                u[0] = float(uniforms.get("u_renpy_dissolve_offset", 0.0))
                packed_any = True
            except (TypeError, ValueError):
                u[0] = 0.0
            try:
                u[1] = float(uniforms.get("u_renpy_dissolve_multiplier", 1.0))
                packed_any = True
            except (TypeError, ValueError):
                u[1] = 1.0
            return u

        # Product alias image_dissolve (HuangmeiC dissolve_transform):
        #   fade = clamp((control.r * (1 - t) - anim + t) / t, 0, 1)
        #   mix(new, old, fade)  with slots remapped to control/old/new
        # Map onto stock imagedissolve a=clamp((ctrl+offset)*mult) with
        # data0.z=1 (red channel — rule.png is grayscale RGB, a=1).
        # Progressive wipe: offset = -1 + 2*anim so anim=0 → a=0 (old),
        # anim=1 → a=1 (new) across the full control ramp.
        if "u_transition" in uniforms or "u_animation" in uniforms:
            try:
                anim = float(uniforms.get("u_animation", 0.0))
            except (TypeError, ValueError):
                anim = 0.0
            anim = max(0.0, min(1.0, anim))
            u[0] = float(-1.0 + 2.0 * anim)  # offset
            u[1] = 1.0  # mult
            u[2] = 1.0  # red-channel control (not alpha)
            packed_any = True
            return u

        if "u_renpy_blur_log2" in uniforms:
            try:
                u[0] = float(uniforms["u_renpy_blur_log2"])
                packed_any = True
            except (TypeError, ValueError):
                pass

        if "u_renpy_mask_multiplier" in uniforms or "u_renpy_mask_offset" in uniforms:
            try:
                u[0] = float(uniforms.get("u_renpy_mask_multiplier", 1.0))
                packed_any = True
            except (TypeError, ValueError):
                u[0] = 1.0
            try:
                u[1] = float(uniforms.get("u_renpy_mask_offset", 0.0))
                packed_any = True
            except (TypeError, ValueError):
                u[1] = 0.0

        return u if packed_any else None

    # --- Phase 5 transition helpers (exercise host pipelines) -----------------

    def draw_textured(self, texture: int, mesh: Optional[int] = None):
        import renpy_host  # type: ignore  # via host_bridge

        self._ensure_pipes()
        self._dm(self._tex_pipe, mesh or self._quad_mesh, texture)

    def draw_dissolve(self, texture: int, mesh: Optional[int] = None):
        """Draw with renpy.dissolve pipeline (alpha from vertex color / tex)."""
        import renpy_host  # type: ignore  # via host_bridge

        self._ensure_pipes()
        self._dm(self._dissolve_pipe, mesh or self._quad_mesh, texture)

    def draw_blur(
        self,
        texture: int,
        blur_log2: float = 2.0,
        mesh: Optional[int] = None,
    ):
        """Draw with renpy.blur pipeline; uniforms[0] = blur_log2."""
        import renpy_host  # type: ignore  # via host_bridge

        self._ensure_pipes()
        u = [float(blur_log2)] + [0.0] * 15
        self._dm(
            self._blur_pipe, mesh or self._quad_mesh, texture, None, u
        )

    def draw_matrixcolor(
        self,
        texture: int,
        matrix: Sequence[float],
        mesh: Optional[int] = None,
    ):
        """Draw with renpy.matrixcolor; matrix is 16 floats (column-major 4x4)."""
        import renpy_host  # type: ignore  # via host_bridge

        self._ensure_pipes()
        u = list(matrix)[:16]
        while len(u) < 16:
            u.append(0.0)
        self._dm(
            self._matrixcolor_pipe, mesh or self._quad_mesh, texture, None, u
        )

    def draw_mask(
        self,
        src: int,
        mask: int,
        mult: float = 1.0,
        offset: float = 0.0,
        mesh: Optional[int] = None,
        alpha_only: bool = False,
    ):
        """Draw dual-tex mask (or alpha_mask if alpha_only)."""
        import renpy_host  # type: ignore  # via host_bridge

        self._ensure_pipes()
        pipe = self._alpha_mask_pipe if alpha_only else self._mask_pipe
        u = [float(mult), float(offset)] + [0.0] * 14
        self._dm(pipe, mesh or self._quad_mesh, src, mask, u)

    # --- Phase 8 model mesh helpers ------------------------------------------

    def create_mesh(
        self,
        vertices: Sequence[float],
        indices: Optional[Sequence[int]] = None,
    ) -> int:
        """
        Upload a mesh buffer to GpuArena.

        Vertex layout: pos.xy, uv.xy, color.rgba (8 f32 / vertex).
        """
        import renpy_host  # type: ignore

        idx = list(indices) if indices is not None else None
        return int(renpy_host.create_mesh(list(vertices), idx))

    def destroy_mesh(self, mesh: int) -> None:
        import renpy_host  # type: ignore

        renpy_host.destroy_mesh(int(mesh))

    def draw_model_mesh(
        self,
        mesh: int,
        texture: Optional[int] = None,
        pipeline: Optional[int] = None,
        uniforms: Optional[Sequence[float]] = None,
    ) -> None:
        """
        Draw an uploaded mesh via the primary draw_model path.

        texture=None → solid pipeline; otherwise textured (or explicit pipeline).
        """
        import renpy_host  # type: ignore  # via host_bridge

        self._ensure_pipes()
        if pipeline is None:
            pipeline = self._tex_pipe if texture is not None else self._solid_pipe
        u = list(uniforms) if uniforms is not None else None
        self._dm(pipeline, int(mesh), texture, None, u)


    def draw_live2d_mask(
        self,
        src: int,
        mask: int,
        model_size: Sequence[float] = (1.0, 1.0),
        ppu: float = 1.0,
        offset: Sequence[float] = (0.0, 0.0),
        mesh: Optional[int] = None,
        inverted: bool = False,
    ):
        """Draw with live2d.mask / live2d.inverted_mask (mask UV from pos*ppu+offset)."""
        import renpy_host  # type: ignore  # via host_bridge

        self._ensure_pipes()
        pipe = (
            self._live2d_inverted_mask_pipe if inverted else self._live2d_mask_pipe
        )
        u = [
            float(model_size[0]),
            float(model_size[1]),
            float(ppu),
            float(offset[0]),
            float(offset[1]),
        ] + [0.0] * 11
        self._dm(pipe, mesh or self._quad_mesh, src, mask, u)

    def draw_live2d_colors(
        self,
        texture: int,
        multiply: Sequence[float] = (1.0, 1.0, 1.0, 1.0),
        screen: Sequence[float] = (0.0, 0.0, 0.0, 0.0),
        mesh: Optional[int] = None,
    ):
        """Draw with live2d.colors (multiply then screen blend)."""
        import renpy_host  # type: ignore  # via host_bridge

        self._ensure_pipes()
        m = list(multiply)[:4]
        while len(m) < 4:
            m.append(1.0)
        s = list(screen)[:4]
        while len(s) < 4:
            s.append(0.0)
        u = m + s + [0.0] * 8
        self._dm(
            self._live2d_colors_pipe, mesh or self._quad_mesh, texture, None, u
        )

    def draw_live2d_flip(self, texture: int, mesh: Optional[int] = None):
        """Draw with live2d.flip_texture (V flip)."""
        import renpy_host  # type: ignore  # via host_bridge

        self._ensure_pipes()
        self._dm(
            self._live2d_flip_pipe, mesh or self._quad_mesh, texture
        )

    def is_pixel_opaque(self, what, x=None, y=None):
        """Return True if the sampled pixel is not fully transparent.

        Call-site parity:
          - ``Render.is_pixel_opaque`` → ``draw.is_pixel_opaque(what)`` where
            ``what`` is already a 1×1 subsurface (see ``render.pyx``).
          - Optional ``x``/``y`` accepted for abstract Renderer signature.

        Always samples via a fresh RTT + ``read_texture_rgba`` (sample textures
        lack COPY_SRC on host). On failure returns True (conservative hit).
        """
        try:
            import renpy_host  # type: ignore

            self._ensure_pipes()

            handle = None
            # Existing GPU handle / HostTexture: blit into a fresh RTT for COPY_SRC.
            src = None
            sw = sh = 0
            if isinstance(what, HostTexture) and what.handle > 0:
                src = int(what.handle)
                sw, sh = max(1, int(what.w)), max(1, int(what.h))
            elif isinstance(what, int) and not isinstance(what, bool) and what > 0:
                src = int(what)
                sw, sh = 1, 1
                if x is not None and y is not None:
                    # Unknown full size; sample via 1×1 RTT after full-quad draw.
                    sw, sh = 1, 1

            opaque_rtt = None
            opaque_size = None
            if src is not None:
                sw, sh = max(1, int(sw)), max(1, int(sh))
                rtt = self._acquire_rtt(sw, sh)
                opaque_rtt, opaque_size = int(rtt), (sw, sh)
                renpy_host.begin_target(rtt)
                renpy_host.begin_frame()
                try:
                    self._dm(self._tex_pipe, self._quad_mesh, src)
                finally:
                    try:
                        self._end_frame_present()
                    except Exception:
                        pass
                    try:
                        renpy_host.end_target()
                    except Exception:
                        pass
                handle = int(rtt)
            else:
                # Render tree / surface / size → render_to_texture (creates RTT).
                rtt = self.render_to_texture(what)
                if isinstance(rtt, HostTexture):
                    handle = int(rtt.handle)
                    opaque_rtt = handle
                    opaque_size = (max(1, int(rtt.w)), max(1, int(rtt.h)))
                elif isinstance(rtt, int) and not isinstance(rtt, bool):
                    handle = int(rtt)
                    opaque_rtt = handle

            if handle is None or handle <= 0:
                if opaque_rtt is not None:
                    self._release_rtt_now(
                        opaque_rtt,
                        *(opaque_size if opaque_size else (None, None)),
                    )
                return True

            try:
                tw, th, rgba = renpy_host.read_texture_rgba(handle)
            finally:
                # Short-lived sample RTT: return immediately (not frame-tracked).
                if opaque_rtt is not None:
                    if opaque_size is not None:
                        self._release_rtt_now(
                            opaque_rtt, opaque_size[0], opaque_size[1]
                        )
                    else:
                        self._release_rtt_now(opaque_rtt)

            if tw <= 0 or th <= 0 or not rgba or len(rgba) < 4:
                return True

            if x is not None and y is not None and (tw > 1 or th > 1):
                sx = max(0, min(int(tw) - 1, int(x)))
                sy = max(0, min(int(th) - 1, int(y)))
            else:
                sx = int(tw) // 2
                sy = int(th) // 2

            i = (sy * int(tw) + sx) * 4
            if i + 3 >= len(rgba):
                return True
            return rgba[i + 3] > 0
        except Exception as e:
            try:
                print(
                    f"WgpuDraw.is_pixel_opaque: {type(e).__name__}: {e}",
                    flush=True,
                )
            except Exception:
                pass
            return True

    def get_physical_size(self):
        return self.physical_size

    def get_virtual_size(self):
        return self.virtual_size

    def get_drawable_size(self):
        return self.drawable_size

    def translate_point(self, x, y):
        """Physical window coords → virtual game coords (GL2Draw parity).

        WgpuDraw stretches virtual content across the full window (see
        ``_virt_rect_to_ndc``), so this is a pure size scale — not letterbox.
        Identity was residual #5: clicks on a non-1280x720 surface missed
        main-menu hit targets (Start/Prefs) even when events arrived.
        """
        try:
            pw, ph = self.physical_size
            vw, vh = self.virtual_size
            pw = max(1, int(pw))
            ph = max(1, int(ph))
            vw = max(1, int(vw))
            vh = max(1, int(vh))
            return (int(1.0 * float(x) * vw / pw), int(1.0 * float(y) * vh / ph))
        except Exception:
            return (int(x), int(y))

    def untranslate_point(self, x, y):
        """Virtual → physical (inverse of translate_point)."""
        try:
            pw, ph = self.physical_size
            vw, vh = self.virtual_size
            pw = max(1, int(pw))
            ph = max(1, int(ph))
            vw = max(1, int(vw))
            vh = max(1, int(vh))
            return (int(1.0 * float(x) * pw / vw), int(1.0 * float(y) * ph / vh))
        except Exception:
            return (int(x), int(y))

    def mouse_event(self, ev):
        """Return virtual (x, y) for the event — GL2Draw/SWDraw contract."""
        try:
            pos = getattr(ev, "pos", None)
            if isinstance(pos, (tuple, list)) and len(pos) >= 2:
                return self.translate_point(int(pos[0]), int(pos[1]))
            x = getattr(ev, "x", None)
            y = getattr(ev, "y", None)
            if x is not None and y is not None:
                return self.translate_point(int(x), int(y))
        except Exception:
            pass
        return self.get_mouse_pos()

    def get_mouse_pos(self):
        try:
            import renpy.pygame as pygame

            x, y = pygame.mouse.get_pos()
            return self.translate_point(x, y)
        except Exception:
            return (0, 0)

    def set_mouse_pos(self, x, y):
        try:
            import renpy.pygame as pygame

            px, py = self.untranslate_point(x, y)
            pygame.mouse.set_pos((px, py))
        except Exception:
            pass

    def screenshot(self, surftree=None):
        """
        Capture pre-present game RT via read_game_rt_rgba → host Surface.

        Returns Surface or None on failure. Format Rgba8Unorm tight rows.
        """
        try:
            import renpy_host  # type: ignore
            from renpy.pygame.surface import Surface

            w, h, rgba = renpy_host.read_game_rt_rgba()
            if w <= 0 or h <= 0 or not rgba:
                return None
            surf = Surface((int(w), int(h)))
            data = bytes(rgba)
            n = int(w) * int(h) * 4
            if len(data) < n:
                return None
            surf._pixels = bytearray(data[:n])
            return surf
        except Exception:
            return None

    def screenshot_rgba(self):
        """Raw (width, height, bytes) readback for golden harnesses."""
        try:
            import renpy_host  # type: ignore

            return renpy_host.read_game_rt_rgba()
        except Exception:
            return (0, 0, b"")

    def kill_textures(self):
        """Destroy arena textures and drop Python-side handle caches.

        Must not leave renpy.display.im.cache entries pointing at destroyed
        handles (const_size PNGs after maximize) — that blanked the main-menu
        overlay. Clear the image cache when available, then destroy handles.
        """
        try:
            import renpy.display.im as im

            im.cache.clear()
        except Exception:
            pass
        try:
            import renpy_host  # type: ignore

            for _fp, handle, _tw, _th in list(self.texture_cache.values()):
                try:
                    renpy_host.destroy_texture(handle)
                except Exception:
                    pass
            for tent in list(self._transient_tex.values()):
                handle = tent[0] if tent else 0
                _w = tent[1] if tent and len(tent) > 1 else 0
                _h = tent[2] if tent and len(tent) > 2 else 0
                _fp = tent[3] if tent and len(tent) > 3 else None
                try:
                    renpy_host.destroy_texture(int(handle))
                except Exception:
                    pass
        except Exception:
            pass
        self.texture_cache.clear()
        self._transient_tex.clear()
        # Keep _handle_pixels across kill_textures so surftree-held HostTextures
        # can revive on the next present (class b). Clear only the remap table —
        # dead ids are no longer valid mappings. Cap eviction still bounds RAM.
        try:
            self._handle_remap = {}
        except Exception:
            pass
        try:
            self._destroy_all_rtts()
        except Exception:
            pass
        # Drop mesh geometry cache handles + any still-pending deferred destroys.
        try:
            import renpy_host  # type: ignore

            for handle in list(self._mesh_cache.values()):
                try:
                    renpy_host.destroy_mesh(int(handle))
                except Exception:
                    pass
            for handle in list(getattr(self, "_mesh_deferred_destroy", None) or []):
                try:
                    renpy_host.destroy_mesh(int(handle))
                except Exception:
                    pass
        except Exception:
            pass
        self._mesh_cache.clear()
        self._mesh_deferred_destroy = []
        self._quad_mesh = None

    def event_peek_sleep(self):
        return None

    def get_info(self) -> dict[str, Any]:
        return dict(self.info)
