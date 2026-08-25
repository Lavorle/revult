"""draw_pipeline — pipeline/uniform/draw_* mixin extracted from draw.py."""
from __future__ import annotations

from collections.abc import Sequence

from .host_texture import HostTexture


class PipelineMixin:
    _solid_pipe: object
    _tex_pipe: object
    _dissolve_pipe: object
    _imagedissolve_pipe: object
    _blur_pipe: object
    _matrixcolor_pipe: object
    _alpha_mask_pipe: object
    _mask_pipe: object
    _live2d_mask_pipe: object
    _live2d_inverted_mask_pipe: object
    _live2d_colors_pipe: object
    _live2d_flip_pipe: object
    _quad_mesh: object
    virtual_size: tuple[int, int]
    _clip_rect: object
    _rtt_free: dict
    _rtt_prev_frame: list
    _rtt_curr_frame: list

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
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            def composition_mode(_n):
                return None

            def host_pipeline_key(_n):
                return None

            def is_atomic(_n):
                return False

            def is_mergeable(_n):
                return False

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
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                    except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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


    def _matrix_to_floats(self, matrix) -> list | None:
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
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
        try:
            m = list(matrix.m)  # type: ignore[attr-defined]
            u = [float(x) for x in m[:16]]
            while len(u) < 16:
                u.append(0.0)
            return u
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            return None


    def _pack_uniforms(self, uniforms, shaders) -> list | None:
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


    def draw_textured(self, texture: int, mesh: int | None = None):

        self._ensure_pipes()
        self._dm(self._tex_pipe, mesh or self._quad_mesh, texture)


    def draw_dissolve(self, texture: int, mesh: int | None = None):
        """Draw with renpy.dissolve pipeline (alpha from vertex color / tex)."""

        self._ensure_pipes()
        self._dm(self._dissolve_pipe, mesh or self._quad_mesh, texture)


    def draw_blur(
        self,
        texture: int,
        blur_log2: float = 2.0,
        mesh: int | None = None,
    ):
        """Draw with renpy.blur pipeline; uniforms[0] = blur_log2."""

        self._ensure_pipes()
        u = [float(blur_log2)] + [0.0] * 15
        self._dm(
            self._blur_pipe, mesh or self._quad_mesh, texture, None, u
        )


    def draw_matrixcolor(
        self,
        texture: int,
        matrix: Sequence[float],
        mesh: int | None = None,
    ):
        """Draw with renpy.matrixcolor; matrix is 16 floats (column-major 4x4)."""

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
        mesh: int | None = None,
        alpha_only: bool = False,
    ):
        """Draw dual-tex mask (or alpha_mask if alpha_only)."""

        self._ensure_pipes()
        pipe = self._alpha_mask_pipe if alpha_only else self._mask_pipe
        u = [float(mult), float(offset)] + [0.0] * 14
        self._dm(pipe, mesh or self._quad_mesh, src, mask, u)

    # --- Phase 8 model mesh helpers ------------------------------------------


    def create_mesh(
        self,
        vertices: Sequence[float],
        indices: Sequence[int] | None = None,
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
        texture: int | None = None,
        pipeline: int | None = None,
        uniforms: Sequence[float] | None = None,
    ) -> None:
        """
        Draw an uploaded mesh via the primary draw_model path.

        texture=None → solid pipeline; otherwise textured (or explicit pipeline).
        """

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
        mesh: int | None = None,
        inverted: bool = False,
    ):
        """Draw with live2d.mask / live2d.inverted_mask (mask UV from pos*ppu+offset)."""

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
        mesh: int | None = None,
    ):
        """Draw with live2d.colors (multiply then screen blend)."""

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


    def draw_live2d_flip(self, texture: int, mesh: int | None = None):
        """Draw with live2d.flip_texture (V flip)."""

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
                    except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        pass
                    try:
                        renpy_host.end_target()
                    except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            try:
                print(
                    f"WgpuDraw.is_pixel_opaque: {type(e).__name__}: {e}",
                    flush=True,
                )
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass
            return True

__all__ = ["PipelineMixin"]
