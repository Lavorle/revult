"""draw_screen — screen/present mixin extracted from draw.py."""
from __future__ import annotations
import os, sys, time as _time, threading
from typing import Any, Optional, Sequence
from .draw_debug import _DRAW_SCREEN_LOCK, _draw_screen_lock, _HOST_DRAW_FAIL_LOGGED, _UI_TRACE_LOGGED, _PHASE0_LAST_DISSOLVE_T, _PHASE0_LAST_WRITE_T, _PHASE0_LAST_FRAME_T, _PHASE0_DISSOLVE_INTERVAL, _PHASE0_WRITE_INTERVAL, _PHASE0_FRAME_INTERVAL, _PHASE0_LAST_GENERIC, _phase0_signals_enabled, _phase0_log, _phase0_due, _phase0_due_dissolve, _phase0_due_write, _phase0_due_frame, _safe_print, _ui_trace_once, _host_draw_fail
from .host_texture import HostTexture

class ScreenMixin:
    _draw_batch: list
    _draw_screen_depth: int
    _clip_rect: object
    virtual_size: tuple[int, int]
    physical_size: tuple[int, int]
    drawable_size: tuple[int, int]
    _rtt_free: dict
    _rtt_prev_frame: list
    _rtt_curr_frame: list

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

__all__ = ["ScreenMixin"]
