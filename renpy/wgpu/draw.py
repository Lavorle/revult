"""
WgpuDraw — host renderer (Phase 2–5, Phase 8 model mesh).

Primary path: begin_frame → draw_model → end_frame_present.
Phase 5: RTT (create_render_texture + begin/end_target), screenshot via
read_game_rt_rgba, dissolve/blur/matrixcolor/mask helpers.
Phase 8: create_mesh upload + draw_model_mesh for assimp/procedural models.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import time as _time
from typing import Any, Optional, Sequence, Union

# Product present lock — created lazily so renpy.import_all module backup
# (pickle of module attrs) does not see a non-picklable RLock at import time.
# Host FFI locks per-call only; without process-side serialization a
# force-redraw thread and the interact loop can interleave end_frame_present
# and wipe a good game RT to arena clear (confirm_alone_2 residual).
_DRAW_SCREEN_LOCK = None  # type: Optional[threading.RLock]


def _draw_screen_lock() -> "threading.RLock":
    global _DRAW_SCREEN_LOCK
    lock = _DRAW_SCREEN_LOCK
    if lock is None:
        lock = threading.RLock()
        _DRAW_SCREEN_LOCK = lock
    return lock

# Log-once keys: (where, exception_type_name)
_HOST_DRAW_FAIL_LOGGED: set[tuple[str, str]] = set()

# Env-gated once-log keys for RENPY_HOST_UI_TRACE=1 (Phase 1 evidence matrix).
# Fixed keys only: alpha_zero, draw_text_exc, empty_upload, dead_present,
# reverse_branch, drop_bake_residual, arena_count, face_fallback.
_UI_TRACE_LOGGED: set[str] = set()

# Phase 0 dissolve / write_texture sample throttle (RENPY_HOST_PHASE0_SIGNALS).
_PHASE0_LAST_DISSOLVE_T: float = 0.0
_PHASE0_LAST_WRITE_T: float = 0.0
_PHASE0_LAST_FRAME_T: float = 0.0
_PHASE0_DISSOLVE_INTERVAL = 0.25  # seconds during mid-dissolve window
_PHASE0_WRITE_INTERVAL = 1.0  # seconds for write_texture_ms
_PHASE0_FRAME_INTERVAL = 0.5  # seconds for prepare/draw/present samples


def _phase0_signals_enabled() -> bool:
    return os.environ.get("RENPY_HOST_PHASE0_SIGNALS", "").strip() in ("1", "true", "yes")


def _phase0_log(msg: str) -> None:
    """stderr PHASE0_SIGNAL line; same format as renpysound_host._phase0_log."""
    if not _phase0_signals_enabled():
        return
    try:
        import sys

        print(
            f"PHASE0_SIGNAL t={_time.monotonic():.3f} {msg}",
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        pass


def _phase0_due_dissolve() -> bool:
    """True once per _PHASE0_DISSOLVE_INTERVAL while mid-dissolve samples."""
    global _PHASE0_LAST_DISSOLVE_T
    if not _phase0_signals_enabled():
        return False
    now = _time.monotonic()
    if (now - _PHASE0_LAST_DISSOLVE_T) < _PHASE0_DISSOLVE_INTERVAL:
        return False
    _PHASE0_LAST_DISSOLVE_T = now
    return True


def _phase0_due_write() -> bool:
    """True once per _PHASE0_WRITE_INTERVAL for write_texture_ms samples."""
    global _PHASE0_LAST_WRITE_T
    if not _phase0_signals_enabled():
        return False
    now = _time.monotonic()
    if (now - _PHASE0_LAST_WRITE_T) < _PHASE0_WRITE_INTERVAL:
        return False
    _PHASE0_LAST_WRITE_T = now
    return True


def _phase0_due_frame() -> bool:
    """True once per _PHASE0_FRAME_INTERVAL for prepare/draw frame samples."""
    global _PHASE0_LAST_FRAME_T
    if not _phase0_signals_enabled():
        return False
    now = _time.monotonic()
    if (now - _PHASE0_LAST_FRAME_T) < _PHASE0_FRAME_INTERVAL:
        return False
    _PHASE0_LAST_FRAME_T = now
    return True


def _safe_print(msg: str) -> None:
    """Write to real stdout — never ``sys.stdout`` after ``renpy.log`` redirect.

    Bare hermetic gates import ``WgpuDraw`` before ``renpy.config`` exists.
    Redirected ``print`` → ``renpy.log`` then raises ``AttributeError`` and
    aborts the frame (solid/frame gates read pure arena clear).
    """
    try:
        import renpy.log as _rlog  # type: ignore

        out = getattr(_rlog, "real_stdout", None) or sys.__stdout__
    except Exception:
        out = sys.__stdout__
    try:
        out.write(msg + "\n")
        out.flush()
    except Exception:
        try:
            sys.__stdout__.write(msg + "\n")
            sys.__stdout__.flush()
        except Exception:
            pass


def _ui_trace_once(key: str, msg: str) -> None:
    """Once-log under RENPY_HOST_UI_TRACE=1; keys fixed by plan (no spam)."""
    if os.environ.get("RENPY_HOST_UI_TRACE") != "1":
        return
    if key in _UI_TRACE_LOGGED:
        return
    _UI_TRACE_LOGGED.add(key)
    _safe_print(f"[UI_TRACE {key}] {msg}")


def _host_draw_fail(where: str, exc: BaseException) -> None:
    """Log a host-draw failure once per (where, type); optionally re-raise.

    When ``RENPY_HOST_DRAW_RAISE=1``, re-raises so CI / debug sessions surface
    the original traceback. Otherwise prints once and returns so the frame can
    continue with a typed placeholder.

    Always uses :func:`_safe_print` — never routes through ``renpy.log`` (needs
    full ``renpy.config`` and blows up bare host gates / early init).
    """
    key = (where, type(exc).__name__)
    if key not in _HOST_DRAW_FAIL_LOGGED:
        _HOST_DRAW_FAIL_LOGGED.add(key)
        msg = f"WgpuDraw.{where}: {type(exc).__name__}: {exc}"
        _safe_print(msg)
    if os.environ.get("RENPY_HOST_DRAW_RAISE") == "1":
        raise exc


def _surf_fingerprint(pixels: bytes, w: int, h: int) -> bytes:
    """Cheap content fingerprint: size + first/mid/last 64 RGBA bytes."""
    hsh = hashlib.blake2b(digest_size=8)
    hsh.update(w.to_bytes(4, "little"))
    hsh.update(h.to_bytes(4, "little"))
    hsh.update(pixels[:64])
    if pixels:
        mid = max(0, (len(pixels) // 2) - 32)
        hsh.update(pixels[mid : mid + 64])
        hsh.update(pixels[-64:])
    return hsh.digest()


class HostTexture:
    """
    Lightweight texture handle wrapper for product call sites that expect
    GLTexture-like objects (``.subsurface``, ``.get_size``, dimensions).

    `handle` is the GpuArena texture id returned by renpy_host.create_texture_rgba.
    """

    def __init__(self, handle: int, width: int, height: int, x: int = 0, y: int = 0, w: int | None = None, h: int | None = None):
        self.handle = int(handle)
        self.width = int(width)
        self.height = int(height)
        self.x = int(x)
        self.y = int(y)
        self.w = int(width if w is None else w)
        self.h = int(height if h is None else h)
        # Aliases used by various product paths
        self.texture = self.handle

    def get_size(self):
        return (self.w, self.h)

    def subsurface(self, rect):
        x, y, w, h = rect
        return HostTexture(
            self.handle,
            self.width,
            self.height,
            x=self.x + int(x),
            y=self.y + int(y),
            w=max(0, int(w)),
            h=max(0, int(h)),
        )

    def __int__(self):
        return self.handle

    def __index__(self):
        return self.handle


class WgpuDraw:
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

    def get_texture_size(self):
        free_n = sum(len(v) for v in self._rtt_free.values())
        live_n = len(self._rtt_prev_frame) + len(self._rtt_curr_frame)
        return (live_n + free_n, len(self.texture_cache))

    def _acquire_rtt(self, w, h):
        """Borrow or create a render texture of size (w, h); tracked for recycle.

        Bounds live RTTs per size to ``_rtt_pool_cap``. When the cap is hit
        mid-prepare (before any product present can freelist), **reuse** the
        oldest live handle of that size in place instead of allocating. This
        is required for HuangmeiC splash/dissolve: frames=1 for many seconds
        while nested mesh_bake thrash would otherwise allocate thousands of
        1920×1080 targets and OOM the process.
        """
        import renpy_host  # type: ignore

        w = max(1, int(w))
        h = max(1, int(h))
        # Feel residual H3: reverse-inflated RTT sizes (e.g. 2219x5567).
        # Cap strictly to live drawable / layout virtual. Never use temporary
        # mesh-bake virtual_size (it is set to the RTT size itself and would
        # pass reverse-inflated requests through).
        try:
            dw = max(1, int(self.drawable_size[0]))
            dh = max(1, int(self.drawable_size[1]))
            lv = getattr(self, "layout_virtual_size", None) or self.virtual_size
            lw = max(1, int(lv[0]))
            lh = max(1, int(lv[1]))
            # Strict ceiling: min(layout, drawable). Large windows must not pass
            # reverse-inflated RTT requests; small windows must not allocate
            # above the live surface.
            hard_w = min(lw, dw)
            hard_h = min(lh, dh)
            if hard_w < 1:
                hard_w = max(lw, dw, 1)
            if hard_h < 1:
                hard_h = max(lh, dh, 1)
            if w > hard_w:
                w = hard_w
            if h > hard_h:
                h = hard_h
        except Exception:
            w = min(w, 1920)
            h = min(h, 1080)
        key = (w, h)
        free = self._rtt_free.get(key)
        if free:
            handle = int(free.pop())
            self._rtt_curr_frame.append((handle, w, h))
            return handle

        live_same = [t for t in self._rtt_curr_frame if t[1] == w and t[2] == h]
        if len(live_same) >= self._rtt_pool_cap:
            # Reuse oldest live RTT of this size (overwrite). Safe for bake
            # targets that are re-drawn every call; concurrent unique content
            # is limited to _rtt_pool_cap simultaneous slots.
            old_handle, _, _ = live_same[0]
            # Rotate tracking: drop first occurrence, re-append as newest.
            dropped = False
            new_curr = []
            for t in self._rtt_curr_frame:
                if not dropped and t[0] == old_handle and t[1] == w and t[2] == h:
                    dropped = True
                    continue
                new_curr.append(t)
            new_curr.append((old_handle, w, h))
            self._rtt_curr_frame = new_curr
            return int(old_handle)

        handle = int(renpy_host.create_render_texture(w, h))
        self._rtt_curr_frame.append((handle, w, h))
        return handle

    def _release_rtt_now(self, handle, w=None, h=None):
        """Return a short-lived RTT (e.g. is_pixel_opaque) to the freelist/destroy."""
        if handle is None:
            return
        try:
            handle = int(handle)
        except (TypeError, ValueError):
            return
        if handle <= 0:
            return
        # Drop from curr/prev tracking if present (avoid double-free on recycle).
        self._rtt_curr_frame = [
            t for t in self._rtt_curr_frame if t[0] != handle
        ]
        self._rtt_prev_frame = [
            t for t in self._rtt_prev_frame if t[0] != handle
        ]
        key = None
        if w is not None and h is not None:
            key = (max(1, int(w)), max(1, int(h)))
        if key is not None:
            bucket = self._rtt_free.setdefault(key, [])
            if len(bucket) < self._rtt_pool_cap:
                bucket.append(handle)
                return
        try:
            import renpy_host  # type: ignore

            renpy_host.destroy_texture(handle)
        except Exception:
            pass

    def _recycle_frame_rtts(self):
        """End-of-frame: freelist previous frame RTTs; promote current → previous.

        Keeps RTTs alive for one extra frame so late same-frame / next-frame
        reads of a just-baked handle remain valid, then reuses or destroys.
        """
        try:
            import renpy_host  # type: ignore
        except Exception:
            renpy_host = None  # type: ignore

        for handle, w, h in self._rtt_prev_frame:
            key = (w, h)
            bucket = self._rtt_free.setdefault(key, [])
            if len(bucket) < self._rtt_pool_cap:
                bucket.append(handle)
            elif renpy_host is not None:
                try:
                    renpy_host.destroy_texture(handle)
                except Exception:
                    pass
        self._rtt_prev_frame = self._rtt_curr_frame
        self._rtt_curr_frame = []

    def _destroy_all_rtts(self):
        """Destroy freelist + tracked frame RTTs (kill_textures / resize)."""
        try:
            import renpy_host  # type: ignore
        except Exception:
            renpy_host = None  # type: ignore

        handles = []
        for bucket in self._rtt_free.values():
            handles.extend(bucket)
        handles.extend(h for h, _w, _h in self._rtt_prev_frame)
        handles.extend(h for h, _w, _h in self._rtt_curr_frame)
        self._rtt_free.clear()
        self._rtt_prev_frame = []
        self._rtt_curr_frame = []
        if renpy_host is None:
            return
        for handle in handles:
            try:
                renpy_host.destroy_texture(int(handle))
            except Exception:
                pass

    def _refresh_scale(self):
        """Keep draw_per_virt / virt↔draw matrices in sync with window size."""
        vw = max(1, int(self.virtual_size[0]))
        vh = max(1, int(self.virtual_size[1]))
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
                        lock = getattr(cache, "lock", None)
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

    # --- GL2-parity load_all_textures prepass ---------------------------------

    def _is_render_like(self, n):
        """True for Render-like nodes (have both children and mesh attrs)."""
        return hasattr(n, "children") and hasattr(n, "mesh")

    def _is_surface_like(self, n):
        """True for Surface-like pixel sources.

        Render also has get_size — callers must check ``_is_render_like`` first.
        """
        return hasattr(n, "get_size") and (
            hasattr(n, "_pixels")
            or hasattr(n, "get_buffer")
            or hasattr(n, "get_at")
        )

    def _is_imagedissolve_node(self, node):
        """True when a node is renpy.imagedissolve (3-tex control/bottom/top).

        Also matches product alias ``image_dissolve`` (HuangmeiC dissolve_transform).
        """
        if node is None:
            return False
        shaders = getattr(node, "shaders", None) or ()
        if any(
            s in ("renpy.imagedissolve", "imagedissolve", "image_dissolve")
            for s in shaders
        ):
            return True
        uniforms = getattr(node, "uniforms", None)
        if isinstance(uniforms, dict) and (
            "u_renpy_dissolve_offset" in uniforms
            or "u_renpy_dissolve_multiplier" in uniforms
            # Product alias uniforms (HuangmeiC image_dissolve).
            or ("u_transition" in uniforms and "u_animation" in uniforms)
        ):
            # ImageDissolve stamps offset/multiplier; plain Dissolve uses u_renpy_dissolve.
            if "u_renpy_dissolve" not in uniforms:
                return True
        op = getattr(node, "operation", None)
        if op in ("IMAGEDISSOLVE", "imagedissolve"):
            return True
        try:
            # renpy.display.render.IMAGEDISSOLVE == 2
            return int(op) == 2
        except (TypeError, ValueError):
            return False

    def _is_dissolve_node(self, node):
        """True when a Render/model should dual-draw as renpy.dissolve.

        Detects product Dissolve/Fade stamps: shader name, u_renpy_dissolve
        uniform, or operation_complete with DISSOLVE-like operation.
        ImageDissolve (3-tex) is excluded — handled by imagedissolve pipeline.
        """
        if node is None:
            return False
        if self._is_imagedissolve_node(node):
            return False
        shaders = getattr(node, "shaders", None) or ()
        if any(s in ("renpy.dissolve", "dissolve") for s in shaders):
            return True
        uniforms = getattr(node, "uniforms", None)
        if isinstance(uniforms, dict) and "u_renpy_dissolve" in uniforms:
            return True
        op_complete = getattr(node, "operation_complete", None)
        if op_complete is None:
            return False
        # operation may be an int enum (DISSOLVE=1 in render.pyx historically).
        op = getattr(node, "operation", None)
        if op is None:
            return False
        if op in ("DISSOLVE", "dissolve"):
            return True
        try:
            # renpy.display.render.DISSOLVE=1 (IMAGEDISSOLVE=2 handled above).
            return int(op) == 1
        except (TypeError, ValueError):
            return False

    def _dissolve_complete(self, node):
        """Return dissolve amount in [0,1], or None if not a dissolve amount.

        Priority:
          1. explicit ``u_renpy_dissolve`` uniform (authoritative mid-fade)
          2. ``operation_complete`` only when operation is DISSOLVE **and** the
             amount is clearly mid-range (0,1) — Render defaults complete=0.0
             on every node, so 0.0 alone is NOT "start of dissolve"
          3. shader-only ``renpy.dissolve`` shell (no uniform) → 1.0 (finished
             sticky shell; walk NEW/main_menu)

        AC-Idle: sticky renpy.dissolve with default complete=0 used to walk the
        empty OLD child → permanent arena clear while dock HostTextures lived
        under NEW.
        """
        if node is None:
            return None
        uniforms = getattr(node, "uniforms", None)
        if isinstance(uniforms, dict) and "u_renpy_dissolve" in uniforms:
            try:
                return max(0.0, min(1.0, float(uniforms["u_renpy_dissolve"])))
            except (TypeError, ValueError):
                return 1.0
        shaders = getattr(node, "shaders", None) or ()
        has_shader = any(s in ("renpy.dissolve", "dissolve") for s in shaders)
        op = getattr(node, "operation", None)
        op_c = getattr(node, "operation_complete", None)
        is_diss_op = False
        if op is not None:
            try:
                is_diss_op = op in ("DISSOLVE", "dissolve") or int(op) == 1
            except (TypeError, ValueError):
                is_diss_op = op in ("DISSOLVE", "dissolve")
        if is_diss_op and op_c is not None:
            try:
                c = float(op_c)
            except (TypeError, ValueError):
                c = None
            if c is not None:
                # Only trust mid-range amounts. Exact 0.0 is the Render default
                # and also "transition not started" — with a renpy.dissolve
                # shader still on the tree that almost always means a finished
                # sticky shell, not a real t=0 frame.
                if 0.001 < c < 0.999:
                    return max(0.0, min(1.0, c))
                if c >= 0.999:
                    return 1.0
                # c <= 0.001: fall through
        if has_shader:
            return 1.0
        return None

    def _reverse_axis_scale(self, node):
        """Return (xdx, ydy) for axis-aligned reverse, or None if not applicable.

        Used by Frame/Solid stretch and Text drawable oversample (``draw_to_virt``).
        """
        rev = getattr(node, "reverse", None)
        if rev is None:
            return None
        xdx = float(getattr(rev, "xdx", 1.0) or 1.0)
        ydy = float(getattr(rev, "ydy", 1.0) or 1.0)
        xdy = float(getattr(rev, "xdy", 0.0) or 0.0)
        ydx = float(getattr(rev, "ydx", 0.0) or 0.0)
        # Only axis-aligned scale; skip rotation/shear.
        if abs(xdy) > 1e-6 or abs(ydx) > 1e-6:
            return None
        return xdx, ydy

    def _node_needs_axis_scale(self, node, children):
        """True when node should apply reverse axis scale to children.

        Frame (imagelike) stamps ``reverse`` Matrix2D with non-identity xdx/ydy
        so source tiles fill dest border pieces. Solid uses the same path:
        ``solid_texture(10,10)`` + ``reverse = Matrix2D(W/10, 0, 0, H/10)``.

        Product Text stamps ``reverse = layout.reverse``:
        - ``draw_per_virt=1`` → IDENTITY (must never stretch typewriter mid-st)
        - ``draw_per_virt>1`` (maximize) → uniform ``1/oversample`` (drawable res)

        Both Frame/Solid and oversampled Text return True for non-identity reverse.
        Dest size must be ``child_size * |scale|`` (see draw path) — **not** always
        the full parent box. Stretching typewriter partials into the full text box
        after maximize reintroduced AC-T1 balloon glyphs.
        """
        sc = self._reverse_axis_scale(node)
        if sc is None:
            return False
        xdx, ydy = sc
        # Non-identity axis scale → apply reverse mapping.
        if abs(xdx - 1.0) > 1e-6 or abs(ydy - 1.0) > 1e-6:
            return True
        # Identity reverse: never stretch on size mismatch (typewriter mid-st).
        return False

    def _reverse_dest_size(self, node, child, parent_size):
        """Dest size for a reverse-scaled child.

        Product reverse contracts:

        1. **Uniform scale-down** (``|xdx|<1`` and ``|ydy|<1``) — Text / image
           drawable oversample (``reverse = 1/os``), and Transform fit cover
           when the child is larger than the dest box (e.g. HuangmeiC splash
           3840×2160 under ``full_fill`` → reverse 0.5 into 1920×1080):

           - **Full** HostTexture (entire atlas UV): dest = reverse node layout
             box (``parent_size``). Main-menu overlay is a 1280×720 PNG with only
             the left 280px opaque; it must still cover the full virtual canvas
             so the strip reaches the bottom. Mapping ``child*inv_os`` when the
             texture was not re-uploaded at physical res shrank it (~853×480)
             and left a hole — user "overlay incomplete after maximize".
             Same rule covers true 2× full images under cover/oversample: a
             full 3840×2160 HostTexture under reverse 0.5 fills parent 1920×1080
             (not child*scale→1920 if parent wrong, not 3840 double-draw).

           - **Subsurface** HostTexture (typewriter mid-st): dest =
             ``child_size * |scale|`` so partial glyphs stay partial.
             **Do not** collapse this branch into parent_size — that reintroduces
             AC-T balloon after maximize (worker-3 typewriter path depends on it).

        2. **Scale-up / layout fill** (Solid 10×10→dest, Frame pieces):
           dest = reverse node layout box (``parent_size``).
        """
        sc = self._reverse_axis_scale(node)
        if sc is None:
            return parent_size
        xdx, ydy = sc
        pw, ph = int(parent_size[0]), int(parent_size[1])
        # Prefer parent layout box for scale-up / unknown.
        if not (abs(xdx) < 1.0 - 1e-6 and abs(ydy) < 1.0 - 1e-6):
            if os.environ.get("RENPY_HOST_UI_TRACE") == "1" and "reverse_branch" not in _UI_TRACE_LOGGED:
                _ui_trace_once(
                    "reverse_branch",
                    f"branch=scale_up_or_fill pw={pw} ph={ph} xdx={xdx} ydy={ydy} dw={max(1, pw)} dh={max(1, ph)}",
                )
            return max(1, pw), max(1, ph)

        # Uniform scale-down (oversample).
        ht = None
        try:
            ht = self._resolve_texture_full(child)
        except Exception:
            ht = None
        if ht is not None and self._host_tex_is_full(ht):
            # Full image/text line texture → fill reverse node's virtual box.
            if os.environ.get("RENPY_HOST_UI_TRACE") == "1" and "reverse_branch" not in _UI_TRACE_LOGGED:
                _ui_trace_once(
                    "reverse_branch",
                    f"branch=full_oversample pw={pw} ph={ph} xdx={xdx} ydy={ydy} "
                    f"ht=({getattr(ht, 'w', '?')},{getattr(ht, 'h', '?')}) dw={max(1, pw)} dh={max(1, ph)}",
                )
            return max(1, pw), max(1, ph)

        cw, ch = self._node_size(child, default=(0, 0))
        if cw <= 0 or ch <= 0:
            if os.environ.get("RENPY_HOST_UI_TRACE") == "1" and "reverse_branch" not in _UI_TRACE_LOGGED:
                _ui_trace_once(
                    "reverse_branch",
                    f"branch=unknown_child_fallback pw={pw} ph={ph} xdx={xdx} ydy={ydy} cw={cw} ch={ch}",
                )
            return max(1, pw), max(1, ph)
        # Partial UV (typewriter): map drawable partial → virtual partial.
        dw = max(1, int(round(abs(float(cw) * float(xdx)))))
        dh = max(1, int(round(abs(float(ch) * float(ydy)))))
        if os.environ.get("RENPY_HOST_UI_TRACE") == "1" and "reverse_branch" not in _UI_TRACE_LOGGED:
            _ui_trace_once(
                "reverse_branch",
                f"branch=subsurface_partial pw={pw} ph={ph} xdx={xdx} ydy={ydy} "
                f"cw={cw} ch={ch} dw={dw} dh={dh}",
            )
        return dw, dh

    def _extract_host_texture(self, child, depth=0):
        """Pull a pure HostTexture leaf from a subsurface/image Render without RTT.

        Frame pieces often look like::

            reverse Render(dest)
              └─ subsurface Render(src)   # no cached_texture (Render.subsurface
                                         # does not copy it)
                   └─ HostTexture(uv)

        Pre-A1, falling through to ``render_to_texture`` baked those pieces into
        an opaque-clear RTT (black slabs under transparent idle/hover buttons).
        Post-A1 clear is transparent, but skipping RTT still preserves UV rects
        and avoids baking fully-transparent PNGs into intermediate targets.

        Returns None when the node is mesh, multi-child, reverse-scaled, or
        otherwise not a pure texture chain (caller may RTT).
        """
        if child is None or depth > 8:
            return None
        if isinstance(child, HostTexture):
            return child if child.handle > 0 else None
        if isinstance(child, int) and not isinstance(child, bool):
            return HostTexture(child, 1, 1) if child > 0 else None

        ct = getattr(child, "cached_texture", None)
        if isinstance(ct, HostTexture) and ct.handle > 0:
            return ct
        if isinstance(ct, int) and not isinstance(ct, bool) and ct > 0:
            w, h = self._node_size(child, default=(1, 1))
            return HostTexture(ct, max(1, w), max(1, h))

        if not self._is_render_like(child):
            return None
        # Mesh / reverse nodes need bake or axis-scale, not a bare leaf extract.
        if getattr(child, "mesh", None):
            return None
        if getattr(child, "reverse", None) is not None:
            return None
        # C3: never peel through xclipping/yclipping. Host crop+zoom builds
        #   reverse(zoom) → clip(crop band) → full child at negative offset
        # so axis-aligned mesh-crop can run without reverse-forward clip maps.
        # Peeling the clip wrapper returned the full HostTexture and dropped the
        # crop → text_config preview painted oversized / wrong band.
        if bool(getattr(child, "xclipping", False)) or bool(
            getattr(child, "yclipping", False)
        ):
            return None
        # Effect shaders (matrixcolor / blur / mask / product image_dissolve) must
        # bake via RTT. Peeling the HostTexture leaf would drop red→alpha control
        # for ImageDissolve and HuangmeiC dissolve_transform uniforms.
        shaders = getattr(child, "shaders", None) or ()
        if shaders:
            try:
                from renpy.wgpu.shaders import composition_mode

                if any(composition_mode(s) is None for s in shaders):
                    return None
            except Exception:
                return None
        uniforms = getattr(child, "uniforms", None)
        if isinstance(uniforms, dict) and (
            "u_renpy_matrixcolor" in uniforms
            or "u_renpy_blur_log2" in uniforms
            or "u_renpy_mask_multiplier" in uniforms
            or "u_renpy_mask_offset" in uniforms
            or "u_transition" in uniforms
            or "u_animation" in uniforms
        ):
            return None

        kids = list(self._iter_children(child))
        if len(kids) != 1:
            return None
        only, _xo, _yo = kids[0]
        return self._extract_host_texture(only, depth + 1)

    def _solid_reverse_slot_texture(self, child):
        """Solid reverse (10×10 + axis scale) → dissolve-slot HostTexture without RTT.

        Product Fade uses ``Solid`` as the mid widget: ``solid_texture(10,10)``
        blitted into a dest-sized Render with non-identity ``reverse``. Dissolve
        dual-draw sizes its mesh to the dissolve node, so a full-UV uniform leaf
        already fills the slot when sampled with 0–1 UVs.

        Returning that leaf skips ``render_to_texture`` of reverse Solid. Nested
        RTT under product Fade (hold_time=0 boundary, dual-draw both slots) is the
        residual that drops mid-black / flickers when the reverse bake misses or
        the transparent RTT clear is presented as the slot.

        Also peels one non-reverse single-child clip wrapper (Dissolve may
        ``subsurface`` a reverse Solid when sizes differ — outer has no reverse,
        inner is the Solid reverse Render).

        Guards (must not steal Frame multipiece reverse pieces):
          - single child at (0,0) on the reverse node
          - reverse is axis-scale (``_node_needs_axis_scale``)
          - leaf is a full-rect HostTexture (not UV subsurface)
        """
        if child is None or not self._is_render_like(child):
            return None
        # Already expanded / memoized this frame (Render is frame-local).
        memo = getattr(child, "_wgpu_solid_slot", None)
        if isinstance(memo, HostTexture) and memo.handle > 0:
            return memo

        # Locate the reverse Solid node: either this child, or one non-mesh
        # single-child clip wrapper whose only child is the reverse Solid.
        solid_node = child
        kids = list(self._iter_children(solid_node))
        if not self._node_needs_axis_scale(solid_node, kids):
            # Peel one pure clip wrapper (Dissolve subsurface of reverse Solid).
            if (
                len(kids) == 1
                and not getattr(solid_node, "mesh", None)
                and getattr(solid_node, "reverse", None) is None
            ):
                only, xo, yo = kids[0]
                try:
                    if abs(float(xo)) > 1e-3 or abs(float(yo)) > 1e-3:
                        return None
                except (TypeError, ValueError):
                    return None
                if not self._is_render_like(only):
                    return None
                solid_node = only
                kids = list(self._iter_children(solid_node))
            else:
                return None
            if not self._node_needs_axis_scale(solid_node, kids):
                return None

        if len(kids) != 1:
            return None
        only, xo, yo = kids[0]
        try:
            if abs(float(xo)) > 1e-3 or abs(float(yo)) > 1e-3:
                return None
        except (TypeError, ValueError):
            return None
        # Direct HostTexture (product Solid) or one pure non-reverse wrapper.
        if isinstance(only, HostTexture):
            leaf = only if only.handle > 0 else None
        else:
            # Do not walk through another reverse (nested scale is not Solid).
            if self._is_render_like(only) and getattr(only, "reverse", None) is not None:
                return None
            leaf = self._extract_host_texture(only)
        if leaf is None or leaf.handle <= 0:
            return None
        # Frame edge/corner pieces are UV subsurfaces — never treat as Solid.
        if not self._host_tex_is_full(leaf):
            return None
        nw, nh = self._node_size(child, default=(0, 0))
        if nw <= 0 or nh <= 0:
            return None
        # Dual-draw / textured mesh uses parent NDC size, not leaf.w/h.
        # Full-UV sample of a uniform solid leaf fills the dissolve slot.
        try:
            child._wgpu_solid_slot = leaf  # type: ignore[attr-defined]
        except Exception:
            pass
        return leaf

    def _child_to_texture(self, child):
        """Convert a mesh-child to HostTexture for model texture slots.

        Order: HostTexture as-is → cached_texture → pure single-child HostTexture
        chain (Frame subsurface, no RTT) → Solid reverse leaf (no RTT) →
        Surface-like upload → else ``render_to_texture`` wrapped as HostTexture.
        """
        if child is None:
            return None
        if isinstance(child, HostTexture):
            return child if child.handle > 0 else None
        if isinstance(child, int) and not isinstance(child, bool):
            return HostTexture(child, 1, 1) if child > 0 else None

        # Render-like first (Render may also expose get_size).
        if self._is_render_like(child):
            ct = getattr(child, "cached_texture", None)
            if isinstance(ct, HostTexture) and ct.handle > 0:
                return ct
            if isinstance(ct, int) and not isinstance(ct, bool) and ct > 0:
                w, h = self._node_size(child)
                return HostTexture(ct, w, h)
            # Frame/image subsurface pieces: prefer HostTexture UV leaf over RTT.
            # Transparent idle/hover PNGs must not be baked into intermediate
            # targets when a direct textured draw would contribute nothing.
            pure = self._extract_host_texture(child)
            if pure is not None:
                return pure
            # Product Solid under Fade/Dissolve: reverse axis-scale + full leaf.
            # Prefer leaf over RTT so dual-draw mid-black does not depend on nested
            # reverse bake (transparent RTT clear = missing black mid / flicker).
            solid_slot = self._solid_reverse_slot_texture(child)
            if solid_slot is not None:
                return solid_slot
            # Ensure prepared before RTT. Outer load_all_textures may have marked
            # ancestors loaded without fully folding this child's mesh subtree
            # under the child virtual size; render_to_texture also prepares.
            if not getattr(child, "loaded", False):
                try:
                    self.load_all_textures(child)
                except Exception as e:
                    _host_draw_fail("child_to_texture.load_all_textures", e)
                # Prepare may have produced cached_texture / HostTexture children.
                pure = self._extract_host_texture(child)
                if pure is not None:
                    return pure
                solid_slot = self._solid_reverse_slot_texture(child)
                if solid_slot is not None:
                    return solid_slot
                ct = getattr(child, "cached_texture", None)
                if isinstance(ct, HostTexture) and ct.handle > 0:
                    return ct
            handle = self.render_to_texture(child)
            ht = None
            if isinstance(handle, HostTexture):
                ht = handle if handle.handle > 0 else None
            elif isinstance(handle, int) and not isinstance(handle, bool) and handle > 0:
                w, h = self._node_size(child)
                ht = HostTexture(handle, w, h)
            # Cache dissolve-slot RTTs so product Fade dual-draw does not re-nest
            # begin_frame/end_frame_present every interact frame (live flicker residual).
            if ht is not None:
                try:
                    child.cached_texture = ht
                except Exception:
                    pass
            return ht

        # Pure surface leaf → upload (fingerprint-cached).
        if self._is_surface_like(child):
            tex = self.load_texture(child)
            if isinstance(tex, HostTexture):
                return tex if tex.handle > 0 else None
            if isinstance(tex, int) and not isinstance(tex, bool) and tex > 0:
                try:
                    w, h = child.get_size()
                    return HostTexture(tex, max(1, int(w)), max(1, int(h)))
                except Exception:
                    return HostTexture(tex, 1, 1)
            return None

        # Fallback: surface-ish get_size without pixel attrs, or other drawables.
        if hasattr(child, "get_size") or hasattr(child, "_pixels"):
            try:
                tex = self.load_texture(child)
                if isinstance(tex, HostTexture):
                    return tex if tex.handle > 0 else None
                if isinstance(tex, int) and not isinstance(tex, bool) and tex > 0:
                    try:
                        w, h = child.get_size()
                        return HostTexture(tex, max(1, int(w)), max(1, int(h)))
                    except Exception:
                        return HostTexture(tex, 1, 1)
            except Exception:
                pass
        try:
            handle = self.render_to_texture(child)
            ht = None
            if isinstance(handle, HostTexture):
                ht = handle if handle.handle > 0 else None
            elif isinstance(handle, int) and not isinstance(handle, bool) and handle > 0:
                w, h = self._node_size(child)
                ht = HostTexture(handle, w, h)
            if ht is not None and self._is_render_like(child):
                try:
                    child.cached_texture = ht
                except Exception:
                    pass
            return ht
        except Exception:
            pass
        return None

    def _make_model_leaf(self, w, h, textures, mesh_obj=None, shaders=None, uniforms=None):
        """Lightweight model leaf for prepared ``cached_model`` (GL2Model stand-in).

        ``texture``/``textures`` hold HostTexture slots; mesh is True or the
        original mesh object when it is not the boolean True flag.

        ``shaders`` / ``uniforms`` are copied from the source Render so dissolve
        and other multi-tex model ops survive the prepare prepass.
        """
        class _ModelLeaf:
            pass

        leaf = _ModelLeaf()
        leaf.width = int(w)
        leaf.height = int(h)
        leaf.texture = textures[0] if textures else None
        leaf.textures = list(textures) if textures else None
        leaf.mesh = True if mesh_obj is None else mesh_obj
        if shaders:
            leaf.shaders = tuple(shaders)
        else:
            leaf.shaders = ("renpy.texture",) if textures else ("renpy.solid",)
        leaf.color = None
        leaf.vertices = None
        leaf.indices = None
        leaf.pipeline = None
        leaf.uniforms = uniforms
        leaf.ndc = None
        leaf.children = None
        leaf.cached_model = None
        leaf.blits = None
        return leaf

    def load_all_textures(self, what, reverse=None):
        """GL2-parity prepass: Surfaces→HostTexture; mesh Renders→cached_model.

        Mirrors ``GL2Draw.load_all_textures`` altitude: upload surface leaves,
        recurse children, and for mesh-truthy Renders build a drawable model
        whose texture slots are child HostTextures / RTTs.

        ``reverse`` is accepted for call-site parity (matrix stack); unused MVP.
        Frame-local: ``_invalidate_prepared`` clears ``loaded``/``cached_model``.

        Top-level prepare must not leave host ``in_frame`` open. Nested RTT
        bake during prepare can orphan a stuck frame; the next product
        ``draw_screen`` then nests and ``end_frame_present`` drops cmds
        (confirm arena-clear residual after flowchart mesh thrash).

        Top-level prepare also takes the product draw lock (reentrant RLock) so
        a force-redraw thread cannot nest RTT begin_frame into a concurrent
        interact ``draw_screen`` (confirm thrash residual companion).
        """
        # Top-level entry tracking: only the outermost call cleans frame state.
        top = not getattr(self, "_prepare_depth", 0)
        lock = None
        if top:
            lock = _draw_screen_lock()
            if not lock.acquire(blocking=True, timeout=30.0):
                _host_draw_fail(
                    "load_all_textures",
                    RuntimeError("draw_screen lock timeout (prepare)"),
                )
                return
        try:
            if top:
                self._prepare_depth = 1
                # Snapshot: if we entered prepare outside a product frame, we must
                # exit outside a product frame (drain orphan RTT nests).
                self._prepare_entered_in_frame = False
                try:
                    import renpy_host  # type: ignore

                    if hasattr(renpy_host, "in_frame"):
                        self._prepare_entered_in_frame = bool(renpy_host.in_frame())
                except Exception:
                    self._prepare_entered_in_frame = False
            else:
                self._prepare_depth = int(getattr(self, "_prepare_depth", 0) or 0) + 1
            try:
                self._load_all_textures_inner(what, reverse)
            finally:
                self._prepare_depth = int(getattr(self, "_prepare_depth", 1) or 1) - 1
                if self._prepare_depth <= 0:
                    self._prepare_depth = 0
                    # Drain orphan frames only when prepare was entered idle.
                    if not getattr(self, "_prepare_entered_in_frame", False):
                        try:
                            import renpy_host  # type: ignore

                            stuck = False
                            if hasattr(renpy_host, "in_frame"):
                                stuck = bool(renpy_host.in_frame())
                            if stuck:
                                self._recover_frame_state()
                        except Exception:
                            pass
        finally:
            if lock is not None:
                try:
                    lock.release()
                except Exception:
                    pass

    def _load_all_textures_inner(self, what, reverse=None):
        """Inner prepare walk (see ``load_all_textures`` for top-level guard)."""
        if what is None:
            return
        if isinstance(what, HostTexture):
            # Class (b) dead_present: surftree-held HostTextures never re-enter
            # load_texture. Revive + LRU-touch during prepare so thrash eviction
            # prefers idle uploads over confirm/preferences chrome still on tree.
            try:
                self._ensure_host_texture_alive(what)
            except Exception as e:
                _host_draw_fail("prepare.ensure_host_texture", e)
            return
        if isinstance(what, int) and not isinstance(what, bool):
            # Bare handle: best-effort LRU touch so thrash does not kill it.
            try:
                import renpy_host  # type: ignore

                if what > 0 and hasattr(renpy_host, "touch_texture"):
                    if (not hasattr(renpy_host, "texture_alive")) or renpy_host.texture_alive(
                        int(what)
                    ):
                        renpy_host.touch_texture(int(what))
            except Exception:
                pass
            return

        # Surface-like (not Render): upload so parents can use texture.
        if self._is_surface_like(what) and not self._is_render_like(what):
            self.load_texture(what)
            return

        # Image-cache Render: ``cached_texture`` is the live chrome HostTexture
        # even when children are empty. Always revive/touch it — class (b) thrash
        # after flowchart leaves dead handles on im.cache entries that never
        # re-enter load_texture (confirm_alone_2 residual).
        try:
            ct = getattr(what, "cached_texture", None)
            if isinstance(ct, HostTexture):
                self._ensure_host_texture_alive(ct)
            elif isinstance(ct, int) and not isinstance(ct, bool) and ct > 0:
                import renpy_host  # type: ignore

                if hasattr(renpy_host, "touch_texture"):
                    if (not hasattr(renpy_host, "texture_alive")) or renpy_host.texture_alive(
                        int(ct)
                    ):
                        renpy_host.touch_texture(int(ct))
                    else:
                        # Dead bare handle on cached_texture: wrap + revive if stash.
                        ht = HostTexture(int(ct), 1, 1)
                        self._ensure_host_texture_alive(ht)
                        if getattr(ht, "handle", 0) > 0 and int(ht.handle) != int(ct):
                            try:
                                what.cached_texture = ht
                            except Exception:
                                pass
        except Exception as e:
            _host_draw_fail("prepare.cached_texture", e)

        # Also revive prepared multi-slot model textures (dissolve/imagedissolve).
        try:
            cm = getattr(what, "cached_model", None)
            if cm is not None:
                texs = getattr(cm, "textures", None) or ()
                if isinstance(texs, (list, tuple)):
                    for t in texs:
                        if isinstance(t, HostTexture):
                            self._ensure_host_texture_alive(t)
                raw = getattr(cm, "texture", None)
                if isinstance(raw, HostTexture):
                    self._ensure_host_texture_alive(raw)
        except Exception:
            pass

        # Already prepared this frame (also guards prepare↔RTT recursion).
        # Still walk children so HostTexture leaves get dead_present revive /
        # LRU touch under thrash even when ancestors stay ``loaded=True``.
        if getattr(what, "loaded", False):
            try:
                for child, _xo, _yo in self._iter_children(what):
                    self._load_all_textures_inner(child, reverse)
            except Exception:
                pass
            return
        if hasattr(what, "loaded"):
            what.loaded = True

        children = list(self._iter_children(what))
        for child, _xo, _yo in children:
            self._load_all_textures_inner(child, reverse)

        mesh = getattr(what, "mesh", None)
        if mesh:
            # mesh=True leaf with no children: nothing to bake into a model.
            if mesh is True and not children:
                return
            # Multi-child boolean mesh without dissolve/imagedissolve must NOT
            # collapse into one cached_model. _make_model_leaf + _draw_model_like
            # only emit textures[0] as a single parent-sized quad and drop every
            # sibling — that is the AC-Idle dock wipe class (logo/dock buttons
            # prepared as HostTexture leaves but never committed to the game RT
            # when a parent mesh layer ate them). Leave children for pure walk /
            # reverse axis-scale paths (GL2 draws multi-blit mesh geometry; we
            # approximate by walking).
            if (
                (mesh is True or mesh == "quad")
                and len(children) > 1
                and not self._is_dissolve_node(what)
                and not self._is_imagedissolve_node(what)
            ):
                return
            # Multi-child renpy.dissolve: NEVER RTT-collapse into cached_model.
            # Product enter/splash dissolve often sticks mid-frame with empty OLD
            # + content-rich NEW (main_menu dock). Collapsing via _child_to_texture
            # loses nested HostTextures and paints near-clear (AC-Idle). Draw path
            # full-walks children / prefers NEW when OLD is sparse.
            if (
                (mesh is True or mesh == "quad")
                and len(children) >= 2
                and self._is_dissolve_node(what)
                and not self._is_imagedissolve_node(what)
            ):
                return
            textures = []
            for child, _xo, _yo in children:
                # Prefer already-HostTexture children; avoid RTT when possible.
                # Keep ALL child textures (dissolve needs bottom + top slots).
                tex = self._child_to_texture(child)
                if tex is not None:
                    textures.append(tex)
            # Dissolve dual-draw needs both slots; ImageDissolve needs three
            # (control, bottom, top). Retry missing slots before degraded model.
            need_slots = 0
            if self._is_imagedissolve_node(what):
                need_slots = 3
            elif self._is_dissolve_node(what):
                need_slots = 2
            if need_slots and len(children) >= need_slots and len(textures) < need_slots:
                textures = []
                for child, _xo, _yo in children:
                    tex = self._child_to_texture(child)
                    if tex is not None:
                        textures.append(tex)
            if not textures and mesh is True:
                return
            w, h = self._node_size(what)
            shaders = getattr(what, "shaders", None)
            uniforms = getattr(what, "uniforms", None)
            # Fold dissolve amount via _dissolve_complete (never stamp the
            # Render default operation_complete=0.0 as a real mid-dissolve).
            if self._is_dissolve_node(what):
                complete = self._dissolve_complete(what)
                if complete is not None:
                    if isinstance(uniforms, dict):
                        if "u_renpy_dissolve" not in uniforms:
                            uniforms = dict(uniforms)
                            uniforms["u_renpy_dissolve"] = float(complete)
                    elif uniforms is None:
                        uniforms = {"u_renpy_dissolve": float(complete)}
            model = self._make_model_leaf(
                w,
                h,
                textures,
                mesh if mesh is not True else None,
                shaders=shaders,
                uniforms=uniforms,
            )
            # Stash origin so dual-draw / 3-tex can recover missing slots from children
            # when prepare still produced a degraded cached_model (H4).
            if (
                (self._is_dissolve_node(what) or self._is_imagedissolve_node(what))
                and model is not None
            ):
                try:
                    model._dissolve_origin = what
                except Exception:
                    pass
            what.cached_model = model

    def _invalidate_prepared(self, what):
        """Clear frame-local ``loaded`` / ``cached_model`` so hover re-prepares."""
        if what is None:
            return
        if isinstance(what, (HostTexture, int, float, bool, str, bytes, bytearray)):
            return
        if hasattr(what, "loaded"):
            try:
                what.loaded = False
            except Exception:
                pass
        if hasattr(what, "cached_model"):
            try:
                what.cached_model = None
            except Exception:
                pass
        # Frame-local solid reverse dissolve-slot memo (Agent A Fade path).
        if hasattr(what, "_wgpu_solid_slot"):
            try:
                what._wgpu_solid_slot = None
            except Exception:
                pass
        # Do NOT clear cached_texture here: product image cache and dissolve-slot
        # RTTs reuse HostTextures across frames. Solid reverse memo is enough for
        # hover; full texture kill is kill_textures / fingerprint re-upload.
        try:
            for child, _xo, _yo in self._iter_children(what):
                self._invalidate_prepared(child)
        except Exception:
            pass

    # --- Tree walk (duck-typed Render / Model / Surface) ---------------------

    def _virt_rect_to_ndc(self, x, y, w, h):
        """Virtual-pixel top-left rect → NDC axis-aligned (x0,y0,x1,y1)."""
        vw = float(self.virtual_size[0]) or 1.0
        vh = float(self.virtual_size[1]) or 1.0
        x0 = 2.0 * float(x) / vw - 1.0
        x1 = 2.0 * (float(x) + float(w)) / vw - 1.0
        # Virtual y grows downward; NDC y grows upward.
        y1 = 1.0 - 2.0 * float(y) / vh
        y0 = 1.0 - 2.0 * (float(y) + float(h)) / vh
        return x0, y0, x1, y1

    # --- Axis-aligned clip stack (GL2 mesh-crop parity, v1) --------------------
    # GL2 (gl2draw.pyx:1661–1673): when r.xclipping/yclipping, push a local
    # polygon and intersect with the current clip; empty → skip. Mesh is then
    # cropped against clip_polygon (1552–1557) before draw. Wgpu v1 mirrors this
    # with an absolute virtual-pixel AABB stack; no GPU set_scissor.
    # Residual (not in v1): reverse-transformed clip rects — GL2 multiplies the
    # clip polygon by r.forward when has_reverse (1745–1746). Axis-aligned only.

    _CLIP_BIG = 65536.0  # GL2 BIG_PIXELS — unbounded axis when only one flag set.

    def _clip_intersect(self, a, b):
        """Intersect two AABBs (x0,y0,x1,y1). None with anything → other; empty → None."""
        if a is None:
            return b
        if b is None:
            return a
        x0 = max(float(a[0]), float(b[0]))
        y0 = max(float(a[1]), float(b[1]))
        x1 = min(float(a[2]), float(b[2]))
        y1 = min(float(a[3]), float(b[3]))
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1, y1)

    def _clip_push_from_node(self, node, ox, oy):
        """
        If node has xclipping/yclipping, return (new_clip, empty).

        new_clip is the absolute virtual-pixel AABB to install for children /
        self-draw; empty is True when the intersect is vacant (caller must skip).
        When the node does not clip, returns (current, False) unchanged.

        Local clip box (GL2 parity):
          x: [0, width] if xclipping else [-BIG, +BIG]
          y: [0, height] if yclipping else [-BIG, +BIG]
        then shifted by (ox, oy) into absolute virtual coords and intersected
        with ``self._clip_rect``.

        v1 residual: when node.reverse is non-identity, GL2 forward-maps the
        clip polygon; we do **not**. Documented residual — no half-implement.
        """
        xclip = bool(getattr(node, "xclipping", False))
        yclip = bool(getattr(node, "yclipping", False))
        if not xclip and not yclip:
            return self._clip_rect, False

        big = self._CLIP_BIG
        try:
            nw = float(getattr(node, "width", 0) or getattr(node, "w", 0) or 0)
            nh = float(getattr(node, "height", 0) or getattr(node, "h", 0) or 0)
        except Exception:
            nw = nh = 0.0
        if nw <= 0 or nh <= 0:
            try:
                if hasattr(node, "get_size"):
                    gw, gh = node.get_size()
                    if nw <= 0:
                        nw = float(gw or 0)
                    if nh <= 0:
                        nh = float(gh or 0)
            except Exception:
                pass
        if nw <= 0:
            nw = big if not xclip else 0.0
        if nh <= 0:
            nh = big if not yclip else 0.0

        lx0 = 0.0 if xclip else -big
        ly0 = 0.0 if yclip else -big
        lx1 = float(nw) if xclip else big
        ly1 = float(nh) if yclip else big
        # Absolute virtual-pixel box at the node's draw offset.
        local = (
            float(ox) + lx0,
            float(oy) + ly0,
            float(ox) + lx1,
            float(oy) + ly1,
        )
        inter = self._clip_intersect(self._clip_rect, local)
        if inter is None:
            return None, True
        return inter, False

    def _crop_virt_quad_uv(self, ox, oy, w, h, u0, v_bottom, u1, v_top):
        """
        Crop a virtual-pixel dest rect against ``self._clip_rect`` and remap UVs.

        Returns (x, y, cw, ch, u0', v_bottom', u1', v_top') or None if empty.

        UV convention matches ``_mesh_quad_ndc``: v_bottom is the larger image-v
        (bottom of virtual rect), v_top is the smaller image-v (top of virtual).
        Cropping the top edge increases oy and moves toward v_top; cropping the
        bottom edge decreases height and moves toward v_bottom.
        """
        if w <= 0 or h <= 0:
            return None
        x0 = float(ox)
        y0 = float(oy)
        x1 = float(ox) + float(w)
        y1 = float(oy) + float(h)
        clip = self._clip_rect
        if clip is None:
            return (x0, y0, float(w), float(h), float(u0), float(v_bottom), float(u1), float(v_top))

        cx0, cy0, cx1, cy1 = clip
        ix0 = max(x0, float(cx0))
        iy0 = max(y0, float(cy0))
        ix1 = min(x1, float(cx1))
        iy1 = min(y1, float(cy1))
        if ix1 <= ix0 or iy1 <= iy0:
            return None

        span_x = x1 - x0
        span_y = y1 - y0
        if span_x <= 0.0 or span_y <= 0.0:
            return None

        # Fractional edges of the crop inside the original dest rect.
        fx0 = (ix0 - x0) / span_x
        fx1 = (ix1 - x0) / span_x
        fy0 = (iy0 - y0) / span_y  # top edge (virtual y down)
        fy1 = (iy1 - y0) / span_y  # bottom edge

        u0f = float(u0)
        u1f = float(u1)
        vb = float(v_bottom)
        vt = float(v_top)
        # u interpolates left→right; v interpolates top(vt)→bottom(vb).
        nu0 = u0f + (u1f - u0f) * fx0
        nu1 = u0f + (u1f - u0f) * fx1
        nvt = vt + (vb - vt) * fy0
        nvb = vt + (vb - vt) * fy1
        return (ix0, iy0, ix1 - ix0, iy1 - iy0, nu0, nvb, nu1, nvt)

    def _remap_uv_frac(self, u0, v_bottom, u1, v_top, frac):
        """Remap UV by dest-crop fractions (fx0, fy0, fx1, fy1) from _crop_virt_quad_uv."""
        if frac is None:
            return float(u0), float(v_bottom), float(u1), float(v_top)
        fx0, fy0, fx1, fy1 = frac
        u0f = float(u0)
        u1f = float(u1)
        vb = float(v_bottom)
        vt = float(v_top)
        nu0 = u0f + (u1f - u0f) * float(fx0)
        nu1 = u0f + (u1f - u0f) * float(fx1)
        nvt = vt + (vb - vt) * float(fy0)
        nvb = vt + (vb - vt) * float(fy1)
        return nu0, nvb, nu1, nvt

    def _mesh_quad_ndc(
        self,
        x0=-1.0,
        y0=-1.0,
        x1=1.0,
        y1=1.0,
        color=(1.0, 1.0, 1.0, 1.0),
        u0=0.0,
        v0=1.0,
        u1=1.0,
        v1=0.0,
    ):
        """Upload an axis-aligned NDC quad (pos.xy, uv.xy, color.rgba).

        UV convention (full texture default): bottom v=1, top v=0 so virtual-y
        down maps to image-y down when the host samples with top-left origin.
        Subsurface callers pass u0/u1/v0/v1 covering only the rect.

        Results are cached by quantized geometry so identical quads (common for
        full-screen and button rects) reuse one mesh handle instead of leaking
        create_mesh allocations every draw.
        """
        import renpy_host  # type: ignore

        r, g, b, a = (list(color) + [1.0, 1.0, 1.0, 1.0])[:4]

        def _q(v, n=3):
            try:
                return round(float(v), n)
            except (TypeError, ValueError):
                return 0.0

        # Coarse color quantization: dissolve dual-draw animates vertex alpha
        # every frame; fine color keys would thrash create_mesh unbounded.
        key = (
            _q(x0),
            _q(y0),
            _q(x1),
            _q(y1),
            _q(u0),
            _q(v0),
            _q(u1),
            _q(v1),
            _q(r, 2),
            _q(g, 2),
            _q(b, 2),
            _q(a, 1),  # alpha to 0.1 steps
        )
        cached = self._mesh_cache.get(key)
        if cached is not None:
            # Host mesh FIFO can destroy handles while Python still caches them
            # (no mid-frame pin before touch_mesh/pin fix). Prefer alive reuse;
            # drop dead entries so we recreate rather than draw a missing mesh
            # id (encode_pass skip → prefs hover chrome holes).
            alive = True
            try:
                probe = getattr(renpy_host, "mesh_alive", None)
                if probe is not None:
                    try:
                        alive = bool(probe(int(cached)))
                    except Exception:
                        # Probe exists but failed — treat as dead and recreate
                        # rather than risk encode_pass skip of a bad handle.
                        alive = False
            except Exception:
                alive = True  # binding absent / import path
            if alive:
                try:
                    touch = getattr(renpy_host, "touch_mesh", None)
                    if touch is not None:
                        touch(int(cached))
                except Exception:
                    pass
                return cached
            try:
                del self._mesh_cache[key]
            except Exception:
                self._mesh_cache.pop(key, None)

        verts = [
            float(x0), float(y0), float(u0), float(v0), float(r), float(g), float(b), float(a),
            float(x1), float(y0), float(u1), float(v0), float(r), float(g), float(b), float(a),
            float(x1), float(y1), float(u1), float(v1), float(r), float(g), float(b), float(a),
            float(x0), float(y1), float(u0), float(v1), float(r), float(g), float(b), float(a),
        ]
        handle = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
        # Cap cache: drop arbitrary old entries. Do NOT destroy_mesh mid-frame —
        # the handle may already be in host frame_cmds from an earlier leaf in
        # this same draw_screen walk (dialog_config dense tree residual). Queue
        # for destroy after end_frame_present instead.
        if len(self._mesh_cache) >= self._mesh_cache_cap:
            try:
                old_key, old_h = next(iter(self._mesh_cache.items()))
                del self._mesh_cache[old_key]
                try:
                    pending = self._mesh_deferred_destroy
                except AttributeError:
                    pending = []
                    self._mesh_deferred_destroy = pending
                pending.append(int(old_h))
            except Exception:
                # If eviction fails, still drop a key so the Python map stops
                # growing; arena residual is bounded by cap churn + deferred flush.
                if self._mesh_cache:
                    self._mesh_cache.pop(next(iter(self._mesh_cache)), None)
        self._mesh_cache[key] = handle
        return handle

    def _flush_deferred_meshes(self):
        """Destroy mesh handles queued during mid-frame cache eviction.

        Must run only after ``end_frame_present`` has drained ``frame_cmds``;
        earlier destroy would make encode_pass skip draw cmds that still hold
        those mesh ids (dense dialog_config panel residual).
        """
        try:
            pending = self._mesh_deferred_destroy
        except AttributeError:
            self._mesh_deferred_destroy = []
            return
        if not pending:
            return
        self._mesh_deferred_destroy = []
        try:
            import renpy_host  # type: ignore

            destroy = getattr(renpy_host, "destroy_mesh", None)
            if destroy is None:
                return
            for h in pending:
                try:
                    destroy(int(h))
                except Exception:
                    pass
        except Exception:
            pass

    def _host_tex_uv(self, ht: HostTexture):
        """Mesh UVs (u0, v_bottom, u1, v_top) for a HostTexture sub-rect.

        Full texture (x,y,w,h)=(0,0,width,height) → (0, 1, 1, 0).
        """
        pw = float(ht.width) or 1.0
        ph = float(ht.height) or 1.0
        u0 = float(ht.x) / pw
        u1 = float(ht.x + ht.w) / pw
        # Bottom of image rect → higher v (matches full-quad v0=1).
        v_bottom = float(ht.y + ht.h) / ph
        v_top = float(ht.y) / ph
        return u0, v_bottom, u1, v_top

    def _host_tex_is_full(self, ht: HostTexture) -> bool:
        return (
            ht.x == 0
            and ht.y == 0
            and ht.w == ht.width
            and ht.h == ht.height
        )

    def _resolve_texture(self, tex):
        """Normalize texture-like values to a host handle (int) or None."""
        ht = self._resolve_texture_full(tex)
        return ht.handle if ht is not None else None

    def _resolve_texture_full(self, tex):
        """Normalize texture-like values to HostTexture or None (keeps UV rect).

        Also runs present-path dead-handle recovery (class b) so surftree-held
        HostTextures whose GPU slots were destroyed still draw when pixel stash
        has the full-texture RGBA. Does not re-enter load_texture cache-miss.
        """
        if tex is None:
            return None
        if isinstance(tex, HostTexture):
            if tex.handle <= 0:
                return None
            return self._ensure_host_texture_alive(tex)
        if isinstance(tex, int) and not isinstance(tex, bool):
            # Bare handle: unknown size; HostTexture(1x1 full) → UV 0–1.
            if tex <= 0:
                return None
            ht = HostTexture(tex, 1, 1)
            return self._ensure_host_texture_alive(ht)
        # Nested object with .handle / .texture
        for attr in ("handle", "texture"):
            inner = getattr(tex, attr, None)
            if isinstance(inner, HostTexture):
                if inner.handle <= 0:
                    return None
                return self._ensure_host_texture_alive(inner)
            if isinstance(inner, int) and not isinstance(inner, bool) and inner > 0:
                ht = HostTexture(inner, 1, 1)
                return self._ensure_host_texture_alive(ht)
        # Surface-like → upload (HostTexture or raw handle)
        if hasattr(tex, "get_size") or hasattr(tex, "_pixels"):
            h = self.load_texture(tex, transient=True)
            if isinstance(h, HostTexture):
                return h if h.handle > 0 else None
            if isinstance(h, int) and not isinstance(h, bool) and h > 0:
                try:
                    w, hh = tex.get_size()
                    return HostTexture(h, max(1, int(w)), max(1, int(hh)))
                except Exception:
                    return HostTexture(h, 1, 1)
        return None

    def _resolve_mesh(self, node, x0=-1.0, y0=-1.0, x1=1.0, y1=1.0, color=None, uv=None):
        """
        Resolve mesh from a Model-like node.

        Accepts:
          - int handle
          - MeshData (vertices / indices attrs)
          - True / "quad" → unit or rect NDC quad
          - node.vertices (+ optional node.indices)

        ``uv`` is optional (u0, v_bottom, u1, v_top) for HostTexture sub-rects.
        Explicit node.vertices always win (caller-supplied UVs preserved).
        """
        import renpy_host  # type: ignore

        mesh = getattr(node, "mesh", None) if not isinstance(node, (int, float)) else None

        # Explicit vertices on the node win for one-shot models.
        verts = getattr(node, "vertices", None) if not isinstance(node, (int, float)) else None
        if verts is not None:
            idx = getattr(node, "indices", None)
            return renpy_host.create_mesh(list(verts), list(idx) if idx is not None else None)

        # bool is a subclass of int — treat True/"quad"/None as "make a rect quad"
        # before accepting integer mesh handles.
        if mesh is True or mesh == "quad" or mesh is None:
            col = color
            if col is None and not isinstance(node, (int, float, bool)):
                col = getattr(node, "color", None)
            if col is None:
                col = (1.0, 1.0, 1.0, 1.0)
            u0, v0, u1, v1 = (0.0, 1.0, 1.0, 0.0) if uv is None else uv
            # Prefer cached full-window unit quad when covering NDC fully white + full UV.
            if (
                x0 == -1.0
                and y0 == -1.0
                and x1 == 1.0
                and y1 == 1.0
                and tuple(col)[:4] == (1.0, 1.0, 1.0, 1.0)
                and (u0, v0, u1, v1) == (0.0, 1.0, 1.0, 0.0)
            ):
                self._ensure_pipes()
                return self._quad_mesh
            return self._mesh_quad_ndc(x0, y0, x1, y1, col, u0, v0, u1, v1)

        if isinstance(mesh, int) and not isinstance(mesh, bool) and mesh > 0:
            return mesh

        # MeshData-like (host MeshData / future pure-Python meshes).
        if mesh is not None and hasattr(mesh, "vertices"):
            idx = getattr(mesh, "indices", None)
            return renpy_host.create_mesh(
                list(mesh.vertices), list(idx) if idx is not None else None
            )

        # Cython Mesh2 from Model.render (texture_rectangle / rectangle): no
        # ``vertices`` attr — exposes get_points() / points / triangles. Product
        # Model multi-tex (HuangmeiC dissolve_transform) stores Mesh2 on the
        # Render; treat it as a full-UV NDC quad at the already-computed rect.
        if mesh is not None and (
            hasattr(mesh, "get_points") or hasattr(mesh, "points")
        ):
            col = color
            if col is None and not isinstance(node, (int, float, bool)):
                col = getattr(node, "color", None)
            if col is None:
                col = (1.0, 1.0, 1.0, 1.0)
            u0, v0, u1, v1 = (0.0, 1.0, 1.0, 0.0) if uv is None else uv
            return self._mesh_quad_ndc(x0, y0, x1, y1, col, u0, v0, u1, v1)

        return None

    def _draw_model_like(self, node, ox=0.0, oy=0.0):
        """Emit one draw_model for a Model-like / textured leaf."""
        import renpy_host  # type: ignore

        self._ensure_pipes()

        # HostTexture leaf: size is the sub-rect (w,h); UVs sample parent atlas.
        if isinstance(node, HostTexture):
            # Class (b): revive dead handle in place before draw_model.
            node = self._ensure_host_texture_alive(node)
            if node is None or node.handle <= 0:
                return
            w, h = float(node.w), float(node.h)
            if w <= 0 or h <= 0:
                return
            if self._host_tex_is_full(node):
                u0, v_bottom, u1, v_top = 0.0, 1.0, 1.0, 0.0
            else:
                u0, v_bottom, u1, v_top = self._host_tex_uv(node)
            # Axis-aligned mesh crop against current clip stack (GL2 parity).
            cropped = self._crop_virt_quad_uv(ox, oy, w, h, u0, v_bottom, u1, v_top)
            if cropped is None:
                return
            cox, coy, cw, ch, u0, v_bottom, u1, v_top = cropped
            x0, y0, x1, y1 = self._virt_rect_to_ndc(cox, coy, cw, ch)
            mesh = self._mesh_quad_ndc(
                x0, y0, x1, y1, (1.0, 1.0, 1.0, 1.0), u0, v_bottom, u1, v_top
            )
            self._dm(self._tex_pipe, int(mesh), node.handle, None, None)
            return

        # Optional virtual-pixel placement on the model itself.
        w = float(
            getattr(node, "width", 0)
            or getattr(node, "w", 0)
            or 0
        )
        h = float(
            getattr(node, "height", 0)
            or getattr(node, "h", 0)
            or 0
        )
        if w <= 0 or h <= 0:
            try:
                if hasattr(node, "get_size"):
                    w, h = node.get_size()
                    w, h = float(w), float(h)
            except Exception:
                w = h = 0.0

        # NDC rect: explicit node.ndc wins; else virtual size at offset; else full NDC.
        # When clip stack is active and dest is virtual-sized, crop dest (and later UV
        # via the same fractions) so reverse-scaled Frame/Solid and model leaves honor
        # ancestor xclipping/yclipping (C1 + C3 crop+zoom preview).
        ndc = getattr(node, "ndc", None)
        _clip_uv_frac = None  # (fx0, fy0, fx1, fy1) when mesh dest was cropped
        if ndc is not None and len(ndc) >= 4:
            x0, y0, x1, y1 = (float(ndc[0]), float(ndc[1]), float(ndc[2]), float(ndc[3]))
        elif w > 0 and h > 0:
            if self._clip_rect is not None:
                # Full UV first; crop remaps after we know source UV below — use
                # unit UV here only to get dest crop; UV fractions retained.
                cropped = self._crop_virt_quad_uv(
                    ox, oy, w, h, 0.0, 1.0, 1.0, 0.0
                )
                if cropped is None:
                    return
                cox, coy, cw, ch, _u0, _vb, _u1, _vt = cropped
                # Fractional edges relative to original dest (for UV remap).
                span_x = float(w) if float(w) else 1.0
                span_y = float(h) if float(h) else 1.0
                _clip_uv_frac = (
                    (cox - float(ox)) / span_x,
                    (coy - float(oy)) / span_y,
                    (cox + cw - float(ox)) / span_x,
                    (coy + ch - float(oy)) / span_y,
                )
                x0, y0, x1, y1 = self._virt_rect_to_ndc(cox, coy, cw, ch)
            else:
                x0, y0, x1, y1 = self._virt_rect_to_ndc(ox, oy, w, h)
        else:
            x0, y0, x1, y1 = -1.0, -1.0, 1.0, 1.0

        color = getattr(node, "color", None)
        if color is not None and len(color) >= 3:
            # Accept 0–255 or 0–1.
            c = list(color)[:4]
            if len(c) == 3:
                c.append(255 if max(c) > 1.0 else 1.0)
            if max(c) > 1.0:
                c = [float(v) / 255.0 for v in c]
            color = tuple(c)
        else:
            color = None

        # Collect texture slots (HostTexture preferred so UV rects survive).
        tex_slots = []
        textures_attr = getattr(node, "textures", None)
        if textures_attr:
            for t in textures_attr:
                ht = None
                if isinstance(t, HostTexture):
                    ht = self._ensure_host_texture_alive(t) if t.handle > 0 else None
                    if ht is not None and ht.handle <= 0:
                        ht = None
                else:
                    ht = self._resolve_texture_full(t)
                if ht is not None:
                    tex_slots.append(ht)

        # Texture: keep HostTexture so subsurface UV rect survives into the mesh.
        # Prefer an explicit HostTexture on texture/textures over nested .texture ints
        # (HostTexture.texture is the bare handle alias).
        ht = None
        raw_tex = getattr(node, "texture", None)
        if isinstance(raw_tex, HostTexture):
            if raw_tex.handle > 0:
                ht = self._ensure_host_texture_alive(raw_tex)
                if ht is not None and ht.handle <= 0:
                    ht = None
        elif raw_tex is not None:
            ht = self._resolve_texture_full(raw_tex)
        if ht is None and tex_slots:
            ht = tex_slots[0]
        if ht is None and (
            hasattr(node, "get_size") or hasattr(node, "_pixels")
        ):
            ht = self._resolve_texture_full(node)
        if ht is not None and not tex_slots:
            tex_slots = [ht]
        # Ensure all slots are alive before multi-tex dissolve / draw.
        if tex_slots:
            alive_slots = []
            for s in tex_slots:
                if isinstance(s, HostTexture):
                    s = self._ensure_host_texture_alive(s)
                if s is not None and getattr(s, "handle", 0) > 0:
                    alive_slots.append(s)
            tex_slots = alive_slots
            if ht is not None and tex_slots:
                # Keep ht in sync with first alive slot if it was remapped.
                if ht not in tex_slots:
                    # ht may have been remapped in place; prefer it if still good.
                    if getattr(ht, "handle", 0) <= 0:
                        ht = tex_slots[0]
        tex = ht.handle if ht is not None and getattr(ht, "handle", 0) > 0 else None

        shaders = getattr(node, "shaders", None) or ()
        uniforms = getattr(node, "uniforms", None)

        # --- ImageDissolve 3-tex path (control / bottom / top) ---
        # product blit order: image(control), bottom(old), top(new)
        if self._is_imagedissolve_node(node) and tex_slots:
            slots = list(tex_slots)
            if len(slots) < 3:
                src = getattr(node, "_dissolve_origin", None) or node
                kids = list(self._iter_children(src))
                if len(kids) >= 3:
                    recovered = []
                    for child, _cx, _cy in kids:
                        tex_ht = self._child_to_texture(child)
                        if tex_ht is not None:
                            recovered.append(tex_ht)
                    if len(recovered) >= 3:
                        slots = recovered
            if len(slots) >= 3:
                control_ht, bottom_ht, top_ht = slots[0], slots[1], slots[2]
                # HuangmeiC image_dissolve texture order:
                #   Model().child(new).texture(old).texture(rule)
                # Host imagedissolve expects control/bottom/top.
                # dissolve_transform: child=new, tex0=old, tex1=rule
                # → slots often [new, old, rule] → remap to (rule, old, new).
                if any(s == "image_dissolve" for s in (shaders or ())):
                    a, b, c = slots[0], slots[1], slots[2]
                    control_ht, bottom_ht, top_ht = c, b, a
                # Prefer first slot's UV (control); fall back to full UV.
                uv_id = None
                if control_ht is not None and not self._host_tex_is_full(control_ht):
                    uv_id = self._host_tex_uv(control_ht)
                if uv_id is None:
                    uv_id = (0.0, 1.0, 1.0, 0.0)
                if _clip_uv_frac is not None:
                    uv_id = self._remap_uv_frac(*uv_id, _clip_uv_frac)
                mesh_id = self._mesh_quad_ndc(
                    x0, y0, x1, y1, color or (1, 1, 1, 1),
                    *uv_id,
                )
                u = self._pack_uniforms(uniforms, shaders) or [0.0] * 16
                # Defaults match GL2 ImageDissolve mid-ramp if uniforms missing.
                # Do not clobber product-alias packing (u_transition / u_animation).
                if isinstance(uniforms, dict):
                    product_alias = (
                        "u_transition" in uniforms or "u_animation" in uniforms
                    )
                    if not product_alias:
                        if "u_renpy_dissolve_offset" not in uniforms:
                            u[0] = 0.0
                        if "u_renpy_dissolve_multiplier" not in uniforms:
                            u[1] = 1.0
                self._dm(
                    self._imagedissolve_pipe,
                    int(mesh_id),
                    control_ht.handle,
                    bottom_ht.handle,
                    u,
                    top_ht.handle,
                )
                return
            # Fall through to dual-draw / textured if slots incomplete.

        # --- Dissolve crossfade (Python dual-draw; host dissolve is 1-tex only) ---
        # product: rv.add_shader("renpy.dissolve"); rv.add_uniform("u_renpy_dissolve", c)
        # blit(bottom/old), blit(top/new). Prefer standard over-blend: draw old full,
        # then new with vertex alpha = complete (textured shader does tex * v.color).
        complete = None
        if isinstance(uniforms, dict) and "u_renpy_dissolve" in uniforms:
            try:
                complete = float(uniforms["u_renpy_dissolve"])
            except (TypeError, ValueError):
                complete = 1.0
        elif any(s in ("renpy.dissolve", "dissolve") for s in shaders):
            # Shader named dissolve but no uniform — use _dissolve_complete so a
            # sticky shell with default operation_complete=0.0 is treated as
            # finished (1.0), not as "show only empty old".
            dc = self._dissolve_complete(node)
            complete = 1.0 if dc is None else float(dc)

        if complete is not None and tex_slots:
            complete = max(0.0, min(1.0, float(complete)))
            # Slot 0 = old/bottom, slot 1 = new/top (product blit order).
            old_ht = tex_slots[0]
            new_ht = tex_slots[1] if len(tex_slots) > 1 else None

            # Last-chance slot recovery when prepare only got one child texture.
            # Product Fade mid (old↔Solid black / black↔new) hard-cuts if we stay
            # on the single-slot degraded path while children still exist (H4).
            if new_ht is None:
                src = getattr(node, "_dissolve_origin", None) or node
                kids = list(self._iter_children(src))
                if len(kids) >= 2:
                    recovered = []
                    for child, _cx, _cy in kids:
                        tex = self._child_to_texture(child)
                        if tex is not None:
                            recovered.append(tex)
                    if len(recovered) >= 2:
                        old_ht = recovered[0]
                        new_ht = recovered[1]
                        tex_slots = recovered

            if new_ht is not None and old_ht is not None:
                # True 2-tex GPU mix (GL2 renpy.dissolve) when both slots ready.
                # Prefer first slot UV; full-quad if full textures.
                uv_d = None
                if not self._host_tex_is_full(old_ht):
                    uv_d = self._host_tex_uv(old_ht)
                if uv_d is None:
                    uv_d = (0.0, 1.0, 1.0, 0.0)
                if _clip_uv_frac is not None:
                    uv_d = self._remap_uv_frac(*uv_d, _clip_uv_frac)
                m = self._mesh_quad_ndc(
                    x0, y0, x1, y1, color or (1, 1, 1, 1),
                    *uv_d,
                )
                u_amt = [float(complete)] + [0.0] * 15
                # Phase 0: dual-slot dissolve samples (throttle mid-window).
                if 0.0 < complete < 1.0 and _phase0_due_dissolve():
                    nw = int(getattr(new_ht, "width", 0) or 0)
                    nh = int(getattr(new_ht, "height", 0) or 0)
                    # movie_ready: N/A from draw alone (Movie surface presence
                    # is owned by video.py / renpysound_host frame0 hold).
                    _phase0_log(
                        f"dissolve_complete={complete:.4f} dual_slot_count=2 "
                        f"new_tex_size={nw}x{nh} movie_ready=N/A "
                        f"path=dual_gpu"
                    )
                self._dm(
                    self._dissolve_pipe,
                    int(m),
                    old_ht.handle,
                    new_ht.handle,
                    u_amt,
                )
            else:
                # Degraded single-slot dissolve: show the sole texture fully.
                # Phase 0: single-slot mid-dissolve is the critical diagnostic.
                if complete is not None and 0.0 < complete < 1.0 and _phase0_due_dissolve():
                    sole = new_ht if new_ht is not None else old_ht
                    nw = int(getattr(sole, "width", 0) or 0) if sole is not None else 0
                    nh = int(getattr(sole, "height", 0) or 0) if sole is not None else 0
                    nslots = sum(
                        1
                        for s in (old_ht, new_ht)
                        if s is not None and int(getattr(s, "handle", 0) or 0) > 0
                    )
                    _phase0_log(
                        f"dissolve_complete={complete:.4f} dual_slot_count={nslots} "
                        f"new_tex_size={nw}x{nh} movie_ready=N/A "
                        f"path=single_slot_degraded"
                    )

                def _draw_slot(slot_ht, alpha):
                    if slot_ht is None or slot_ht.handle <= 0:
                        return
                    if alpha <= 0.0:
                        return
                    a = max(0.0, min(1.0, float(alpha)))
                    col = (1.0, 1.0, 1.0, a)
                    slot_uv = None if self._host_tex_is_full(slot_ht) else self._host_tex_uv(slot_ht)
                    if slot_uv is None:
                        slot_uv = (0.0, 1.0, 1.0, 0.0)
                    if _clip_uv_frac is not None:
                        slot_uv = self._remap_uv_frac(*slot_uv, _clip_uv_frac)
                    m = self._mesh_quad_ndc(
                        x0, y0, x1, y1, col,
                        *slot_uv,
                    )
                    self._dm(self._tex_pipe, int(m), slot_ht.handle, None, None)

                _draw_slot(old_ht, 1.0)
            return

        uv = None
        if ht is not None and not self._host_tex_is_full(ht):
            uv = self._host_tex_uv(ht)
        if uv is None:
            uv = (0.0, 1.0, 1.0, 0.0)
        if _clip_uv_frac is not None:
            uv = self._remap_uv_frac(*uv, _clip_uv_frac)

        mesh = self._resolve_mesh(
            node, x0, y0, x1, y1, color=color or (1, 1, 1, 1), uv=uv
        )
        if mesh is None:
            return

        # Pipeline: explicit → shader-name map → solid/textured fallback.
        pipe = getattr(node, "pipeline", None)
        if not isinstance(pipe, int) or pipe <= 0:
            pipe = self._pipeline_for_shaders(shaders, tex is not None)

        # renpy.alpha composition: fold u_renpy_alpha / u_renpy_over into vertex color.
        # GL2: gl_FragColor *= vec4(a, a, a, a*over). Host textured does tex*v.color then premul.
        draw_color = color or (1.0, 1.0, 1.0, 1.0)
        if isinstance(uniforms, dict) and shaders and any(
            s in ("renpy.alpha", "alpha") for s in shaders
        ):
            try:
                a = float(uniforms.get("u_renpy_alpha", 1.0))
            except (TypeError, ValueError):
                a = 1.0
            try:
                over = float(uniforms.get("u_renpy_over", 1.0))
            except (TypeError, ValueError):
                over = 1.0
            a = max(0.0, min(1.0, a))
            over = max(0.0, min(1.0, over))
            cr, cg, cb, ca = draw_color
            # Premul-friendly: scale RGB by alpha, A by alpha*over (GL2 shape).
            draw_color = (cr * a, cg * a, cb * a, ca * a * over)
            # Rebuild mesh with adjusted vertex color when we already built one.
            mesh = self._resolve_mesh(
                node, x0, y0, x1, y1, color=draw_color, uv=uv
            )
            if mesh is None:
                return

        u = self._pack_uniforms(uniforms, shaders)

        tex1 = self._resolve_texture(getattr(node, "texture1", None))
        if tex1 is None and len(tex_slots) > 1:
            tex1 = tex_slots[1].handle

        self._dm(pipe, int(mesh), tex, tex1, u)

    def _iter_children(self, node):
        """Yield (child, xo, yo) from Render-like children / blits."""
        children = getattr(node, "children", None)
        if children:
            for entry in children:
                if entry is None:
                    continue
                if isinstance(entry, (tuple, list)):
                    child = entry[0]
                    xo = float(entry[1]) if len(entry) > 1 else 0.0
                    yo = float(entry[2]) if len(entry) > 2 else 0.0
                    yield child, xo, yo
                else:
                    yield entry, 0.0, 0.0

        blits = getattr(node, "blits", None)
        if blits:
            for entry in blits:
                if entry is None:
                    continue
                if isinstance(entry, (tuple, list)):
                    child = entry[0]
                    xo = float(entry[1]) if len(entry) > 1 else 0.0
                    yo = float(entry[2]) if len(entry) > 2 else 0.0
                    yield child, xo, yo
                else:
                    yield entry, 0.0, 0.0

    def _draw_node(self, node, ox=0.0, oy=0.0):
        """Recursive duck-typed tree walk (mirrors GL2DrawingContext.draw_one altitude).

        Per-node errors are logged once and skipped so one bad leaf cannot abort
        the whole frame (which would leave host ``in_frame`` stuck / blank RT).
        """
        if node is None:
            return
        try:
            self._draw_node_inner(node, ox, oy)
        except Exception as e:
            _host_draw_fail("draw_node", e)

    def _draw_node_inner(self, node, ox=0.0, oy=0.0):
        # GL2 clip push (gl2draw.pyx:1661–1673): when xclipping/yclipping is set
        # on this Render, install the intersected absolute AABB for the subtree.
        # Empty intersect → skip (same as GL2 returning early). Leaf HostTexture /
        # bare handles do not carry clip flags; they inherit self._clip_rect from
        # ancestors and crop at mesh emit time.
        prev_clip = self._clip_rect
        pushed = False
        if not isinstance(node, HostTexture) and not (
            isinstance(node, int) and not isinstance(node, bool)
        ):
            new_clip, empty = self._clip_push_from_node(node, ox, oy)
            if empty:
                return
            if new_clip is not prev_clip:
                self._clip_rect = new_clip
                pushed = True
        try:
            self._draw_node_inner_body(node, ox, oy)
        finally:
            if pushed:
                self._clip_rect = prev_clip

    def _draw_node_inner_body(self, node, ox=0.0, oy=0.0):
        # Bare GPU texture handle → full-window (or offset) textured quad.
        # Exclude bool (subclass of int) so True is never treated as handle 1.
        if isinstance(node, int) and not isinstance(node, bool):
            if node > 0:
                class _TexLeaf:
                    pass

                leaf = _TexLeaf()
                leaf.texture = node
                leaf.mesh = True
                self._draw_model_like(leaf, ox, oy)
            return

        # HostTexture early: image-cache leaves are HostTexture with .texture=int
        # alias; draw the sub-rect at the accumulated virtual offset.
        if isinstance(node, HostTexture):
            # LRU touch so thrash eviction prefers idle uploads over dock chrome.
            try:
                import renpy_host  # type: ignore

                if node.handle > 0 and hasattr(renpy_host, "touch_texture"):
                    renpy_host.touch_texture(int(node.handle))
            except Exception:
                pass
            self._draw_model_like(node, ox, oy)
            return

        # Prefer prepared cached_model when set (W1 load_all_textures prepass).
        # GL2 draw_render draws the model, not raw children — even when the
        # Render still lists children. Early return avoids double-draw.
        # Style-sensitive: _invalidate_prepared clears cached_model after present
        # so hover/restyle re-prepares next frame (no sticky cross-frame bake).
        #
        # Safety (AC-Idle residual / H-Idle-A′):
        # - A multi-texture non-dissolve cached_model only paints slot 0.
        # - A single-texture cached_model on a multi-child mesh parent is just as
        #   bad: prepare may have collapsed N siblings into textures[0] (or a
        #   stale bake from a 1-child epoch), and early-return then never walks
        #   dock/logo HostTexture leaves — LIVE game RT stays arena-clear while
        #   surftree still shows prepared handles. Always fall through to child
        #   walk for multi-child boolean mesh unless this is a real dissolve /
        #   imagedissolve (those need the multi-slot model).
        children_preview = list(self._iter_children(node))
        cached = getattr(node, "cached_model", None)
        if cached is not None:
            c_texs = getattr(cached, "textures", None) or ()
            c_shaders = getattr(cached, "shaders", None) or ()
            multi_tex = isinstance(c_texs, (list, tuple)) and len(c_texs) > 1
            multi_child = len(children_preview) > 1
            mesh_attr_early = getattr(node, "mesh", None)
            bool_mesh = mesh_attr_early is True or mesh_attr_early == "quad"
            special = False
            try:
                special = self._is_dissolve_node(cached) or self._is_imagedissolve_node(
                    cached
                )
            except Exception:
                special = False
            if not special:
                try:
                    special = self._is_dissolve_node(node) or self._is_imagedissolve_node(
                        node
                    )
                except Exception:
                    special = False
            if not special and isinstance(c_shaders, (list, tuple)):
                special = any(
                    s in ("renpy.dissolve", "dissolve", "renpy.imagedissolve", "image_dissolve")
                    for s in c_shaders
                )
            # Multi-child dissolve: never trust a collapsed cached_model bake.
            # Drop it and fall through to the mesh/dissolve walk (AC-Idle).
            if special and multi_child and (
                self._is_dissolve_node(node) or self._is_dissolve_node(cached)
            ) and not (
                self._is_imagedissolve_node(node) or self._is_imagedissolve_node(cached)
            ):
                try:
                    node.cached_model = None
                except Exception:
                    pass
                # Fall through — do not early-return _draw_model_like(cached).
                # children_preview still available for later dissolve walk.
            elif False:
                pass
            # Drop bad bakes: multi-tex non-dissolve OR multi-child boolean mesh.
            drop_bake = (multi_tex and not special) or (
                multi_child and bool_mesh and not special
            )
            if drop_bake:
                if os.environ.get("RENPY_HOST_UI_TRACE") == "1" and "drop_bake_residual" not in _UI_TRACE_LOGGED:
                    _ui_trace_once(
                        "drop_bake_residual",
                        f"drop_bake=1 multi_child={int(bool(multi_child))} multi_tex={int(bool(multi_tex))} "
                        f"bool_mesh={int(bool(bool_mesh))} special={int(bool(special))} "
                        f"children_n={len(children_preview) if children_preview is not None else -1}",
                    )
                try:
                    node.cached_model = None
                except Exception:
                    pass
            elif getattr(node, "cached_model", None) is not None:
                # Residual class: multi-child proceeds with single-slot bake (not dropped).
                if (
                    os.environ.get("RENPY_HOST_UI_TRACE") == "1"
                    and "drop_bake_residual" not in _UI_TRACE_LOGGED
                    and multi_child
                    and not special
                ):
                    _ui_trace_once(
                        "drop_bake_residual",
                        f"drop_bake=0 residual_single_slot_bake multi_child=1 multi_tex={int(bool(multi_tex))} "
                        f"bool_mesh={int(bool(bool_mesh))} special=0 "
                        f"children_n={len(children_preview) if children_preview is not None else -1}",
                    )
                self._draw_model_like(cached, ox, oy)
                return

        children = children_preview

        # Frame/Solid reverse axis-scale (imagelike):
        # - Solid: 10×10 solid_texture + reverse Matrix2D(W/10,0,0,H/10) → fill dest
        # - Frame piece: reverse on single-child Render sized (cdw,cdh) with source
        #   subsurface; GL2 multiplies reverse into the model matrix.
        # - Frame parent: NO reverse; pure-container walk; each child piece applies
        #   its own reverse into its dest slot at the correct offset.
        # Without this, chrome draws unscaled source tiles / 10×10 Solid specks.
        if children and not self._is_dissolve_node(node):
            nw, nh = self._node_size(node, default=(0, 0))
            if nw > 0 and nh > 0 and self._node_needs_axis_scale(node, children):
                # Single child (Solid / Frame piece / Text oversample):
                # dest = child_size * |reverse| — NOT always full parent.
                # Typewriter mid-st is a partial HostTexture; full-parent stretch
                # after maximize (uniform 1/oversample reverse) ballooned glyphs.
                if len(children) == 1:
                    child, cx, cy = children[0]
                    tex = self._child_to_texture(child)
                    if tex is not None:
                        dw, dh = self._reverse_dest_size(node, child, (nw, nh))
                        self._draw_texture_at(tex, ox + cx, oy + cy, (dw, dh))
                    else:
                        # Nested non-texture under scaled container: recurse.
                        self._draw_node(child, ox + cx, oy + cy)
                    return
                # Multi-child + reverse on this node (rare): never stamp every
                # child into full parent size. Nested reverse pieces recurse so
                # each piece stretches into its own dest size; bare textures use
                # reverse-mapped child size when known.
                for child, cx, cy in children:
                    if self._is_render_like(child) and (
                        getattr(child, "reverse", None) is not None
                        or list(self._iter_children(child))
                    ):
                        self._draw_node(child, ox + cx, oy + cy)
                        continue
                    tex = self._child_to_texture(child)
                    if tex is not None:
                        dw, dh = self._reverse_dest_size(node, child, (nw, nh))
                        self._draw_texture_at(tex, ox + cx, oy + cy, (dw, dh))
                    else:
                        self._draw_node(child, ox + cx, oy + cy)
                return

        # Image-cache Render: often mesh=None, single HostTexture child, and
        # cached_texture set. Prefer drawing cached_texture as a node-sized
        # quad when the node has a positive size (covers full-screen bg even if
        # child walk/offset is wrong). Still walk children when multiple.
        ctex = getattr(node, "cached_texture", None)
        if ctex is not None and not children:
            self._draw_texture_at(ctex, ox, oy, self._node_size(node))
            return
        if (
            ctex is not None
            and len(children) == 1
            and isinstance(children[0][0], (HostTexture, int))
        ):
            # Single texture blit — use node size when available so a subsurface
            # HostTexture still fills the Render's layout box when appropriate.
            nw, nh = self._node_size(node, default=(0, 0))
            child, cx, cy = children[0]
            if nw > 0 and nh > 0:
                # Draw child HostTexture at child offset (preserves UV/subsurface).
                self._draw_node(child, ox + cx, oy + cy)
                return
            self._draw_node(child, ox + cx, oy + cy)
            return

        # Model-like leaf: explicit mesh handle / MeshData / vertices, or mesh=True
        # with textures and no further children.
        mesh_attr = getattr(node, "mesh", None)
        has_model_payload = (
            mesh_attr is not None
            or getattr(node, "vertices", None) is not None
            or getattr(node, "texture", None) is not None
            or getattr(node, "textures", None)
            or getattr(node, "pipeline", None) is not None
            or isinstance(node, HostTexture)
        )

        # Solid / textured model with no children → draw directly.
        if has_model_payload and not children:
            self._draw_model_like(node, ox, oy)
            return

        # mesh set with children but no prepared cached_model (prepare failed or
        # node was not visited by load_all_textures). Fall back carefully.
        if mesh_attr is not None and children:
            # Already has explicit texture/geometry → draw as leaf first, then kids.
            # NOTE: bool is a subclass of int — mesh=True must NOT count as a
            # mesh handle (that short-circuits dissolve dual-draw: has_own draws
            # the empty dissolve shell then walks NEW last → hard cut).
            has_own = (
                getattr(node, "vertices", None) is not None
                or (isinstance(mesh_attr, int) and not isinstance(mesh_attr, bool))
                or (mesh_attr is not True and mesh_attr != "quad" and hasattr(mesh_attr, "vertices"))
                or getattr(node, "texture", None) is not None
                or getattr(node, "textures", None)
            )
            if has_own:
                self._draw_model_like(node, ox, oy)
                for child, cx, cy in children:
                    self._draw_node(child, ox + cx, oy + cy)
                return

            # Boolean mesh=True / "quad" without prepare: product UI screens often
            # set mesh=True for GL2 model conversion. Walk children directly so
            # the tree still shows when prepare could not build a model.
            if mesh_attr is True or mesh_attr == "quad":
                # Dissolve: mesh=True + 2 children (old/bottom, new/top).
                # AC-Idle root: sticky renpy.dissolve on the live product surftree
                # with complete≈0 / missing amount RTT-collapses both kids and
                # only paints empty/old → permanent arena clear while dock
                # HostTextures exist deeper in the NEW child. Extreme complete
                # must FULL-WALK the live child (not _child_to_texture RTT).
                if self._is_dissolve_node(node) and len(children) >= 2:
                    complete = self._dissolve_complete(node)
                    if complete is not None and complete <= 0.001:
                        child, cx, cy = children[0]
                        self._draw_node(child, ox + cx, oy + cy)
                        return
                    if complete is None or complete >= 0.999:
                        child, cx, cy = children[-1]
                        self._draw_node(child, ox + cx, oy + cy)
                        return
                    # Mid dissolve.
                    #
                    # AC-Idle sticky enter/splash: empty/sparse OLD + content-rich
                    # NEW must NOT RTT-collapse (wipes dock HostTextures). Prefer
                    # NEW fully when OLD is a sparse shell.
                    #
                    # AC-X1 product Dissolve mid: true content↔content (or solid
                    # RTT scenes) needs GPU dual-draw 2-tex mix. Walking both kids
                    # without α just paints NEW on top → hard cut / flat mean.
                    def _ht_count(n, budget=None):
                        if budget is None:
                            budget = [120]
                        if n is None or budget[0] <= 0:
                            return 0
                        budget[0] -= 1
                        if isinstance(n, HostTexture):
                            return 1
                        if isinstance(n, int) and not isinstance(n, bool):
                            return 1 if n > 0 else 0
                        total = 0
                        for ch, _x, _y in self._iter_children(n):
                            total += _ht_count(ch, budget)
                            if total > 8:
                                return total
                        return total

                    old_c, ox0, oy0 = children[0]
                    new_c, nx0, ny0 = children[-1]
                    old_n = _ht_count(old_c)
                    new_n = _ht_count(new_c)
                    # Prefer GPU dual-draw FIRST when both slots can be textures.
                    # Product end_splash is white Solid (old_n≈1) → main_menu Movie
                    # + dock (new_n≥3). The previous sticky empty→content short-
                    # circuit ran BEFORE dual-draw and painted NEW at full opacity
                    # for the whole mid window → hard cut / Movie pop (AC-T1/T2).
                    # Splash logo/white Dissolves still dual-bake via RTT of Solids
                    # and take the dual path below; sticky NEW-only remains a
                    # last-resort only when dual-slot bake fails.
                    #
                    # Do NOT call load_all_textures here while product in_frame —
                    # nested prepare/RTT can nest frame_cmd_stack and drop the
                    # subsequent dual-draw cmds. Surfaces/HostTextures are
                    # already prepared by draw_screen's top-level load_all_textures.
                    textures = []
                    for child, _cx, _cy in children[:2]:
                        try:
                            tex = self._child_to_texture(child)
                        except Exception as e:
                            _host_draw_fail("dissolve_mid.child_to_texture", e)
                            tex = None
                        if tex is not None:
                            textures.append(tex)
                    if len(textures) >= 2:
                        w, h = self._node_size(node)
                        if w <= 0 or h <= 0:
                            w, h = 1280, 720
                        uniforms = getattr(node, "uniforms", None)
                        if not isinstance(uniforms, dict):
                            uniforms = {}
                        else:
                            uniforms = dict(uniforms)
                        uniforms["u_renpy_dissolve"] = float(complete)
                        shaders = getattr(node, "shaders", None) or ("renpy.dissolve",)
                        leaf = self._make_model_leaf(
                            w,
                            h,
                            textures[:2],
                            shaders=shaders,
                            uniforms=uniforms,
                        )
                        try:
                            leaf._dissolve_origin = node
                        except Exception:
                            pass
                        self._draw_model_like(leaf, ox, oy)
                        return
                    # Dual-slot bake failed. Sticky empty→content shell (main_menu
                    # enter residual): only when OLD is sparse AND NEW is rich AND
                    # we could not form dual textures — prefer NEW over blank OLD
                    # so dock HostTextures still appear. Does not fire when dual
                    # bake succeeds (end_splash white→Movie now dual-draws).
                    if old_n <= 1 and new_n >= 3:
                        self._draw_node(new_c, ox + nx0, oy + ny0)
                        return
                    if complete < 0.15 and old_n <= 2 and new_n >= 3:
                        self._draw_node(new_c, ox + nx0, oy + ny0)
                        return
                    # Last resort: walk both (no α mix; better than clear).
                    self._draw_node(old_c, ox + ox0, oy + oy0)
                    self._draw_node(new_c, ox + nx0, oy + ny0)
                    return

                if self._is_imagedissolve_node(node):
                    # Prepare before collecting textures so nested Render children
                    # RTT with loaded leaves (GL2 parity; product scene dissolves).
                    # ImageDissolve control often carries matrixcolor (red→alpha);
                    # prepare + _child_to_texture must RTT that, not peel HostTexture.
                    try:
                        self.load_all_textures(node)
                    except Exception as e:
                        _host_draw_fail("dissolve_fallback.load_all_textures", e)
                    cm = getattr(node, "cached_model", None)
                    if cm is not None:
                        self._draw_model_like(cm, ox, oy)
                        return
                    textures = []
                    for child, _cx, _cy in children:
                        tex = self._child_to_texture(child)
                        if tex is not None:
                            textures.append(tex)
                    need = 3
                    if len(children) >= need and len(textures) < need:
                        textures = []
                        for child, _cx, _cy in children:
                            tex = self._child_to_texture(child)
                            if tex is not None:
                                textures.append(tex)
                    if textures:
                        w, h = self._node_size(node)
                        uniforms = getattr(node, "uniforms", None)
                        leaf = self._make_model_leaf(
                            w,
                            h,
                            textures,
                            shaders=getattr(node, "shaders", None),
                            uniforms=uniforms,
                        )
                        try:
                            leaf._dissolve_origin = node
                        except Exception:
                            pass
                        self._draw_model_like(leaf, ox, oy)
                        return
                for child, cx, cy in children:
                    self._draw_node(child, ox + cx, oy + cy)
                return

            baked = self._bake_mesh_children(node, children)
            if baked is not None:
                self._draw_model_like(baked, ox, oy)
                return

            # Bake failed: fall through to direct child walk (best-effort).
            for child, cx, cy in children:
                self._draw_node(child, ox + cx, oy + cy)
            return

        # Non-mesh effect-shader container (Transform matrixcolor / product alias).
        # Product ImageDissolve wraps the control image in matrixcolor with
        # mesh=None; pure-container walk would draw the raw child and drop the
        # red→alpha bake. Promote to model-like so _draw_model_like / RTT apply
        # the named pipeline + packed uniforms.
        #
        # Also promote composition-only renpy.alpha (ATL Transform alpha):
        # composition_mode("renpy.alpha") is truthy so it is NOT an "effect"
        # above, but pure walk would draw the child HostTexture at full opacity
        # and drop u_renpy_alpha (GL2 merges uniforms into child context).
        # Dual idle/activate dock layers (bottom_menu.rpy ~418–450) rely on
        # partial α (e.g. 0.6 insensitive) and on the vertex fold in
        # _draw_model_like. Binary α=0 is already handled by host depends_on
        # (no blit); this path covers α in (0,1) and belt-and-suspenders α=0.
        shaders_attr = getattr(node, "shaders", None) or ()
        if children and shaders_attr and mesh_attr is None:
            effect = False
            try:
                from renpy.wgpu.shaders import composition_mode

                effect = any(composition_mode(s) is None for s in shaders_attr)
            except Exception:
                effect = True
            uniforms_attr = getattr(node, "uniforms", None)
            if not effect and isinstance(uniforms_attr, dict):
                effect = any(
                    k in uniforms_attr
                    for k in (
                        "u_renpy_matrixcolor",
                        "u_renpy_blur_log2",
                        "u_renpy_mask_multiplier",
                        "u_renpy_mask_offset",
                        "u_transition",
                        "u_animation",
                    )
                )
            # Composition renpy.alpha alone: still promote so vertex fold runs.
            if not effect and any(
                s in ("renpy.alpha", "alpha") for s in shaders_attr
            ):
                effect = True
            if effect:
                # α<=0: nothing to contribute (hide layer). Skip promote/draw.
                if isinstance(uniforms_attr, dict) and any(
                    s in ("renpy.alpha", "alpha") for s in shaders_attr
                ):
                    try:
                        a_hide = float(uniforms_attr.get("u_renpy_alpha", 1.0))
                    except (TypeError, ValueError):
                        a_hide = 1.0
                    if a_hide <= 0.0:
                        return
                textures = []
                # HuangmeiC dissolve_transform: outer ATL Transform stamps
                # shader "image_dissolve" + u_animation/u_transition, while the
                # multi-tex Model is the *child* (mesh truthy, textures=new/old/rule).
                # MUST fold multi-tex mesh slots BEFORE _child_to_texture: that
                # helper RTT-collapses mesh nodes into a single HostTexture and
                # would starve imagedissolve of its 3 slots (selected nav wipe dead).
                want_multi = any(
                    s
                    in (
                        "image_dissolve",
                        "renpy.imagedissolve",
                        "imagedissolve",
                        "renpy.dissolve",
                        "dissolve",
                    )
                    for s in shaders_attr
                ) or (
                    isinstance(uniforms_attr, dict)
                    and (
                        "u_animation" in uniforms_attr
                        or "u_transition" in uniforms_attr
                        or "u_renpy_dissolve" in uniforms_attr
                    )
                )
                if want_multi:
                    for child, _cx, _cy in children:
                        mesh_c = getattr(child, "mesh", None)
                        if not mesh_c:
                            sub_kids = list(self._iter_children(child))
                            if len(sub_kids) == 1:
                                only, _sx, _sy = sub_kids[0]
                                if getattr(only, "mesh", None):
                                    child = only
                                    mesh_c = getattr(child, "mesh", None)
                        if not mesh_c:
                            continue
                        cm = getattr(child, "cached_model", None)
                        slots = []
                        if cm is not None:
                            raw = getattr(cm, "textures", None) or ()
                            if isinstance(raw, (list, tuple)):
                                slots = [
                                    t
                                    for t in raw
                                    if t is not None
                                    and (
                                        isinstance(t, HostTexture)
                                        or (
                                            isinstance(t, int)
                                            and not isinstance(t, bool)
                                            and t > 0
                                        )
                                    )
                                ]
                            if not slots:
                                one = getattr(cm, "texture", None)
                                if one is not None:
                                    slots = [one]
                        if not slots:
                            raw = getattr(child, "textures", None) or ()
                            if isinstance(raw, (list, tuple)):
                                for t in raw:
                                    if isinstance(t, HostTexture) and t.handle > 0:
                                        slots.append(t)
                                    elif (
                                        isinstance(t, int)
                                        and not isinstance(t, bool)
                                        and t > 0
                                    ):
                                        slots.append(HostTexture(t, 1, 1))
                                    elif t is not None and not getattr(
                                        t, "mesh", None
                                    ):
                                        # Leaf texture children of Model, not mesh RTT.
                                        ht = None
                                        if isinstance(t, HostTexture):
                                            ht = t
                                        elif self._is_render_like(t) and not getattr(
                                            t, "mesh", None
                                        ):
                                            ht = self._extract_host_texture(t)
                                        if ht is not None:
                                            slots.append(ht)
                        if not slots:
                            for gc, _gx, _gy in self._iter_children(child):
                                if isinstance(gc, HostTexture) and gc.handle > 0:
                                    slots.append(gc)
                                elif (
                                    isinstance(gc, int)
                                    and not isinstance(gc, bool)
                                    and gc > 0
                                ):
                                    slots.append(HostTexture(gc, 1, 1))
                                elif self._is_render_like(gc) and not getattr(
                                    gc, "mesh", None
                                ):
                                    ht = self._extract_host_texture(gc)
                                    if ht is not None:
                                        slots.append(ht)
                        if len(slots) >= 2:
                            textures = list(slots)
                            break
                # Single-tex matrixcolor / alpha promote: preserve the child's
                # blit offset (prefs nav ColorizeMatrix icons are not at 0,0).
                # Multi-tex dissolve keeps parent origin (slots already full-size).
                child_draw_ox = ox
                child_draw_oy = oy
                if not textures:
                    for child, cx, cy in children:
                        # Skip mesh multi-tex children here — they need fold above,
                        # not single-slot RTT collapse via _child_to_texture.
                        if getattr(child, "mesh", None) and want_multi:
                            continue
                        tex = self._child_to_texture(child)
                        if tex is not None:
                            textures.append(tex)
                            if len(textures) == 1 and len(children) == 1:
                                child_draw_ox = ox + float(cx)
                                child_draw_oy = oy + float(cy)
                if textures:
                    # Prefer child size for single-tex offset promote so the
                    # effect quad matches the icon, not the full parent box.
                    if len(textures) == 1 and (
                        child_draw_ox != ox or child_draw_oy != oy
                    ):
                        tw = float(getattr(textures[0], "w", 0) or 0)
                        th = float(getattr(textures[0], "h", 0) or 0)
                        if tw <= 0 or th <= 0:
                            tw, th = self._node_size(node)
                        w, h = tw, th
                    else:
                        w, h = self._node_size(node)
                    leaf = self._make_model_leaf(
                        w,
                        h,
                        textures,
                        shaders=shaders_attr,
                        uniforms=uniforms_attr,
                    )
                    try:
                        leaf._dissolve_origin = node
                    except Exception:
                        pass
                    self._draw_model_like(leaf, child_draw_ox, child_draw_oy)
                    return

        # Pure container: walk children (and optional self-draw if Surface-like).
        if children:
            for child, cx, cy in children:
                self._draw_node(child, ox + cx, oy + cy)
            return

        # Surface-like leaf with no children.
        if hasattr(node, "get_size") or hasattr(node, "_pixels"):
            self._draw_model_like(node, ox, oy)
            return

        # Last resort: treat as model-like if anything drawable remains.
        if has_model_payload:
            self._draw_model_like(node, ox, oy)

    def _draw_texture_at(self, tex, ox, oy, size):
        """Draw a texture-like value as a virtual-pixel quad at (ox,oy) with size.

        Layout size (NDC) and source UV are independent:
        - ``size`` / (ox,oy) → destination rect in virtual pixels
        - ``ht.x/y/w/h`` + parent ``ht.width/height`` → UV sub-rect

        Used by Solid reverse (10×10 → full dest) and Frame 9-slice pieces
        (source subsurface UV stretched into each piece dest).

        Never overwrite ``HostTexture.w/h`` with the dest size: those fields feed
        ``_host_tex_uv`` / ``_host_tex_is_full``. Doing so made Frame stretch
        sample wrong UVs (ClampToEdge edge-fill looked like a solid-tile pass).
        """
        import renpy_host  # type: ignore

        # _resolve_texture_full already runs class-(b) dead-handle recovery.
        ht = self._resolve_texture_full(tex)
        if ht is None or ht.handle <= 0:
            if ht is not None and int(getattr(ht, "handle", 0) or 0) <= 0:
                _ui_trace_once(
                    "empty_upload",
                    f"class=c handle=0 tag=draw_texture_at_skip size={size}",
                )
            return
        try:
            w, h = int(size[0]), int(size[1])
        except Exception:
            w, h = int(ht.w), int(ht.h)
        if w <= 0 or h <= 0:
            w, h = int(ht.w), int(ht.h)
        if w <= 0 or h <= 0:
            return
        self._ensure_pipes()
        # Source UV from original sub-rect (full or subsurface) — NOT dest size.
        u0, v_bottom, u1, v_top = self._host_tex_uv(ht)
        # GL2 mesh crop: when an ancestor set xclipping/yclipping, crop dest + UV.
        cropped = self._crop_virt_quad_uv(ox, oy, w, h, u0, v_bottom, u1, v_top)
        if cropped is None:
            return
        cox, coy, cw, ch, u0, v_bottom, u1, v_top = cropped
        x0, y0, x1, y1 = self._virt_rect_to_ndc(cox, coy, cw, ch)
        mesh = self._mesh_quad_ndc(
            x0, y0, x1, y1, (1.0, 1.0, 1.0, 1.0), u0, v_bottom, u1, v_top
        )
        self._dm(self._tex_pipe, int(mesh), ht.handle, None, None)

    def _node_size(self, node, default=None):
        """Best-effort (w, h) from a Render/Model/Surface-like node.

        When ``default`` is ``(0, 0)`` (or any non-positive), returns ``(0, 0)``
        rather than inventing a 1×1 fallback — callers use that to mean "unknown".

        HostTexture: ``.width/.height`` are the full atlas size; the drawable UV
        rect is ``.w/.h`` (also ``get_size()``). Prefer the UV rect so reverse
        dest sizing for typewriter partials / Frame subsurfaces is correct.
        """
        if default is None:
            default = self.virtual_size
        if isinstance(node, HostTexture):
            try:
                return max(1, int(node.w)), max(1, int(node.h))
            except Exception:
                pass
        w = float(getattr(node, "width", 0) or getattr(node, "w", 0) or 0)
        h = float(getattr(node, "height", 0) or getattr(node, "h", 0) or 0)
        if w > 0 and h > 0:
            return max(1, int(w)), max(1, int(h))
        try:
            if hasattr(node, "get_size"):
                gw, gh = node.get_size()
                if gw and gh:
                    return max(1, int(gw)), max(1, int(gh))
        except Exception:
            pass
        dw = int(default[0]) if default and len(default) > 0 else 0
        dh = int(default[1]) if default and len(default) > 1 else 0
        if dw <= 0 or dh <= 0:
            return 0, 0
        return max(1, dw), max(1, dh)

    def _bake_mesh_children(self, node, children):
        """
        GL2-style mesh bake: render children into an RTT, return a textured leaf.

        Children are drawn in RTT local space (offsets relative to the mesh node).
        Nested begin_frame is supported by host frame_cmd_stack so the outer
        product draw_screen command list is preserved — but only if every
        begin_frame is matched by end_frame_present (try/finally).
        """
        try:
            import renpy_host  # type: ignore

            self._ensure_pipes()
            w, h = self._node_size(node)
            if w <= 0 or h <= 0:
                w, h = self.virtual_size

            # Composite all children into one RTT (GL2 uses one texture per child;
            # MVP composites into a single full-size RTT for renpy.texture meshes).
            # Borrow from freelist — re-bake every draw would otherwise leak.
            rtt = self._acquire_rtt(w, h)
            renpy_host.begin_target(rtt)
            renpy_host.begin_frame()
            old_vs = self.virtual_size
            old_clip = self._clip_rect
            try:
                # Draw children at their relative offsets inside the RTT. Use a
                # temporary virtual_size so _virt_rect_to_ndc maps into RTT NDC.
                # Clip is local to the RTT (children drawn in local coords); clear
                # product clip so RTT-local coords are not filtered by screen AABB.
                self.virtual_size = (w, h)
                self._clip_rect = None
                for child, cx, cy in children:
                    self._draw_node(child, float(cx), float(cy))
            finally:
                self.virtual_size = old_vs
                self._clip_rect = old_clip
                try:
                    self._end_frame_present()
                except Exception as e:
                    _host_draw_fail("mesh_bake.end_frame", e)
                try:
                    renpy_host.end_target()
                except Exception as e:
                    _host_draw_fail("mesh_bake.end_target", e)

            class _BakedLeaf:
                pass

            leaf = _BakedLeaf()
            leaf.width = w
            leaf.height = h
            leaf.texture = int(rtt)
            leaf.mesh = True
            leaf.shaders = ("renpy.texture",)
            leaf.color = None
            leaf.textures = None
            leaf.vertices = None
            leaf.indices = None
            leaf.pipeline = None
            leaf.uniforms = None
            leaf.ndc = None
            leaf.children = None
            leaf.cached_model = None
            leaf.blits = None
            # Do not assign node.cached_model across frames: style/hover can
            # change child textures and a sticky bake would freeze wrong art.
            # Re-bake each draw when mesh=True + children (MVP correctness).
            return leaf
        except Exception as e:
            _host_draw_fail("mesh_bake", e)
            return None

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
            composition_mode = lambda _n: None  # type: ignore
            host_pipeline_key = lambda _n: None  # type: ignore
            is_atomic = lambda _n: False  # type: ignore
            is_mergeable = lambda _n: False  # type: ignore

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
        import renpy_host  # type: ignore

        self._ensure_pipes()
        self._dm(self._tex_pipe, mesh or self._quad_mesh, texture)

    def draw_dissolve(self, texture: int, mesh: Optional[int] = None):
        """Draw with renpy.dissolve pipeline (alpha from vertex color / tex)."""
        import renpy_host  # type: ignore

        self._ensure_pipes()
        self._dm(self._dissolve_pipe, mesh or self._quad_mesh, texture)

    def draw_blur(
        self,
        texture: int,
        blur_log2: float = 2.0,
        mesh: Optional[int] = None,
    ):
        """Draw with renpy.blur pipeline; uniforms[0] = blur_log2."""
        import renpy_host  # type: ignore

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
        import renpy_host  # type: ignore

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
        import renpy_host  # type: ignore

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
        import renpy_host  # type: ignore

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
        import renpy_host  # type: ignore

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
        import renpy_host  # type: ignore

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
        import renpy_host  # type: ignore

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
