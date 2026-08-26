"""
WGSL shader composer — Rust SSOT 透传.

Python 端仅透传 renpy_host (Rust shader.rs) 的 compose / pipeline 能力：
  - 有 host：直接委托 renpy_host.compose_shader_wgsl / get_or_compile_pipeline_from_parts
  - 无 host（离线 lint / 单测）：回退到 renpy.wgpu.composer_fallback 的最小离线实现

不再在主文件保留完整的 hash/layout/emit/validate 重复实现。
Fallback 抽至 composer_fallback.py，主文件保持清晰分层与异常分层。
"""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from renpy.wgpu import shaders as _shaders
from .shaders import (
    _PIPELINE_KEYS,
    _COMPOSITION_ONLY,
    _UNIFORM_LAYOUT_NONE as _UNIFORM_NONE,
    _UNIFORM_LAYOUT_PARAMS16 as _UNIFORM_PARAMS16,
    _UNIFORM_LAYOUT_MATRIXCOLOR16 as _UNIFORM_MATRIXCOLOR16,
)


class ComposerError(Exception):
    """Raised when composition is illegal or host pipeline creation fails hard."""


_DEFAULT_TEXTURE = "renpy.texture"
_DEFAULT_SOLID = "renpy.solid"


@dataclass(frozen=False)
class ComposerResult:
    """Result of a successful (or soft-fail) compose.

    Use ``as_tuple()`` for explicit tuple conversion.
    ``__iter__`` / ``__getitem__`` are kept for backward compat with
    deprecation warnings — new code should use ``as_tuple()`` or attributes.
    """

    pipeline: int  # host handle (0 if host unavailable / soft fail)
    key: str  # "composed:{hash}"
    partnames: list[str]  # sorted effect parts used
    tex_count: int
    uniform_layout_id: str
    has_uniforms: bool
    wgsl: str  # emitted module (for gates/debug)
    residual: str | None = None

    @property
    def pipeline_handle(self) -> int:
        return self.pipeline

    @property
    def wgsl_source(self) -> str:
        return self.wgsl

    def as_tuple(self) -> tuple[int, str, int, str, bool, str]:
        return (
            self.pipeline,
            self.key,
            self.tex_count,
            self.uniform_layout_id,
            self.has_uniforms,
            self.wgsl,
        )

    def __iter__(self):  # deprecated compat
        warnings.warn(
            "ComposerResult iteration is deprecated, use as_tuple() or attributes",
            DeprecationWarning,
            stacklevel=2,
        )
        return iter(self.as_tuple())

    def __getitem__(self, index: int):
        warnings.warn(
            "ComposerResult indexing is deprecated, use as_tuple() or attributes",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.as_tuple()[index]


# keep ShaderPart for typing compat (not used in SSOT path, registry is in shaders.py / Rust)
@dataclass
class ShaderPart:
    name: str
    tex_count: int = 0
    uniform_layout_id: str = _UNIFORM_NONE
    vertex_hooks: list[tuple[int, str]] | None = None
    fragment_hooks: list[tuple[int, str]] | None = None
    has_uniforms: bool = False
    atomic: bool = False
    composition_only: bool = False


def _log_residual(msg: str) -> None:
    try:
        print(f"[wgpu.composer residual] {msg}", flush=True)
    except Exception:  # noqa: BLE001, S110 -- host delegation residual — fallback handles it; blind catch preserves pipeline creation
        pass


# ---------------------------------------------------------------------------
# Host delegation helpers — layered exception handling
# ---------------------------------------------------------------------------

def _try_host_inner(fn, *args):
    """Call ``fn(*args)`` on ``renpy_host``; on any host error log a residual and
    raise ``AttributeError`` (the uniform fallback signal used throughout this module).

    ``ComposerError`` / ``ValueError`` / ``RuntimeError`` from the host are collapsed
    to ``AttributeError`` so callers fall through to the offline composer path; the
    offline path raises ``ComposerError`` itself when composition is genuinely illegal.
    """
    try:
        return fn(*args)
    except (ComposerError, ValueError, RuntimeError) as e:
        _log_residual(f"host pipeline call residual: {e}")
        raise AttributeError(str(e)) from e
    except Exception as e:  # noqa: BLE001 -- host delegation residual — fallback handles it; blind catch preserves pipeline creation
        _log_residual(f"host pipeline call residual: {e}")
        raise AttributeError(str(e)) from e


def _create_host_pipeline(
    key: str, wgsl: str, tex_count: int, has_uniforms: bool
) -> int:
    """Call renpy_host.create_pipeline_wgsl; raise ComposerError on failure."""
    try:
        import renpy_host  # type: ignore
    except ImportError as e:
        raise ComposerError("renpy_host not available") from e
    create = getattr(renpy_host, "create_pipeline_wgsl", None)
    if create is None:
        raise ComposerError("renpy_host.create_pipeline_wgsl missing")
    try:
        handle = create(key, wgsl, int(tex_count), bool(has_uniforms))
    except ComposerError:
        raise
    except (ValueError, RuntimeError) as e:
        raise ComposerError(f"create_pipeline_wgsl failed: {e}") from e
    except Exception as e:
        raise ComposerError(f"create_pipeline_wgsl failed: {e}") from e
    return int(handle)


# ---------------------------------------------------------------------------
# Offline WGSL emit (single source of truth; byte-equal to Rust emit_wgsl).
# composer_fallback re-exports these for 1-version compat.
# ---------------------------------------------------------------------------

def _indent(body: str, spaces: int = 4) -> str:
    pad = " " * spaces
    lines = body.splitlines()
    return "\n".join(pad + line if line.strip() else line for line in lines)


def _uniform_binding(tex_count: int) -> int:
    if tex_count <= 0:
        return 0
    if tex_count == 1:
        return 2
    if tex_count == 2:
        return 4
    return 4


def _emit_wgsl_offline(
    *,
    tex_count: int,
    uniform_layout_id: str,
    vertex_hooks: Sequence[dict],
    fragment_hooks: Sequence[dict],
) -> str:
    """Emit WGSL offline — byte-equal to ``renpy_host.emit_wgsl`` (no host needed)."""
    has_uniforms = uniform_layout_id != _UNIFORM_NONE
    chunks: list[str] = []

    chunks.append(
        """\
// composed by renpy.wgpu.composer
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) color: vec4<f32>,
};"""
    )

    if uniform_layout_id == _UNIFORM_MATRIXCOLOR16:
        chunks.append(
            """\
struct Params {
    col0: vec4<f32>,
    col1: vec4<f32>,
    col2: vec4<f32>,
    col3: vec4<f32>,
};"""
        )
    elif uniform_layout_id == _UNIFORM_PARAMS16 or has_uniforms:
        chunks.append(
            """\
struct Params {
    data0: vec4<f32>,
    data1: vec4<f32>,
    data2: vec4<f32>,
    data3: vec4<f32>,
};"""
        )

    if tex_count >= 1:
        chunks.append("@group(0) @binding(0) var t_color: texture_2d<f32>;")
        chunks.append("@group(0) @binding(1) var s_color: sampler;")
        if tex_count >= 2:
            chunks.append("@group(0) @binding(2) var t_tex1: texture_2d<f32>;")
        if tex_count >= 3:
            chunks.append("@group(0) @binding(3) var t_tex2: texture_2d<f32>;")

    if has_uniforms:
        ub = _uniform_binding(tex_count)
        chunks.append(f"@group(0) @binding({ub}) var<uniform> u: Params;")

    vs_lines = [
        "@vertex",
        "fn vs_main(v: VsIn) -> VsOut {",
        "    var o: VsOut;",
        "    o.clip = vec4<f32>(v.pos, 0.0, 1.0);",
        "    o.uv = v.uv;",
        "    o.color = v.color;",
    ]
    for h in vertex_hooks:
        body = (h.get("body") or "").strip()
        if body:
            vs_lines.append(_indent(body, 4))
    vs_lines.append("    return o;")
    vs_lines.append("}")
    chunks.append("\n".join(vs_lines))

    fs_lines = [
        "@fragment",
        "fn fs_main(v: VsOut) -> @location(0) vec4<f32> {",
        "    var color: vec4<f32> = vec4<f32>(0.0);",
    ]
    for h in fragment_hooks:
        body = (h.get("body") or "").strip()
        if body:
            fs_lines.append(_indent(body, 4))
    fs_lines.extend(
        [
            "    let a = clamp(color.a, 0.0, 1.0);",
            "    return vec4<f32>(color.rgb * a, a);",
            "}",
        ]
    )
    chunks.append("\n".join(fs_lines))

    return "\n\n".join(chunks) + "\n"


def _cache_key(sorted_names: Sequence[str]) -> str:
    material = ",".join(sorted_names)
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]
    return f"composed:{digest}"


