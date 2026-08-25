"""draw_model - model-leaf emit + node helpers (mixin)."""
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
from .host_texture import HostTexture


class ModelMixin:
    # Expected attributes on the concrete WgpuDraw (type hints only)
    virtual_size: tuple[int, int]  # type: ignore
    _clip_rect: object  # type: ignore
    _mesh_cache: dict  # type: ignore
    _mesh_cache_cap: int  # type: ignore
    _mesh_deferred_destroy: list[int]  # type: ignore

    def _draw_model_like(self, node, ox=0.0, oy=0.0):
        """Emit one draw_model for a Model-like / textured leaf."""

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



__all__ = ["ModelMixin"]
