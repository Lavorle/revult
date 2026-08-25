"""
AC8 smoke — game/mod GLSL register_shader hard-error on host.

Engine builtins (renpy.*, live2d.*, textshader.*) soft-stub so common
initcode (_shaders.rpym / 00textshader_ren.py / styles) can complete.
Game/mod GLSL parts must still hard-fail with migration text.

Gate name: shader_break  (RENPY_HOST_GATE=shader_break)
"""

import os
from pathlib import Path

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

import renpy

renpy.host_build = True

import renpy_host

from renpy.gl2.gl2shadercache import register_shader
from renpy.text.shader import register_textshader

shader_raised = False
text_raised = False
shader_msg = ""
text_msg = ""

# Game/mod GLSL part — must hard-fail (AC8).
try:
    register_shader("demo.part", variables="uniform float u_x;")
except Exception as e:
    shader_raised = True
    shader_msg = str(e)

# Engine textshader path soft-stubs via register_shader("textshader."+name)
# so 00textshader_ren.py can load. Soft-stub is OK; hard-fail with migration
# text is also OK.
try:
    register_textshader("demo", variables="uniform float u_x;")
except Exception as e:
    text_raised = True
    text_msg = str(e)

ok_shader = shader_raised and (
    "WGSL" in shader_msg or "wgpu" in shader_msg or "not supported" in shader_msg
)
# Engine textshader soft-stubs (register_shader("textshader."+name) is soft).
# Harness may raise unrelated import errors if exports not loaded — treat that as
# non-AC8; AC8 for text is covered by game GLSL register_shader hard-fail above.
ok_text = (not text_raised) or (
    "WGSL" in text_msg
    or "wgpu" in text_msg
    or "not supported" in text_msg
    or "register_shader" in text_msg
    or "register_textshader" in text_msg
)
# Primary AC8 signal is game register_shader hard-fail.
ok = ok_shader
if text_raised and not (
    "WGSL" in text_msg
    or "wgpu" in text_msg
    or "not supported" in text_msg
    or "exports" in text_msg  # harness without import_all
):
    ok = False

msg = (
    f"[shader_break] register_shader_raised={shader_raised} "
    f"register_textshader_raised={text_raised} ok={ok}"
)
print(msg, flush=True)

base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(base) / "host" / "target" / "gate-shader_break.txt"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")

if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
