"""
AC5 first-interact helpers under renpy-host (task #31).

Mechanism 1 only:
  - event_wait already uses renpy_host.wait_until
  - inject_key / inject_mouse / inject_text via renpy_host to advance

Designed to be importable from host/python/gates/main.py. All public entry
points no-op safely when Interface / script are not yet available (task #30
dependency). They never rewrite interact_core to tick().

Report fields produced by stage_first_interact / smoke_advance:
  interact_count, events_seen, advanced, frame_ok
"""

from __future__ import annotations

import os
import time
import traceback
from typing import Any

from host.python.gates._harness import gate_harness, parametrized_gate


# SDL / host keycodes used by default dismiss / button_select bindings.
K_RETURN = 13
K_SPACE = 32
K_ESCAPE = 27


def _host():
    import renpy_host  # type: ignore

    return renpy_host


def inject_key_pulse(key: int = K_RETURN, hold_ms: int = 30) -> dict:
    """
    KEYDOWN then KEYUP for `key` with a short nested wait between.

    No-ops (returns status) if renpy_host is missing.
    """
    out = {"injected": False, "key": int(key), "error": ""}
    try:
        h = _host()
        h.inject_key(int(key), True)
        deadline = h.get_ticks_ms() + max(1, int(hold_ms))
        h.wait_until(deadline)
        h.inject_key(int(key), False)
        out["injected"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def inject_mouse_click(x: int = 640, y: int = 360, button: int = 1, hold_ms: int = 30) -> dict:
    """MOUSEBUTTONDOWN/UP at (x,y). No-ops if host missing."""
    out = {
        "injected": False,
        "x": int(x),
        "y": int(y),
        "button": int(button),
        "error": "",
    }
    try:
        h = _host()
        # Keep host_pygame mouse pos in sync for get_pos() consumers.
        try:
            from renpy import pygame

            pygame.mouse.set_pos((int(x), int(y)))
        except Exception:
            pass
        h.inject_mouse(int(x), int(y), int(button), True)
        deadline = h.get_ticks_ms() + max(1, int(hold_ms))
        h.wait_until(deadline)
        h.inject_mouse(int(x), int(y), int(button), False)
        out["injected"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def inject_text(text: str = " ") -> dict:
    """TEXTINPUT inject. No-ops if host missing."""
    out = {"injected": False, "text": text, "error": ""}
    try:
        h = _host()
        h.inject_text(str(text))
        out["injected"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def queue_renpy_event(name: str = "dismiss", up: bool = False) -> dict:
    """
    Queue a named Ren'Py keymap event (e.g. 'dismiss', 'button_select').

    More reliable than raw KEY injects for say/menu advance: goes through
    renpy.display.behavior.queue_event → EVENTNAME with eventnames.
    """
    out = {"queued": False, "name": name, "up": bool(up), "error": ""}
    try:
        import renpy

        renpy.exports.queue_event(name, up=bool(up))
        out["queued"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def advance_dialogue_pulse() -> dict:
    """
    Best-effort dialogue/menu advance under host auto-advance.

    Queues dismiss + button_select (down/up), then raw RETURN/SPACE + center click
    as fallback. Returns counts of what landed.
    """
    out = {"queued": 0, "injected": 0, "error": ""}
    try:
        for name in ("dismiss", "button_select"):
            r = queue_renpy_event(name, up=False)
            if r.get("queued"):
                out["queued"] += 1
            r = queue_renpy_event(name, up=True)
            if r.get("queued"):
                out["queued"] += 1
        r = inject_key_pulse(K_RETURN)
        if r.get("injected"):
            out["injected"] += 1
        r = inject_key_pulse(K_SPACE)
        if r.get("injected"):
            out["injected"] += 1
        r = inject_mouse_click(640, 360)
        if r.get("injected"):
            out["injected"] += 1
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def in_main_menu() -> bool:
    """True when product store is in the main-menu nested context."""
    try:
        import renpy

        if getattr(renpy.store, "main_menu", False):
            return True
        try:
            from renpy import game

            ctx = game.context()
            if getattr(ctx, "_main_menu", False):
                return True
        except Exception:
            pass
        return False
    except Exception:
        return False


def activate_main_menu_start(label: str = "start") -> dict:
    """
    Leave main menu the same way Start() does: JumpOutException(label).

    Raises renpy.game.JumpOutException on success so product call_in_new_context
    unwinds the menu context and the parent jumps to `label`. Callers that wrap
    interact must re-raise CONTROL_EXCEPTIONS (do not promote to HostStop).

    Returns a status dict only when Start was not attempted / not applicable.
    """
    out: dict = {"started": False, "label": label, "reason": "", "error": ""}
    import renpy
    from renpy import game

    if not in_main_menu():
        out["reason"] = "not_main_menu"
        return out

    # Main menu is call_in_new_context — need a parent context to jump into.
    if len(getattr(game, "contexts", []) or []) < 2:
        out["reason"] = "no_menu_context"
        return out

    out["started"] = True
    out["reason"] = "jump_out"
    # Prefer stock Start action when available (same JumpOutException).
    # Do not wrap JumpOut in bare except-Exception that swallows re-raise.
    Start = getattr(renpy.store, "Start", None)
    if Start is not None:
        try:
            renpy.display.behavior.run(Start(label))
        except game.CONTROL_EXCEPTIONS:
            raise
        except Exception as e:
            out["error"] = f"Start:{type(e).__name__}: {e}"
            # Fall through to direct JumpOut.
    raise game.JumpOutException(label)


def pump_ms(ms: int = 16) -> None:
    """Nested Mechanism 1 wait slice (or no-op outside embed)."""
    try:
        h = _host()
        h.wait_until(h.get_ticks_ms() + max(0, int(ms)))
    except Exception:
        time.sleep(max(0, int(ms)) / 1000.0)


def drain_events(max_n: int = 64) -> list[dict]:
    """
    Poll host event queue without blocking. Returns list of event dicts
    (type + payload). Empty outside embed / when queue dry.
    """
    seen: list[dict] = []
    try:
        h = _host()
    except Exception:
        return seen
    for _ in range(max(0, int(max_n))):
        try:
            ev = h.poll_event()
        except Exception:
            break
        if ev is None:
            break
        if isinstance(ev, dict):
            seen.append(dict(ev))
        else:
            seen.append({"type": getattr(ev, "type", None), "raw": repr(ev)})
    return seen


def interface_ready() -> tuple[bool, str, Any]:
    """
    Return (ready, reason, interface_or_None).

    ready only when renpy.display.interface exists and has been constructed.
    """
    try:
        import renpy
    except Exception as e:
        return False, f"renpy_import:{type(e).__name__}", None

    iface = getattr(getattr(renpy, "display", None), "interface", None)
    if iface is None:
        # Also check renpy.game.interface (Interface.__init__ sets both).
        try:
            from renpy import game

            iface = getattr(game, "interface", None)
        except Exception:
            iface = None

    if iface is None:
        return False, "interface_absent", None
    return True, "interface_present", iface


def script_ready() -> tuple[bool, str]:
    """True when renpy.game.script has been created (post load_script)."""
    try:
        from renpy import game

        script = getattr(game, "script", None)
        if script is None:
            return False, "script_absent"
        # Soft probes — presence is enough for stage gating.
        n = None
        for attr in ("namemap", "all_stmts", "files"):
            v = getattr(script, attr, None)
            if v is not None:
                try:
                    n = len(v)  # type: ignore[arg-type]
                except Exception:
                    n = True
                break
        return True, f"script_present:{n}"
    except Exception as e:
        return False, f"script_error:{type(e).__name__}:{e}"


def snapshot_context() -> dict:
    """Collect light context markers used to detect advance."""
    out: dict = {
        "label": None,
        "say": None,
        "menu": None,
        "interacting": None,
        "interaction_counter": None,
        "ticks": None,
        "scene_layers": None,
    }
    try:
        import renpy
        from renpy import game

        ctx = None
        try:
            ctx = game.context()
        except Exception:
            ctxs = getattr(game, "contexts", None) or []
            ctx = ctxs[-1] if ctxs else None

        if ctx is not None:
            out["label"] = getattr(ctx, "current", None) or getattr(ctx, "label", None)
            out["interacting"] = bool(getattr(ctx, "interacting", False))
            # Say / menu markers when present.
            out["say"] = getattr(ctx, "say", None) or getattr(ctx, "current_say_attributes", None)
            try:
                sl = getattr(ctx, "scene_lists", None)
                if sl is not None:
                    layers = getattr(sl, "layers", None)
                    if layers is not None:
                        out["scene_layers"] = list(layers) if not isinstance(layers, list) else layers
            except Exception:
                pass

        iface = getattr(getattr(renpy, "display", None), "interface", None)
        if iface is None:
            iface = getattr(game, "interface", None)
        if iface is not None:
            out["interaction_counter"] = getattr(iface, "interaction_counter", None)
            out["ticks"] = getattr(iface, "ticks", None)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _resolve_wgpu_draw():
    """
    Return (draw, source) for the active WgpuDraw, or (None, reason).

    Prefers renpy.display.draw when it is already a WgpuDraw (product path);
    otherwise constructs a local WgpuDraw for surrogate present.
    """
    try:
        import renpy

        draw = getattr(getattr(renpy, "display", None), "draw", None)
        if draw is not None and hasattr(draw, "draw_screen"):
            # Prefer product instance even if class name differs slightly.
            name = type(draw).__name__
            if name == "WgpuDraw" or getattr(draw, "info", {}).get("renderer") == "wgpu":
                return draw, f"product:{name}"
    except Exception:
        pass

    try:
        from renpy.wgpu.draw import WgpuDraw

        d = WgpuDraw()
        # Match product virtual size when available.
        vw, vh = 1280, 720
        try:
            import renpy

            vw = int(getattr(renpy.config, "screen_width", vw) or vw)
            vh = int(getattr(renpy.config, "screen_height", vh) or vh)
        except Exception:
            pass
        if not d.init((vw, vh)):
            return None, "wgpu_init_failed"
        return d, "surrogate:WgpuDraw"
    except Exception as e:
        return None, f"wgpu_import:{type(e).__name__}:{e}"


def _ensure_interface_started(iface=None):
    """
    Ensure Interface.start() has run so renpy.display.draw is a live WgpuDraw.

    V1 pre-interact capture wraps Interface.interact and captures *before*
    orig_interact, so start()/set_mode never ran unless we force it here.
    """
    try:
        import renpy
        from renpy import game

        if iface is None:
            iface = getattr(getattr(renpy, "display", None), "interface", None)
            if iface is None:
                iface = getattr(game, "interface", None)
        if iface is None:
            return False, "interface_absent"
        if not getattr(iface, "started", False):
            iface.start()
        draw = getattr(getattr(renpy, "display", None), "draw", None)
        if draw is None:
            return False, "draw_still_none"
        return True, type(draw).__name__
    except Exception as e:
        return False, f"{type(e).__name__}:{e}"


def _rebuild_product_root(iface=None):
    """
    Rebuild a displayable root from Interface.compute_scene / scene_lists.

    Needed for pre-interact capture: Interface.draw_screen stores surftree only
    after interact_core has drawn once, and root_widget is a local variable.
    main_menu_screen does show_screen before ui.interact, so scene_lists already
    holds the main_menu screen when V1 captures pre-interact.
    """
    try:
        import renpy
        from renpy import game

        if iface is None:
            iface = getattr(getattr(renpy, "display", None), "interface", None)
            if iface is None:
                iface = getattr(game, "interface", None)
        if iface is None:
            return None

        # Prefer Interface.compute_scene (same path as interact_core).
        try:
            ctx = game.context()
            sl = getattr(ctx, "scene_lists", None)
        except Exception:
            sl = None
        if sl is None:
            return None

        if hasattr(iface, "compute_scene"):
            scene = iface.compute_scene(sl)
            # scene[None] is the MultiBox root of all config.layers.
            root = scene.get(None) if isinstance(scene, dict) else None
            if root is not None:
                return root

        # Fallback: MultiBox of layer displayables.
        try:
            layers = list(getattr(renpy.config, "layers", None) or [])
            mb = renpy.display.layout.MultiBox(layout="fixed")
            for layer in layers:
                d = None
                try:
                    d = sl.make_layer(layer, getattr(iface, "layer_properties", {}).get(layer, {}))
                except Exception:
                    try:
                        d = sl.get_layer(layer)
                    except Exception:
                        d = None
                if d is not None:
                    mb.add(d)
            if getattr(mb, "children", None) or getattr(mb, "layers", None) is not None:
                return mb
        except Exception:
            pass
    except Exception:
        return None
    return None


def _surrogate_surftree(vw: int = 1280, vh: int = 720):
    """
    Minimal duck-typed Model-like node for WgpuDraw.draw_screen.

    Solid non-clear color so frame_nonzero can pass after present.
    """

    class _SurrogateModel:
        __slots__ = (
            "blits",
            "cached_model",
            "children",
            "color",
            "height",
            "indices",
            "mesh",
            "ndc",
            "pipeline",
            "shaders",
            "texture",
            "textures",
            "uniforms",
            "vertices",
            "width",
        )

        def __init__(self):
            self.width = int(vw)
            self.height = int(vh)
            # Distinct from host clear (~0.05,0.05,0.08).
            self.color = (0.20, 0.45, 0.75, 1.0)
            self.mesh = True
            self.ndc = (-1.0, -1.0, 1.0, 1.0)
            self.shaders = ("renpy.solid",)
            self.texture = None
            self.textures = None
            self.vertices = None
            self.indices = None
            self.pipeline = None
            self.uniforms = None
            self.children = None
            self.cached_model = None
            self.blits = None

    return _SurrogateModel()


def _rt_exists() -> tuple[bool, int, int]:
    """True when game RT was created by a prior end_frame_present."""
    try:
        h = _host()
        w, hgt, rgba = h.read_game_rt_rgba()
        if w and hgt and rgba is not None and len(rgba) > 0:
            return True, int(w), int(hgt)
    except Exception:
        pass
    return False, 0, 0


def ensure_frame_present(*, force: bool = False) -> dict:
    """
    Ensure at least one begin_frame → draw_model → end_frame_present cycle.

    Product path preference:
      1. interface.surftree via renpy.display.draw.draw_screen (real render tree)
      2. interface.draw_screen(root) if a root widget is available
      3. Surrogate solid Model via WgpuDraw.draw_screen (always presentable)
      4. Raw host begin_frame/end_frame_present (clear only; still frame_ok)

    Dual-tree safe: only touches renpy.display.draw / WgpuDraw; never libSDL*.

    Note: WgpuDraw.draw_screen swallows exceptions, so every attempt is verified
    via read_game_rt_rgba before being counted as presented.
    """
    out: dict = {
        "presented": False,
        "path": "",
        "draw_source": "",
        "error": "",
        "surftree": "",
    }

    # Fast path: game RT already exists and caller did not force a redraw.
    if not force:
        ok, w, hgt = _rt_exists()
        if ok:
            out["presented"] = True
            out["path"] = "already_present"
            out["frame_w"] = w
            out["frame_h"] = hgt
            return out

    draw, draw_src = _resolve_wgpu_draw()
    out["draw_source"] = draw_src

    def _verify(path: str, surftree_name: str = "") -> bool:
        ok, w, hgt = _rt_exists()
        if ok:
            out["presented"] = True
            out["path"] = path
            out["frame_w"] = w
            out["frame_h"] = hgt
            if surftree_name:
                out["surftree"] = surftree_name
            return True
        return False

    # Pre-interact capture: force Interface.start so renpy.display.draw is WgpuDraw.
    try:
        ready, _why, iface0 = interface_ready()
        if ready and iface0 is not None:
            ok_start, start_detail = _ensure_interface_started(iface0)
            out["interface_start"] = start_detail
            if ok_start:
                # Re-resolve product draw after start/set_mode.
                draw2, draw_src2 = _resolve_wgpu_draw()
                if draw2 is not None:
                    draw, draw_src = draw2, draw_src2
                    out["draw_source"] = draw_src
    except Exception as e:
        out["interface_start_error"] = f"{type(e).__name__}: {e}"

    # --- Attempt 1: product Interface.surftree --------------------------------
    if draw is not None:
        try:
            ready, _why, iface = interface_ready()
            if ready and iface is not None:
                surftree = getattr(iface, "surftree", None)
                if surftree is not None:
                    draw.draw_screen(surftree, flip=True)
                    if _verify("product_surftree", type(surftree).__name__):
                        return out
                    out["product_surftree_error"] = "draw_screen returned but RT absent"
                else:
                    out["surftree"] = "absent"
        except Exception as e:
            out["product_surftree_error"] = f"{type(e).__name__}: {e}"

        # --- Attempt 2: product Interface.draw_screen with root widget --------
        try:
            ready, _why, iface = interface_ready()
            if ready and iface is not None and hasattr(iface, "draw_screen"):
                root = None
                for attr in ("root_widget", "displayable", "root"):
                    root = getattr(iface, attr, None)
                    if root is not None:
                        break
                # Pre-interact capture: root_widget is local to interact_core and
                # not stored. Rebuild a scene root from scene_lists / layers so
                # product main-menu chrome can still be rendered.
                if root is None:
                    root = _rebuild_product_root(iface)
                if root is not None:
                    try:
                        iface.draw_screen(root, False, True)
                        if _verify(
                            "product_interface_draw_screen",
                            type(getattr(iface, "surftree", root)).__name__,
                        ):
                            return out
                    except TypeError:
                        draw.draw_screen(root, flip=True)
                        if _verify("product_draw_screen_root", type(root).__name__):
                            return out
                    except Exception as e:
                        out["product_interface_error"] = f"{type(e).__name__}: {e}"
                else:
                    out["product_root"] = "absent"
        except Exception as e:
            out["product_interface_error"] = f"{type(e).__name__}: {e}"

        # --- Attempt 2b: render_screen from rebuilt root → draw_screen -------
        try:
            ready, _why, iface = interface_ready()
            if ready and iface is not None:
                root = _rebuild_product_root(iface)
                if root is not None:
                    import renpy

                    w = int(getattr(renpy.config, "screen_width", 1280) or 1280)
                    h = int(getattr(renpy.config, "screen_height", 720) or 720)
                    surftree = renpy.display.render.render_screen(root, w, h)
                    draw.draw_screen(surftree, flip=True)
                    try:
                        iface.surftree = surftree
                    except Exception:
                        pass
                    if _verify("product_render_screen", type(surftree).__name__):
                        return out
                    out["product_render_screen_error"] = "draw_screen returned but RT absent"
        except Exception as e:
            out["product_render_screen_error"] = f"{type(e).__name__}: {e}"

        # --- Attempt 3: surrogate solid tree ----------------------------------
        try:
            vw, vh = getattr(draw, "virtual_size", (1280, 720)) or (1280, 720)
            try:
                import renpy

                vw = int(getattr(renpy.config, "screen_width", vw) or vw)
                vh = int(getattr(renpy.config, "screen_height", vh) or vh)
            except Exception:
                pass
            tree = _surrogate_surftree(int(vw), int(vh))
            draw.draw_screen(tree, flip=True)
            if _verify("surrogate_solid", type(tree).__name__):
                return out
            out["surrogate_error"] = "draw_screen returned but RT absent"
        except Exception as e:
            out["surrogate_error"] = f"{type(e).__name__}: {e}"
            out["traceback"] = traceback.format_exc()
    else:
        out["error"] = f"no_wgpu_draw:{draw_src}"

    # --- Attempt 4: raw host present (clear only; still creates game RT) ------
    try:
        h = _host()
        h.begin_frame()
        h.end_frame_present()
        if _verify("raw_host_present"):
            return out
        out["error"] = (out.get("error") or "") + ";raw_present_no_rt"
    except Exception as e:
        out["error"] = f"raw_present_failed:{type(e).__name__}:{e}"

    if not out.get("error"):
        out["error"] = "all_present_paths_failed"
    return out


def try_read_frame(*, ensure_present: bool = True, force_present: bool = False) -> dict:
    """
    Optional game RT readback. frame_ok True only if non-empty buffer returned
    (does not assert non-clear content — that is draw worker).

    By default ensures a product or surrogate draw_screen present first so the
    main gate does not fail with "game RT not created".
    """
    out = {
        "frame_ok": False,
        "frame_w": 0,
        "frame_h": 0,
        "frame_bytes": 0,
        "frame_error": "",
        "frame_nonzero": False,
        "present_path": "",
        "present_error": "",
    }

    if ensure_present:
        try:
            pres = ensure_frame_present(force=force_present)
            out["present_path"] = pres.get("path") or ""
            out["present_draw_source"] = pres.get("draw_source") or ""
            if pres.get("error"):
                out["present_error"] = pres.get("error") or ""
            if not pres.get("presented") and not out["present_error"]:
                # Collect soft product errors for diagnostics.
                soft = []
                for k in ("product_surftree_error", "product_interface_error"):
                    if pres.get(k):
                        soft.append(f"{k}={pres[k]}")
                if soft:
                    out["present_error"] = ";".join(soft)
        except Exception as e:
            out["present_error"] = f"ensure:{type(e).__name__}: {e}"

    try:
        h = _host()
        w, hgt, rgba = h.read_game_rt_rgba()
        out["frame_w"] = int(w)
        out["frame_h"] = int(hgt)
        out["frame_bytes"] = len(rgba) if rgba is not None else 0
        out["frame_ok"] = out["frame_bytes"] > 0 and out["frame_w"] > 0 and out["frame_h"] > 0
        if out["frame_ok"] and rgba is not None:
            # Cheap non-clear probe: any channel non-zero in a sparse sample.
            step = max(1, len(rgba) // 4096)
            out["frame_nonzero"] = any(rgba[i] for i in range(0, len(rgba), step))
    except Exception as e:
        out["frame_error"] = f"{type(e).__name__}: {e}"
    return out


def smoke_advance(
    *,
    max_secs: float = 5.0,
    key_pulses: int = 4,
    mouse_clicks: int = 2,
    prefer_key: int = K_RETURN,
) -> dict:
    """
    Inject dismiss/select inputs under host pump and observe whether context
    advances. No-ops (advanced=False, reason=...) when Interface is absent.

    Does NOT call interact() itself — expects product code to already be inside
    (or about to enter) an interact loop that drains the host event queue via
    Mechanism 1 event_wait. When called outside interact, injections still land
    in the host queue for the next poll.
    """
    t0 = time.monotonic()
    result: dict = {
        "interact_count": 0,
        "events_seen": 0,
        "advanced": False,
        "frame_ok": False,
        "reason": "",
        "injects": [],
        "before": {},
        "after": {},
        "frame": {},
        "policy": "Mechanism 1 only; no interact_core→tick rewrite",
    }

    ready, why, iface = interface_ready()
    if not ready:
        result["reason"] = f"noop_no_interface:{why}"
        return result

    s_ok, s_why = script_ready()
    result["script"] = s_why
    if not s_ok:
        # Soft: still try injects if interface exists (main menu may not need full script).
        result["script_soft_fail"] = s_why

    result["before"] = snapshot_context()
    if iface is not None:
        result["interact_count"] = int(getattr(iface, "interaction_counter", 0) or 0)

    # Drain anything already queued so we can attribute new events.
    drain_events(32)

    injects: list[dict] = []
    events: list[dict] = []

    def _budget() -> bool:
        return (time.monotonic() - t0) < max_secs

    # Key pulses (dismiss = K_RETURN / K_SPACE).
    for i in range(max(0, int(key_pulses))):
        if not _budget():
            break
        key = prefer_key if (i % 2 == 0) else K_SPACE
        injects.append({"kind": "key", **inject_key_pulse(key)})
        pump_ms(32)
        events.extend(drain_events(16))

    # Mouse click near center (mouseup_1 is also a dismiss binding).
    for _ in range(max(0, int(mouse_clicks))):
        if not _budget():
            break
        # Prefer config screen size when available.
        x, y = 640, 360
        try:
            import renpy

            w = int(getattr(renpy.config, "screen_width", 1280) or 1280)
            h = int(getattr(renpy.config, "screen_height", 720) or 720)
            x, y = w // 2, h // 2
        except Exception:
            pass
        injects.append({"kind": "mouse", **inject_mouse_click(x, y)})
        pump_ms(32)
        events.extend(drain_events(16))

    # Optional text inject (IME path smoke; usually not needed for dismiss).
    if _budget():
        injects.append({"kind": "text", **inject_text(" ")})
        pump_ms(16)
        events.extend(drain_events(8))

    # Allow product interact loop a few slices to consume injections.
    for _ in range(8):
        if not _budget():
            break
        pump_ms(50)
        events.extend(drain_events(16))

    result["injects"] = injects
    result["events_seen"] = len(events)
    result["events_types"] = sorted({e.get("type") for e in events if isinstance(e, dict)})
    result["after"] = snapshot_context()

    # Refresh interact_count post-pump.
    ready2, _, iface2 = interface_ready()
    if ready2 and iface2 is not None:
        result["interact_count"] = int(getattr(iface2, "interaction_counter", 0) or 0)

    # Advance heuristics (any one is enough for advanced=True).
    before = result["before"]
    after = result["after"]
    reasons = []
    if before.get("label") is not None and after.get("label") is not None:
        if before.get("label") != after.get("label"):
            reasons.append("label_changed")
    if (before.get("interaction_counter") is not None) and (after.get("interaction_counter") is not None):
        try:
            if int(after["interaction_counter"]) > int(before["interaction_counter"]):
                reasons.append("interaction_counter_up")
        except Exception:
            pass
    if (before.get("ticks") is not None) and (after.get("ticks") is not None):
        try:
            if int(after["ticks"]) > int(before["ticks"]):
                reasons.append("ticks_up")
        except Exception:
            pass
    # Successful injects into a live interface counts as partial progress but
    # NOT "advanced" — advanced requires a context/label/counter change.
    if reasons:
        result["advanced"] = True
        result["reason"] = ",".join(reasons)
    else:
        n_ok = sum(1 for i in injects if i.get("injected"))
        if n_ok == 0:
            result["reason"] = "injects_failed"
        else:
            result["reason"] = f"injected_but_no_context_change:injects={n_ok}:events={len(events)}"

    # Optional frame capture after pumps — ensure a product/surrogate present first.
    frame = try_read_frame(ensure_present=True)
    result["frame"] = frame
    result["frame_ok"] = bool(frame.get("frame_ok"))
    if frame.get("present_path"):
        result["present_path"] = frame.get("present_path")
    if frame.get("frame_error"):
        result["frame_error"] = frame.get("frame_error")
    result["elapsed_secs"] = round(time.monotonic() - t0, 3)
    return result


# ---------------------------------------------------------------------------
# Non-blank frame analysis (V1 / F3b — fail-closed AND-chain)
# ---------------------------------------------------------------------------

# Known host clear colors in 0-255 sRGB (full RGB, not ~0.08 shorthand).
GPU_IDLE_CLEAR_RGB = (20, 46, 71)  # (0.08, 0.18, 0.28)
ARENA_RT_CLEAR_RGB = (13, 13, 20)  # (0.05, 0.05, 0.08)
MAGENTA_STUB_RGB = (255, 0, 255)
BLACK_RGB = (0, 0, 0)

# Anti-uniform: reject when both variance and max−min are below these.
# Tuned for 8-bit RGBA; solid clears / stubs fail, photographic BG passes.
NONBLANK_VAR_MIN = 8.0  # mean channel variance across sampled pixels
NONBLANK_RANGE_MIN = 12  # max−min across any channel in sample
NONBLANK_CLEAR_TOL = 6  # per-channel distance for mean≈clear reject
NONBLANK_GRID_MIN_GOOD = 4  # ≥N non-clear opaque samples on 8×8 grid


def _sample_rgba_pixels(w: int, h: int, rgba, *, max_samples: int = 4096) -> list[tuple[int, int, int, int]]:
    """Sparse uniform sample of RGBA pixels as (r,g,b,a) tuples."""
    if not rgba or w <= 0 or h <= 0:
        return []
    n_pix = int(w) * int(h)
    step = max(1, n_pix // max(1, int(max_samples)))
    out: list[tuple[int, int, int, int]] = []
    blen = len(rgba)
    for i in range(0, n_pix, step):
        off = i * 4
        if off + 3 >= blen:
            break
        out.append((rgba[off], rgba[off + 1], rgba[off + 2], rgba[off + 3]))
    return out


def _grid_sample_rgba(w: int, h: int, rgba, grid: int = 8) -> list[tuple[int, int, int, int]]:
    """8×8 (or grid×grid) lattice sample of center-of-cell pixels."""
    if not rgba or w <= 0 or h <= 0:
        return []
    g = max(1, int(grid))
    blen = len(rgba)
    out: list[tuple[int, int, int, int]] = []
    for gy in range(g):
        for gx in range(g):
            x = min(int(w) - 1, int((gx + 0.5) * int(w) / g))
            y = min(int(h) - 1, int((gy + 0.5) * int(h) / g))
            off = (y * int(w) + x) * 4
            if off + 3 >= blen:
                continue
            out.append((rgba[off], rgba[off + 1], rgba[off + 2], rgba[off + 3]))
    return out


def _rgb_dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def _mean_rgb(samples: list[tuple[int, int, int, int]]) -> tuple[float, float, float]:
    if not samples:
        return (0.0, 0.0, 0.0)
    n = float(len(samples))
    return (
        sum(s[0] for s in samples) / n,
        sum(s[1] for s in samples) / n,
        sum(s[2] for s in samples) / n,
    )


def _is_clearish_rgb(r: int, g: int, b: int, tol: int = NONBLANK_CLEAR_TOL) -> bool:
    rgb = (int(r), int(g), int(b))
    if _rgb_dist(rgb, GPU_IDLE_CLEAR_RGB) <= tol:
        return True
    if _rgb_dist(rgb, ARENA_RT_CLEAR_RGB) <= tol:
        return True
    if rgb == BLACK_RGB:
        return True
    return rgb == MAGENTA_STUB_RGB


def analyze_frame_nonblank(
    w: int,
    h: int,
    rgba,
    *,
    var_min: float = NONBLANK_VAR_MIN,
    range_min: int = NONBLANK_RANGE_MIN,
    clear_tol: int = NONBLANK_CLEAR_TOL,
    grid: int = 8,
    grid_min_good: int = NONBLANK_GRID_MIN_GOOD,
) -> dict:
    """
    Fail-closed non-blank algorithm (V1 AND-chain; no OR escape).

    Required rejects:
      - empty buffer or size (1,1)
      - near-uniform (variance AND max−min both below threshold, OR all 8×8 equal)
      - mean ≈ GPU idle clear / arena RT clear / pure black / magenta stub
    Additional (only after anti-uniform + clear rejects pass):
      - ≥ grid_min_good non-clear non-transparent 8×8 lattice samples
    """
    out: dict = {
        "nonblank_ok": False,
        "reasons": [],
        "frame_w": int(w or 0),
        "frame_h": int(h or 0),
        "frame_bytes": len(rgba) if rgba is not None else 0,
        "variance": 0.0,
        "range": 0,
        "mean_rgb": (0.0, 0.0, 0.0),
        "grid_equal": False,
        "grid_good": 0,
        "grid_total": 0,
        "reject": "",
    }
    reasons: list[str] = []

    if rgba is None or out["frame_bytes"] <= 0 or out["frame_w"] <= 0 or out["frame_h"] <= 0:
        out["reject"] = "empty_buffer"
        out["reasons"] = ["empty_buffer"]
        return out
    if (out["frame_w"], out["frame_h"]) == (1, 1):
        out["reject"] = "size_1x1"
        out["reasons"] = ["size_1x1"]
        return out

    samples = _sample_rgba_pixels(out["frame_w"], out["frame_h"], rgba)
    grid_samples = _grid_sample_rgba(out["frame_w"], out["frame_h"], rgba, grid=grid)
    out["grid_total"] = len(grid_samples)

    if not samples:
        out["reject"] = "no_samples"
        out["reasons"] = ["no_samples"]
        return out

    # Stats on RGB only.
    rs = [s[0] for s in samples]
    gs = [s[1] for s in samples]
    bs = [s[2] for s in samples]
    n = float(len(samples))
    mr, mg, mb = sum(rs) / n, sum(gs) / n, sum(bs) / n
    out["mean_rgb"] = (round(mr, 2), round(mg, 2), round(mb, 2))
    var = (
        sum((r - mr) ** 2 for r in rs) / n
        + sum((g - mg) ** 2 for g in gs) / n
        + sum((b - mb) ** 2 for b in bs) / n
    ) / 3.0
    out["variance"] = round(var, 3)
    rng = max(
        max(rs) - min(rs),
        max(gs) - min(gs),
        max(bs) - min(bs),
    )
    out["range"] = int(rng)

    grid_equal = False
    if grid_samples:
        g0 = grid_samples[0][:3]
        grid_equal = all(s[:3] == g0 for s in grid_samples)
    out["grid_equal"] = bool(grid_equal)

    # Mandatory anti-uniform (no OR escape that skips this).
    uniform_by_stats = (var < float(var_min)) or (rng < int(range_min))
    if uniform_by_stats or grid_equal:
        if uniform_by_stats:
            reasons.append(f"anti_uniform:var={var:.3f}<{var_min} or range={rng}<{range_min}")
        if grid_equal:
            reasons.append("anti_uniform:grid_all_equal")
        out["reject"] = "anti_uniform"
        out["reasons"] = reasons
        return out

    # Reject known clear / stub colors via mean proximity (full RGB).
    mean_i = (round(mr), round(mg), round(mb))
    if _rgb_dist(mean_i, GPU_IDLE_CLEAR_RGB) <= clear_tol:
        reasons.append(f"gpu_idle_clear mean={mean_i}")
        out["reject"] = "gpu_idle_clear"
        out["reasons"] = reasons
        return out
    if _rgb_dist(mean_i, ARENA_RT_CLEAR_RGB) <= clear_tol:
        reasons.append(f"arena_rt_clear mean={mean_i}")
        out["reject"] = "arena_rt_clear"
        out["reasons"] = reasons
        return out
    if mean_i == BLACK_RGB or (mr < 2 and mg < 2 and mb < 2):
        reasons.append(f"pure_black mean={mean_i}")
        out["reject"] = "pure_black"
        out["reasons"] = reasons
        return out
    if _rgb_dist(mean_i, MAGENTA_STUB_RGB) <= clear_tol:
        reasons.append(f"magenta_stub mean={mean_i}")
        out["reject"] = "magenta_stub"
        out["reasons"] = reasons
        return out

    # Additional grid evidence (only after anti-uniform + clear rejects pass).
    good = 0
    for r, g, b, a in grid_samples:
        if a < 8:
            continue
        if _is_clearish_rgb(r, g, b, tol=clear_tol):
            continue
        good += 1
    out["grid_good"] = good
    if good < int(grid_min_good):
        reasons.append(f"grid_evidence:good={good}<{grid_min_good}")
        out["reject"] = "grid_evidence"
        out["reasons"] = reasons
        return out

    out["nonblank_ok"] = True
    out["reasons"] = [
        f"ok var={var:.3f} range={rng} mean={mean_i} grid_good={good}/{len(grid_samples)}"
    ]
    return out


def read_present_ownership() -> dict:
    """Snapshot renpy_host present-ownership flag + counters."""
    out = {
        "last_product_present": False,
        "product_presents": 0,
        "idle_clears_after_present": 0,
        "error": "",
    }
    try:
        h = _host()
        out["last_product_present"] = bool(h.last_product_present())
        out["product_presents"] = int(h.product_presents())
        out["idle_clears_after_present"] = int(h.idle_clears_after_present())
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def capture_with_present_ownership(*, force_present: bool = True) -> dict:
    """
    Capture-cycle: reset counters → force product present → readback + ownership.

    Ownership flag last_product_present is NOT cleared by reset_present_stats.
    Counters product_presents / idle_clears_after_present reflect this cycle only.
    """
    out: dict = {
        "frame_ok": False,
        "frame_w": 0,
        "frame_h": 0,
        "frame_bytes": 0,
        "frame_error": "",
        "present_path": "",
        "present_error": "",
        "rgba": None,
        "ownership": {},
        "ownership_ok": False,
        "nonblank": {},
        "nonblank_ok": False,
        "ok": False,
    }
    try:
        h = _host()
        try:
            h.reset_present_stats()
        except Exception as e:
            out["present_error"] = f"reset_present_stats:{type(e).__name__}:{e}"

        # Force a product (or fallback) present so counters cover this cycle.
        fr = try_read_frame(ensure_present=True, force_present=bool(force_present))
        out["frame_ok"] = bool(fr.get("frame_ok"))
        out["frame_w"] = int(fr.get("frame_w") or 0)
        out["frame_h"] = int(fr.get("frame_h") or 0)
        out["frame_bytes"] = int(fr.get("frame_bytes") or 0)
        out["present_path"] = fr.get("present_path") or ""
        out["present_error"] = (out.get("present_error") or "") + (fr.get("present_error") or "")
        if fr.get("frame_error"):
            out["frame_error"] = fr.get("frame_error")

        try:
            w, hgt, rgba = h.read_game_rt_rgba()
            out["frame_w"] = int(w)
            out["frame_h"] = int(hgt)
            out["frame_bytes"] = len(rgba) if rgba is not None else 0
            out["frame_ok"] = out["frame_bytes"] > 0 and out["frame_w"] > 0 and out["frame_h"] > 0
            out["rgba"] = rgba
        except Exception as e:
            out["frame_error"] = f"readback:{type(e).__name__}:{e}"

        own = read_present_ownership()
        out["ownership"] = own
        out["ownership_ok"] = (
            bool(own.get("last_product_present"))
            and int(own.get("product_presents") or 0) >= 1
            and int(own.get("idle_clears_after_present") or 0) == 0
            and not own.get("error")
        )

        if out["frame_ok"] and out.get("rgba") is not None:
            nb = analyze_frame_nonblank(out["frame_w"], out["frame_h"], out["rgba"])
            out["nonblank"] = nb
            out["nonblank_ok"] = bool(nb.get("nonblank_ok"))
        else:
            out["nonblank"] = {
                "nonblank_ok": False,
                "reject": "no_frame",
                "reasons": ["no_frame"],
            }
            out["nonblank_ok"] = False

        out["ok"] = bool(out["ownership_ok"] and out["nonblank_ok"] and out["frame_ok"])
    except Exception as e:
        out["frame_error"] = f"{type(e).__name__}: {e}"
        out["ok"] = False
    return out


def stage_first_interact(*, max_secs: float | None = None) -> tuple[bool, list, str, dict]:
    """
    Gate stage entry for main.py.

    Returns (ok, missing, err, extra) matching other main gate stages.

    ok=True only when advanced=True (dialogue/menu actually moved). When
    Interface is absent, returns ok=False with missing=['interface'] and
    reason noop — caller may treat as soft skip depending on stage policy.
    """
    extra: dict = {
        "interact_count": 0,
        "events_seen": 0,
        "advanced": False,
        "frame_ok": False,
        "interact_policy": "Mechanism 1 inject via renpy_host; no tick() rewrite",
    }

    if max_secs is None:
        raw = os.environ.get("RENPY_HOST_INTERACT_SECS") or os.environ.get("RENPY_HOST_MAX_SECS")
        try:
            max_secs = float(raw) if raw else 5.0
        except ValueError:
            max_secs = 5.0
        max_secs = max(1.0, min(max_secs, 30.0))

    ready, why, _iface = interface_ready()
    if not ready:
        extra["interact_stage"] = "noop"
        extra["interact_reason"] = f"noop_no_interface:{why}"
        extra["advanced"] = False
        # Soft missing — scaffold/load may not have created Interface yet.
        return False, ["interface"], f"first_interact no-op: {why}", extra

    try:
        result = smoke_advance(max_secs=max_secs)
    except Exception as e:
        extra["interact_stage"] = "error"
        extra["interact_error"] = f"{type(e).__name__}: {e}"
        extra["traceback"] = traceback.format_exc()
        return False, ["interact"], f"smoke_advance failed: {e}", extra

    extra["interact_stage"] = "ran"
    extra["interact_count"] = int(result.get("interact_count") or 0)
    extra["events_seen"] = int(result.get("events_seen") or 0)
    extra["advanced"] = bool(result.get("advanced"))
    extra["frame_ok"] = bool(result.get("frame_ok"))
    extra["interact_reason"] = result.get("reason") or ""
    extra["interact_before"] = result.get("before")
    extra["interact_after"] = result.get("after")
    extra["interact_injects_ok"] = sum(
        1 for i in (result.get("injects") or []) if i.get("injected")
    )
    extra["interact_elapsed_secs"] = result.get("elapsed_secs")
    if result.get("frame"):
        fr = result["frame"]
        extra["frame_w"] = fr.get("frame_w")
        extra["frame_h"] = fr.get("frame_h")
        extra["frame_nonzero"] = fr.get("frame_nonzero")
        if fr.get("frame_error"):
            extra["frame_error"] = fr.get("frame_error")
        if fr.get("present_path"):
            extra["present_path"] = fr.get("present_path")
        if fr.get("present_draw_source"):
            extra["present_draw_source"] = fr.get("present_draw_source")
        if fr.get("present_error"):
            extra["present_error"] = fr.get("present_error")
    if result.get("present_path") and "present_path" not in extra:
        extra["present_path"] = result.get("present_path")
    if result.get("script"):
        extra["script"] = result.get("script")

    if extra["advanced"]:
        return True, [], "", extra

    # Interface present but no advance — not a hard crash; report honestly.
    return (
        False,
        ["advance"],
        f"first_interact ran but did not advance: {extra.get('interact_reason')}",
        extra,
    )


__all__ = [
    "ARENA_RT_CLEAR_RGB",
    "GPU_IDLE_CLEAR_RGB",
    "K_ESCAPE",
    "K_RETURN",
    "K_SPACE",
    "activate_main_menu_start",
    "advance_dialogue_pulse",
    "analyze_frame_nonblank",
    "capture_with_present_ownership",
    "drain_events",
    "ensure_frame_present",
    "in_main_menu",
    "inject_key_pulse",
    "inject_mouse_click",
    "inject_text",
    "interface_ready",
    "pump_ms",
    "queue_renpy_event",
    "read_present_ownership",
    "script_ready",
    "smoke_advance",
    "snapshot_context",
    "stage_first_interact",
    "try_read_frame",
]

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
