"""draw_walk - recursive surftree walk (mixin)."""
from __future__ import annotations

from dataclasses import dataclass

from .host_bridge import host_env_bool

try:
    from .constants import ISO_BASIS_X, ISO_BASIS_Y  # noqa: F401
except ImportError:
    ISO_BASIS_X = 0.866
    ISO_BASIS_Y = 0.5

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
    _WALKSTAT_ON,
    _draw_screen_lock,
    _walkstat_skip,
    _host_draw_fail,
    _phase0_due,
    _phase0_due_dissolve,
    _phase0_due_frame,
    _phase0_due_write,
    _phase0_log,
    _phase0_signals_enabled,
    _safe_print,
    _ui_trace_once,
    _BAKE_ENABLED,
    bake_attn,
    bake_caller_pop,
    bake_caller_push,
)
from .draw_surftree import SurftreeMixin  # T5 shim
from .host_texture import HostTexture


def reverse_bake_cache_enabled_fallback() -> bool:
    """hmc_fix3 F3 gate accessor (falls back off when draw_surftree is absent)."""
    try:
        from .draw_surftree import reverse_bake_cache_enabled

        return bool(reverse_bake_cache_enabled())
    except Exception:
        return False


@dataclass
class WalkCtx:
    ox: float
    oy: float
    budget: int = 120
    clip_rect: object = None
    clip_poly: object = None  # Optional[list[tuple[float,float]]] — polygon clip when reverse non-identity; mutual exclusion with clip_rect (poly is not None => poly preferred)


class CachedModelPolicy:
    @staticmethod
    def is_cached(node) -> bool:
        return getattr(node, "cached_model", None) is not None

    @staticmethod
    def should_drop_bake(node, multi_tex, bool_mesh, multi_child, special) -> bool:
        return (multi_tex and not special) or (multi_child and bool_mesh and not special)


class SurftreeWalkerCacheGate:
    """Transition bake cache gate for the walker (old slot only).

    OnceLock parse is owned by draw_debug (RENPY_HOST_TRANS_BAKE_CACHE,
    default on). Fallback-off keeps walker behavior byte-identical to the
    pre-cache product: gate never engages, keys are never built, counters
    never increment.
    """
    @staticmethod
    def enabled() -> bool:
        try:
            from .draw_debug import trans_bake_cache_enabled
            return trans_bake_cache_enabled()
        except Exception:
            return False

class DissolveStrategy:
    @staticmethod
    def needs_mid(node) -> bool:
        try:
            shaders = getattr(node, "shaders", None) or ()
            if any(s in ("renpy.dissolve", "dissolve", "renpy.imagedissolve", "image_dissolve") for s in shaders):
                return True
            # fallback to attribute check
            return False
        except:
            return False


    @staticmethod
    def ht_count(host_texture_cls, int_cls, iter_children, n, budget) -> int:
        if budget is None:
            budget = [120]
        if n is None or budget[0] <= 0:
            return 0
        budget[0] -= 1
        if isinstance(n, host_texture_cls):
            return 1
        if isinstance(n, int_cls) and not isinstance(n, bool):
            return 1 if n > 0 else 0
        total = 0
        for ch, _x, _y in iter_children(n):
            total += DissolveStrategy.ht_count(host_texture_cls, int_cls, iter_children, ch, budget)
            if total > 8:
                return total
        return total

