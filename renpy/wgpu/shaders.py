"""
WGSL shader registration (host).

register_wgsl_shader API + builtin core parts for renpy-host.

P1.0 introduces a Snippet IR on each registered part so a later composer can
merge mergeable fragment/vertex hooks into a single WGSL pipeline. Prebaked
``pipeline=`` factory keys remain the runtime path until the composer lands;
``assert_pipeline_map_honest`` continues to police those keys.
"""

from __future__ import annotations

import renpy

# name → opaque host pipeline key / part metadata (incl. Snippet IR fields)
_WGSL_PARTS: dict[str, dict] = {}

# Map renpy.* part names → renpy_host pipeline factory attribute names.
# Honesty rules:
# - Keys must resolve to a real ``renpy_host.<name>()`` factory, OR
# - Be listed in ``_COMPOSITION_ONLY`` (applied in Python draw path, not a
#   separate WGSL pipeline). Never invent missing factory names.
_PIPELINE_KEYS: dict[str, str] = {
    "renpy.texture": "textured_pipeline",
    "renpy.solid": "solid_pipeline",
    # ftl = full-texture load blit (no model transform). Host MVP uses textured.
    "renpy.ftl": "textured_pipeline",
    "renpy.dissolve": "dissolve_pipeline",
    "renpy.imagedissolve": "imagedissolve_pipeline",
    # Product alias (HuangmeiC dissolve_transform) → same host 3-tex pipeline.
    "image_dissolve": "imagedissolve_pipeline",
    "renpy.blur": "blur_pipeline",
    "renpy.matrixcolor": "matrixcolor_pipeline",
    "renpy.alpha_mask": "alpha_mask_pipeline",
    "renpy.mask": "mask_pipeline",
    # Phase 7 Live2D parts (mirrors renpy/gl2/live2d.py register_shader names).
    "live2d.mask": "live2d_mask_pipeline",
    "live2d.inverted_mask": "live2d_inverted_mask_pipeline",
    "live2d.colors": "live2d_colors_pipeline",
    "live2d.flip_texture": "live2d_flip_pipeline",
}

# Parts that are composition / vertex-color effects, not host pipeline factories.
# geometry: vertex transform (handled by mesh NDC construction).
# alpha: multiplies fragment by u_renpy_alpha / u_renpy_over (vertex color path).
_COMPOSITION_ONLY: dict[str, str] = {
    "renpy.geometry": "mesh_transform",
    "renpy.alpha": "vertex_color_alpha",
}

# Known residual aliases (honest about incomplete GL2 parity).
_RESIDUAL_NOTES: dict[str, str] = {
    "renpy.ftl": "mapped to textured_pipeline (no separate FTL lod bias path)",
}

# ---------------------------------------------------------------------------
# Snippet IR field contract (P1.0)
# ---------------------------------------------------------------------------
# Fields accepted by register_wgsl_shader and stored on _WGSL_PARTS[name]:
#
#   tex_count: int 0..3
#       Number of sampled textures this part contributes.
#   uniform_layout_id: str
#       Layout of the @group(0) uniform block, if any:
#         "none"           — no uniform buffer
#         "params16"       — 4×vec4 (data0..data3), 64 bytes
#         "matrixcolor16"  — 4×vec4 columns (col0..col3), 64 bytes
#   resources: list[str]
#       Symbolic resource names (e.g. "t_color", "s_color") the part binds.
#   vertex_hooks: list[{priority: int, body: str}]
#       Composable vertex WGSL fragments, sorted by priority ascending.
#       Empty ⇒ default pass-through (pos/uv/color) is fine.
#   fragment_hooks: list[{priority: int, body: str}]
#       Composable fragment WGSL fragments. Convention (see snippet-ir.md):
#         - bodies assign to a local ``color: vec4<f32>`` (read-modify-write)
#         - composer wraps hooks, then applies premultiplied-alpha finalization
#   atomic: bool
#       True ⇒ multi-tex / transition effect that must not be multi-part merged
#       (dissolve, imagedissolve). Composer treats atomic parts as prebaked-only.
#   composition_only / composition
#       Non-pipeline parts applied in the Python draw path (geometry/alpha).
#   pipeline
#       Prebaked renpy_host factory attribute name. Forever-stable key for the
#       honesty gate and the current draw path; composer may ignore it when it
#       synthesizes a merged pipeline from IR hooks.
#
# Legacy kwargs (priority, kind, …) are preserved unchanged for call sites.
# ---------------------------------------------------------------------------

