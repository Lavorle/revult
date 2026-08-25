"""draw_traversal - texture extraction / prepare prepass helpers (mixin)."""
from __future__ import annotations

import os
from typing import Any, Optional, Sequence

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
from .host_texture import HostTexture  # noqa: F401

class TraversalMixin:
    # Expected attributes on the concrete WgpuDraw (type hints only)
    virtual_size: tuple[int, int]  # type: ignore
    _clip_rect: object  # type: ignore
    _mesh_cache: dict  # type: ignore
    _mesh_cache_cap: int  # type: ignore
    _mesh_deferred_destroy: list[int]  # type: ignore

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





__all__ = ["TraversalMixin"]
