"""draw_walk - recursive surftree walk (mixin)."""
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

class WalkMixin:
    # Expected attributes on the concrete WgpuDraw (type hints only)
    virtual_size: tuple[int, int]  # type: ignore
    _clip_rect: object  # type: ignore
    _mesh_cache: dict  # type: ignore
    _mesh_cache_cap: int  # type: ignore
    _mesh_deferred_destroy: list[int]  # type: ignore

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



__all__ = ["WalkMixin"]
