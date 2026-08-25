# WGSL Shader Migration (renpy-host / wgpu)

**Status:** Phase 9 AC8 — **wgpu-host v0.6.0** (see `CHANGELOG.md` + `sphinx/source/changelog.rst:8.99.99`)
**Audience:** game authors and engine contributors using `register_shader` / text shaders

## What changed on host

On **renpy-host** builds (`renpy.host_build is True`):

- `renpy.register_shader(...)` **soft-stubs**: no GLSL compile; returns a
  `HostShaderPart` and mirrors the name into `renpy.register_wgsl_shader(...)`
  when available. Required so product init (`_errorhandling` → `_shaders.rpym`
  → full initcode including `00style.rpy`) can complete.
- GLSL text in `variables=` / `vertex_*` / `fragment_*` is **not** auto-translated
  into WGSL pipelines; host rendering uses WGSL builtins / host pipelines.
- `textshader.*` GLSL registrations via `renpy.register_textshader(...)` also
  soft-stub (they call `register_shader("textshader." + name, …)`). Phase 3 host
  text uses `renpy.wgpu.text` bitmap upload for actual glyphs.

SDL/GL reference builds are unchanged. Dual-tree policy: SDL tree source is **not** deleted;
host artifact simply never links SDL (AC2 `ldd` gate).

### Host soft-stub shape

```python
# renpy/gl2/gl2shadercache.py
if getattr(renpy, "host_build", False):
    try:
        import renpy.wgpu.shaders as wgsl
        meta = dict(kwargs)
        meta.setdefault("host_glsl_stub", True)
        wgsl.register_wgsl_shader(name, **meta)
    except Exception:
        pass
    return HostShaderPart(name, **kwargs)
```

Raising from `register_shader` on host was tried and **rejected**: it aborts
`_errorhandling` mid-boot (`load_module("_shaders")`), leaves `initcode_len=0`,
and never creates `style.default` from `00style.rpy` — breaking AC5 main.

## What to use instead

```python
# Host path — register a named WGSL-producing part (store + pipeline key map).
import renpy
renpy.host_build = True  # set by renpy-host process

from renpy.wgpu.shaders import register_wgsl_shader, register_builtin_core, host_pipeline_key

register_builtin_core()  # renpy.* + live2d.* builtins

register_wgsl_shader(
    "mygame.tint",
    priority=300,
    kind="textured",  # maps to a host pipeline key when available
)

# Resolve host pipeline for draw_model:
# key = host_pipeline_key("renpy.dissolve")  # → "dissolve_pipeline"
# pipe = getattr(renpy_host, key)()
```

Builtin parts ported as WGSL-producing registrations (host pipelines in `GpuArena`):

| Part | Host pipeline | Role |
|------|---------------|------|
| `renpy.geometry` | (compose) | clip transform |
| `renpy.texture` | `textured_pipeline` | sample tex0 |
| `renpy.solid` | `solid_pipeline` | flat color |
| `renpy.ftl` | (compose) | premultiply / FTL |
| `renpy.alpha` | (compose) | alpha modulate |
| `renpy.dissolve` / `renpy.imagedissolve` | `dissolve_pipeline` | dissolve |
| `renpy.blur` | `blur_pipeline` | blur (`uniforms[0]=blur_log2`) |
| `renpy.matrixcolor` | `matrixcolor_pipeline` | 4×4 color matrix |
| `renpy.alpha_mask` | `alpha_mask_pipeline` | dual-tex alpha |
| `renpy.mask` | `mask_pipeline` | dual-tex mask mult/offset |
| `live2d.mask` | `live2d_mask_pipeline` | Live2D mask RTT |
| `live2d.inverted_mask` | `live2d_inverted_mask_pipeline` | inverted mask |
| `live2d.colors` | `live2d_colors_pipeline` | multiply/screen |
| `live2d.flip_texture` | `live2d_flip_pipeline` | V flip |

Host text (MVP): bitmap upload via `renpy.wgpu.text` (Pillow glyphs → `create_texture_rgba` →
textured quad). Full `textshader.*` WGSL atlas compose is a later product step; GLSL textshader
registration remains hard-broken on host.

## Color / format contract

- Game RT + swapchain: **`Rgba8Unorm`** (non-sRGB)
- Blend: One / OneMinusSrcAlpha (premultiplied)
- Goldens capture **pre-present** game RT; MAE ≤ 2/255, max delta ≤ 16
- Suite: G01–G08 under `testcases/wgpu_golden/` (`host/scripts/phase9_gates.sh`)

## Draw path

Primary FFI (do not use `submit_frame` as product path):

```python
renpy_host.begin_frame()
renpy_host.draw_model(pipeline_id, mesh_id, texture_id_or_None, texture1=None, uniforms=None)
renpy_host.end_frame_present()
```

RTT:

```python
rtt = renpy_host.create_render_texture(w, h)
renpy_host.begin_frame()
renpy_host.begin_target(rtt)
renpy_host.draw_model(...)
renpy_host.end_target()
renpy_host.end_frame_present()
```

## Dual-tree note

Keep GLSL shaders working for the SDL reference tree. Host-only breakage is intentional and
documented here. Phase 9 **does not delete** SDL/GL sources — strip is host-artifact / link-line
only (`ldd` no `libSDL*`).

## Sphinx / CHANGELOG pointer

- This file is the in-tree migration SSOT for host authors.
- Sphinx page (when wired): `sphinx/source/wgsl_shaders.rst` may mirror this content.
- Game authors: replace GLSL `register_shader` / `register_textshader` with
  `register_wgsl_shader` + host pipeline keys before shipping on renpy-host.
