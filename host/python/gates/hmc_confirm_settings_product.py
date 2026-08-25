"""Product gate for HuangmeiC confirm-settings mappings.

The gate opens each real preferences variant, locates the opposite ``save``
``SetDict`` action, focuses it with a move-only event, activates it with one
real mouse click, and verifies both the mapping mutation and product pixels.
"""

import json
import os
import sys
import threading
import time
import traceback
import types
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---
try:
    from _harness import gate_harness, parametrized_gate  # type: ignore
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore
    except ImportError:
        gate_harness = None  # type: ignore
        parametrized_gate = None  # type: ignore
# fallback


TRANSIENT_SCREENS = (
    "preferences",
    "dialog_config_1",
    "dialog_config_2",
    "confirm",
    "yesno_prompt",
    "load",
    "save",
    "appreciation",
    "flowchart",
)

MAPPINGS = (
    "preferences_confirm_requirement_mapping",
    "preferences_auto_move_mouse_type_mapping",
)

TRACE_EVENTS = []


def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult").resolve()


def _artifact(base=None):
    base = (base or _base()).resolve()
    raw = os.environ.get("RENPY_HOST_GATE_ARTIFACT")
    root_raw = os.environ.get("RENPY_HOST_GATE_EVIDENCE_ROOT")
    if not raw or not root_raw:
        raise RuntimeError("gate artifact and evidence root are required")

    path = Path(raw).resolve()
    root = Path(root_raw).resolve()
    try:
        root.relative_to((base / ".omx" / "tmp").resolve())
    except ValueError as e:
        raise RuntimeError("evidence root must be below repository .omx/tmp") from e
    if root.name != "gates" or root.parent.name != "evidence":
        raise RuntimeError("evidence root must end in evidence/gates")
    try:
        path.relative_to(root)
    except ValueError as e:
        raise RuntimeError("artifact must be below the approved evidence root") from e
    if path.parent != root or path.suffix != ".txt":
        raise RuntimeError("artifact must be a .txt directly below evidence/gates")
    return path


def _write_report(out, lines):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _log(message):
    message = str(message)
    try:
        sys.__stdout__.write(f"[confirm_settings] {message}\n")
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        with _artifact().with_suffix(".log").open("a", encoding="utf-8") as stream:
            stream.write(message + "\n")
    except Exception:
        pass


def _fail(out, classification, evidence):
    _write_report(
        out,
        (
            f"classification={classification}",
            f"evidence={json.dumps(evidence, sort_keys=True, default=str)}",
            "ok=False",
        ),
    )


def _quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:
        pass


def _clear_falsey(name):
    value = os.environ.get(name)
    if value is not None and str(value).strip().lower() in ("", "0", "false", "no", "off", "n"):
        os.environ.pop(name, None)


def _pre():
    try:
        import renpy.audio.renpysound_host as sound
        from renpy import audio

        sys.modules["renpy.audio.renpysound"] = sound
        audio.renpysound = sound
    except Exception as e:
        _log(f"sound prelude: {e}")

    try:
        import host_pygame
        import host_pygame.locals as locals_mod
        from host_pygame import scrap

        if not hasattr(host_pygame, "constants"):
            host_pygame.constants = locals_mod
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules["renpy.pygame.scrap"] = scrap
        sys.modules["pygame.scrap"] = scrap
        from renpy import pygame

        if not hasattr(pygame, "constants"):
            pygame.constants = host_pygame.constants
        pygame.scrap = scrap
        try:
            pygame.import_as_pygame()
        except Exception:
            pass
    except Exception as e:
        _log(f"pygame prelude: {e}")

    try:
        import renpy_uguu_host as uguu

        sys.modules["renpy.uguu.uguu"] = uguu
        sys.modules["renpy.uguu.gl"] = uguu
        package = sys.modules.get("renpy.uguu") or types.ModuleType("renpy.uguu")
        package.__path__ = []
        sys.modules["renpy.uguu"] = package
        for name in dir(uguu):
            if name.startswith("GL_") or name in ("clear_errors", "get_error"):
                setattr(package, name, getattr(uguu, name))
        package.uguu = uguu
        package.gl = uguu
        import renpy

        renpy.uguu = package
    except Exception as e:
        _log(f"uguu prelude: {e}")

    try:
        import renpy_ecsign_host as ecsign

        sys.modules["renpy.ecsign"] = ecsign
        import renpy

        renpy.ecsign = ecsign
    except Exception as e:
        _log(f"ecsign prelude: {e}")


