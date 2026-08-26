"""Rect true-geometry regression — T1.

Covers clip/union/colliderect/contains (6 cases).
Robust import: loads rect.py directly via importlib to avoid host_pygame/__init__
pulling renpy_host (Rust extension not present in pytest).
Fallback also supports sys.path insertion for environments where package import works.
"""

import importlib.util
import pathlib
import sys

_RECT_PATH = pathlib.Path(__file__).resolve().parents[1] / "host" / "python" / "host_pygame" / "rect.py"

# Try direct file load first (no package __init__ side effects).
spec = importlib.util.spec_from_file_location("host_pygame.rect", _RECT_PATH)
assert spec and spec.loader, f"cannot load spec for {_RECT_PATH}"
_mod = importlib.util.module_from_spec(spec)
# Ensure the module is discoverable as host_pygame.rect for any downstream imports.
sys.modules.setdefault("host_pygame.rect", _mod)
spec.loader.exec_module(_mod)  # type: ignore[union-attr]
Rect = _mod.Rect




def test_clip_intersect():
    r1 = Rect(0, 0, 10, 10)
    r2 = Rect(5, 5, 10, 10)
    c = r1.clip(r2)
    assert tuple(c) == (5, 5, 5, 5)
    # also with tuple arg
    c2 = r1.clip((5, 5, 10, 10))
    assert tuple(c2) == (5, 5, 5, 5)
    # clipping is symmetric
    c3 = r2.clip(r1)
    assert tuple(c3) == (5, 5, 5, 5)


def test_clip_empty():
    r1 = Rect(0, 0, 10, 10)
    r2 = Rect(20, 20, 5, 5)
    c = r1.clip(r2)
    assert c.w == 0 and c.h == 0
    # also disjoint via tuple
    c2 = r1.clip((20, 20, 5, 5))
    assert c2.w == 0 and c2.h == 0
    # empty on touching edge (no overlap when just adjacent)
    r3 = Rect(10, 0, 10, 10)
    c3 = r1.clip(r3)
    assert c3.w == 0 and c3.h == 0


def test_union():
    r1 = Rect(0, 0, 10, 10)
    r2 = Rect(5, 5, 10, 10)
    u = r1.union(r2)
    assert tuple(u) == (0, 0, 15, 15)
    # also via tuple
    u2 = r1.union((5, 5, 10, 10))
    assert tuple(u2) == (0, 0, 15, 15)
    # non-overlapping union
    r3 = Rect(20, 20, 5, 5)
    u3 = r1.union(r3)
    assert tuple(u3) == (0, 0, 25, 25)


def test_colliderect_true():
    r1 = Rect(0, 0, 10, 10)
    r2 = Rect(5, 5, 10, 10)
    assert r1.colliderect(r2) is True
    assert r2.colliderect(r1) is True
    # with tuple arg
    assert r1.colliderect((5, 5, 10, 10)) is True
    # contained
    assert r1.colliderect(Rect(2, 2, 2, 2)) is True


def test_colliderect_false():
    r1 = Rect(0, 0, 10, 10)
    r2 = Rect(20, 20, 5, 5)
    assert r1.colliderect(r2) is False
    assert r2.colliderect(r1) is False
    # tuple false
    assert r1.colliderect((20, 20, 5, 5)) is False
    # edge-adjacent is not overlapping
    r3 = Rect(10, 0, 10, 10)
    assert r1.colliderect(r3) is False


def test_contains():
    outer = Rect(0, 0, 10, 10)
    inner = Rect(2, 2, 5, 5)
    assert outer.contains(inner) is True
    assert outer.contains((2, 2, 5, 5)) is True
    assert inner.contains(outer) is False
    # exactly equal contains itself
    assert outer.contains(Rect(0, 0, 10, 10)) is True
    # partially outside
    assert outer.contains(Rect(5, 5, 10, 10)) is False
    # touching edge but outside due to width
    assert outer.contains(Rect(0, 0, 11, 10)) is False
