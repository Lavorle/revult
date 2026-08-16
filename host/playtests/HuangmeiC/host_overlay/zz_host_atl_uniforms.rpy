# Host-only safety net (not present in recovered_project).
# Ensures HuangmeiC dissolve_transform ATL uniforms are registered even if
# product register_shader runs after an early ATL compile, or HostShaderPart
# parsing is skipped. Engine path already registers these via HostShaderPart.
init -2 python:
    try:
        for _n in ("u_transition", "u_animation"):
            renpy.display.transform.add_uniform(_n, "float")
    except Exception:
        pass
