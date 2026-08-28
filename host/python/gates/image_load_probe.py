"""AC3b exit probe: host_pygame.image.load path/BytesIO + pgrender + arity.

Gate: RENPY_HOST_GATE=image_load_probe
Writes: host/target/gate-image-load-probe.txt

Asset: the_question/game/gui/button/choice_hover_background.png (790x35 RGBA).

Note: run_file prepends imports before this source — no __future__ here.
Fail-closed: ok=False + raise RuntimeError on any required check failure.
"""

import io
import os
import traceback


import host_pygame.image as pimage  # type: ignore
import renpy_host  # type: ignore


def _find_png(base: str) -> str:
    """Resolve choice_hover_background.png via RENPY_HOST_BASE then cwd walk."""
    rel = os.path.join(
        "the_question", "game", "gui", "button", "choice_hover_background.png"
    )
    candidates = [
        os.path.join(base, rel),
        os.path.join(os.getcwd(), rel),
        os.path.abspath(rel),
    ]
    # Walk up from base / cwd a few levels for monorepo layouts.
    for root in (base, os.getcwd()):
        cur = os.path.abspath(root)
        for _ in range(6):
            candidates.append(os.path.join(cur, rel))
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    raise FileNotFoundError(
        "choice_hover_background.png not found; tried: " + ", ".join(candidates[:8])
    )


def _size_of(surf):
    if hasattr(surf, "get_size"):
        return surf.get_size()
    if hasattr(surf, "get_width") and hasattr(surf, "get_height"):
        return (surf.get_width(), surf.get_height())
    w = getattr(surf, "width", None)
    h = getattr(surf, "height", None)
    if w is not None and h is not None:
        return (w, h)
    raise TypeError(f"surface has no size API: {type(surf)!r}")


