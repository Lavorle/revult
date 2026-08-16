"""
Host stub for renpy.uguu (SDL/GL Cython extension — Class B).

Under renpy-host, product code only needs a handful of GL blend-func enum
constants from renpy.config.init(). Real GL entry points are never called on
the wgpu path (WgpuDraw does not use renpy.uguu).

Installed by renpy-host embed as:
    sys.modules["renpy.uguu.uguu"] = renpy_uguu_host
    sys.modules["renpy.uguu.gl"] = renpy_uguu_host

Dual-tree: does not replace renpy/uguu/*.pyx; only pre-seeds sys.modules so
`from renpy.uguu.uguu import *` resolves without the SDL-linked .so.
"""

from __future__ import annotations

# GLES2 / GL blend and common constants (Khronos values).
GL_ZERO = 0
GL_ONE = 1
GL_SRC_COLOR = 0x0300
GL_ONE_MINUS_SRC_COLOR = 0x0301
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_DST_ALPHA = 0x0304
GL_ONE_MINUS_DST_ALPHA = 0x0305
GL_DST_COLOR = 0x0306
GL_ONE_MINUS_DST_COLOR = 0x0307
GL_SRC_ALPHA_SATURATE = 0x0308

GL_FUNC_ADD = 0x8006
GL_MIN = 0x8007
GL_MAX = 0x8008
GL_FUNC_SUBTRACT = 0x800A
GL_FUNC_REVERSE_SUBTRACT = 0x800B

GL_CONSTANT_COLOR = 0x8001
GL_ONE_MINUS_CONSTANT_COLOR = 0x8002
GL_CONSTANT_ALPHA = 0x8003
GL_ONE_MINUS_CONSTANT_ALPHA = 0x8004

# A few more that may be referenced by shaders / model code on import.
GL_TEXTURE_2D = 0x0DE1
GL_RGBA = 0x1908
GL_UNSIGNED_BYTE = 0x1401
GL_FLOAT = 0x1406
GL_TRIANGLES = 0x0004
GL_BLEND = 0x0BE2
GL_SCISSOR_TEST = 0x0C11
GL_DEPTH_TEST = 0x0B71
GL_CULL_FACE = 0x0B44
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_FRAMEBUFFER = 0x8D40
GL_COLOR_ATTACHMENT0 = 0x8CE0
GL_NEAREST = 0x2600
GL_LINEAR = 0x2601
GL_CLAMP_TO_EDGE = 0x812F
GL_REPEAT = 0x2901
GL_TEXTURE0 = 0x84C0
GL_ARRAY_BUFFER = 0x8892
GL_ELEMENT_ARRAY_BUFFER = 0x8893
GL_STATIC_DRAW = 0x88E4
GL_DYNAMIC_DRAW = 0x88E8
GL_FRAGMENT_SHADER = 0x8B30
GL_VERTEX_SHADER = 0x8B31
GL_COMPILE_STATUS = 0x8B81
GL_LINK_STATUS = 0x8B82
GL_TRUE = 1
GL_FALSE = 0


def _noop(*_a, **_k):
    return None


def _noop_int(*_a, **_k):
    return 0


def _noop_true(*_a, **_k):
    return True


# Minimal no-op surface so accidental GL calls under host do not crash hard.
glClear = _noop
glClearColor = _noop
glEnable = _noop
glDisable = _noop
glBlendFunc = _noop
glBlendFuncSeparate = _noop
glBlendEquation = _noop
glBlendEquationSeparate = _noop
glViewport = _noop
glScissor = _noop
glActiveTexture = _noop
glBindTexture = _noop
glBindFramebuffer = _noop
glBindBuffer = _noop
glBindVertexArray = _noop
glUseProgram = _noop
glDrawArrays = _noop
glDrawElements = _noop
glGenTextures = _noop_int
glGenBuffers = _noop_int
glGenFramebuffers = _noop_int
glCreateProgram = _noop_int
glCreateShader = _noop_int
glGetError = _noop_int


def glGetString(*_a, **_k):
    # Named function so renpy import_all backup can pickle this module.
    return b"renpy-host-uguu-stub"


glGetIntegerv = _noop

# ptr helper used by real uguu for buffer views — host never calls real GL.
class ptr:  # noqa: N801 — match renpy.uguu.uguu.ptr name
    def __init__(self, o=None, ro=True):
        self.obj = o

    def __repr__(self):
        return f"<host-uguu.ptr {self.obj!r}>"


def get_ptr(o):
    return ptr(o)


__all__ = [n for n in globals() if n.startswith("GL_") or n in ("ptr", "get_ptr")]
