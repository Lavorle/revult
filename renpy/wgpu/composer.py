"""
WGSL shader composer (host).

Merges mergeable Snippet IR parts from ``renpy.wgpu.shaders`` into one WGSL
module, creates a host pipeline via ``renpy_host.create_pipeline_wgsl``, and
caches results by sorted part set.

P1.1–P1.4 behavior (layout-legal stacks only):
- Cache key = ``composed:{sha1(sorted_names)[:16]}`` (order-insensitive).
- Composition-only parts (geometry/alpha) are stripped; empty effect set falls
  back to ``renpy.texture`` / ``renpy.solid`` by ``has_texture``.
- Atomic multi-tex parts (dissolve/imagedissolve) refuse the composed path.
- Conflicting non-``none`` uniform layouts (e.g. matrixcolor16 + params16) raise.
- Hooks merge by priority ascending; premul is deferred to the finalizer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Sequence

from renpy.wgpu import shaders as _shaders


class ComposerError(Exception):
    """Raised when composition is illegal or host pipeline creation fails hard."""


@dataclass
class ComposerResult:
    """Result of a successful (or soft-fail) compose."""

    pipeline: int  # host handle (0 if host unavailable / soft fail)
    key: str  # "composed:{hash}"
    partnames: list[str]  # sorted effect parts used
    tex_count: int
    uniform_layout_id: str
    has_uniforms: bool
    wgsl: str  # emitted module (for gates/debug)
    residual: str | None = None


# ---------------------------------------------------------------------------
# Layout / binding helpers (mirror arena.rs build_bind_group_layout)
# ---------------------------------------------------------------------------

_UNIFORM_NONE = "none"
_UNIFORM_PARAMS16 = "params16"
_UNIFORM_MATRIXCOLOR16 = "matrixcolor16"

_DEFAULT_TEXTURE = "renpy.texture"
_DEFAULT_SOLID = "renpy.solid"


def _uniform_binding(tex_count: int) -> int:
    """Binding index for the uniform buffer after textures (arena.rs:955-960)."""
    if tex_count <= 0:
        return 0
    if tex_count == 1:
        return 2
    if tex_count == 2:
        return 3
    return 4


def _indent(body: str, spaces: int = 4) -> str:
    pad = " " * spaces
    lines = body.strip("\n").splitlines()
    if not lines:
        return ""
    return "\n".join(pad + line if line.strip() else line for line in lines)


def _ensure_builtins() -> None:
    """Register core IR parts if the registry is empty (offline / dual-tree)."""
    if _shaders.get_wgsl_part(_DEFAULT_TEXTURE) is None:
        _shaders.register_builtin_core()


def _normalize_partnames(partnames: Iterable[str]) -> list[str]:
    """Unique sorted part set (order-insensitive cache key base)."""
    names: set[str] = set()
    for raw in partnames:
        if raw is None:
            continue
        name = str(raw).strip()
        if not name or name.startswith("-"):
            # GL2-style "-part" exclusion not fully modeled; skip empty / dash-only.
            if name.startswith("-") and len(name) > 1:
                # Treat as exclusion against future adds; drop the positive form.
                names.discard(name[1:])
            continue
        names.add(name)
    return sorted(names)


def _cache_key(sorted_names: Sequence[str]) -> str:
    material = ",".join(sorted_names)
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]
    return f"composed:{digest}"


def _strip_composition_only(sorted_names: Sequence[str]) -> list[str]:
    effect: list[str] = []
    for name in sorted_names:
        ir = _shaders.get_snippet_ir(name)
        if ir is not None and ir.get("composition_only"):
            continue
        part = _shaders.get_wgsl_part(name) or {}
        if part.get("composition_only"):
            continue
        # Unregistered names that only exist as composition aliases.
        if ir is None and _shaders.composition_mode(name) is not None:
            continue
        effect.append(name)
    return effect


def _resolve_effect_parts(
    sorted_names: Sequence[str], *, has_texture: bool
) -> list[str]:
    effect = _strip_composition_only(sorted_names)
    if not effect:
        effect = [_DEFAULT_TEXTURE if has_texture else _DEFAULT_SOLID]
    return effect


def _collect_irs(effect_parts: Sequence[str]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for name in effect_parts:
        ir = _shaders.get_snippet_ir(name)
        if ir is None:
            raise ComposerError(f"unknown shader part: {name!r}")
        out.append((name, ir))
    return out


def _validate_parts(pairs: Sequence[tuple[str, dict]]) -> None:
    """Atomic / mergeability policy for the composed path."""
    if not pairs:
        raise ComposerError("no effect parts to compose")

    atomics = [n for n, ir in pairs if ir.get("atomic")]
    if atomics:
        if len(pairs) == 1:
            raise ComposerError(
                f"atomic part must use prebaked pipeline: {atomics[0]!r}"
            )
        raise ComposerError(
            f"cannot multi-merge atomic part(s): {', '.join(repr(a) for a in atomics)}"
        )

    for name, ir in pairs:
        if ir.get("composition_only"):
            raise ComposerError(
                f"composition-only part leaked into effect set: {name!r}"
            )
        if not _shaders.is_mergeable(name):
            # Registered but not mergeable and not caught as atomic above.
            raise ComposerError(f"part is not mergeable: {name!r}")


def _resolve_layout(
    pairs: Sequence[tuple[str, dict]],
) -> tuple[int, str]:
    """Return (tex_count, uniform_layout_id) or raise on conflict."""
    tex_count = 0
    layout = _UNIFORM_NONE
    for name, ir in pairs:
        tc = int(ir.get("tex_count") or 0)
        if tc < 0 or tc > 3:
            raise ComposerError(f"{name!r} has illegal tex_count={tc}")
        if tc > tex_count:
            tex_count = tc
        part_layout = str(ir.get("uniform_layout_id") or _UNIFORM_NONE)
        if part_layout == _UNIFORM_NONE:
            continue
        if layout == _UNIFORM_NONE:
            layout = part_layout
        elif layout != part_layout:
            raise ComposerError(
                f"conflicting uniform layouts: {layout!r} vs {part_layout!r} "
                f"(e.g. matrixcolor+blur co-merge forbidden in P1)"
            )
    return tex_count, layout


def _merge_hooks(pairs: Sequence[tuple[str, dict]], field_name: str) -> list[dict]:
    hooks: list[dict] = []
    for _name, ir in pairs:
        for h in ir.get(field_name) or []:
            hooks.append({"priority": int(h.get("priority", 0)), "body": str(h.get("body", ""))})
    hooks.sort(key=lambda h: h["priority"])
    return hooks


def emit_wgsl(
    *,
    tex_count: int,
    uniform_layout_id: str,
    vertex_hooks: Sequence[dict],
    fragment_hooks: Sequence[dict],
) -> str:
    """Emit one complete WGSL module for the composed pipeline.

    Bindings match ``arena.rs`` TEXTURED / MATRIXCOLOR / SOLID / BLUR families:
      tex1: binding0 texture, binding1 sampler, binding2 uniform if has_uniforms
      tex0 solid: binding0 uniform if has_uniforms only
    """
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
    elif uniform_layout_id == _UNIFORM_PARAMS16:
        chunks.append(
            """\
