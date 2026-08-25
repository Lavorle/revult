"""
WgpuDraw — host renderer (Phase 2–5, Phase 8 model mesh).

Primary path: begin_frame → draw_model → end_frame_present.
Phase 5: RTT (create_render_texture + begin/end_target), screenshot via
read_game_rt_rgba, dissolve/blur/matrixcolor/mask helpers.
Phase 8: create_mesh upload + draw_model_mesh for assimp/procedural models.
"""


from __future__ import annotations

import os
from typing import Any

# --- P0 decomposition: re-exports keep pickle/import compat ---
# HostTexture + fingerprint moved to host_texture.py; debug helpers to
# draw_debug.py (with generic _phase0_due); RTT pool to rtt_pool.py.
from .draw_debug import (  # noqa: F401
    _DRAW_SCREEN_LOCK,
    _HOST_DRAW_FAIL_LOGGED,
    _PHASE0_DISSOLVE_INTERVAL,
    _PHASE0_FRAME_INTERVAL,
    _PHASE0_LAST_DISSOLVE_T,
    _PHASE0_LAST_FRAME_T,
    _PHASE0_LAST_GENERIC,
    _PHASE0_LAST_WRITE_T,
    _PHASE0_WRITE_INTERVAL,
    _UI_TRACE_LOGGED,
    _draw_screen_lock,
    _host_draw_fail,
    _phase0_due,
    _phase0_due_dissolve,
    _phase0_due_frame,
    _phase0_due_write,
    _phase0_log,
    _phase0_signals_enabled,
    _safe_print,
    _ui_trace_once,
)
from .draw_model import ModelMixin
from .draw_pipeline import PipelineMixin
from .draw_screen import ScreenMixin
from .draw_surftree import SurftreeMixin
from .draw_texture import TextureMixin
from .draw_traversal import TraversalMixin
from .draw_walk import WalkMixin
from .host_texture import HostTexture, _surf_fingerprint  # noqa: F401
from .rtt_pool import RttPoolMixin

# host_bridge single-point import (optional; fallback to direct import)
try:
    from .host_bridge import renpy_host as _host_bridge
except Exception:  # pragma: no cover
    _host_bridge = None  # type: ignore


class WgpuDraw(RttPoolMixin, SurftreeMixin, TraversalMixin, ModelMixin, WalkMixin, TextureMixin, ScreenMixin, PipelineMixin):
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
            from renpy.display import render

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
            import renpy_host  # type: ignore

            import renpy  # type: ignore

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
            from renpy import pygame

            x, y = pygame.mouse.get_pos()
            return self.translate_point(x, y)
        except Exception:
            return (0, 0)

    def set_mouse_pos(self, x, y):
        try:
            from renpy import pygame

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

    def event_peek_sleep(self):
        return None

    def get_info(self) -> dict[str, Any]:
        return dict(self.info)
