"""
Host stub for renpy.gl2.assimp (SDL/assimp Cython extension on SDL tree).

Provides GLTFModel name for defaultstore; real mesh load remains Phase 8/9 sample path
via renpy.wgpu.model procedural helpers.

IMPORTANT: do not import renpy at module load time — host embed installs this
into sys.modules before renpy.import_all finishes.
"""

from __future__ import annotations


class GLTFModel:
    """Importable stand-in — subclass Displayable lazily when first constructed."""

    def __new__(cls, *args, **kwargs):
        # Promote to Displayable subclass on first use so defaultstore can bind the name
        # without requiring renpy.display at install time.
        from renpy.display.displayable import Displayable

        if not issubclass(cls, Displayable):
            # Create a one-shot subclass inheriting Displayable
            class _GLTFModel(Displayable):
                def __init__(self, filename, shader=None, tangents=False, zoom=1.0, report=False, **kw):
                    super().__init__(**kw)
                    self.filename = filename
                    self.shaders = (shader,) if isinstance(shader, str) else (shader or ())
                    self.tangents = tangents
                    self.zoom = zoom
                    self.report = report

                def render(self, width, height, st, at):
                    raise Exception(
                        "GLTFModel is not available on renpy-host yet "
                        "(assimp.pyx is SDL-linked). Use renpy.wgpu.model for host MVP."
                    )

                def visit(self):
                    return []

            # Replace class for subsequent constructions
            globals()["GLTFModel"] = _GLTFModel
            return _GLTFModel(*args, **kwargs)
        return super().__new__(cls)

    def __init__(self, filename, shader=None, tangents=False, zoom=1.0, report=False, **kwargs):
        self.filename = filename
        self.shaders = (shader,) if isinstance(shader, str) else (shader or ())
        self.tangents = tangents
        self.zoom = zoom
        self.report = report


def preload():
    """Stock assimp preload hook — no-op on host."""
    return


def finish_predict():
    """Stock assimp prediction end hook — no-op on host."""
    return


def free_memory():
    """Stock assimp free_memory — no-op on host."""
    return