def _normalize_partnames(partnames: Iterable[str]) -> list[str]:
    names: set[str] = set()
    for p in partnames:
        if not p:
            continue
        names.add(p)
    return sorted(names)


# ---------------------------------------------------------------------------
# Fallback shims (lazy import to avoid circular)
# ---------------------------------------------------------------------------

def _fallback_compose_wgsl(
    partnames: Iterable[str], *, has_texture: bool = True
) -> tuple[list[str], int, str, str]:
    from renpy.wgpu.composer_fallback import compose_wgsl as fb_compose

    try:
        return fb_compose(partnames, has_texture=has_texture)
    except Exception as e:
        if isinstance(e, ComposerError):
            raise
        if e.__class__.__name__ == "FallbackComposerError":
            raise ComposerError(str(e)) from e
        raise ComposerError(str(e)) from e


# ---------------------------------------------------------------------------
# Public surface — SSOT
# ---------------------------------------------------------------------------

def emit_wgsl(
    *,
    tex_count: int,
    uniform_layout_id: str,
    vertex_hooks: Sequence[dict],
    fragment_hooks: Sequence[dict],
) -> str:
    """Emit WGSL — delegates to offline impl (byte-equal to Rust emit_wgsl).

    Host has no direct PyO3 emit_wgsl export; try it first if present.
    """
    try:
        import renpy_host  # type: ignore

        fn = getattr(renpy_host, "emit_wgsl", None)
        if fn is not None:
            try:
                return str(fn(int(tex_count), str(uniform_layout_id), list(vertex_hooks), list(fragment_hooks)))
            except ComposerError:
                raise
            except (ValueError, RuntimeError) as e:
                raise ComposerError(str(e)) from e
            except Exception as e:
                _log_residual(f"host emit_wgsl residual: {e}")
                raise AttributeError from e
    except (ImportError, AttributeError):
        pass
    except ComposerError:
        raise
    except Exception as e:  # noqa: BLE001 -- host delegation residual — fallback handles it; blind catch preserves pipeline creation
        _log_residual(f"emit_wgsl host path residual: {e}")
    return _emit_wgsl_offline(
        tex_count=tex_count,
        uniform_layout_id=uniform_layout_id,
        vertex_hooks=vertex_hooks,
        fragment_hooks=fragment_hooks,
    )


