"""draw_traversal - texture extraction / prepare prepass helpers (mixin)."""
from __future__ import annotations

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
from .draw_surftree import SurftreeMixin  # T5: single owner shim
from .host_texture import HostTexture

# M1 T3: instance grouping helper available via host_bridge (private _InstanceGroup)
try:
    from .host_bridge import _InstanceGroup  # noqa: F401
except Exception:
    _InstanceGroup = None  # type: ignore


class TraversalMixin:
    # Expected attributes on the concrete WgpuDraw (type hints only)
    virtual_size: tuple[int, int]  # type: ignore
    _clip_rect: object  # type: ignore
    _mesh_cache: dict  # type: ignore
    _mesh_cache_cap: int  # type: ignore
    _mesh_deferred_destroy: list[int]  # type: ignore

    def _extract_host_texture(self, child, depth=0):
        """Shim: canonical in SurftreeMixin (T5 single owner)."""
        return SurftreeMixin._extract_host_texture(self, child, depth)  # type: ignore[attr-defined]

    def _solid_reverse_slot_texture(self, child):
        """Shim: canonical in SurftreeMixin (T5)."""
        return SurftreeMixin._solid_reverse_slot_texture(self, child)  # type: ignore[attr-defined]

    def _child_to_texture(self, child):
        """Shim: canonical in SurftreeMixin (T5)."""
        return SurftreeMixin._child_to_texture(self, child)  # type: ignore[attr-defined]

    def _make_model_leaf(self, w, h, textures, mesh_obj=None, shaders=None, uniforms=None):
        """Shim: canonical in SurftreeMixin (T5)."""
        return SurftreeMixin._make_model_leaf(self, w, h, textures, mesh_obj, shaders, uniforms)  # type: ignore[attr-defined]

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
                except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                            pass
        finally:
            if lock is not None:
                try:
                    lock.release()
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
            except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                _host_draw_fail("prepare.ensure_host_texture", e)
            return
        if isinstance(what, int) and not isinstance(what, bool):
            # Bare handle: best-effort LRU touch so thrash does not kill it.
            try:
                import renpy_host  # type: ignore

                if what > 0 and hasattr(renpy_host, "touch_texture"):  # noqa: SIM102 -- nested check clarifies touch_texture vs texture_alive existence
                    if (not hasattr(renpy_host, "texture_alive")) or renpy_host.texture_alive(
                        int(what)
                    ):
                        renpy_host.touch_texture(int(what))
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                                pass
        except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass

        # Already prepared this frame (also guards prepare↔RTT recursion).
        # Still walk children so HostTexture leaves get dead_present revive /
        # LRU touch under thrash even when ancestors stay ``loaded=True``.
        if getattr(what, "loaded", False):
            try:
                for child, _xo, _yo in self._iter_children(what):
                    self._load_all_textures_inner(child, reverse)
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass
        if hasattr(what, "cached_model"):
            try:
                what.cached_model = None
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass
        # Frame-local solid reverse dissolve-slot memo (Agent A Fade path).
        if hasattr(what, "_wgpu_solid_slot"):
            try:
                what._wgpu_solid_slot = None
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass
        # Do NOT clear cached_texture here: product image cache and dissolve-slot
        # RTTs reuse HostTextures across frames. Solid reverse memo is enough for
        # hover; full texture kill is kill_textures / fingerprint re-upload.
        try:
            for child, _xo, _yo in self._iter_children(what):
                self._invalidate_prepared(child)
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass

    # --- Tree walk (duck-typed Render / Model / Surface) ---------------------





__all__ = ["TraversalMixin"]