class ReverseScaler:
    @staticmethod
    def apply(node, w, h, reverse) -> tuple:
        # Thin wrapper for reverse dest size; kept for spec compat.
        # Actual walk uses _reverse_dest_size per child, but this covers API.
        try:
            # reverse is expected to be a tuple or Matrix; fallback to (w,h)
            if reverse is None:
                return (w, h)
            # if reverse has scale factors, apply
            if isinstance(reverse, (list, tuple)) and len(reverse) >= 2:
                return (w * abs(float(reverse[0])), h * abs(float(reverse[1])))
            return (w, h)
        except:
            return (w, h)

    @staticmethod
    def inverse_matrix(reverse):
        """Return forward (inverse) matrix for a reverse Matrix, or None if identity/unsupported.

        Reused by draw_surftree._transform_quad to avoid duplicating inversion logic.
        """
        if reverse is None:
            return None
        # Matrix-like with .inverse() (renpy.display.matrix.Matrix)
        try:
            inv = getattr(reverse, "inverse", None)
            if callable(inv):
                try:
                    fwd = inv()
                    return fwd
                except Exception:
                    pass
        except Exception:
            pass
        # Fallback: manual 2D affine inverse for Matrix2D-like (xdx, xdy, ydx, ydy, xdw, ydw)
        try:
            a = float(getattr(reverse, "xdx", 1.0))
            b = float(getattr(reverse, "xdy", 0.0))
            c = float(getattr(reverse, "ydx", 0.0))
            d = float(getattr(reverse, "ydy", 1.0))
            tx = float(getattr(reverse, "xdw", 0.0) or 0.0)
            ty = float(getattr(reverse, "ydw", 0.0) or 0.0)
            det = a * d - b * c
            if abs(det) < 1e-9:
                return None
            inv_det = 1.0 / det
            # Inverse linear part
            ia = d * inv_det
            ib = -b * inv_det
            ic = -c * inv_det
            id = a * inv_det
            # Inverse translation: -I * t
            itx = -(ia * tx + ib * ty)
            ity = -(ic * tx + id * ty)
            # Build light object with transform method
            class _Inv:
                def __init__(self, ia, ib, ic, id, itx, ity):
                    self.xdx = ia
                    self.xdy = ib
                    self.ydx = ic
                    self.ydy = id
                    self.xdw = itx
                    self.ydw = ity
                def transform(self, x, y):
                    return (self.xdx * float(x) + self.xdy * float(y) + self.xdw,
                            self.ydx * float(x) + self.ydy * float(y) + self.ydw)
            return _Inv(ia, ib, ic, id, itx, ity)
        except Exception:
            return None

    @staticmethod
    def is_identity(reverse, eps: float = 1e-6) -> bool:
        """Tolerance-based identity check shared with draw_surftree._is_identity."""
        if reverse is None:
            return True
        try:
            # Handle tuple/list simple representation
            if isinstance(reverse, (list, tuple)):
                if len(reverse) == 4:
                    a, b, c, d = [float(v) for v in reverse[:4]]
                    return abs(a - 1.0) < eps and abs(d - 1.0) < eps and abs(b) < eps and abs(c) < eps
                if len(reverse) == 16:
                    # 4x4 matrix list
                    vals = [float(v) for v in reverse]
                    # check diagonal 1, off-diagonal 0 (except zdz/wdw)
                    for i, v in enumerate(vals):
                        expected = 1.0 if i in (0, 5, 10, 15) else 0.0
                        if abs(v - expected) >= eps:
                            return False
                    return True
                return False
            a = float(getattr(reverse, "xdx", 1.0) or 1.0)
            b = float(getattr(reverse, "xdy", 0.0) or 0.0)
            c = float(getattr(reverse, "ydx", 0.0) or 0.0)
            d = float(getattr(reverse, "ydy", 1.0) or 1.0)
            tx = float(getattr(reverse, "xdw", 0.0) or 0.0)
            ty = float(getattr(reverse, "ydw", 0.0) or 0.0)
            # Check extra fields for 3D matrices: zd* / wd* should be identity
            for attr, exp in (("xdz", 0.0), ("ydz", 0.0), ("zdx", 0.0), ("zdy", 0.0), ("zdz", 1.0),
                              ("xdw", 0.0), ("ydw", 0.0), ("zdw", 0.0), ("wdx", 0.0), ("wdy", 0.0), ("wdz", 0.0), ("wdw", 1.0)):
                try:
                    v = float(getattr(reverse, attr, exp) if getattr(reverse, attr, None) is not None else exp)
                except Exception:
                    v = exp
                if abs(v - exp) >= eps:
                    # For 2D checks, only xdx/ydy/xdy/ydx/xdw/ydw matter; but be strict
                    if attr in ("xdx", "xdy", "ydx", "ydy", "xdw", "ydw", "wdw"):
                        return False
                    # ignore z-related for 2D
                    if attr in ("xdx", "xdy", "ydx", "ydy", "xdw", "ydw"):
                        return False
            return abs(a - 1.0) < eps and abs(d - 1.0) < eps and abs(b) < eps and abs(c) < eps and abs(tx) < eps and abs(ty) < eps
        except Exception:
            return False

def _safe_clear_cached(node) -> None:
    try:
        node.cached_model = None
    except Exception:
        pass


def _safe_assign_origin(leaf, node) -> None:
    try:
        leaf._dissolve_origin = node
    except:
        pass