struct Params {
    data0: vec4<f32>,
    data1: vec4<f32>,
    data2: vec4<f32>,
    data3: vec4<f32>,
};"""
        )
    elif has_uniforms:
        # Unknown layout id still needs a 64B Params blob for host min_binding_size.
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
        # Mergeable P1 parts use t_color / s_color. Multi-tex atomics never reach here.
        chunks.append("@group(0) @binding(0) var t_color: texture_2d<f32>;")
        chunks.append("@group(0) @binding(1) var s_color: sampler;")
        if tex_count >= 2:
            chunks.append("@group(0) @binding(2) var t_tex1: texture_2d<f32>;")
        if tex_count >= 3:
            chunks.append("@group(0) @binding(3) var t_tex2: texture_2d<f32>;")

    if has_uniforms:
        ub = _uniform_binding(tex_count)
        chunks.append(f"@group(0) @binding({ub}) var<uniform> u: Params;")

    # Vertex stage — default pass-through + optional hooks.
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

    # Fragment stage — seed color, emit hooks by priority, finalize premul.
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


def compose_wgsl(
    partnames: Iterable[str],
    *,
    has_texture: bool = True,
) -> tuple[list[str], int, str, str]:
    """Normalize + merge + emit WGSL without touching the host.

    Returns ``(effect_parts, tex_count, uniform_layout_id, wgsl)``.
    Raises ``ComposerError`` on illegal stacks / missing parts.
    """
    _ensure_builtins()
    sorted_names = _normalize_partnames(partnames)
    effect_parts = _resolve_effect_parts(sorted_names, has_texture=has_texture)
    pairs = _collect_irs(effect_parts)
    _validate_parts(pairs)
    tex_count, layout = _resolve_layout(pairs)
    v_hooks = _merge_hooks(pairs, "vertex_hooks")
    f_hooks = _merge_hooks(pairs, "fragment_hooks")
    wgsl = emit_wgsl(
        tex_count=tex_count,
        uniform_layout_id=layout,
        vertex_hooks=v_hooks,
        fragment_hooks=f_hooks,
    )
    return list(effect_parts), tex_count, layout, wgsl


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
    except Exception as e:  # noqa: BLE001 — host errors become ComposerError
        raise ComposerError(f"create_pipeline_wgsl failed: {e}") from e
    return int(handle)


def _log_residual(msg: str) -> None:
    try:
        print(f"[wgpu.composer residual] {msg}", flush=True)
    except Exception:
        pass


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
        """Compose (or cache-hit) a pipeline for ``partnames``.

        Parameters
        ----------
        partnames:
            Iterable of ``renpy.*`` / custom part names. Order is ignored;
            composition-only parts are stripped before the cache key.
        hard_fail:
            If True (gates), raise ``ComposerError`` on illegal stacks or host
            failure. If False (product), return a ``ComposerResult`` with
            ``residual`` set and ``pipeline=0``, or ``None`` when composition
            itself cannot proceed.
        has_texture:
            Default base when the effect set is empty after stripping
            composition-only parts: texture vs solid.
        """
        try:
            effect_parts, tex_count, layout, wgsl = compose_wgsl(
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
            # Soft product path: return residual-bearing result (pipeline=0).
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