def main():
    base = os.environ.get("RENPY_HOST_BASE", ".")
    out_path = os.path.join(base, "host", "target", "gate-image-load-probe.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    lines: list[str] = []
    ok = True

    try:
        png_path = _find_png(base)
        with open(png_path, "rb") as f:
            png_bytes = f.read()
        if not png_bytes.startswith(b"\x89PNG"):
            ok = False
            lines.append(f"FAIL: asset is not PNG magic at {png_path!r}")
        else:
            lines.append(f"INFO: asset={png_path} bytes={len(png_bytes)}")

        # 1) Real PNG via path through host_pygame.image.load → size ≠ (1,1)
        try:
            surf_path = pimage.load(png_path)
            sz = _size_of(surf_path)
            if sz == (1, 1):
                ok = False
                lines.append(
                    f"FAIL: path load size==(1,1) (stub/fallback) path={png_path!r}"
                )
            else:
                lines.append(f"PASS: path load size={sz} path={png_path!r}")
        except Exception as e:
            ok = False
            lines.append(f"FAIL: path load raised {type(e).__name__}: {e}")
            surf_path = None

        # 2) Same bytes via BytesIO + namehint → size ≠ (1,1)
        try:
            bio = io.BytesIO(png_bytes)
            surf_bio = pimage.load(bio, "choice_hover_background.png")
            sz2 = _size_of(surf_bio)
            if sz2 == (1, 1):
                ok = False
                lines.append(
                    "FAIL: BytesIO+namehint load size==(1,1) (stub/fallback)"
                )
            else:
                lines.append(f"PASS: BytesIO+namehint load size={sz2}")
        except TypeError as e:
            ok = False
            lines.append(
                f"FAIL: BytesIO+namehint TypeError (arity missing namehint?): {e}"
            )
            surf_bio = None
        except Exception as e:
            ok = False
            lines.append(f"FAIL: BytesIO+namehint load raised {type(e).__name__}: {e}")
            surf_bio = None

        # 3) surf.get_bounding_rect() → tuple(rect) is 4 ints (x,y,w,h)
        surf_for_bounds = surf_path if surf_path is not None else surf_bio
        if surf_for_bounds is None:
            ok = False
            lines.append("FAIL: no surface available for get_bounding_rect")
        else:
            try:
                if not hasattr(surf_for_bounds, "get_bounding_rect"):
                    ok = False
                    lines.append("FAIL: surface missing get_bounding_rect")
                else:
                    rect = surf_for_bounds.get_bounding_rect()
                    t = tuple(rect)
                    if len(t) != 4:
                        ok = False
                        lines.append(
                            f"FAIL: get_bounding_rect tuple len want 4 got {len(t)} {t!r}"
                        )
                    elif not all(isinstance(v, int) and not isinstance(v, bool) for v in t):
                        ok = False
                        lines.append(
                            f"FAIL: get_bounding_rect not 4 ints: {t!r} types="
                            f"{tuple(type(v).__name__ for v in t)}"
                        )
                    else:
                        lines.append(f"PASS: get_bounding_rect={t}")
            except Exception as e:
                ok = False
                lines.append(
                    f"FAIL: get_bounding_rect raised {type(e).__name__}: {e}"
                )

        # 4) renpy.display.pgrender.load_image(BytesIO, name) size ≠ (1,1)
        #    Fail-closed if import/bootstrap fails — plan requires this path.
        #    Light gate mode does not run renpy.import_all(); seed host
        #    accelerator + renpy.pygame so pgrender can import without Cython.
        try:
            import sys as _sys

            if "renpy.display.accelerator" not in _sys.modules:
                try:
                    import renpy_display_accelerator_host as _acc  # type: ignore

                    _sys.modules["renpy.display.accelerator"] = _acc
                except Exception:
                    pass
            # Ensure renpy.pygame is the host shim (embed install_host_pygame
            # already does this; re-bind defensively for isolated re-runs).
            if "renpy.pygame" not in _sys.modules:
                try:
                    import host_pygame as _hp  # type: ignore

                    _sys.modules["renpy.pygame"] = _hp
                    for _n in (
                        "event", "display", "time", "key", "mouse", "surface",
                        "color", "rect", "locals", "error", "joystick",
                        "controller", "scrap", "power", "iostream", "transform",
                        "draw", "image", "sysfont",
                    ):
                        _m = getattr(_hp, _n, None)
                        if _m is not None:
                            _sys.modules[f"renpy.pygame.{_n}"] = _m
                except Exception:
                    pass

            from renpy.display import pgrender  # type: ignore

            # pgrender also does `import renpy` then uses renpy.display.accelerator;
            # bind attribute if package was partially imported without it.
            try:
                import renpy as _r  # type: ignore
                import renpy.display as _rd  # type: ignore

                if not hasattr(_rd, "accelerator") and "renpy.display.accelerator" in _sys.modules:
                    _rd.accelerator = _sys.modules["renpy.display.accelerator"]
                # renpy.config is created by import_all; provide a minimal stub
                # so attribute access during pgrender / log paths does not explode.
                if not hasattr(_r, "config"):
                    import types as _types

                    class _ConfigStub(_types.SimpleNamespace):
                        def __getattr__(self, name):
                            # Prefer empty sequences for plural-ish names so
                            # teardown `for x in config.*` does not TypeError.
                            if name.endswith(("s", "_list")):
                                return []
                            return None

                    _r.config = _ConfigStub(
                        log_to_stdout=False,
                        log_enable=False,
                        developer=False,
                        debug=False,
                    )
            except Exception:
                pass

            surf_pg = pgrender.load_image(
                io.BytesIO(png_bytes), "choice_hover_background.png"
            )
            sz3 = _size_of(surf_pg)
            if sz3 == (1, 1):
                ok = False
                lines.append(
                    "FAIL: pgrender.load_image size==(1,1) (host image.load fallback)"
                )
            else:
                lines.append(f"PASS: pgrender.load_image size={sz3}")
        except Exception as e:
            ok = False
            lines.append(
                f"FAIL: pgrender.load_image unavailable/failed "
                f"{type(e).__name__}: {e}"
            )

        # 5) Garbage input must raise — FAIL if silent 1x1 magenta
        try:
            bad = pimage.load(io.BytesIO(b"not-an-image"), "garbage.png")
            bsz = _size_of(bad)
            # Silent 1x1 magenta stub is a FAIL (fail-closed requirement).
            ok = False
            lines.append(
                f"FAIL: garbage load returned size={bsz} instead of raising "
                f"(silent 1x1 magenta stub is not allowed)"
            )
        except Exception as e:
            lines.append(
                f"PASS: garbage load raised {type(e).__name__}: {e}"
            )

        # 6) Arity: load(fi, namehint) and load(fi, namehint, None) without TypeError
        try:
            s_a = pimage.load(io.BytesIO(png_bytes), "choice_hover_background.png")
            _ = _size_of(s_a)
            lines.append("PASS: load(fi, namehint) accepted")
        except TypeError as e:
            ok = False
            lines.append(f"FAIL: load(fi, namehint) TypeError: {e}")
        except Exception as e:
            # Decode errors are about implementation quality, not arity — still note.
            lines.append(
                f"PASS: load(fi, namehint) arity OK (raised {type(e).__name__}: {e})"
            )

        try:
            s_b = pimage.load(
                io.BytesIO(png_bytes), "choice_hover_background.png", None
            )
            _ = _size_of(s_b)
            lines.append("PASS: load(fi, namehint, None) accepted")
        except TypeError as e:
            ok = False
            lines.append(f"FAIL: load(fi, namehint, None) TypeError: {e}")
        except Exception as e:
            lines.append(
                f"PASS: load(fi, namehint, None) arity OK "
                f"(raised {type(e).__name__}: {e})"
            )

        # Also keyword form size=None when supported.
        try:
            s_c = pimage.load(
                io.BytesIO(png_bytes),
                "choice_hover_background.png",
                size=None,
            )
            _ = _size_of(s_c)
            lines.append("PASS: load(fi, namehint, size=None) accepted")
        except TypeError as e:
            ok = False
            lines.append(f"FAIL: load(fi, namehint, size=None) TypeError: {e}")
        except Exception as e:
            lines.append(
                f"PASS: load(fi, namehint, size=None) arity OK "
                f"(raised {type(e).__name__}: {e})"
            )

    except Exception:
        ok = False
        lines.append("EXCEPTION:")
        lines.append(traceback.format_exc())

    status = "ok=True" if ok else "ok=False"
    body = status + "\n" + "\n".join(lines) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    print(body, end="")
    if not ok:
        raise RuntimeError(f"image_load_probe failed; see {out_path}")
    # Best-effort quit. Light gate may leave renpy partially imported with a
    # config stub; host teardown must not flip a green artifact to red.
    try:
        renpy_host.request_quit()
    except BaseException:
        pass
    return 0


# Gate runner execs this file via py.run (often as __main__); never sys.exit
# so the host does not treat a clean probe as gate failure.
main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