class WalkMixin:
    virtual_size: tuple[int, int]  # type: ignore
    _clip_rect: object  # type: ignore
    _clip_poly: object  # type: ignore  # polygon path when reverse non-identity
    _mesh_cache: dict  # type: ignore
    _mesh_cache_cap: int  # type: ignore
    _mesh_deferred_destroy: list[int]  # type: ignore

    def _draw_cached(self, node, children_preview, ctx: WalkCtx) -> bool:
        cached = getattr(node, "cached_model", None)
        if cached is None:
            return False
        c_texs = getattr(cached, "textures", None) or ()
        c_shaders = getattr(cached, "shaders", None) or ()
        multi_tex = isinstance(c_texs, (list, tuple)) and len(c_texs) > 1
        multi_child = len(children_preview) > 1
        mesh_attr_early = getattr(node, "mesh", None)
        bool_mesh = mesh_attr_early is True or mesh_attr_early == "quad"
        special = False
        try:
            special = self._is_dissolve_node(cached) or self._is_imagedissolve_node(cached)
            if not special:
                special = self._is_dissolve_node(node) or self._is_imagedissolve_node(node)
            if not special and isinstance(c_shaders, (list, tuple)):
                special = any(s in ("renpy.dissolve", "dissolve", "renpy.imagedissolve", "image_dissolve") for s in c_shaders)
        except Exception:
            special = False
        if special and multi_child and (self._is_dissolve_node(node) or self._is_dissolve_node(cached)) and not (self._is_imagedissolve_node(node) or self._is_imagedissolve_node(cached)):
            _safe_clear_cached(node)
        elif CachedModelPolicy.should_drop_bake(node, multi_tex, bool_mesh, multi_child, special):
            if host_env_bool("RENPY_HOST_UI_TRACE") and "drop_bake_residual" not in _UI_TRACE_LOGGED:
                _ui_trace_once("drop_bake_residual", f"drop_bake=1 multi_child={int(bool(multi_child))} multi_tex={int(bool(multi_tex))} bool_mesh={int(bool(bool_mesh))} special={int(bool(special))} children_n={len(children_preview) if children_preview is not None else -1}")
            _safe_clear_cached(node)
        elif getattr(node, "cached_model", None) is not None:
            if host_env_bool("RENPY_HOST_UI_TRACE") and "drop_bake_residual" not in _UI_TRACE_LOGGED and multi_child and not special:
                _ui_trace_once("drop_bake_residual", f"drop_bake=0 residual_single_slot_bake multi_child=1 multi_tex={int(bool(multi_tex))} bool_mesh={int(bool(bool_mesh))} special=0 children_n={len(children_preview) if children_preview is not None else -1}")
            if _WALKSTAT_ON:
                try:
                    _walkstat_skip("cache_hit", f"{type(node).__name__} tex={len(tuple(c_texs))} mc={int(bool(multi_child))} sp={int(bool(special))}")
                except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
                    pass
            self._draw_model_like(cached, ctx.ox, ctx.oy)
            return True
        return False

    def _reverse_dest_override(self, child, node, parent_size):
        """hmc_fix3 F2: dest-size shrink for the ``_draw_reverse`` RTT bake.

        Returns ``(dw, dh)`` ONLY when ``_reverse_dest_size`` yields a box
        strictly smaller than the child's logical bake span (scale-DOWN — the
        crop/zoom case). Any unknown size, scale-up (Frame/Solid stretch) or
        failure returns None → the old child-size bake keeps running
        byte-identically (conservative fallback). The dest box does not change
        where the quad is drawn; only the intermediate bake resolution shrinks
        (see render_to_texture ``_dest_size``).
        """
        try:
            dw, dh = self._reverse_dest_size(node, child, parent_size)
            cw, ch = self._node_size(child, default=(0, 0))
            if cw <= 0 or ch <= 0 or dw <= 0 or dh <= 0:
                return None
            if dw < cw and dh < ch:
                return (int(dw), int(dh))
        except Exception:  # noqa: BLE001 -- override must never abort the frame
            return None
        return None

    def _reverse_bake_key_for(self, child, dest_size):
        """hmc_fix3 F3: gate-checked derived key for the reverse bake cache.

        RENPY_HOST_REVERSE_BAKE_CACHE (default on) gates the whole feature;
        a key failure simply disables the cache for this frame (None → normal
        bake, the conservative direction).
        """
        if not reverse_bake_cache_enabled_fallback():
            return None
        try:
            return self._reverse_bake_key(child, dest_size)
        except Exception as e:  # noqa: BLE001 -- key failure must never abort the frame
            _host_draw_fail("reverse_bake_key_for", e)
            return None

    def _draw_reverse(self, node, children, ctx: WalkCtx) -> bool:
        if not children or self._is_dissolve_node(node):
            return False
        nw, nh = self._node_size(node, default=(0, 0))
        if nw <= 0 or nh <= 0 or not self._node_needs_axis_scale(node, children):
            return False
        if len(children) == 1:
            child, cx, cy = children[0]
            _dest = self._reverse_dest_override(child, node, (nw, nh))
            # hmc_fix3 F3: second entry point of the per-frame subtree bake
            # cache (key derived from the same _trans_bake_key fingerprint,
            # namespaced with the dest box; RENPY_HOST_REVERSE_BAKE_CACHE
            # gates it, default on). Key None → normal bake, unchanged.
            _rev_key = self._reverse_bake_key_for(child, _dest)
            tex = self._child_to_texture(
                child, _trans_key=_rev_key, _trans_kind="rev", _dest_size=_dest
            )
            if tex is not None:
                dw, dh = self._reverse_dest_size(node, child, (nw, nh))
                self._draw_texture_at(tex, ctx.ox + cx, ctx.oy + cy, (dw, dh))
            else:
                self._draw_node(child, ctx.ox + cx, ctx.oy + cy)
            return True
        for child, cx, cy in children:
            if self._is_render_like(child) and (getattr(child, "reverse", None) is not None or list(self._iter_children(child))):
                self._draw_node(child, ctx.ox + cx, ctx.oy + cy)
                continue
            _dest = self._reverse_dest_override(child, node, (nw, nh))
            _rev_key = self._reverse_bake_key_for(child, _dest)
            tex = self._child_to_texture(
                child, _trans_key=_rev_key, _trans_kind="rev", _dest_size=_dest
            )
            if tex is not None:
                dw, dh = self._reverse_dest_size(node, child, (nw, nh))
                self._draw_texture_at(tex, ctx.ox + cx, ctx.oy + cy, (dw, dh))
            else:
                self._draw_node(child, ctx.ox + cx, ctx.oy + cy)
        return True

    def _draw_dissolve_mid(self, node, children, ctx: WalkCtx) -> bool:
        if not (self._is_dissolve_node(node) and len(children) >= 2):
            return False
        complete = self._dissolve_complete(node)
        if complete is not None and complete <= 0.001:
            child, cx, cy = children[0]
            self._draw_node(child, ctx.ox + cx, ctx.oy + cy)
            return True
        if complete is None or complete >= 0.999:
            child, cx, cy = children[-1]
            self._draw_node(child, ctx.ox + cx, ctx.oy + cy)
            return True
        old_c, ox0, oy0 = children[0]
        new_c, nx0, ny0 = children[-1]
        # Phase 0d bake-attribution: the two _child_to_texture calls below are
        # the dissolve transition's bake triggers; push a caller cookie so
        # nested RTT bakes attribute to "dissolve_mid" instead of rtt_pass.
        _bkc = bake_caller_push(self, "dissolve_mid")
        # Transition old-slot cross-frame content cache (hmc_childtrust):
        # the old child's structural fingerprint is flat across the whole
        # transition band, so its full-screen RTT bake can be pinned and
        # reused whenever the key (type tree + deep texture/mesh handles +
        # uniforms minus u_renpy_dissolve + matrix bits + movie publish seq)
        # matches. Key build failure / any exception → None → normal bake
        # (frame order unaffected). The NEW slot never consults the cache.
        _old_key = None
        try:
            # Cross-frame old-slot cache is only engaged in the mid band
            # (complete is strictly inside (0.001, 0.999) here).
            if SurftreeWalkerCacheGate.enabled() and old_c is not None:
                _old_key = self._trans_bake_key(old_c)
        except Exception as e:  # noqa: BLE001 -- key failure must never abort the frame
            _host_draw_fail("dissolve_mid.trans_bake_key", e)
            _old_key = None
        try:
            old_n = DissolveStrategy.ht_count(HostTexture, int, self._iter_children, old_c, [120])
            new_n = DissolveStrategy.ht_count(HostTexture, int, self._iter_children, new_c, [120])
            textures = []
            for _slot_i, (child, _cx, _cy) in enumerate(children[:2]):
                try:
                    # Old slot (children[0]) only — new slot always bakes.
                    if _slot_i == 0:
                        tex = self._child_to_texture(child, _trans_key=_old_key)
                    else:
                        tex = self._child_to_texture(child)
                except Exception as e:
                    _host_draw_fail("dissolve_mid.child_to_texture", e)
                    tex = None
                if tex is not None:
                    textures.append(tex)
        finally:
            bake_caller_pop(self, _bkc)
        if host_env_bool("RENPY_HOST_UI_TRACE"):
            try:
                if not hasattr(self, "_dissolve_mid_log_n"):
                    self._dissolve_mid_log_n = 0
                n = int(self._dissolve_mid_log_n)
                if n < 12 or (n % 200) == 0:
                    _safe_print(
                        "[DISSOLVE_MID] complete=%.4f old_n=%d new_n=%d tex_n=%d"
                        % (float(complete), old_n, new_n, len(textures))
                    )
                self._dissolve_mid_log_n = n + 1
            except Exception:
                pass
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
            leaf = self._make_model_leaf(w, h, textures[:2], shaders=shaders, uniforms=uniforms)
            _safe_assign_origin(leaf, node)
            # A transition composite bake covers the frame canvas by
            # construction (the two baked children share the transition's
            # full-size box). Mark the frame full-canvas so the host's sparse
            # cmd guard encodes it (upstream draws every frame; suppressing
            # froze the dissolve and pinned chrome-less frames in a fresh RT).
            try:
                vw, vh = self.virtual_size
                if int(vw) >= 1 and int(vh) >= 1 and w >= int(vw) - 20 and h >= int(vh) - 20:
                    self._frame_full_canvas = True
            except Exception:
                pass
            self._draw_model_like(leaf, ctx.ox, ctx.oy)
            return True
        if old_n <= 1 and new_n >= 3:
            self._draw_node(new_c, ctx.ox + nx0, ctx.oy + ny0)
            return True
        if complete < 0.15 and old_n <= 2 and new_n >= 3:
            self._draw_node(new_c, ctx.ox + nx0, ctx.oy + ny0)
            return True
        self._draw_node(old_c, ctx.ox + ox0, ctx.oy + oy0)
        self._draw_node(new_c, ctx.ox + nx0, ctx.oy + ny0)
        return True

    def _draw_imagedissolve_fallback(self, node, children, ctx: WalkCtx) -> bool:
        if not self._is_imagedissolve_node(node):
            return False
        if _WALKSTAT_ON:
            try:
                _walkstat_skip("imd_fallback", f"{type(node).__name__} ch={len(children)} baked={int(getattr(node, 'cached_model', None) is not None)}")
            except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
                pass
        try:
            self.load_all_textures(node)
        except Exception as e:
            _host_draw_fail("dissolve_fallback.load_all_textures", e)
        # Phase 0d bake-attribution: imagedissolve fallback bakes its slot
        # children through _child_to_texture — push a caller cookie so nested
        # RTT bakes attribute to "imd_fallback" instead of rtt_pass.
        _bkc = bake_caller_push(self, "imd_fallback")
        try:
            cm = getattr(node, "cached_model", None)
            if cm is not None:
                self._draw_model_like(cm, ctx.ox, ctx.oy)
                return True
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
        finally:
            bake_caller_pop(self, _bkc)
        if textures:
            w, h = self._node_size(node)
            uniforms = getattr(node, "uniforms", None)
            leaf = self._make_model_leaf(w, h, textures, shaders=getattr(node, "shaders", None), uniforms=uniforms)
            _safe_assign_origin(leaf, node)
            self._draw_model_like(leaf, ctx.ox, ctx.oy)
            return True
        for child, cx, cy in children:
            self._draw_node(child, ctx.ox + cx, ctx.oy + cy)
        return True

    def _draw_node_inner_body(self, node, ox=0.0, oy=0.0, ctx: WalkCtx | None = None):
        # T5: WalkCtx explicit — callers should pass ctx; ox/oy kept for compat 1 version.
        if ctx is None:
            ctx = WalkCtx(ox=float(ox), oy=float(oy), budget=120, clip_rect=getattr(self, "_clip_rect", None), clip_poly=getattr(self, "_clip_poly", None))
        if isinstance(node, int) and not isinstance(node, bool):
            if node > 0:
                class _TexLeaf:
                    pass
                leaf = _TexLeaf()
                leaf.texture = node
                leaf.mesh = True
                self._draw_model_like(leaf, ctx.ox, ctx.oy)
            return
        if isinstance(node, HostTexture):
            try:
                import renpy_host  # type: ignore
                if node.handle > 0 and hasattr(renpy_host, "touch_texture"):
                    renpy_host.touch_texture(int(node.handle))
            except Exception:
                pass
            self._draw_model_like(node, ctx.ox, ctx.oy)
            return
        children_preview = list(self._iter_children(node))
        if self._draw_cached(node, children_preview, ctx):
            return
        children = children_preview
        if self._draw_reverse(node, children, ctx):
            return
        # Stable subtree model cache (RENPY_HOST_STATIC_MODEL_CACHE, default on):
        # a render-like subtree whose fingerprint is unchanged on 2 consecutive
        # product frames is drawn from its pinned RTT as ONE quad instead of
        # recursing into it. Placed AFTER _draw_cached/_draw_reverse so both
        # existing bake paths keep their semantics and telemetry, and gated
        # inside _sm_try_draw for nodes owned by the ctex / dissolve paths.
        # Disabled → returns immediately (per-node walk, unchanged).
        if self._sm_try_draw(node, ctx):
            return
        ctex = getattr(node, "cached_texture", None)
        if ctex is not None and not children:
            if _WALKSTAT_ON:
                try:
                    _walkstat_skip("ctex_leaf", f"{type(node).__name__}")
                except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
                    pass
            self._draw_texture_at(ctex, ctx.ox, ctx.oy, self._node_size(node))
            return
        if ctex is not None and len(children) == 1 and isinstance(children[0][0], (HostTexture, int)):
            if _WALKSTAT_ON:
                try:
                    _walkstat_skip("ctex_sub", f"{type(node).__name__}")
                except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
                    pass
            nw, nh = self._node_size(node, default=(0, 0))
            child, cx, cy = children[0]
            self._draw_node(child, ctx.ox + cx, ctx.oy + cy)
            return
        mesh_attr = getattr(node, "mesh", None)
        has_model_payload = (mesh_attr is not None or getattr(node, "vertices", None) is not None or getattr(node, "texture", None) is not None or getattr(node, "textures", None) or getattr(node, "pipeline", None) is not None or isinstance(node, HostTexture))
        if has_model_payload and not children:
            self._draw_model_like(node, ctx.ox, ctx.oy)
            return
        if mesh_attr is not None and children:
            has_own = (getattr(node, "vertices", None) is not None or (isinstance(mesh_attr, int) and not isinstance(mesh_attr, bool)) or (mesh_attr is not True and mesh_attr != "quad" and hasattr(mesh_attr, "vertices")) or getattr(node, "texture", None) is not None or getattr(node, "textures", None))
            if has_own:
                self._draw_model_like(node, ctx.ox, ctx.oy)
                for child, cx, cy in children:
                    self._draw_node(child, ctx.ox + cx, ctx.oy + cy)
                return
            if mesh_attr is True or mesh_attr == "quad":
                if self._draw_dissolve_mid(node, children, ctx):
                    return
                if self._draw_imagedissolve_fallback(node, children, ctx):
                    return
                for child, cx, cy in children:
                    self._draw_node(child, ctx.ox + cx, ctx.oy + cy)
                return
            baked = self._bake_mesh_children(node, children)
            if baked is not None:
                self._draw_model_like(baked, ctx.ox, ctx.oy)
                return
            for child, cx, cy in children:
                self._draw_node(child, ctx.ox + cx, ctx.oy + cy)
            return
        shaders_attr = getattr(node, "shaders", None) or ()
        if children and shaders_attr and mesh_attr is None:
            if self._draw_effect_container(node, children, ctx, shaders_attr, mesh_attr):
                return
        if children:
            for child, cx, cy in children:
                self._draw_node(child, ctx.ox + cx, ctx.oy + cy)
            return
        if hasattr(node, "get_size") or hasattr(node, "_pixels"):
            self._draw_model_like(node, ctx.ox, ctx.oy)
            return
        if has_model_payload:
            self._draw_model_like(node, ctx.ox, ctx.oy)

    def _draw_effect_container(self, node, children, ctx: WalkCtx, shaders_attr, mesh_attr) -> bool:
        effect = False
        try:
            from renpy.wgpu.shaders import composition_mode
            effect = any(composition_mode(s) is None for s in shaders_attr)
        except Exception:
            effect = True
        uniforms_attr = getattr(node, "uniforms", None)
        if not effect and isinstance(uniforms_attr, dict):
            effect = any(k in uniforms_attr for k in ("u_renpy_matrixcolor", "u_renpy_blur_log2", "u_renpy_mask_multiplier", "u_renpy_mask_offset", "u_transition", "u_animation"))
        if not effect and any(s in ("renpy.alpha", "alpha") for s in shaders_attr):
            effect = True
        if not effect:
            return False
        if isinstance(uniforms_attr, dict) and any(s in ("renpy.alpha", "alpha") for s in shaders_attr):
            try:
                a_hide = float(uniforms_attr.get("u_renpy_alpha", 1.0))
            except (TypeError, ValueError):
                a_hide = 1.0
            if a_hide <= 0.0:
                if _WALKSTAT_ON:
                    try:
                        _walkstat_skip("alpha0", f"{type(node).__name__} ch={len(children)}")
                    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
                        pass
                return True
        textures = []
        want_multi = any(s in ("image_dissolve", "renpy.imagedissolve", "imagedissolve", "renpy.dissolve", "dissolve") for s in shaders_attr) or (isinstance(uniforms_attr, dict) and ("u_animation" in uniforms_attr or "u_transition" in uniforms_attr or "u_renpy_dissolve" in uniforms_attr))
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
                        slots = [t for t in raw if t is not None and (isinstance(t, HostTexture) or (isinstance(t, int) and not isinstance(t, bool) and t > 0))]
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
                            elif isinstance(t, int) and not isinstance(t, bool) and t > 0:
                                slots.append(HostTexture(t, 1, 1))
                            elif t is not None and not getattr(t, "mesh", None):
                                ht = None
                                if isinstance(t, HostTexture):
                                    ht = t
                                elif self._is_render_like(t) and not getattr(t, "mesh", None):
                                    ht = self._extract_host_texture(t)
                                if ht is not None:
                                    slots.append(ht)
                if not slots:
                    for gc, _gx, _gy in self._iter_children(child):
                        if isinstance(gc, HostTexture) and gc.handle > 0:
                            slots.append(gc)
                        elif isinstance(gc, int) and not isinstance(gc, bool) and gc > 0:
                            slots.append(HostTexture(gc, 1, 1))
                        elif self._is_render_like(gc) and not getattr(gc, "mesh", None):
                            ht = self._extract_host_texture(gc)
                            if ht is not None:
                                slots.append(ht)
                if len(slots) >= 2:
                    textures = list(slots)
                    if len(children) > 1:
                        if _WALKSTAT_ON:
                            try:
                                _walkstat_skip("multi_1child", f"{type(node).__name__} ch={len(children)} sh={list(shaders_attr)[:3]}")
                            except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
                                pass
                    break
        child_draw_ox = ctx.ox
        child_draw_oy = ctx.oy
        if not textures:
            for child, cx, cy in children:
                if getattr(child, "mesh", None) and want_multi:
                    continue
                tex = self._child_to_texture(child)
                if tex is not None:
                    textures.append(tex)
                    if len(textures) == 1 and len(children) == 1:
                        child_draw_ox = ctx.ox + float(cx)
                        child_draw_oy = ctx.oy + float(cy)
        if textures:
            if len(textures) == 1 and (child_draw_ox != ctx.ox or child_draw_oy != ctx.oy):
                tw = float(getattr(textures[0], "w", 0) or 0)
                th = float(getattr(textures[0], "h", 0) or 0)
                if tw <= 0 or th <= 0:
                    tw, th = self._node_size(node)
                w, h = tw, th
            else:
                w, h = self._node_size(node)
            leaf = self._make_model_leaf(w, h, textures, shaders=shaders_attr, uniforms=uniforms_attr)
            _safe_assign_origin(leaf, node)
            self._draw_model_like(leaf, child_draw_ox, child_draw_oy)
            return True
        return False

    def _bake_mesh_children(self, node, children):
        """Shim: canonical in SurftreeMixin (T5)."""
        return SurftreeMixin._bake_mesh_children(self, node, children)  # type: ignore[attr-defined]


__all__ = ["WalkMixin", "WalkCtx", "CachedModelPolicy", "DissolveStrategy", "SurftreeWalkerCacheGate", "ReverseScaler", "reverse_bake_cache_enabled_fallback"]