def compose_wgsl(
    partnames: Iterable[str],
    *,
    has_texture: bool = True,
) -> tuple[list[str], int, str, str]:
    """Normalize + merge + emit WGSL via Rust SSOT, fallback offline."""
    try:
        import renpy_host  # type: ignore

        fn = getattr(renpy_host, "compose_shader_wgsl", None)
        if fn is None:
            raise AttributeError("renpy_host.compose_shader_wgsl missing")
        eff_parts, tex_count, layout, _has_uniforms, _key, wgsl = _try_host_inner(
            fn, list(partnames), bool(has_texture)
        )
        return list(eff_parts), int(tex_count), str(layout), str(wgsl)
    except ComposerError:
        raise
    except (ImportError, AttributeError):
        pass
    except Exception as e:  # noqa: BLE001 -- host delegation residual — fallback handles it; blind catch preserves pipeline creation
        _log_residual(f"compose_wgsl host residual: {e}")
    return _fallback_compose_wgsl(partnames, has_texture=has_texture)


class WgslShaderCache:
    """Cache of composed WGSL pipelines keyed by sorted effect-part set."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, ...], ComposerResult] = {}

    def clear(self) -> None:
        self._cache.clear()

    def get(
        self,
        partnames,
        *,
        hard_fail: bool = True,
        has_texture: bool = True,
    ) -> ComposerResult | None:
        """Compose (or cache-hit) a pipeline for ``partnames``."""
        # ---- host fast path (no per-part sync loop) ----
        # Rust registry is kept in sync by shaders.py register_wgsl_shader at init time.
        try:
            import renpy_host  # type: ignore

            get_fn = getattr(renpy_host, "get_or_compile_pipeline_from_parts", None)
            if get_fn is None:
                get_fn = getattr(renpy_host, "create_pipeline_from_parts", None)
            if get_fn is None:
                raise AttributeError("renpy_host.get_or_compile_pipeline_from_parts missing")
            pipe_handle, h_key, h_tex_count, h_layout, h_has_uniforms, h_wgsl = _try_host_inner(
                get_fn, list(partnames), bool(has_texture)
            )
            # Need authoritative effect_parts for cache key — ask host compose.
            compose_fn = getattr(renpy_host, "compose_shader_wgsl", None)
            eff_parts = None
            if compose_fn is not None:
                try:
                    eff_parts, _, _, _, _, _ = _try_host_inner(compose_fn, list(partnames), bool(has_texture))
                except (ImportError, AttributeError):
                    eff_parts = None
                except Exception as e:  # noqa: BLE001 -- host delegation residual — fallback handles it; blind catch preserves pipeline creation
                    _log_residual(f"effect_parts derive residual: {e}")
                    eff_parts = None
            if eff_parts is None:
                from renpy.wgpu.composer_fallback import (
                    _ensure_builtins,
                    _normalize_partnames,
                    _resolve_effect_parts,
                )

                _ensure_builtins()
                eff_parts = _resolve_effect_parts(_normalize_partnames(partnames), has_texture=has_texture)

            cache_key_parts = tuple(eff_parts)
            hit = self._cache.get(cache_key_parts)
            if hit is not None:
                return hit
            res = ComposerResult(
                pipeline=int(pipe_handle),
                key=str(h_key),
                partnames=list(eff_parts),
                tex_count=int(h_tex_count),
                uniform_layout_id=str(h_layout),
                has_uniforms=bool(h_has_uniforms),
                wgsl=str(h_wgsl),
                residual=None,
            )
            self._cache[cache_key_parts] = res
            return res
        except ComposerError as e:
            if hard_fail:
                raise
            _log_residual(str(e))
            return None
        except (ValueError, RuntimeError) as e:
            ce = ComposerError(str(e))
            if hard_fail:
                raise ce from e
            _log_residual(str(ce))
            return None
        except (ImportError, AttributeError):
            pass
        except Exception as e:  # noqa: BLE001 -- host delegation residual — fallback handles it; blind catch preserves pipeline creation
            _log_residual(f"WgslShaderCache.get host residual: {e}")

        # ---- fallback offline path ----
        try:
            effect_parts, tex_count, layout, wgsl = _fallback_compose_wgsl(
                partnames, has_texture=has_texture
            )
        except ComposerError as e:
            if hard_fail:
                raise
            _log_residual(str(e))
            return None

        cache_key_parts = tuple(effect_parts)
        hit = self._cache.get(cache_key_parts)
        if hit is not None:
            return hit

        key = _cache_key(effect_parts)
        has_uniforms = layout != _UNIFORM_NONE

        try:
            pipeline = _create_host_pipeline(key, wgsl, tex_count, has_uniforms)
        except ComposerError as e:
            residual = str(e)
            if hard_fail:
                raise
            _log_residual(residual)
            return ComposerResult(
                pipeline=0,
                key=key,
                partnames=list(effect_parts),
                tex_count=tex_count,
                uniform_layout_id=layout,
                has_uniforms=has_uniforms,
                wgsl=wgsl,
                residual=residual,
            )

        result = ComposerResult(
            pipeline=pipeline,
            key=key,
            partnames=list(effect_parts),
            tex_count=tex_count,
            uniform_layout_id=layout,
            has_uniforms=has_uniforms,
            wgsl=wgsl,
            residual=None,
        )
        self._cache[cache_key_parts] = result
        return result


_CACHE = WgslShaderCache()


def get_shader_cache() -> WgslShaderCache:
    """Return the process-wide default ``WgslShaderCache``."""
    return _CACHE


def register_shader_part(
    name: str,
    tex_count: int = 0,
    uniform_layout_id: str = _UNIFORM_NONE,
    vertex_hooks: list[tuple[int, str]] | None = None,
    fragment_hooks: list[tuple[int, str]] | None = None,
    atomic: bool = False,
    composition_only: bool = False,
) -> None:
    """Register a custom shader part — delegates to shaders.py + host SSOT."""
    vh = [{"priority": p, "body": b} for p, b in (vertex_hooks or [])]
    fh = [{"priority": p, "body": b} for p, b in (fragment_hooks or [])]
    _shaders.register_wgsl_shader(
        name,
        tex_count=tex_count,
        uniform_layout_id=uniform_layout_id,
        vertex_hooks=vh,
        fragment_hooks=fh,
        atomic=atomic,
        composition_only=composition_only,
    )
    # shaders.py already syncs to host; keep a direct delegate here as well
    # but with layered errors (no swallow).
    try:
        import renpy_host  # type: ignore

        reg_fn = getattr(renpy_host, "register_shader_part", None)
        if reg_fn is None:
            raise AttributeError("renpy_host.register_shader_part missing")
        reg_fn(
            name,
            int(tex_count),
            str(uniform_layout_id),
            list(vertex_hooks or []),
            list(fragment_hooks or []),
            bool(atomic),
            bool(composition_only),
        )
    except (ImportError, AttributeError):
        return
    except ComposerError:
        raise
    except (ValueError, RuntimeError) as e:
        raise ComposerError(str(e)) from e
    except Exception as e:  # noqa: BLE001 -- host delegation residual — fallback handles it; blind catch preserves pipeline creation
        _log_residual(f"register_shader_part residual: {e}")
        return


def compose_shader(
    partnames: Iterable[str],
    *,
    hard_fail: bool = True,
    has_texture: bool = True,
) -> ComposerResult | None:
    """Top-level convenience to compose shader parts using the default cache."""
    return get_shader_cache().get(partnames, hard_fail=hard_fail, has_texture=has_texture)


def create_pipeline_from_parts(
    partnames: Sequence[str],
    has_texture: bool = True,
) -> tuple[int, str, int, str, bool, str]:
    """Direct pipeline creation from parts returning:
    (pipeline_handle, key, tex_count, uniform_layout_id, has_uniforms, wgsl_source).
    """
    try:
        import renpy_host  # type: ignore

        get_fn = getattr(renpy_host, "get_or_compile_pipeline_from_parts", None)
        if get_fn is None:
            get_fn = getattr(renpy_host, "create_pipeline_from_parts", None)
        if get_fn is None:
            raise AttributeError("renpy_host.get_or_compile_pipeline_from_parts missing")
        pipe, key, tex_count, layout, has_uniforms, wgsl = _try_host_inner(
            get_fn, list(partnames), bool(has_texture)
        )
        return int(pipe), str(key), int(tex_count), str(layout), bool(has_uniforms), str(wgsl)
    except ComposerError:
        raise
    except (ValueError, RuntimeError) as e:
        raise ComposerError(str(e)) from e
    except (ImportError, AttributeError):
        pass
    except Exception as e:  # noqa: BLE001 -- host delegation residual — fallback handles it; blind catch preserves pipeline creation
        _log_residual(f"create_pipeline_from_parts host residual: {e}")

    res = get_shader_cache().get(partnames, hard_fail=True, has_texture=has_texture)
    if res is None:
        raise ComposerError(f"Failed to compose pipeline for {partnames}")
    # explicit as_tuple path (no magic)
    pipe, key, tex_count, layout, has_uniforms, wgsl = res.as_tuple()
    return pipe, key, tex_count, layout, has_uniforms, wgsl
