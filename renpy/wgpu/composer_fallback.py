"""
Offline fallback composer (host not compiled).

Mirrors Rust shader.rs logic for environments without renpy_host.
Kept separate from composer.py to keep the SSOT host透传 path lean.
Hash/layout/emit must stay byte-equal to host/renpy-host/src/shader.rs
so that `cargo test` parity and offline lint both pass.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence

from renpy.wgpu import shaders as _shaders

_UNIFORM_NONE = "none"
_UNIFORM_PARAMS16 = "params16"
_UNIFORM_MATRIXCOLOR16 = "matrixcolor16"

_DEFAULT_TEXTURE = "renpy.texture"
_DEFAULT_SOLID = "renpy.solid"


class FallbackComposerError(Exception):
    """Offline validation error (translated to ComposerError by composer.py)."""


def _uniform_binding(tex_count: int) -> int:
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
    if _shaders.get_wgsl_part(_DEFAULT_TEXTURE) is None:
        _shaders.register_builtin_core()


def _normalize_partnames(partnames: Iterable[str]) -> list[str]:
    names: set[str] = set()
    for raw in partnames:
        if raw is None:
            continue
        name = str(raw).strip()
        if not name or name.startswith("-"):
            if name.startswith("-") and len(name) > 1:
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
            raise FallbackComposerError(f"unknown shader part: {name!r}")
        out.append((name, ir))
    return out


def _validate_parts(pairs: Sequence[tuple[str, dict]]) -> None:
    if not pairs:
        raise FallbackComposerError("no effect parts to compose")
    atomics = [n for n, ir in pairs if ir.get("atomic")]
    if atomics:
        if len(pairs) == 1:
            raise FallbackComposerError(
                f"atomic part must use prebaked pipeline: {atomics[0]!r}"
            )
        raise FallbackComposerError(
            f"cannot multi-merge atomic part(s): {', '.join(repr(a) for a in atomics)}"
        )
    for name, ir in pairs:
        if ir.get("composition_only"):
            raise FallbackComposerError(
                f"composition-only part leaked into effect set: {name!r}"
            )
        if not _shaders.is_mergeable(name):
            raise FallbackComposerError(f"part is not mergeable: {name!r}")


def _resolve_layout(
    pairs: Sequence[tuple[str, dict]],
) -> tuple[int, str]:
    tex_count = 0
    layout = _UNIFORM_NONE
    for name, ir in pairs:
        tc = int(ir.get("tex_count") or 0)
        if tc < 0 or tc > 3:
            raise FallbackComposerError(f"{name!r} has illegal tex_count={tc}")
        tex_count = max(tex_count, tc)
        part_layout = str(ir.get("uniform_layout_id") or _UNIFORM_NONE)
        if part_layout == _UNIFORM_NONE:
            continue
        if layout == _UNIFORM_NONE:
            layout = part_layout
        elif layout != part_layout:
            raise FallbackComposerError(
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


def compose_wgsl(
    partnames: Iterable[str],
    *,
    has_texture: bool = True,
) -> tuple[list[str], int, str, str]:
    """Normalize + merge + emit WGSL without touching the host."""
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