_UNIFORM_LAYOUT_NONE = "none"
_UNIFORM_LAYOUT_PARAMS16 = "params16"
_UNIFORM_LAYOUT_MATRIXCOLOR16 = "matrixcolor16"

_SNIPPET_IR_KEYS = (
    "tex_count",
    "uniform_layout_id",
    "resources",
    "vertex_hooks",
    "fragment_hooks",
    "atomic",
    "composition_only",
    "composition",
    "pipeline",
)


def _normalize_hooks(hooks) -> list[dict]:
    """Normalize hook list to ``[{priority, body}, ...]`` sorted by priority.

    Accepts:
      - None / omitted → []
      - list of (priority, body) tuples
      - list of {priority, body} (or {priority, wgsl_body}) dicts
    """
    if not hooks:
        return []
    out: list[dict] = []
    for item in hooks:
        if isinstance(item, dict):
            pri = int(item.get("priority", 0))
            body = item.get("body", item.get("wgsl_body", ""))
            out.append({"priority": pri, "body": str(body)})
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            out.append({"priority": int(item[0]), "body": str(item[1])})
        else:
            raise TypeError(
                f"hook entry must be (priority, body) or "
                f"{{priority, body}}; got {type(item)!r}"
            )
    out.sort(key=lambda h: h["priority"])
    return out


def _apply_snippet_ir_defaults(meta: dict) -> dict:
    """Fill Snippet IR defaults for any missing fields (non-destructive)."""
    if "tex_count" not in meta:
        meta["tex_count"] = 0
    else:
        tc = int(meta["tex_count"])
        if tc < 0 or tc > 3:
            raise ValueError(f"tex_count must be 0..3, got {tc}")
        meta["tex_count"] = tc

    meta.setdefault("uniform_layout_id", _UNIFORM_LAYOUT_NONE)
    meta.setdefault("resources", [])
    if meta["resources"] is None:
        meta["resources"] = []
    else:
        meta["resources"] = list(meta["resources"])

    meta["vertex_hooks"] = _normalize_hooks(meta.get("vertex_hooks"))
    meta["fragment_hooks"] = _normalize_hooks(meta.get("fragment_hooks"))
    meta.setdefault("atomic", False)
    meta.setdefault("composition_only", False)
    return meta


def _sync_to_host(name: str, meta: dict) -> None:
    """Best-effort sync a single part to Rust registry with layered errors."""
    try:
        import renpy_host  # type: ignore
        fn = getattr(renpy_host, "register_shader_part", None)
        if fn is None:
            raise AttributeError("renpy_host.register_shader_part missing")
        vh = [(int(h.get("priority", 0)), str(h.get("body", ""))) for h in meta.get("vertex_hooks") or []]
        fh = [(int(h.get("priority", 0)), str(h.get("body", ""))) for h in meta.get("fragment_hooks") or []]
        fn(
            str(name),
            int(meta.get("tex_count", 0)),
            str(meta.get("uniform_layout_id", _UNIFORM_LAYOUT_NONE)),
            vh,
            fh,
            bool(meta.get("atomic", False)),
            bool(meta.get("composition_only", False)),
        )
    except (ImportError, AttributeError):
        return
    except Exception as e:
        try:
            print(f"[wgpu.shaders residual] sync {name!r}: {e}", flush=True)
        except Exception:
            pass
        return


