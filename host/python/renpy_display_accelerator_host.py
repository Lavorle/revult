"""
Host stand-in for renpy.display.accelerator (SDL/gil-copy + RenderTransform).

`nogil_copy` becomes a software surface blit.

`RenderTransform` is a pure-Python port of the Cython accelerator path that
implements the properties HuangmeiC relies on for splash/layout:

  - xsize / ysize (absolute)
  - fit: cover | contain | scale-up | scale-down | fill
  - zoom / xzoom / yzoom
  - rotate (non-camera, rotate_pad)
  - crop / corner1/corner2 (basic)
  - alpha / subpixel blit
  - reverse / forward Matrix stamping on the output Render

Camera/perspective are best-effort stubs (product splash does not need them).
final_render ports matrixcolor / shader / uniforms / alpha so HuangmeiC prefs
nav ColorizeMatrix hover and dissolve_transform (image_dissolve + u_animation)
reach WgpuDraw. Full GL mesh acceleration is not available on host.
"""

from __future__ import annotations

import math


def nogil_copy(src, dest):
    """Copy src surface pixels into dest (same size preferred)."""
    if src is None or dest is None:
        return
    try:
        dest.blit(src, (0, 0))
    except Exception:
        sp = getattr(src, "_pixels", None)
        dp = getattr(dest, "_pixels", None)
        if sp is not None and dp is not None:
            n = min(len(sp), len(dp))
            dp[:n] = sp[:n]


def _identity():
    try:
        return renpy.display.render.IDENTITY  # type: ignore[name-defined]
    except Exception:
        return None


def _matrix3(rxdx, rxdy, rydx, rydy, rzdz=1.0):
    """Build reverse Matrix / Matrix2D (WgpuDraw reads xdx/ydy)."""
    # Prefer Matrix2D for axis-aligned (no shear, rzdz==1).
    if abs(rxdy) < 1e-12 and abs(rydx) < 1e-12 and abs(rzdz - 1.0) < 1e-12:
        try:
            from renpy.display.matrix import Matrix2D

            return Matrix2D(float(rxdx), float(rxdy), float(rydx), float(rydy))
        except Exception:
            pass
    try:
        from renpy.display.matrix import Matrix

        # 9-element row-major 3×3.
        return Matrix(
            [
                float(rxdx),
                float(rxdy),
                0.0,
                float(rydx),
                float(rydy),
                0.0,
                0.0,
                0.0,
                float(rzdz),
            ]
        )
    except Exception:
        class _M:
            def __init__(self, xdx, xdy, ydx, ydy):
                self.xdx = float(xdx)
                self.xdy = float(xdy)
                self.ydx = float(ydx)
                self.ydy = float(ydy)

            def inverse(self):
                ix = 1.0 / self.xdx if abs(self.xdx) > 1e-12 else 1.0
                iy = 1.0 / self.ydy if abs(self.ydy) > 1e-12 else 1.0
                return _M(ix, 0.0, 0.0, iy)

        return _M(rxdx, rxdy, rydx, rydy)