def _mapping(name):
    import renpy

    return getattr(renpy.store.persistent, name)


def _copies():
    return {name: dict(_mapping(name)) for name in MAPPINGS}


def _restore(copies):
    for name, baseline in copies.items():
        mapping = _mapping(name)
        mapping.clear()
        mapping.update(baseline)


def _counter():
    import interact_helpers as ih

    ready, _why, interface = ih.interface_ready()
    if not ready or interface is None:
        return -1
    return int(getattr(interface, "interaction_counter", 0) or 0)


def _hide_transients():
    import interact_helpers as ih

    import renpy

    for name in TRANSIENT_SCREENS:
        try:
            renpy.display.screen.hide_screen(name)
        except Exception:
            try:
                renpy.store.Hide(name)()
            except Exception:
                pass

    focus = renpy.display.focus
    for call, args in (
        (getattr(focus, "clear_focus", None), ()),
        (getattr(focus, "clear_capture_focus", None), (None,)),
        (getattr(focus, "set_grab", None), (None,)),
    ):
        if call is not None:
            try:
                call(*args)
            except Exception:
                pass
    for name in ("tooltip", "last_tooltip", "override"):
        try:
            setattr(focus, name, None)
        except Exception:
            pass
    try:
        renpy.store.preferences_hint.hide_hint()
    except Exception:
        pass
    try:
        renpy.restart_interaction()
    except Exception:
        pass
    ih.pump_ms(120)
    return {
        name: renpy.display.screen.get_screen(name) is None
        for name in ("preferences", "dialog_config_1", "dialog_config_2")
    }


def _show_prefs(kind):
    """Exact navigation sequence established by hmc_phase0_class_labels."""
    import renpy

    try:
        renpy.display.screen.show_screen("preferences", kind=kind)
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        return True, "show_screen_preferences_kind"
    except Exception as first:
        try:
            renpy.store.ShowMenu("preferences")()
            renpy.display.screen.show_screen("preferences", kind=kind)
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            return True, f"ShowMenu+kind:{first}"
        except Exception as second:
            return False, f"fail:{first}/{second}"


def _screen_kind():
    import renpy

    screen = renpy.display.screen.get_screen("preferences")
    if screen is None:
        return None
    scope = getattr(screen, "scope", None) or {}
    value = scope.get("kind") if hasattr(scope, "get") else None
    if value is None:
        args = getattr(screen, "kwargs", None) or getattr(screen, "arguments", None)
        value = args.get("kind") if hasattr(args, "get") else None
    return value