def register_wgsl_shader(name: str, **kwargs):
    """
    Register a WGSL shader part on host builds.

    Keyword args mirror GLSL register_shader priorities but values are WGSL
    snippets or structured part metadata. P1.0 Snippet IR fields are accepted
    and normalized (see module docstring / snippet-ir.md).
    """
    if not getattr(renpy, "host_build", False):
        pass
    meta = dict(kwargs)
    if "pipeline" not in meta and name in _PIPELINE_KEYS:
        meta["pipeline"] = _PIPELINE_KEYS[name]
    meta = _apply_snippet_ir_defaults(meta)
    _WGSL_PARTS[name] = meta
    _sync_to_host(name, meta)
    return name


def get_wgsl_part(name: str):
    return _WGSL_PARTS.get(name)


def list_wgsl_parts():
    return sorted(_WGSL_PARTS.keys())


def host_pipeline_key(name: str) -> str | None:
    """Return renpy_host pipeline factory name for a renpy.* part, if known.

    Composition-only parts return None (see ``composition_mode``).
    """
    part = _WGSL_PARTS.get(name) or {}
    if part.get("composition_only"):
        return None
    key = part.get("pipeline") or _PIPELINE_KEYS.get(name)
    if key in (None, "geometry", "alpha", "ftl"):
        # Defensive: never return dead factory names.
        return _PIPELINE_KEYS.get(name)
    return key


def composition_mode(name: str) -> str | None:
    """How a non-pipeline part is applied (or None if it has a host pipeline)."""
    part = _WGSL_PARTS.get(name) or {}
    if part.get("composition_only"):
        return part.get("composition") or _COMPOSITION_ONLY.get(name)
    return _COMPOSITION_ONLY.get(name)


def residual_note(name: str) -> str | None:
    return _RESIDUAL_NOTES.get(name)


def get_snippet_ir(name: str) -> dict | None:
    """Return the Snippet IR dict for ``name``, or None if unregistered.

    The returned dict is a shallow copy of the IR-relevant fields only so
    callers cannot mutate the registry by accident.
    """
    part = _WGSL_PARTS.get(name)
    if part is None:
        return None
    ir = {k: part.get(k) for k in _SNIPPET_IR_KEYS if k in part}
    # defensive copies of mutable fields
    if "resources" in ir and ir["resources"] is not None:
        ir["resources"] = list(ir["resources"])
    if "vertex_hooks" in ir and ir["vertex_hooks"] is not None:
        ir["vertex_hooks"] = [dict(h) for h in ir["vertex_hooks"]]
    if "fragment_hooks" in ir and ir["fragment_hooks"] is not None:
        ir["fragment_hooks"] = [dict(h) for h in ir["fragment_hooks"]]
    return ir


def is_atomic(name: str) -> bool:
    """True if ``name`` is registered as an atomic (non-mergeable multi-tex) part."""
    part = _WGSL_PARTS.get(name)
    if part is None:
        return False
    return bool(part.get("atomic"))


def is_mergeable(name: str) -> bool:
    """True if ``name`` has Snippet IR and can be multi-part composed.

    A part is mergeable when it is registered, not composition_only, and not
    atomic. (Missing hooks still count — empty hooks mean pass-through.)
    """
    part = _WGSL_PARTS.get(name)
    if part is None:
        return False
    if part.get("composition_only"):
        return False
    if part.get("atomic"):
        return False
    # Require at least one IR field beyond defaults to have been intentional;
    # all register_wgsl_shader paths now always set IR defaults, so presence
    # in _WGSL_PARTS with composition_only=False and atomic=False is enough.
    return True


def assert_pipeline_map_honest(renpy_host_mod=None) -> list[str]:
    """Return list of dishonest/missing mappings (empty = OK).

    When ``renpy_host_mod`` is provided, also checks callables exist.
    """
    problems: list[str] = []
    for name, key in _PIPELINE_KEYS.items():
        if key in ("geometry", "alpha", "ftl"):
            problems.append(f"{name}→{key} is a dead factory name")
        if renpy_host_mod is not None and not callable(getattr(renpy_host_mod, key, None)):
            problems.append(f"{name}→{key} missing on renpy_host")
    for name in _COMPOSITION_ONLY:
        if host_pipeline_key(name) is not None:
            problems.append(f"{name} composition-only but has pipeline key")
    return problems