class RenderTransform:
    """
    Pure-Python Transform renderer (host replacement for Cython accelerator).

    Critical for HuangmeiC splash:
      transform full_fill: xsize 1920 ysize 1080 fit "cover"
      child splash logo 3840×2160 → mul=0.5 → dest 1920×1080 with reverse scale.
    """

    def __init__(self, transform):
        self.transform = transform
        self.state = getattr(transform, "state", None)

        self.widtho = 0
        self.heighto = 0
        self.perspective = None
        self.cr = None
        self.mr = None
        self.width = 0
        self.height = 0
        self.xsize = None
        self.ysize = None
        self.xo = 0.0
        self.yo = 0.0
        self.clipping = False
        self.reverse = None

    def render(self, width, height, st, at):
        import renpy  # type: ignore

        transform = self.transform
        state = self.state
        if state is None:
            return self._empty(width, height)

        self.widtho = width
        self.heighto = height

        # --- render child with xsize/ysize as available size -----------------
        child = getattr(transform, "child", None)
        if child is None:
            try:
                child = renpy.display.transform.get_null()
            except Exception:
                return self._empty(width, height)

        xsize = getattr(state, "xsize", None)
        ysize = getattr(state, "ysize", None)

        try:
            from renpy.display.core import absolute
        except Exception:
            absolute = None

        widtho = self.widtho
        heighto = self.heighto
        if xsize is not None:
            if absolute is not None:
                try:
                    rel = widtho if getattr(renpy.config, "relative_transform_size", False) else 1
                    xsize = absolute.compute_raw(xsize, rel)
                except Exception:
                    xsize = float(xsize)
            else:
                xsize = float(xsize)
            widtho = xsize
        if ysize is not None:
            if absolute is not None:
                try:
                    rel = heighto if getattr(renpy.config, "relative_transform_size", False) else 1
                    ysize = absolute.compute_raw(ysize, rel)
                except Exception:
                    ysize = float(ysize)
            else:
                ysize = float(ysize)
            heighto = ysize

        self.xsize = xsize
        self.ysize = ysize
        self.widtho = widtho
        self.heighto = heighto

        child_st_base = float(getattr(transform, "child_st_base", 0) or 0)
        try:
            from renpy.display.render import render as renpy_render

            cr = renpy_render(child, widtho, heighto, st - child_st_base, at)
        except Exception:
            try:
                cr = child.render(widtho, heighto, st - child_st_base, at)
            except Exception:
                return self._empty(width, height)

        self.cr = cr
        self.width = float(getattr(cr, "width", 0) or 0)
        self.height = float(getattr(cr, "height", 0) or 0)
        transform.child_size = (self.width, self.height)

        # --- crop (basic) ----------------------------------------------------
        self._cropping()

        # --- size / fit / zoom / rotate --------------------------------------
        self._size_zoom_rotate()

        # --- build final Render ----------------------------------------------
        # Never return the pre-scale child after size_zoom_rotate — that
        # is exactly the splash double-size failure mode.
        Render = None
        identity = None
        try:
            from renpy.display.render import IDENTITY as _IDENTITY
            from renpy.display.render import Render as _Render

            Render = _Render
            identity = _IDENTITY
        except Exception:
            Render = None
            identity = None

        if Render is not None:
            rv = Render(self.width, self.height)
        else:
            # Minimal render-like so gates / product walk still see size + reverse.
            # Mirrors Render.add_shader / add_uniform enough for final_render stamps.
            class _MiniRender:
                def __init__(self, w, h):
                    self.width = w
                    self.height = h
                    self.children = []
                    self.reverse = None
                    self.forward = None
                    self.xclipping = False
                    self.yclipping = False
                    self.shaders = None
                    self.uniforms = None
                    self.properties = None
                    self.alpha = 1.0
                    self.over = 1.0
                    self.nearest = None

                def blit(self, source, pos):
                    self.children.append((source, pos[0], pos[1]))

                def subpixel_blit(self, source, pos):
                    self.blit(source, pos)

                def depends_on(self, *a, **k):
                    return None

                def add_shader(self, shader):
                    if self.shaders is None:
                        self.shaders = (shader,)
                    elif shader not in self.shaders:
                        self.shaders = self.shaders + (shader,)

                def add_uniform(self, name, value):
                    if self.uniforms is None:
                        self.uniforms = {name: value}
                    else:
                        self.uniforms[name] = value

                def add_property(self, name, value):
                    if self.properties is None:
                        self.properties = {name: value}
                    else:
                        self.properties[name] = value

            rv = _MiniRender(self.width, self.height)

        transform.reverse = self.reverse if self.reverse is not None else identity

        if self.reverse is not None and (identity is None or self.reverse is not identity):
            rv.reverse = self.reverse
            try:
                inv = self.reverse.inverse()
                transform.forward = inv
                rv.forward = inv
            except Exception:
                transform.forward = identity
                rv.forward = identity
        else:
            transform.forward = identity
            if self.reverse is None:
                self.reverse = identity

        pos = (self.xo, self.yo)
        # Preserve 0.0: `x or 1.0` collapses ATL hide (alpha 0.0 → 1.0) and
        # forces dual idle/activate dock layers both opaque (AC-Sel2 fail).
        # Match accelerator.pyx: float(state.alpha) with clamp, not truthiness.
        try:
            raw_a = getattr(state, "alpha", 1.0)
            alpha = 1.0 if raw_a is None else float(raw_a)
        except (TypeError, ValueError):
            alpha = 1.0
        if alpha < 0.0:
            alpha = 0.0
        elif alpha > 1.0:
            alpha = 1.0

        try:
            raw_add = getattr(state, "additive", 0.0)
            additive = 0.0 if raw_add is None else float(raw_add)
        except (TypeError, ValueError):
            additive = 0.0
        over = 1.0 - additive

        if alpha <= 0.0:
            # GL2 accelerator.pyx:1071–1072 — keep focus deps, do not blit.
            try:
                rv.depends_on(self.cr, focus=True)
            except Exception:
                pass
        elif getattr(state, "subpixel", False):
            try:
                rv.subpixel_blit(self.cr, pos)
            except Exception:
                try:
                    rv.blit(self.cr, pos)
                except Exception:
                    pass
        else:
            try:
                rv.blit(self.cr, pos)
            except Exception:
                pass

        if self.clipping:
            rv.xclipping = True
            rv.yclipping = True

        # final_render parity with accelerator.pyx:904–985.
        # Alpha alone was not enough: prefs top-nav ColorizeMatrix hover and
        # dissolve_transform (shader image_dissolve + u_animation) never reached
        # WgpuDraw without matrixcolor / shader / uniform stamps.
        self._final_render(rv, st, at, alpha=alpha, over=over)

        transform.offsets = [pos]
        transform.render_size = (self.width, self.height)
        return rv

    def _stamp_shader(self, rv, name):
        try:
            if hasattr(rv, "add_shader"):
                rv.add_shader(name)
                return
            shaders = list(getattr(rv, "shaders", None) or ())
            if name not in shaders:
                shaders.append(name)
            rv.shaders = tuple(shaders)
        except Exception:
            pass

    def _stamp_uniform(self, rv, name, value):
        try:
            if hasattr(rv, "add_uniform"):
                rv.add_uniform(name, value)
                return
            uniforms = dict(getattr(rv, "uniforms", None) or {})
            uniforms[name] = value
            rv.uniforms = uniforms
        except Exception:
            pass

    def _stamp_property(self, rv, name, value):
        try:
            if hasattr(rv, "add_property"):
                rv.add_property(name, value)
                return
            props = dict(getattr(rv, "properties", None) or {})
            props[name] = value
            rv.properties = props
        except Exception:
            pass

    def _final_render(self, rv, st, at, alpha=None, over=None):
        """
        Apply transform final properties to ``rv`` (GL2 accelerator.pyx final_render).

        Ports: matrixcolor, nearest, blend, alpha/additive, state.shader,
        transform.uniforms, gl_properties. Mesh/blur/camera remain out of scope.
        """
        state = self.state
        if state is None or rv is None:
            return

        # --- Matrixcolor (ColorizeMatrix / TintMatrix / IdentityMatrix) --------
        matrixcolor = getattr(state, "matrixcolor", None)
        if matrixcolor is not None:
            matrix = matrixcolor
            try:
                if callable(matrix):
                    matrix = matrix(None, 1.0)
            except Exception:
                matrix = None
            if matrix is not None:
                # GL2 rejects im.matrix; host soft-accepts anything Matrix-like.
                try:
                    from renpy.display.matrix import Matrix as _Matrix  # type: ignore

                    if not isinstance(matrix, _Matrix):
                        # Still stamp if it has Matrix fields (Matrix2D / duck).
                        if not all(
                            hasattr(matrix, a)
                            for a in ("xdx", "ydx", "xdy", "ydy")
                        ):
                            matrix = None
                except Exception:
                    pass
            if matrix is not None:
                self._stamp_shader(rv, "renpy.matrixcolor")
                self._stamp_uniform(rv, "u_renpy_matrixcolor", matrix)

        # --- Nearest / blend --------------------------------------------------
        nearest = getattr(state, "nearest", None)
        try:
            rv.nearest = nearest
        except Exception:
            pass
        if nearest:
            self._stamp_property(rv, "texture_scaling", "nearest")
        elif nearest is not None:
            self._stamp_property(rv, "texture_scaling", "linear_mipmap_nearest")

        blend = getattr(state, "blend", None)
        if blend:
            try:
                import renpy  # type: ignore

                funcs = getattr(renpy.config, "gl_blend_func", None) or {}
                if blend in funcs:
                    self._stamp_property(rv, "blend_func", funcs[blend])
            except Exception:
                pass

        # --- Alpha / additive -------------------------------------------------
        if alpha is None:
            try:
                raw_a = getattr(state, "alpha", 1.0)
                alpha = 1.0 if raw_a is None else float(raw_a)
            except (TypeError, ValueError):
                alpha = 1.0
            if alpha < 0.0:
                alpha = 0.0
            elif alpha > 1.0:
                alpha = 1.0
        if over is None:
            try:
                raw_add = getattr(state, "additive", 0.0)
                additive = 0.0 if raw_add is None else float(raw_add)
            except (TypeError, ValueError):
                additive = 0.0
            over = 1.0 - additive

        try:
            rv.alpha = alpha
            rv.over = over
        except Exception:
            pass

        if (alpha != 1.0) or (over != 1.0):
            self._stamp_shader(rv, "renpy.alpha")
            self._stamp_uniform(rv, "u_renpy_alpha", alpha)
            self._stamp_uniform(rv, "u_renpy_over", over)

        # --- Shader (dissolve_transform: "image_dissolve") --------------------
        shader = getattr(state, "shader", None)
        if shader is not None:
            if isinstance(shader, str):
                self._stamp_shader(rv, shader)
            else:
                try:
                    for name in shader:
                        self._stamp_shader(rv, name)
                except TypeError:
                    self._stamp_shader(rv, shader)

        # --- Uniforms (u_transition / u_animation / custom register_shader) ---
        try:
            import renpy.display.transform as _tf  # type: ignore

            uniform_names = getattr(_tf, "uniforms", None) or set()
        except Exception:
            uniform_names = set()

        for name in list(uniform_names):
            try:
                value = getattr(state, name, None)
            except Exception:
                value = None
            if value is None:
                continue
            # Displayable uniforms → render (GL2 final_render).
            try:
                from renpy.display.displayable import Displayable  # type: ignore

                if isinstance(value, Displayable):
                    try:
                        from renpy.display.render import render as renpy_render

                        value = renpy_render(
                            value, rv.width, rv.height, st, at
                        )
                    except Exception:
                        try:
                            value = value.render(rv.width, rv.height, st, at)
                        except Exception:
                            continue
            except Exception:
                pass
            self._stamp_uniform(rv, name, value)

        # --- GL properties ----------------------------------------------------
        try:
            import renpy.display.transform as _tf  # type: ignore

            gl_props = getattr(_tf, "gl_properties", None) or set()
        except Exception:
            gl_props = set()

        for name in list(gl_props):
            try:
                value = getattr(state, name, None)
            except Exception:
                value = None
            if value is None:
                continue
            # GL props are "gl_*" → property without prefix when possible.
            prop = name[3:] if isinstance(name, str) and name.startswith("gl_") else name
            # Prefer mesh render (mr) if present (GL2); host usually stamps rv.
            target = getattr(self, "mr", None) or rv
            try:
                if hasattr(target, "add_property"):
                    target.add_property(prop, value)
                else:
                    self._stamp_property(rv, prop, value)
            except Exception:
                pass

    def _empty(self, width, height):
        try:
            from renpy.display.render import Render

            return Render(width, height)
        except Exception:

            class _R:
                def __init__(self, w, h):
                    self.width = w
                    self.height = h
                    self.children = []

            return _R(width, height)

    def _cropping(self):
        """
        Crop / corner1/corner2 — host parity with accelerator.pyx:cropping.

        GL2 non-rotate path stamps ``self.clipping`` + negative blit offsets on
        the outer transform Render (clip then reverse-zoom). Wgpu v1 clip is
        axis-aligned only and does **not** forward-map clip under reverse
        (see draw.py ``_clip_push_from_node`` residual). That combination empties
        the crop band for text_config's crop+zoom preview.

        Host therefore uses the rotate-style intermediate clip Render for the
        **no-rotate** path too: crop into a (w×h) Render with xclipping/yclipping,
        then let zoom/reverse scale that band. Draw walk must not peel the clip
        wrapper (C3 / ``_extract_host_texture``). Outer ``self.clipping`` stays
        False so we do not double-clip after reverse shrinks the box.
        """
        state = self.state
        cr = self.cr
        if cr is None or state is None:
            return

        crop = getattr(state, "crop", None)
        if crop is None:
            c1 = getattr(state, "corner1", None)
            c2 = getattr(state, "corner2", None)
            if c1 is not None and c2 is not None:
                x1, y1 = c1
                x2, y2 = c2
                crop = (x1, y1, x2 - x1, y2 - y1)

        if crop is None:
            self.xo = 0.0
            self.yo = 0.0
            return

        try:
            x, y, w, h = crop
        except Exception:
            self.xo = 0.0
            self.yo = 0.0
            return

        # crop_relative (GL2 accelerator.pyx:relative_for_crop + cropping):
        #   int / absolute → absolute pixels (unchanged)
        #   float          → fraction of child size (value * room)
        # Default when state.crop_relative is None is config.crop_relative_default
        # (True since Ren'Py 8.x). Product text_config uses absolute pixel crop
        # (0, 825, 1920, 255) — must NOT multiply ints by child size, or the
        # intermediate clip becomes ~3.6M×275k and zoom paints a full-canvas
        # street scene over the preferences panel.
        #
        # Keep original types until after relative resolution so int vs float
        # identity is preserved (float(825) would be treated as fraction).
        crop_relative = getattr(state, "crop_relative", None)
        if crop_relative is None:
            try:
                import renpy  # type: ignore

                crop_relative = bool(
                    getattr(renpy.config, "crop_relative_default", False)
                )
            except Exception:
                crop_relative = False
        if crop_relative:
            cw = float(getattr(cr, "width", 1) or 1)
            ch = float(getattr(cr, "height", 1) or 1)

            def _rel_crop(n, room):
                """Match absolute.compute_raw / GL2 relative_for_crop.

                ATL crop properties are typed as position_or_none, so product
                ``crop (0, 825, 1920, 255)`` arrives as position objects:
                  int 825 → position(absolute=825, relative=0)
                  float 0.5 → position(absolute=0, relative=0.5)
                compute_raw: relative * room + absolute.
                """
                if n is None:
                    return 0.0
                # Prefer real absolute.compute_raw when available (handles
                # position / absolute / int / float correctly).
                try:
                    from renpy.display.position import absolute as _abs  # type: ignore

                    return float(_abs.compute_raw(n, room))
                except Exception:
                    pass
                # position duck-type (absolute + relative attrs).
                if hasattr(n, "absolute") and hasattr(n, "relative"):
                    try:
                        return float(n.relative) * room + float(n.absolute)
                    except Exception:
                        pass
                if isinstance(n, bool):
                    return float(int(n))
                if isinstance(n, int):
                    return float(n)
                tname = type(n).__name__
                if tname == "absolute":
                    return float(n)
                if isinstance(n, float):
                    return float(n) * room
                try:
                    return float(n) * room
                except Exception:
                    return 0.0

            x = _rel_crop(x, cw)
            y = _rel_crop(y, ch)
            w = _rel_crop(w, cw)
            h = _rel_crop(h, ch)
        else:
            # Non-relative: still resolve position objects to pixels (room=1
            # for pure absolute, or use child size for relative component).
            cw = float(getattr(cr, "width", 1) or 1)
            ch = float(getattr(cr, "height", 1) or 1)

            def _abs_crop(n, room):
                try:
                    from renpy.display.position import absolute as _abs  # type: ignore

                    # GL2 non-relative path: relative_for_crop(n, 1, limit)
                    # uses base=1 so floats stay near-zero unless already abs.
                    return float(_abs.compute_raw(n, 1.0 if room else 1.0))
                except Exception:
                    pass
                if hasattr(n, "absolute") and hasattr(n, "relative"):
                    try:
                        return float(n.relative) * 1.0 + float(n.absolute)
                    except Exception:
                        pass
                try:
                    return float(n)
                except Exception:
                    return 0.0

            try:
                x = _abs_crop(x, cw)
                y = _abs_crop(y, ch)
                w = _abs_crop(w, cw)
                h = _abs_crop(h, ch)
            except Exception:
                self.xo = 0.0
                self.yo = 0.0
                return

        # Always intermediate-clip (see docstring). Fail soft → leave uncropped.
        try:
            from renpy.display.render import Render

            tcr = Render(w, h)
            try:
                tcr.subpixel_blit(cr, (-x, -y))
            except Exception:
                tcr.blit(cr, (-x, -y))
            tcr.xclipping = True
            tcr.yclipping = True
            self.cr = tcr
            self.width = w
            self.height = h
            # Offsets consumed by the intermediate blit; outer reverse only zooms.
            self.xo = 0.0
            self.yo = 0.0
            # Do not set self.clipping — intermediate already clips the band.
            # Setting both would clip the post-zoom box again (usually harmless
            # but confuses UI_TRACE n_clip / double-push).
            self.clipping = False
        except Exception:
            # Fallback: GL2 offset+clipping path (needs reverse-forward clip to
            # draw correctly under zoom; may empty on wgpu v1).
            self.xo = -x
            self.yo = -y
            self.width = w
            self.height = h
            self.clipping = True

    def _size_zoom_rotate(self):
        """
        Port of accelerator.pyx size_zoom_rotate — size, fit, zoom, rotate.

        fit "cover" with xsize/ysize is the HuangmeiC splash path:
          scale = [xsize/cw, ysize/ch]; mul = max(scale); dest = mul*cw × mul*ch
          reverse = Matrix(mul, 0, 0, mul) (axis-aligned).
        """
        state = self.state
        if state is None:
            return

        width = float(self.width)
        height = float(self.height)
        xo = float(self.xo)
        yo = float(self.yo)
        xsize = self.xsize
        ysize = self.ysize
        fit = getattr(state, "fit", None)

        rxdx = 1.0
        rxdy = 0.0
        rydx = 0.0
        rydy = 1.0
        rzdz = 1.0

        if (width != 0) and (height != 0):
            maxsize = getattr(state, "maxsize", None)
            mul = None

            if maxsize is not None:
                try:
                    maxsizex, maxsizey = maxsize
                    mul = min(float(maxsizex) / width, float(maxsizey) / height)
                except Exception:
                    mul = None

            scale = []
            if xsize is not None:
                scale.append(float(xsize) / width)
            if ysize is not None:
                scale.append(float(ysize) / height)

            if fit and not scale:
                # fit without explicit size uses available widtho/heighto
                try:
                    scale = [float(self.widtho) / width, float(self.heighto) / height]
                except Exception:
                    scale = []

            if fit is None:
                fit = "fill"

            if scale:
                if fit == "scale-up":
                    mul = max(1.0, *scale)
                elif fit == "scale-down":
                    mul = min(1.0, *scale)
                elif fit == "contain":
                    mul = min(scale)
                elif fit == "cover":
                    mul = max(scale)
                else:
                    # fill / stretch — independent axes via xsize/ysize below
                    if xsize is None:
                        xsize = width
                    if ysize is None:
                        ysize = height

            if mul is not None:
                xsize = mul * width
                ysize = mul * height

            if (xsize is not None) and (ysize is not None) and ((xsize, ysize) != (width, height)):
                nw = float(xsize)
                nh = float(ysize)
                xzoom = 1.0 * nw / width
                yzoom = 1.0 * nh / height
                rxdx = xzoom
                rydy = yzoom
                xo *= xzoom
                yo *= yzoom
                width = xsize
                height = ysize

        # zoom
        zoom = float(getattr(state, "zoom", 1.0) or 1.0)
        xzoom = zoom * float(getattr(state, "xzoom", 1.0) or 1.0)
        yzoom = zoom * float(getattr(state, "yzoom", 1.0) or 1.0)

        if xzoom != 1:
            rxdx *= xzoom
            if xzoom < 0:
                width *= -xzoom
            else:
                width *= xzoom
            xo *= xzoom
            if xzoom < 0:
                xo += width

        if yzoom != 1:
            rydy *= yzoom
            if yzoom < 0:
                height *= -yzoom
            else:
                height *= yzoom
            yo *= yzoom
            if yzoom < 0:
                yo += height

        try:
            import renpy  # type: ignore

            if zoom != 1 and getattr(renpy.config, "zoom_zaxis", False):
                rzdz = zoom
        except Exception:
            pass

        # Rotation (non-camera).
        rotate = getattr(state, "rotate", None)
        if rotate is not None:
            cw = width
            ch = height
            angle = float(rotate) * math.pi / 180.0
            cosa = math.cos(angle)
            sina = math.sin(angle)
            # At this point rxdy/rydx are 0 for pure scale.
            rxdy = rydy * -sina
            rydx = rxdx * sina
            rxdx *= cosa
            rydy *= cosa

            px = cw / 2.0
            if xzoom < 0:
                px = -px
            py = ch / 2.0
            if yzoom < 0:
                py = -py

            if getattr(state, "rotate_pad", True):
                width = height = math.hypot(cw, ch)
                xo = -px * cosa + py * sina
                yo = -px * sina - py * cosa
            else:
                xo = -px * cosa + py * sina
                yo = -px * sina - py * cosa
                x2 = -px * cosa - py * sina
                y2 = -px * sina + py * cosa
                x3 = px * cosa - py * sina
                y3 = px * sina + py * cosa
                x4 = px * cosa + py * sina
                y4 = px * sina - py * cosa
                width = max(xo, x2, x3, x4) - min(xo, x2, x3, x4)
                height = max(yo, y2, y3, y4) - min(yo, y2, y3, y4)
            xo += width / 2.0
            yo += height / 2.0

        self.height = height
        self.width = width
        self.yo = yo
        self.xo = xo

        if (
            abs(rxdx - 1.0) < 1e-12
            and abs(rxdy) < 1e-12
            and abs(rydx) < 1e-12
            and abs(rydy - 1.0) < 1e-12
            and abs(rzdz - 1.0) < 1e-12
        ):
            try:
                from renpy.display.render import IDENTITY

                self.reverse = IDENTITY
            except Exception:
                self.reverse = _matrix3(1, 0, 0, 1)
        else:
            self.reverse = _matrix3(rxdx, rxdy, rydx, rydy, rzdz)
