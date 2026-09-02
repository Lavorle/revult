"""
WgpuDraw — host renderer (Phase 2–5, Phase 8 model mesh).

Primary path: begin_frame → draw_model → end_frame_present.
Phase 5: RTT (create_render_texture + begin/end_target), screenshot via
read_game_rt_rgba, dissolve/blur/matrixcolor/mask helpers.
Phase 8: create_mesh upload + draw_model_mesh for assimp/procedural models.
"""


from __future__ import annotations

from .constants import (
    AUTO_MIPMAP_THRESH,
    GOLDEN_FALLBACK_H,
    GOLDEN_FALLBACK_W,
    HANDLE_PIXELS_CAP,
    MAX_TEX_H,
    MAX_TEX_W,
    MESH_CACHE_CAP,
    RTT_FREELIST_CAP,
    RTT_POOL_MAX_PER_SIZE,
)
from .host_bridge import host_env_bool
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
except Exception:  # pragma: no cover  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
    _host_bridge = None  # type: ignore
# Instance grouping (M1 T3) — private helper imported here to avoid circular deps
try:
    from .host_bridge import _InstanceGroup
except Exception:  # noqa: BLE001
    _InstanceGroup = None  # type: ignore


class GpuHandleCache:
    """Generic LRU-style GPU-handle cache bounded by a count cap.

    Core: a plain ``dict`` ``{K: V}`` plus an ordered ``list[K]`` recency
    ring. Lookup is O(1) via the dict; eviction pops the LRU key(s) once the
    cap is exceeded. No host/renpy import — safe to construct at import time
    and from pure unit tests (no GPU/renpy_host needed).

    ``alive_fn`` is an optional probe ``Callable[[V], bool]``; when supplied it
    is consulted on ``get`` so dead-stale entries fall through to a caller
    re-create path instead of returning a destroyed handle (mesh-alive parity).

    Two eviction policies are supported (not mutually exclusive):
      * ``_evict`` — drop the single LRU key when count exceeds ``cap`` (the
        default ``set`` path).
      * ``evict_some`` — size-aware bulk drop, used by the pixel stash which
        prefers to keep small UI chrome and drop oversize frames.

    ``deferred_destroy`` buffers popped handles (mesh ids already queued in a
    frame's ``frame_cmds``) so the host destroy runs only after present drains.
    """

    __slots__ = ("_map", "_ring", "_cap", "_alive_fn", "_deferred")

    def __init__(self, cap, alive_fn=None):
        self._map = {}  # type: dict
        self._ring = []  # type: list
        self._cap = int(cap)
        self._alive_fn = alive_fn
        self._deferred = []  # type: list

    # --- dict-like core -----------------------------------------------------
    def get(self, key, default=None):
        v = self._map.get(key, default)
        if v is default:
            return v
        if self._alive_fn is not None:
            try:
                if not self._alive_fn(v):
                    return default
            except Exception:  # noqa: BLE001 -- probe must never abort a frame
                pass
        self._touch(key)
        return v

    def set(self, key, value):
        existed = key in self._map
        self._map[key] = value
        if not existed:
            self._ring.append(key)
        else:
            self._touch(key)
        self._evict_if_needed()
        return value

    def pop(self, key, default=None):
        if key not in self._map:
            return default
        v = self._map.pop(key)
        try:
            self._ring.remove(key)
        except ValueError:
            pass
        return v

    def __contains__(self, key):
        return key in self._map

    def __len__(self):
        return len(self._map)

    def __iter__(self):
        return iter(self._map)

    def items(self):
        return self._map.items()

    def keys(self):
        return self._map.keys()

    def values(self):
        return self._map.values()

    def clear(self):
        self._map.clear()
        del self._ring[:]
        del self._deferred[:]

    # --- eviction -----------------------------------------------------------
    def _touch(self, key):
        ring = self._ring
        try:
            ring.remove(key)
        except ValueError:
            pass
        ring.append(key)

    def set_cap(self, cap):
        self._cap = int(cap)
        self._evict_if_needed()

    def _evict_if_needed(self):
        while len(self._map) > self._cap and self._ring:
            old = self._ring.pop(0)
            self._map.pop(old, None)

    def evict_if_needed(self):
        """Public single-point eviction trigger (wraps internal trim)."""
        self._evict_if_needed()

    def evict_some(self, n, area_fn=None, keep_below=None):
        """Drop up to ``n`` keys, preferring large-area entries first.

        ``area_fn(V) -> int`` returns the pixel area of a value (default 0).
        When ``keep_below`` is given, entries with area <= it are never
        dropped while oversize entries remain (UI-chrome keep policy).
        Returns the number of keys actually removed.
        """
        if n <= 0 or not self._map:
            return 0
        items = list(self._map.items())
        if area_fn is not None:
            def _area(v):
                try:
                    return int(area_fn(v) or 0)
                except Exception:  # noqa: BLE001
                    return 0
            if keep_below is not None:
                over = [(k, _area(v)) for k, v in items if _area(v) > int(keep_below)]
                over.sort(key=lambda kv: kv[1], reverse=True)
                dropped = 0
                for k, _a in over:
                    if dropped >= n:
                        break
                    self.pop(k)
                    dropped += 1
                if dropped >= n:
                    return dropped
                mid = [(k, _area(v)) for k, v in items if _area(v) > 0]
                mid.sort(key=lambda kv: kv[1], reverse=True)
                for k, _a in mid:
                    if dropped >= n:
                        break
                    self.pop(k)
                    dropped += 1
                return dropped
            alls = [(k, _area(v)) for k, v in items]
            alls.sort(key=lambda kv: kv[1], reverse=True)
            dropped = 0
            for k, _a in alls:
                if dropped >= n:
                    break
                self.pop(k)
                dropped += 1
            return dropped
        dropped = 0
        for k in list(self._map.keys()):
            if dropped >= n:
                break
            self.pop(k)
            dropped += 1
        return dropped

    # --- deferred host destroy ---------------------------------------------
    def deferred_destroy(self, handle):
        """Buffer a handle for destroy after present (see _flush_deferred)."""
        self._deferred.append(int(handle))

    def take_deferred(self):
        if not self._deferred:
            return None
        out = self._deferred
        self._deferred = []
        return out


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
        self._transient_tex = {}
        # Movie / transient uploads: id(surf) → (handle, w, h, fingerprint).
        # Stock get_movie_texture always passes transient=True on a stable
        # channel Surface; without reuse, each frame allocates a full-size
        # sample texture and arena FIFO eviction kills dock chrome handles.
        # Present-path dead-handle recovery (class b): handle → (w, h, rgba).
        # load_texture already re-uploads on cache miss (~657–664) when callers
        # re-enter load_texture; surftree-held HostTextures after kill_textures
        # / FIFO eviction never re-enter that path. Stash full-texture pixels at
        # upload so _draw_texture_at / resolve can re-create and remap in place.
        # Cap count + skip large Movie frames to bound RAM.
        #
        # T7: unified under one GpuHandleCache (was _handle_pixels + ad-hoc cap).
        # Cap now sourced from constants.HANDLE_PIXELS_CAP (single source).
        self._handle_pixels = GpuHandleCache(HANDLE_PIXELS_CAP)
        self._handle_pixels_cap = HANDLE_PIXELS_CAP  # legacy alias for mixins
        # dead_handle → live_handle (subsurface share). Small table, unified too.
        self._handle_remap = GpuHandleCache(RTT_POOL_MAX_PER_SIZE)  # remap table cap
        # Nested product draw depth (reentrancy guard inside the process lock).
        self._draw_screen_depth = 0
        self._rtt_pool_cap = RTT_FREELIST_CAP  # per-size free-handle cap
        # renpy_host.draw_models (single host lock) before end_frame_present.
        self._draw_batch = []
        # Size-keyed freelist for offscreen render targets. mesh_bake /
        # render_to_texture re-create full-screen RTTs every frame; without
        # recycle, HuangmeiC splash/dissolve thrash OOMs the X server
        # (thousands of full-HD RTTs in seconds).
        # rtt_pool keeps _rtt_* as plain dicts (their shape is list-valued
        # freelists + frame rings, not KV handle maps); mutations route through
        # GpuHandleCache helpers in rtt_pool only as the single eviction point.
        self._rtt_free = {}  # (w, h) -> [handle, ...]
        self._rtt_prev_frame = []  # [(handle, w, h), ...]
        self._rtt_curr_frame = []
        # Geometry-keyed mesh cache. Every textured draw used to call
        # create_mesh for an identical NDC quad; HuangmeiC main-menu trees
        # allocate thousands of mesh buffers per second and OOM the process.
        #
        # T7: unified under one GpuHandleCache (was _mesh_cache + loose cap).
        # Deferred-destroy buffer kept alongside so mid-frame eviction can queue
        # host destroy_mesh after present (see _flush_deferred_meshes).
        self._mesh_cache = GpuHandleCache(MESH_CACHE_CAP)
        self._mesh_cache_cap = MESH_CACHE_CAP  # legacy alias for mixins
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
        self._unit_quad = None
        self._unit_quad_is_instance_source = False
        try:
            import renpy_host as _rh  # type: ignore
            verts = [0, 0, 0, 0, 1, 1, 1, 1, 1, 0, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 0, 1, 1, 1, 1, 1]
            self._unit_quad = _rh.create_mesh(verts, [0, 1, 2, 0, 2, 3])
            self._unit_quad_is_instance_source = True
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — unit quad fallback to legacy path
            self._unit_quad = None
            self._unit_quad_is_instance_source = False
        # M1 T3 instance grouping for solid/textured quads
        try:
            if _InstanceGroup is not None:
                self._instance_group = _InstanceGroup()
            else:
                self._instance_group = None
        except Exception:
            self._instance_group = None
        # GL2-parity clip stack (virtual-pixel absolute coords).
        # None = no clip. Pushed when Render.xclipping/yclipping is set; intersected
        # with parent; empty intersect skips the subtree. Mesh crop (GPU scissor fast path, stencil polygon for rotated).
        self._clip_rect = None  # type: Optional[tuple[float, float, float, float]]
        self._clip_poly = None  # type: Optional[list[tuple[float, float]]]  # polygon clip when reverse non-identity (mutual exclusion with _clip_rect)
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
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass

    # --- M1 T3 instance grouping helpers ------------------------------------
    def _can_instance(self, pipeline, texture1, texture2, uniforms) -> bool:
        """True when this draw can be grouped into instance batch."""
        try:
            if uniforms is not None:
                return False
            if texture1 is not None or texture2 is not None:
                return False
            if self._unit_quad is None:
                return False
            if getattr(self, "_instance_group", None) is None:
                return False
            # Only plain solid/textured (tex_count 0/1, no uniforms) may instance
            # composition_only alpha/geometry handled elsewhere via color fold
            if pipeline is None:
                return False
            # Pipelines may still be None before _ensure_pipes; allow tentative true then fallback
            if self._solid_pipe is None or self._tex_pipe is None:
                try:
                    self._ensure_pipes()
                except Exception:
                    pass
            if pipeline == self._solid_pipe or pipeline == self._tex_pipe:
                return True
            return False
        except Exception:
            return False

    def _instance_add(self, pipeline, texture, x0, y0, x1, y1, u0, v0, u1, v1, color):
        """Add one instance to the pending group; returns True if grouped."""
        try:
            grp = getattr(self, "_instance_group", None)
            if grp is None or self._unit_quad is None:
                return False
            # solid vs textured texture handling
            tex = texture if texture is not None else None
            grp.add(pipeline, tex, None, None, x0, y0, x1, y1, u0, v0, u1, v1, color)
            return True
        except Exception:
            return False

    def _flush_instance_group(self):
        """Flush pending instance groups via host draw_instances."""
        try:
            grp = getattr(self, "_instance_group", None)
            if grp is None or grp.empty():
                return
            grp.flush(self)
        except Exception:
            pass

    def _flush_instance_fallback(self, pipeline, texture, _t1, _t2, datas):
        """Fallback for hermetic/lint when host lacks draw_instances: emit per-quad via _dm."""
        try:
            # datas is flat 12 floats per instance; need to recreate meshes
            n = len(datas) // 12
            for i in range(n):
                base = i * 12
                rox, roy, rsx, rsy, uox, voy, usx, vsy, cr, cg, cb, ca = datas[base:base+12]
                x0 = float(rox)
                y0 = float(roy)
                x1 = x0 + float(rsx)
                y1 = y0 + float(rsy)
                u0 = float(uox)
                v0 = float(voy)
                u1 = u0 + float(usx)
                v1 = v0 + float(vsy)
                color = (cr, cg, cb, ca)
                # Recreate mesh via _mesh_quad_ndc then _dm (fallback 1:1)
                try:
                    mesh = self._mesh_quad_ndc(x0, y0, x1, y1, color, u0, v0, u1, v1)
                    self._dm(pipeline, int(mesh), texture, None, None)
                except Exception:
                    continue
        except Exception:
            pass


    def _refresh_scale(self):
        """Keep draw_per_virt / virt↔draw matrices in sync with window size."""
        vw = max(1, int(self.virtual_size[0]))
        _ = max(1, int(self.virtual_size[1]))
        dw = max(1, int(self.drawable_size[0]))
        dh = max(1, int(self.drawable_size[1]))
        self.auto_mipmap = self.draw_per_virt < AUTO_MIPMAP_THRESH
        self.draw_per_virt = float(dw) / float(vw)
        try:
            from renpy.display import render

            self.virt_to_draw = render.Matrix2D(self.draw_per_virt, 0, 0, self.draw_per_virt)
            self.draw_to_virt = render.Matrix2D(1.0 / self.draw_per_virt, 0, 0, 1.0 / self.draw_per_virt)
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                            pass
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
            except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        # product full-HD after init (constructor default is 1280×720 only).
        # Do NOT hardcode constructor default for all games.
        try:
            if host_env_bool("RENPY_HOST_ASSERT_VIRTUAL"):
                import sys

                vw = int(virtual_size[0]) if virtual_size else 0
                vh = int(virtual_size[1]) if virtual_size else 0
                print(
                    f"AC2_VIRTUAL virtual_size=({vw}, {vh})",
                    file=sys.stderr,
                    flush=True,
                )
                if (vw, vh) != (GOLDEN_FALLBACK_W, GOLDEN_FALLBACK_H):
                    print(
                        f"AC2_WARN virtual_size=({vw}, {vh}) expected=({GOLDEN_FALLBACK_W}, {GOLDEN_FALLBACK_H}) "
                        f"for HuangmeiC full-bleed",
                        file=sys.stderr,
                        flush=True,
                    )
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
            width = min(width, MAX_TEX_W)
            height = min(height, MAX_TEX_H)

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
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass

            # Keep preferences.fullscreen honest if host rejected (optional).
            try:
                if hasattr(renpy_host, "is_fullscreen"):
                    live = bool(renpy_host.is_fullscreen())
                    # Only trust live after a settle; do not force-clear want_fs
                    # immediately (Wayland may apply asynchronously).
                    self.fullscreen = live if live == want_fs else want_fs
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass

        except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            try:
                _host_draw_fail("resize", e)
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
        return self.get_mouse_pos()

    def get_mouse_pos(self):
        try:
            from renpy import pygame

            x, y = pygame.mouse.get_pos()
            return self.translate_point(x, y)
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            return (0, 0)

    def set_mouse_pos(self, x, y):
        try:
            from renpy import pygame

            px, py = self.untranslate_point(x, y)
            pygame.mouse.set_pos((px, py))
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            return None

    def screenshot_rgba(self):
        """Raw (width, height, bytes) readback for golden harnesses."""
        try:
            import renpy_host  # type: ignore

            return renpy_host.read_game_rt_rgba()
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            return (0, 0, b"")

    def event_peek_sleep(self):
        return None

    def get_info(self) -> dict[str, Any]:
        return dict(self.info)