def _panel_probe(width, height, rgba):
    if width <= 0 or height <= 0 or not rgba:
        return {"ok": False, "error": "empty_frame"}

    regions = {
        "panel_left": (0.10, 0.20, 0.30, 0.40),
        "panel_center": (0.40, 0.35, 0.60, 0.65),
        "panel_right": (0.70, 0.20, 0.90, 0.40),
    }
    clears = ((13, 13, 20), (20, 46, 71), (0, 0, 0), (255, 0, 255))
    report = {}
    for name, (x0, y0, x1, y1) in regions.items():
        xs, xe = int(x0 * width), max(int(x0 * width) + 1, int(x1 * width))
        ys, ye = int(y0 * height), max(int(y0 * height) + 1, int(y1 * height))
        sx = max(1, (xe - xs) // 16)
        sy = max(1, (ye - ys) // 12)
        samples = []
        for y in range(ys + sy // 2, ye, sy):
            for x in range(xs + sx // 2, xe, sx):
                offset = (y * width + x) * 4
                samples.append(tuple(int(rgba[offset + i]) for i in range(3)))
        count = len(samples)
        mean = tuple(sum(pixel[i] for pixel in samples) / count for i in range(3))
        variance = sum(
            sum((pixel[i] - mean[i]) ** 2 for i in range(3)) for pixel in samples
        ) / count
        nonclear = sum(
            min(sum(abs(pixel[i] - clear[i]) for i in range(3)) for clear in clears) > 35
            for pixel in samples
        )
        report[name] = {
            "mean": tuple(round(value, 1) for value in mean),
            "variance": round(variance, 1),
            "nonclear": nonclear,
            "samples": count,
        }

    ok = all(
        sum(region["mean"]) / 3.0 > 100
        and region["variance"] > 5
        and region["nonclear"] >= int(region["samples"] * 0.80)
        for region in report.values()
    )
    return {"ok": ok, "width": width, "height": height, "regions": report}


def _present():
    import interact_helpers as ih
    import renpy_host

    import renpy

    ready, why, interface = ih.interface_ready()
    if not ready or interface is None:
        return {"ok": False, "status": f"interface:{why}"}
    root = ih._rebuild_product_root(interface)
    if root is None:
        return {"ok": False, "status": "product_root_absent"}

    try:
        renpy_host.reset_present_stats()
        renpy.display.render.process_redraws()
        virtual_w = int(getattr(renpy.config, "screen_width", 1920) or 1920)
        virtual_h = int(getattr(renpy.config, "screen_height", 1080) or 1080)
        tree = renpy.display.render.render_screen(root, virtual_w, virtual_h)
        draw = renpy.display.draw
        if hasattr(draw, "load_all_textures"):
            draw.load_all_textures(tree)
        draw.draw_screen(tree, flip=True)
        interface.surftree = tree
        renpy.display.focus.take_focuses()
        width, height, rgba = renpy_host.read_game_rt_rgba()
        ownership = {
            "last_product_present": bool(renpy_host.last_product_present()),
            "product_presents": int(renpy_host.product_presents()),
            "idle_clears_after_present": int(renpy_host.idle_clears_after_present()),
        }
        ownership_ok = bool(
            ownership["last_product_present"]
            and ownership["product_presents"] >= 1
            and ownership["idle_clears_after_present"] == 0
        )
        panels = _panel_probe(int(width), int(height), rgba)
        return {
            "ok": bool(ownership_ok and panels.get("ok")),
            "status": "ok",
            "virtual": (virtual_w, virtual_h),
            "rt": (int(width), int(height)),
            "ownership": ownership,
            "ownership_ok": ownership_ok,
            "panels": panels,
        }
    except Exception as e:
        return {
            "ok": False,
            "status": f"{type(e).__name__}:{e}",
            "traceback": traceback.format_exc(),
        }


def _action_meta(action, mapping, allowed):
    meta = {
        "type": type(action).__name__ if action is not None else None,
        "mapping_match": False,
        "key": None,
        "value": None,
    }
    if action is None or meta["type"] != "SetDict":
        return meta
    try:
        values = vars(action)
    except Exception:
        values = {}
    meta["mapping_match"] = any(value is mapping for value in values.values())
    if not meta["mapping_match"]:
        for name in ("dict", "dictionary", "mapping", "object"):
            try:
                if getattr(action, name) is mapping:
                    meta["mapping_match"] = True
                    break
            except Exception:
                pass
    for value in values.values():
        if value == "save":
            meta["key"] = "save"
        if value in allowed:
            meta["value"] = value
    return meta


def _candidates(mapping, allowed):
    import renpy

    rows = []
    for entry in list(getattr(renpy.display.focus, "focus_list", None) or []):
        widget = getattr(entry, "widget", None)
        action = getattr(widget, "action", None) if widget is not None else None
        meta = _action_meta(action, mapping, allowed)
        rect = tuple(getattr(entry, name, None) for name in ("x", "y", "w", "h"))
        if not (
            meta["mapping_match"]
            and meta["key"] == "save"
            and meta["value"] in allowed
            and all(isinstance(value, (int, float)) for value in rect)
        ):
            continue
        x, y, width, height = rect
        rows.append(
            {
                "x": int(x),
                "y": int(y),
                "w": int(width),
                "h": int(height),
                "action": meta,
            }
        )
    rows.sort(key=lambda row: (row["y"], row["x"]))
    return rows


def _focused(mapping, allowed):
    import renpy

    widget = renpy.display.focus.get_focused()
    action = getattr(widget, "action", None) if widget is not None else None
    return {
        "widget": type(widget).__name__ if widget is not None else None,
        "action": _action_meta(action, mapping, allowed),
    }


def _wait(predicate, timeout=3.0):
    import interact_helpers as ih

    deadline = time.time() + timeout
    value = predicate()
    while not value and time.time() < deadline:
        ih.pump_ms(25)
        value = predicate()
    return value


def _exercise(kind, mapping_name, allowed, originals):
    import interact_helpers as ih
    import renpy_host

    import renpy

    result = {"kind": kind, "mapping": mapping_name, "allowed": list(allowed)}
    trace_start = len(TRACE_EVENTS)
    _restore(originals)
    mapping = _mapping(mapping_name)
    baseline = dict(mapping)
    other_name = next(name for name in MAPPINGS if name != mapping_name)
    other_baseline = dict(_mapping(other_name))
    baseline_save = baseline.get("save")
    result["baseline_save"] = baseline_save

    try:
        result["absent_before"] = _hide_transients()
        result["targets_absent_before"] = all(result["absent_before"].values())
        opened, navigation = _show_prefs(kind)
        result["opened"] = opened
        result["navigation"] = navigation
        _wait(lambda: _screen_kind() == kind)
        result["actual_kind"] = _screen_kind()

        before = _present()
        result["present_before_click"] = before
        candidates = _wait(lambda: _candidates(mapping, allowed)) or []
        result["candidates"] = candidates
        desired = next(
            (row for row in candidates if row["action"]["value"] != baseline_save),
            None,
        )
        result["selected"] = desired
        if desired is None:
            result["error"] = "opposite_save_SetDict_not_found"
            return result

        expected = desired["action"]["value"]
        virtual_w, virtual_h = before.get("virtual") or (0, 0)
        rt_w, rt_h = before.get("rt") or (0, 0)
        if min(virtual_w, virtual_h, rt_w, rt_h) <= 0:
            result["error"] = "invalid_present_dimensions"
            return result
        vx = desired["x"] + desired["w"] / 2.0
        vy = desired["y"] + desired["h"] / 2.0
        px = min(rt_w - 1, max(0, round(vx * rt_w / virtual_w)))
        py = min(rt_h - 1, max(0, round(vy * rt_h / virtual_h)))
        result["expected_save"] = expected
        result["focus_virtual"] = (vx, vy)
        result["focus_physical"] = (px, py)
        result["scale"] = (rt_w / virtual_w, rt_h / virtual_h)

        try:
            renpy.pygame.mouse.set_pos((px, py))
        except Exception:
            pass
        renpy_host.inject_mouse(px, py, 0, False)
        result["move_only_injected"] = True

        def focus_is_exact():
            focused = _focused(mapping, allowed)
            result["focused"] = focused
            action = focused["action"]
            return bool(
                action["mapping_match"]
                and action["key"] == "save"
                and action["value"] == expected
            )

        result["focus_action_ok"] = bool(_wait(focus_is_exact, 2.0))
        if not result["focus_action_ok"]:
            result["error"] = "move_only_focus_mismatch"
            return result

        counter_before = _counter()
        result["counter_before_click"] = counter_before
        result["injected"] = ih.inject_mouse_click(px, py)

        def click_observed():
            return mapping.get("save") == expected and _counter() > counter_before

        _wait(click_observed, 3.0)
        after = dict(mapping)
        result["counter_after_click"] = _counter()
        result["interaction_counter_up"] = result["counter_after_click"] > counter_before
        result["mapping_mutated"] = after.get("save") == expected
        result["only_save_changed"] = set(after) == set(baseline) and all(
            after[key] == (expected if key == "save" else baseline[key]) for key in baseline
        )
        result["other_mapping_unchanged"] = dict(_mapping(other_name)) == other_baseline
        result["present_after_click"] = _present()
        result["ok"] = all(
            (
                result["targets_absent_before"],
                opened,
                result["actual_kind"] == kind,
                before.get("ok"),
                result["focus_action_ok"],
                bool(result["injected"].get("injected")),
                result["interaction_counter_up"],
                result["mapping_mutated"],
                result["only_save_changed"],
                result["other_mapping_unchanged"],
                result["present_after_click"].get("ok"),
            )
        )
        return result
    finally:
        _restore(originals)
        result["restored"] = _copies() == originals
        result["trace_diagnostics"] = TRACE_EVENTS[trace_start:]
        if result.get("ok") and not result["restored"]:
            result["ok"] = False


def _install_trace_capture():
    import renpy.wgpu.draw as wgpu_draw

    original = wgpu_draw._ui_trace_once

    def wrapped(key, message):
        TRACE_EVENTS.append({"key": str(key), "message": str(message)})
        return original(key, message)

    wgpu_draw._ui_trace_once = wrapped
    return wgpu_draw, original


def _worker():
    out = _artifact()
    lines = []
    results = []
    trace_module = None
    trace_original = None
    originals = None
    try:
        import renpy

        deadline = time.time() + 90.0
        while time.time() < deadline and not bool(getattr(renpy.store, "main_menu", False)):
            time.sleep(0.2)
        main_menu = bool(getattr(renpy.store, "main_menu", False))
        lines.append(f"main_menu={main_menu}")
        if not main_menu:
            lines.extend(("classification=other:main_menu_timeout", "ok=False"))
            _write_report(out, lines)
            return

        trace_module, trace_original = _install_trace_capture()
        originals = _copies()
        results = [
            _exercise(
                "dialog_config_1",
                "preferences_confirm_requirement_mapping",
                (True, False),
                originals,
            ),
            _exercise(
                "dialog_config_2",
                "preferences_auto_move_mouse_type_mapping",
                ("confirm", "cancel"),
                originals,
            ),
        ]
        for result in results:
            lines.append("RESULT " + json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        ok = len(results) == 2 and all(result.get("ok") for result in results)
        classification = (
            "other:product_gate_green"
            if ok
            else "other:product_gate_failure"
        )
        lines.extend((f"classification={classification}", f"ok={ok}"))
        _write_report(out, lines)
        _log(f"wrote {out} ok={ok} classification={classification}")
    except Exception:
        lines.append("EXCEPTION " + json.dumps(traceback.format_exc()))
        lines.extend(("classification=other:gate_exception", "ok=False"))
        _write_report(out, lines)
        _log(traceback.format_exc())
    finally:
        if originals is not None:
            _restore(originals)
        try:
            _hide_transients()
        except Exception:
            pass
        if trace_module is not None and trace_original is not None:
            trace_module._ui_trace_once = trace_original
        time.sleep(0.3)
        _quit()


def main():
    base = _base()
    try:
        out = _artifact(base)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".log").write_text("start\n", encoding="utf-8")
    except Exception as e:
        _log(f"artifact validation failed: {e}")
        _quit()
        return

    game = os.environ.get("RENPY_HOST_GAME") or str(base / "host" / "playtests" / "HuangmeiC")
    os.environ["RENPY_HOST_BASE"] = str(base)
    os.environ["RENPY_HOST_BUILD"] = "1"
    os.environ["RENPY_HOST_GAME"] = game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    os.environ.setdefault("RENPY_HOST_UI_TRACE", "1")
    os.environ.setdefault("RENPY_HOST_PHASE0_SIGNALS", "1")
    _clear_falsey("RENPY_SKIP_MAIN_MENU")
    _clear_falsey("RENPY_SKIP_SPLASHSCREEN")
    for path in (str(base / "host" / "python" / "gates"), str(base / "host" / "python")):
        if path not in sys.path:
            sys.path.insert(0, path)

    try:
        import bootstrap as boot

        for name, call in (
            ("import_renpy", boot.stage_import_renpy),
            ("import_all", boot.stage_import_all),
            ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
        ):
            good, missing, error, extra = call()
            _log(f"stage {name} good={good} missing={missing} error={error!r}")
            if not good:
                _fail(out, f"other:bootstrap_{name}", {"missing": missing, "error": error, "extra": extra})
                _quit()
                return

        import renpy

        renpy.host_build = True
        try:
            import renpy_main_host

            renpy_main_host.install(renpy)
        except Exception as e:
            _log(f"main_host: {e}")
        try:
            import renpy.arguments

            basedir = getattr(renpy.config, "basedir", None) or game
            argv0 = sys.argv[0] if sys.argv else "renpy-host"
            sys.argv = [argv0, basedir, "run"]
            if not getattr(renpy.arguments, "commands", None):
                try:
                    renpy.arguments.register_command("run", renpy.arguments.run, True)
                    renpy.arguments.register_command("quit", renpy.arguments.quit)
                except Exception:
                    pass
            renpy.game.args = renpy.arguments.bootstrap()
        except Exception as e:
            _fail(out, "other:arguments_bootstrap", {"error": str(e)})
            _quit()
            return

        _pre()
        threading.Thread(target=_worker, daemon=True).start()
        import renpy.main as renpy_main

        try:
            renpy_main.main()
        except BaseException as e:
            _log(f"main exit {type(e).__name__}:{e}")
    except Exception:
        _fail(out, "other:main_exception", {"traceback": traceback.format_exc()})
        _log(traceback.format_exc())
        _quit()


if __name__ == "__main__":
    main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
