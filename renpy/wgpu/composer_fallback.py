"""
Offline fallback composer (host not compiled) — DEPRECATED re-export shim.

This module is kept only for one release of backwards-compat
(``from renpy.wgpu.composer_fallback import emit_wgsl`` still resolves). The
offline WGSL emit + shared helpers are now the single-source-of-truth in
``renpy.wgpu.composer``; this file re-exports them. New code should import
from ``renpy.wgpu.composer`` directly.

The offline ``compose_wgsl`` (merge/validate) logic remains here because it is
the only consumer path when ``renpy_host`` is absent; it delegates emit to the
composer's offline impl (byte-equal to host/renpy-host/src/shader.rs).
"""

from __future__ import annotations

import warnings
from .composer import (  # noqa: F401
    _cache_key,
    _DEFAULT_SOLID,  # type: ignore
    _DEFAULT_TEXTURE,  # type: ignore
    _normalize_partnames,
    _uniform_binding,
    emit_wgsl,
)

from renpy.wgpu import shaders as _shaders

warnings.warn(
    "renpy.wgpu.composer_fallback is deprecated; import emit_wgsl/_uniform_binding/"
    "_cache_key/_normalize_partnames from renpy.wgpu.composer",
    DeprecationWarning,
    stacklevel=2,
)

_UNIFORM_NONE = "none"
_UNIFORM_PARAMS16 = "params16"
_UNIFORM_MATRIXCOLOR16 = "matrixcolor16"

_FALLBACK_DEPRECATED = True


class FallbackComposerError(Exception):
    """Offline validation error (translated to ComposerError by composer.py)."""


def _ensure_builtins() -> None:
    if _shaders.get_wgsl_part(_DEFAULT_TEXTURE) is None:
        _shaders.register_builtin_core()


def _strip_composition_only(sorted_names: Sequence[str]) -> list[str]:
    effect: list[str] = []
    for n in sorted_names:
        if n in _shaders._COMPOSITION_ONLY:
            continue
        effect.append(n)
    if not effect:
        effect = [_DEFAULT_TEXTURE]
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


__all__ = [
    "FallbackComposerError",
    "compose_wgsl",
    "emit_wgsl",
    "_cache_key",
    "_normalize_partnames",
    "_uniform_binding",
]