# ---------------------------------------------------------------------------
# Builtin Snippet IR bodies (extracted from arena.rs SOLID/TEXTURED/… WGSL)
# ---------------------------------------------------------------------------
# Fragment convention: each body is a composable fragment that reads/writes
# a local ``color: vec4<f32>``. The composer:
#   1. declares ``var color: vec4<f32> = vec4(0.0);`` (or seeds from v.color)
#   2. emits hooks sorted by priority
#   3. finalizes with premultiplied alpha:
#        let a = clamp(color.a, 0.0, 1.0);
#        return vec4(color.rgb * a, a);
# Standalone prebaked pipelines in arena.rs keep their full fs_main; IR is
# the mergeable source of truth for the future composer.

_SOLID_FS = """\
// renpy.solid — vertex color as fragment color (premul deferred to composer)
color = v.color;
"""

_TEXTURED_FS = """\
// renpy.texture / renpy.ftl — sample t_color, modulate by vertex color
// (premul deferred to composer so matrixcolor can operate on straight color)
let tex = textureSample(t_color, s_color, v.uv);
color = tex * v.color;
"""

_MATRIXCOLOR_FS = """\
// renpy.matrixcolor — 4x4 color matrix from uniform columns col0..col3
// Operates on incoming ``color`` (mergeable after texture). Standalone
// prebaked path samples t_color itself (see arena.rs MATRIXCOLOR_WGSL).
let m = mat4x4<f32>(u.col0, u.col1, u.col2, u.col3);
color = m * color;
"""

_BLUR_FS = """\
// renpy.blur — 5-tap cross gaussian; radius from u.data0.x = blur_log2
// Mergeable alone. Composer must refuse co-merge with matrixcolor (layout
// conflict: params16 vs matrixcolor16) — enforced at composer, not IR.
let dims = vec2<f32>(textureDimensions(t_color));
let texel = vec2<f32>(1.0 / max(dims.x, 1.0), 1.0 / max(dims.y, 1.0));
let blur_log2 = u.data0.x;
let radius = max(exp2(blur_log2), 0.5) * max(v.color.a, 0.01);
var acc = vec4<f32>(0.0, 0.0, 0.0, 0.0);
var norm = 0.0;
acc = acc + textureSample(t_color, s_color, v.uv) * 1.0;
norm = norm + 1.0;
acc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(radius, 0.0) * texel) * 0.6;
norm = norm + 0.6;
acc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(-radius, 0.0) * texel) * 0.6;
norm = norm + 0.6;
acc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(0.0, radius) * texel) * 0.6;
norm = norm + 0.6;
acc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(0.0, -radius) * texel) * 0.6;
norm = norm + 0.6;
// Use blur_tex (not tex) so co-merge with renpy.texture's `let tex` is legal WGSL.
let blur_tex = acc / max(norm, 0.0001);
color = blur_tex * vec4<f32>(v.color.r, v.color.g, v.color.b, 1.0);
"""


