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


# ============================================================================
# renpy_host per-node import hoist (perf micro, zero behavior change).
#
# The prepare walk rides hundreds of nodes per frame and called
# ``import renpy_host`` (machine-level sys.modules fetch + module probe) from
# up to four per-node sites, plus ``hasattr`` chains around optional FFI
# surface (``touch_texture`` / ``texture_alive``). This cell resolves the
# module and the optional attributes **once** at first use and caches them
# even when ``renpy_host`` turns out to be absent (hermetic gates) so hot
# loops after that stay short-circuit. Same semantics as per-node import +
# hasattr; only lookup cost changes.
# ============================================================================
_RENPY_HOST_CELL: list[object] = [None]


def _prepare_rh():
    c = _RENPY_HOST_CELL[0]
    if c is not None:
        return c
    try:
        import renpy_host as _rh  # type: ignore
        c = _rh
    except Exception:  # noqa: BLE001 -- host shim absent in hermetic contexts
        c = False
    _RENPY_HOST_CELL[0] = c
    return c


def _prepare_touch_texture(handle: int) -> None:
    """Best-effort per-node LRU touch with the cached module + attr probe."""
    m = _prepare_rh()
    if m is False or m is None:
        return
    touch = getattr(m, "touch_texture", None)
    if touch is None:
        return
    alive_fn = getattr(m, "texture_alive", None)
    if alive_fn is not None and not alive_fn(int(handle)):
        return
    try:
        touch(int(handle))
    except Exception:  # noqa: BLE001 -- residual must not abort frames
        pass


class TraversalMixin:
    # Expected attributes on the concrete WgpuDraw (type hints only)
    virtual_size: tuple[int, int]  # type: ignore
    _clip_rect: object  # type: ignore
    _mesh_cache: dict  # type: ignore
    _mesh_cache_cap: int  # type: ignore
    _mesh_deferred_destroy: list[int]  # type: ignore
    # Frame-generation counter advanced once by ``_invalidate_prepared``
    # (O(1) root marker); per-node frame-local clears fold into prepare.
    _frame_gen: int
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
        Frame-local: ``_invalidate_prepared`` advances the frame generation
        (single O(1) root marker); this walk clears stale ``loaded`` /
        ``cached_model`` / ``_wgpu_solid_slot`` stamps per node on first
        touch, so every node is reset before any in-tree reader observes it.

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
            if what > 0:
                _prepare_touch_texture(int(what))
            return

        # --- frame-generation fold (a)+(b) -----------------------------------
        # ``_invalidate_prepared`` no longer walks the tree per node (that was
        # 3×hasattr + up to 3×setattr + full recursion per node, every frame).
        # It advances ``self._frame_gen`` and stamps the root once. The frame
        # flag is folded into ``loaded`` itself: freshly prepared nodes carry
        # ``loaded == self._frame_gen + 1`` (truthy int; old code wrote
        # ``True``), a stale node carries an older tick (or False). Here, each
        # node behind the expected tick gets the same frame-local clears the
        # old invalidate performed — lazily on first touch, before any reader
        # of ``cached_model``/``_wgpu_solid_slot`` below can observe them.
        # ``loaded`` is not written False first: this walk immediately re-stamps
        # the current tick and never reads it in between, so the False→True
        # roundtrip was pure per-node overhead.
        gen = getattr(self, "_frame_gen", 0)
        expected = gen + 1
        lg = getattr(what, "loaded", False)
        if isinstance(what, (float, str, bytes, bytearray)):
            lg = False
        elif lg != expected:
            if gen > 0:
                # epoch 0 = no ``_invalidate_prepared`` has ever run this
                # process; old code cleared nothing outside that call, so
                # the folds below only fire once an epoch exists.
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
                m = _prepare_rh()
                if m is not False and m is not None and hasattr(m, "touch_texture"):
                    alive_fn = getattr(m, "texture_alive", None)
                    if alive_fn is None or alive_fn(int(ct)):
                        m.touch_texture(int(ct))
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
        # LRU touch under thrash even when ancestors stay mark-truthy.
        # Tick-gated: a mark from an older generation (or False) means not
        # prepared this frame — the folded invalidate clears above already
        # ran, so fall through and re-prepare, exactly like the old
        # ``loaded=False`` post-invalidate state did.
        if lg == expected:
            try:
                for child, _xo, _yo in self._iter_children(what):
                    self._load_all_textures_inner(child, reverse)
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass
            return
        if hasattr(what, "loaded"):
            # Stamp the current tick (truthy; same contract as old ``True``).
            what.loaded = expected

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
        """Advance the frame generation; per-node clears fold into prepare.

        Historical form walked the whole tree once per frame: per node, 3
        ``hasattr`` (``loaded`` / ``cached_model`` / ``_wgpu_solid_slot``) + up
        to 3 ``setattr`` + full ``_iter_children`` recursion — pure Python
        overhead at O(tree size) on every present.

        Semantic contract preserved for every reader::

            - prepare skip: nodes re-prepare every frame exactly as before;
              the per-node frame-local tick/cached_model/slot clears happen at
              first touch in ``_load_all_textures_inner``, before any state
              read of that node;
            - ``_child_to_texture``'s ``getattr(child, "loaded", False)``
              gate behaves identically (truthiness preserved — prepared nodes
              carry a truthy tick, unprepared carry False / absent);
            - draw-walk ``cached_model`` / solid-slot memo reads see fresh
              values, because the clears happen at the start of the next
              prepare visit — before the draw walk runs (frame order:
              prepare → draw → present → invalidate_root);
            - exclusions mirrored: ``None`` and the primitive basket
              (HostTexture / int / float / bool / str / bytes / bytearray)
              must not advance the generation — the old walk was a no-op
              for those, so bumping here would over-invalidate.

        Root marker: one ``setattr`` of ``_frame_gen`` on the root node is
        kept so node-level introspection sites can observe the epoch.
        """
        if what is None:
            return
        if isinstance(what, (HostTexture, int, float, bool, str, bytes, bytearray)):
            return
        self._frame_gen = int(getattr(self, "_frame_gen", 0)) + 1
        gen = self._frame_gen
        try:
            what._frame_gen = gen
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
        # Do NOT clear cached_texture here: product image cache and dissolve-slot
        # RTTs reuse HostTextures across frames. Solid reverse memo is enough for
        # hover; full texture kill is kill_textures / fingerprint re-upload.
        # Per-node ``loaded`` tick / ``cached_model`` / ``_wgpu_solid_slot``
        # clears are folded into ``_load_all_textures_inner`` (first-touch on
        # stale ticks) — this call stays O(1).

    # --- Tree walk (duck-typed Render / Model / Surface) ---------------------





__all__ = ["TraversalMixin"]
