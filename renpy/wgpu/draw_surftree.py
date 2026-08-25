"""
draw_surftree — geometry / UV / clip helpers extracted from draw.py.

This P0 iteration extracts the low-risk geometry/UV/clip helpers as a
mixin (no surftree tree-walk logic yet). Full surftree traversal
(_is_render_like, _extract_host_texture, _child_to_texture, etc.)
remains in draw.py with a TODO for the next iteration.

Usage:
    from .draw_surftree import SurftreeMixin
    class WgpuDraw(RttPoolMixin, SurftreeMixin): ...
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .host_texture import HostTexture


class SurftreeMixin:
    # Expected attributes on the concrete WgpuDraw (type hints only)
    virtual_size: tuple[int, int]  # type: ignore
    _clip_rect: object  # type: ignore
    _mesh_cache: dict  # type: ignore
    _mesh_cache_cap: int  # type: ignore
    _mesh_deferred_destroy: list[int]  # type: ignore

    _CLIP_BIG = 65536.0  # GL2 BIG_PIXELS — unbounded axis when only one flag set.

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

    def _host_tex_uv(self, ht: "HostTexture"):
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

    def _host_tex_is_full(self, ht: "HostTexture") -> bool:
        return (
            ht.x == 0
            and ht.y == 0
            and ht.w == ht.width
            and ht.h == ht.height
        )

    def _resolve_texture(self, tex):
        """Normalize texture-like values to a host handle (int) or None."""
        ht = self._resolve_texture_full(tex)  # type: ignore[attr-defined]
        return ht.handle if ht is not None else None

    def _resolve_texture_full(self, tex):
        """Normalize texture-like values to HostTexture or None (keeps UV rect).

        Also runs present-path dead-handle recovery (class b) so surftree-held
        HostTextures whose GPU slots were destroyed still draw when pixel stash
        has the full-texture RGBA. Does not re-enter load_texture cache-miss.
        """
        if tex is None:
            return None
        # Local import to avoid circular at module load
        from .host_texture import HostTexture

        if isinstance(tex, HostTexture):
            if tex.handle <= 0:
                return None
            return self._ensure_host_texture_alive(tex)  # type: ignore[attr-defined]
        if isinstance(tex, int) and not isinstance(tex, bool):
            # Bare handle: unknown size; HostTexture(1x1 full) → UV 0–1.
            if tex <= 0:
                return None
            ht = HostTexture(tex, 1, 1)
            return self._ensure_host_texture_alive(ht)  # type: ignore[attr-defined]
        # Nested object with .handle / .texture
        for attr in ("handle", "texture"):
            inner = getattr(tex, attr, None)
            if isinstance(inner, HostTexture):
                if inner.handle <= 0:
                    return None
                return self._ensure_host_texture_alive(inner)  # type: ignore[attr-defined]
            if isinstance(inner, int) and not isinstance(inner, bool) and inner > 0:
                ht = HostTexture(inner, 1, 1)
                return self._ensure_host_texture_alive(ht)  # type: ignore[attr-defined]
        # Surface-like → upload (HostTexture or raw handle)
        if hasattr(tex, "get_size") or hasattr(tex, "_pixels"):
            h = self.load_texture(tex, transient=True)  # type: ignore[attr-defined]
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
                self._ensure_pipes()  # type: ignore[attr-defined]
                return self._quad_mesh  # type: ignore[attr-defined]
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



# TODO(P0-next): move remaining surftree traversal helpers:
#   _is_render_like, _is_surface_like, _is_dissolve_node,
#   _is_imagedissolve_node, _dissolve_complete, _reverse_axis_scale,
#   _node_needs_axis_scale, _reverse_dest_size, _extract_host_texture,
#   _solid_reverse_slot_texture, _child_to_texture, _make_model_leaf,
#   _resolve_texture, _resolve_texture_full, _resolve_mesh, _iter_children,
#   _node_size, _bake_mesh_children, etc.  Kept in draw.py this iteration.

__all__ = ["SurftreeMixin"]