def register_builtin_core():
    """Phase 2–5 core builtins (geometry/texture/solid + transitions).

    Mergeable parts carry Snippet IR hooks extracted from arena.rs WGSL.
    Atomic multi-tex transitions keep pipeline keys and set atomic=True.
    """
    # Geometry / base — geometry & alpha are composition-only (no host factory).
    register_wgsl_shader(
        "renpy.geometry",
        priority=0,
        kind="geometry",
        composition_only=True,
        composition="mesh_transform",
        tex_count=0,
        uniform_layout_id=_UNIFORM_LAYOUT_NONE,
        resources=[],
        vertex_hooks=[],
        fragment_hooks=[],
        atomic=False,
    )
    register_wgsl_shader(
        "renpy.texture",
        priority=200,
        kind="texture",
        tex_count=1,
        uniform_layout_id=_UNIFORM_LAYOUT_NONE,
        resources=["t_color", "s_color"],
        vertex_hooks=[],
        fragment_hooks=[(200, _TEXTURED_FS)],
        atomic=False,
    )
    register_wgsl_shader(
        "renpy.solid",
        priority=200,
        kind="solid",
        tex_count=0,
        uniform_layout_id=_UNIFORM_LAYOUT_NONE,
        resources=[],
        vertex_hooks=[],
        fragment_hooks=[(200, _SOLID_FS)],
        atomic=False,
    )
    register_wgsl_shader(
        "renpy.ftl",
        priority=500,
        kind="ftl",
        tex_count=1,
        uniform_layout_id=_UNIFORM_LAYOUT_NONE,
        resources=["t_color", "s_color"],
        vertex_hooks=[],
        # Same sample path as renpy.texture; residual note covers lod bias gap.
        fragment_hooks=[(500, _TEXTURED_FS)],
        atomic=False,
    )
    register_wgsl_shader(
        "renpy.alpha",
        priority=300,
        kind="alpha",
        composition_only=True,
        composition="vertex_color_alpha",
        tex_count=0,
        uniform_layout_id=_UNIFORM_LAYOUT_NONE,
        resources=[],
        vertex_hooks=[],
        fragment_hooks=[],
        atomic=False,
    )
    # Phase 5 transitions / RTT effects (WGSL host pipelines)
    # dissolve / imagedissolve are multi-tex atomic — not multi-part mergeable.
    register_wgsl_shader(
        "renpy.dissolve",
        priority=400,
        kind="dissolve",
        tex_count=2,
        uniform_layout_id=_UNIFORM_LAYOUT_PARAMS16,
        resources=["t_old", "s_color", "t_new"],
        vertex_hooks=[],
        fragment_hooks=[],  # atomic: prebaked pipeline only
        atomic=True,
    )
    register_wgsl_shader(
        "renpy.imagedissolve",
        priority=400,
        kind="imagedissolve",
        tex_count=3,
        uniform_layout_id=_UNIFORM_LAYOUT_PARAMS16,
        resources=["t_control", "s_color", "t_bottom", "t_top"],
        vertex_hooks=[],
        fragment_hooks=[],  # atomic: prebaked pipeline only
        atomic=True,
    )
    register_wgsl_shader(
        "renpy.blur",
        priority=400,
        kind="blur",
        tex_count=1,
        uniform_layout_id=_UNIFORM_LAYOUT_PARAMS16,
        resources=["t_color", "s_color"],
        vertex_hooks=[],
        fragment_hooks=[(400, _BLUR_FS)],
        atomic=False,  # mergeable alone; co-merge w/ matrixcolor forbidden at composer
    )
    register_wgsl_shader(
        "renpy.matrixcolor",
        priority=350,
        kind="matrixcolor",
        tex_count=1,
        uniform_layout_id=_UNIFORM_LAYOUT_MATRIXCOLOR16,
        resources=["t_color", "s_color"],
        vertex_hooks=[],
        fragment_hooks=[(350, _MATRIXCOLOR_FS)],
        atomic=False,
    )
    register_wgsl_shader("renpy.alpha_mask", priority=400, kind="alpha_mask")
    register_wgsl_shader("renpy.mask", priority=400, kind="mask")
    # Phase 7 Live2D (WGSL-producing registrations; no Cubism Core required for sample).
    register_wgsl_shader("live2d.mask", priority=200, kind="live2d_mask")
    register_wgsl_shader("live2d.inverted_mask", priority=200, kind="live2d_inverted_mask")
    register_wgsl_shader("live2d.colors", priority=250, kind="live2d_colors")
    register_wgsl_shader("live2d.flip_texture", priority=250, kind="live2d_flip")


# Install on renpy module for call sites.
def _install():
    renpy.register_wgsl_shader = register_wgsl_shader  # type: ignore[attr-defined]
    if getattr(renpy, "host_build", False):
        register_builtin_core()


_install()
