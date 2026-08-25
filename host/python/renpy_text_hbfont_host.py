"""
Host stand-in for renpy.text.hbfont — delegates to ftfont host stub.
"""

from __future__ import annotations

from renpy_text_ftfont_host import FTFace as HBFace  # noqa: F401
from renpy_text_ftfont_host import FTFont as _FTFont


class HBFont(_FTFont):
    def __init__(
        self,
        face,
        size,
        bold,
        italics,
        outline,
        antialias,
        vertical,
        hinting,
        instance=None,
        axis=None,
        features=None,
    ):
        super().__init__(face, size, bold, italics, outline, antialias, vertical, hinting)
        self.instance = instance
        self.axis = axis
        self.features = features
        # Pillow FreeTypeFont can select named VF instances / axis coords.
        # Product prefs headings use instance "Medium" on AlimamaFangYuanTiVF.
        # GL2 lowercases instance names (text.py / hbfont.pyx); resolve case-
        # insensitively so "{instance=medium}" does not silently fall to Regular.
        self._apply_variations()

    def _recompute_metrics(self):
        self.ascent, self.descent = self._compute_metrics()
        self._apply_outline_metrics()

    def _apply_variations(self):
        font = getattr(self, "_font", None)
        if font is None:
            return
        applied = False

        # Named instance — match Pillow's get_variation_names() case-insensitively.
        inst = self.instance
        if isinstance(inst, str) and inst and hasattr(font, "set_variation_by_name"):
            try:
                match = None
                want = inst.lower()
                names = []
                try:
                    names = list(font.get_variation_names() or [])
                except Exception:
                    names = []
                for n in names:
                    label = n.decode("utf-8", "replace") if isinstance(n, bytes) else str(n)
                    if label.lower() == want:
                        match = n
                        break
                if match is not None:
                    font.set_variation_by_name(match)
                    applied = True
                else:
                    # Fall back to exact / encoded attempts (non-listed names).
                    try:
                        font.set_variation_by_name(inst)
                        applied = True
                    except Exception:
                        try:
                            font.set_variation_by_name(inst.encode("utf-8"))
                            applied = True
                        except Exception:
                            pass
            except Exception:
                pass

        # Axis overrides always apply after instance (GL2: named style then axis).
        if isinstance(self.axis, dict) and self.axis and hasattr(font, "set_variation_by_axes"):
            try:
                coords = []
                axes = []
                try:
                    axes = list(font.get_variation_axes() or [])
                except Exception:
                    axes = []
                if axes:
                    for ax in axes:
                        name = ax.get("name") if isinstance(ax, dict) else None
                        if isinstance(name, bytes):
                            name = name.decode("utf-8", "replace")
                        key = (name or "").lower()
                        val = None
                        for k, v in self.axis.items():
                            if str(k).lower() == key:
                                val = float(v)
                                break
                        if val is None:
                            val = float(ax.get("default", 0)) if isinstance(ax, dict) else 0.0
                        coords.append(val)
                else:
                    coords = [float(v) for v in self.axis.values()]
                if coords:
                    font.set_variation_by_axes(coords)
                    applied = True
            except Exception:
                pass

        if applied:
            self._recompute_metrics()
