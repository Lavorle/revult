"""
HuangmeiC product Start→say reconfirm (AC-T* / AC-C2 entry helper).

Gate name: hmc_say_product_reconfirm  (RENPY_HOST_GATE=hmc_say_product_reconfirm)

Boots product HuangmeiC path, waits for main_menu, injects Start (Enter),
then observes whether the say screen appears and whether progressive Text
render (blits_typewriter path) is exercised with non-empty RT.

Authority: human full-screen interact remains AC authority for AC-T1/T2/C2–C4.
This gate only proves engine can leave main_menu and present say chrome /
typewriter path under product inject — used as ENTRY reconfirm evidence before
any dialogue surgery (reopen plan Step 3).

Writes:
  host/target/gate-hmc_say_product_reconfirm.txt
  .omc/artifacts/huangmeic-visual-residual-reopen-20260718e/gates/hmc_say_product_reconfirm.txt

Note: no from __future__; host run_file prepends imports.
"""

import os
import sys
import threading
import time
import traceback
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---
from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore


def _base():
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path("/mnt/nvme1n1p2/revult")


def _log(msg):
    try:
        sys.__stdout__.write(f"[hmc_say_reconfirm] {msg}\n")
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        open("/tmp/hmc_say_product_reconfirm.log", "a").write(msg + "\n")  # noqa: SIM115
    except Exception:
        pass


def _request_quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:
        pass


def _clear_falsey_skip(name):
    val = os.environ.get(name)
    if val is None:
        return
    if str(val).strip().lower() in ("", "0", "false", "no", "off", "n"):
        os.environ.pop(name, None)


def _pre_main_host_stubs():
    """Mirror hmc_nav_chrome_product stubs (sound/pygame/uguu/ecsign)."""
    import types

    try:
        import renpy.audio as _ra
        import renpy.audio.renpysound_host as _rs_host

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        _log("renpysound rebound")
    except Exception as e:
        _log(f"renpysound soft-fail: {e}")

    try:
        import host_pygame
        import host_pygame.locals as _loc
        import host_pygame.scrap as _host_scrap

        if not hasattr(host_pygame, "constants"):
            host_pygame.constants = _loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules.setdefault("pygame.constants", host_pygame.constants)
        sys.modules["renpy.pygame.scrap"] = _host_scrap
        sys.modules["pygame.scrap"] = _host_scrap
        import renpy.pygame as rpg

        if not hasattr(rpg, "constants"):
            rpg.constants = host_pygame.constants
        try:
            rpg.scrap = _host_scrap
        except Exception:
            pass
        try:
            rpg.import_as_pygame()
        except Exception as e:
            _log(f"import_as_pygame soft-fail: {e}")
        _log("pygame host shim ok")
    except Exception as e:
        _log(f"pygame soft-fail: {e}")

    try:
        import renpy_uguu_host as _uguu

        sys.modules["renpy.uguu.uguu"] = _uguu
        sys.modules["renpy.uguu.gl"] = _uguu
        pkg = sys.modules.get("renpy.uguu")
        if pkg is None:
            pkg = types.ModuleType("renpy.uguu")
            pkg.__path__ = []  # type: ignore[attr-defined]
            sys.modules["renpy.uguu"] = pkg
        for _name in dir(_uguu):
            if _name.startswith("GL_") or _name in ("clear_errors", "get_error"):
                setattr(pkg, _name, getattr(_uguu, _name))
        pkg.uguu = _uguu
        pkg.gl = _uguu
        try:
            import renpy

            renpy.uguu = pkg
        except Exception:
            pass
        _log("uguu host stub installed")
    except Exception as e:
        _log(f"uguu soft-fail: {e}")

    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        try:
            import renpy as _renpy_pkg

            _renpy_pkg.ecsign = _ecsign
        except Exception:
            pass
        _log("ecsign host stub installed")
    except Exception as e:
        _log(f"ecsign soft-fail: {e}")


def _force_product_redraw():
    import interact_helpers as ih

    import renpy

    info = {"path": None, "error": None}
    try:
        ready, why, iface = ih.interface_ready()
        if not ready or iface is None:
            info["error"] = f"iface:{why}"
            return info
        root = ih._rebuild_product_root(iface)
        if root is None:
            info["error"] = "root_absent"
            return info
        w = int(getattr(renpy.config, "screen_width", 1920) or 1920)
        h = int(getattr(renpy.config, "screen_height", 1080) or 1080)
        surftree = renpy.display.render.render_screen(root, w, h)
        draw = getattr(renpy.display, "draw", None)
        if draw is None or not hasattr(draw, "draw_screen"):
            info["error"] = "no_draw"
            return info
        draw.draw_screen(surftree, flip=True)
        try:
            iface.surftree = surftree
        except Exception:
            pass
        info["path"] = "rebuild_render_screen"
        return info
    except Exception as e:
        info["error"] = f"{type(e).__name__}:{e}"
        return info


def _sample_rt():
    """Sparse mean/var/pure_black of game RT after product redraw."""
    import renpy_host

    pres = _force_product_redraw()
    if pres.get("error"):
        try:
            import interact_helpers as ih

            pres2 = ih.ensure_frame_present(force=True)
            pres = {
                "path": "fallback:{}".format(pres2.get("path")),
                "error": pres.get("error"),
                "fallback_error": pres2.get("error"),
            }
        except Exception as e:
            pres = {"path": None, "error": "{}|fallback:{}".format(pres.get("error"), e)}

    try:
        rw, rh, rt = renpy_host.read_game_rt_rgba()
    except Exception as e:
        return {"ok": False, "error": f"read_rt:{e}", "present": pres}
    if not rw or not rh or not rt:
        return {"ok": False, "error": "empty_rt", "present": pres}

    rs = gs = bs = n = pure = 0
    step_x = max(1, rw // 32)
    step_y = max(1, rh // 18)
    # lower band (textbox region ~ bottom 20%)
    low_y0 = int(rh * 0.78)
    low_rs = low_gs = low_bs = low_n = low_pure = 0
    for y in range(step_y // 2, rh, step_y):
        for x in range(step_x // 2, rw, step_x):
            o = (y * rw + x) * 4
            r, g, b = rt[o], rt[o + 1], rt[o + 2]
            rs += r
            gs += g
            bs += b
            n += 1
            if r < 8 and g < 8 and b < 8:
                pure += 1
            if y >= low_y0:
                low_rs += r
                low_gs += g
                low_bs += b
                low_n += 1
                if r < 8 and g < 8 and b < 8:
                    low_pure += 1
    if n == 0:
        return {"ok": False, "error": "no_samples", "present": pres}
    mean = (rs / n, gs / n, bs / n)
    pure_frac = pure / float(n)
    low_mean = (
        (low_rs / low_n, low_gs / low_n, low_bs / low_n) if low_n else (0, 0, 0)
    )
    low_pure_frac = (low_pure / float(low_n)) if low_n else 1.0
    # featureless black if pure_frac very high and mean near 0
    featureless = pure_frac > 0.92 and (mean[0] + mean[1] + mean[2]) < 24
    return {
        "ok": True,
        "w": rw,
        "h": rh,
        "mean": mean,
        "pure_frac": pure_frac,
        "featureless_black": featureless,
        "low_mean": low_mean,
        "low_pure_frac": low_pure_frac,
        "present": pres,
    }


def _get_screen(name):
    try:
        import renpy

        return renpy.display.screen.get_screen(name)
    except Exception:
        return None


def _write_png_strip(path, rgba, w, h, band_y0=None, band_h=None):
    """Optional tiny evidence PNG of lower band via zlib/struct (no Pillow req)."""
    try:
        import struct
        import zlib

        if band_y0 is None:
            band_y0 = int(h * 0.78)
        if band_h is None:
            band_h = max(1, h - band_y0)
        band_y0 = max(0, min(h - 1, int(band_y0)))
        band_h = max(1, min(h - band_y0, int(band_h)))
        rows = []
        for y in range(band_y0, band_y0 + band_h):
            o = y * w * 4
            rows.append(b"\x00" + bytes(rgba[o : o + w * 4]))
        raw = b"".join(rows)
        comp = zlib.compress(raw, 6)

        def chunk(tag, data):
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        ihdr = struct.pack(">IIBBBBB", w, band_h, 8, 6, 0, 0, 0)
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp) + chunk(b"IEND", b"")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        return str(path)
    except Exception as e:
        return f"png_fail:{e}"


def run():
    base = _base()
    out = base / "host" / "target" / "gate-hmc_say_product_reconfirm.txt"
    art = (
        base
        / ".omc"
        / "artifacts"
        / "huangmeic-visual-residual-reopen-20260718e"
        / "gates"
        / "hmc_say_product_reconfirm.txt"
    )
    photo_dir = (
        base
        / ".omc"
        / "artifacts"
        / "huangmeic-visual-residual-reopen-20260718e"
        / "photos"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    art.parent.mkdir(parents=True, exist_ok=True)
    photo_dir.mkdir(parents=True, exist_ok=True)
    lines = []

    def rec(m):
        lines.append(m)
        _log(m)

    game = os.environ.get("RENPY_HOST_GAME") or str(base / "host" / "playtests" / "HuangmeiC")
    os.environ["RENPY_HOST_BASE"] = str(base)
    os.environ["RENPY_HOST_BUILD"] = "1"
    os.environ["RENPY_HOST_GAME"] = game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    _clear_falsey_skip("RENPY_SKIP_MAIN_MENU")
    _clear_falsey_skip("RENPY_SKIP_SPLASHSCREEN")

    gates = str(base / "host" / "python" / "gates")
    host_py = str(base / "host" / "python")
    if gates not in sys.path:
        sys.path.insert(0, gates)
    if host_py not in sys.path:
        sys.path.insert(0, host_py)

    import bootstrap as boot
    import renpy_host  # type: ignore

    for name, call in (
        ("import_renpy", boot.stage_import_renpy),
        ("import_all", boot.stage_import_all),
        ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ):
        good, _miss, err, _extra = call()
        rec(f"stage {name} good={good} err={err!r}")
        if not good:
            body = f"gate=hmc_say_product_reconfirm\nok=False\nerror={err}\n"
            out.write_text(body)
            art.write_text(body)
            _request_quit()
            return

    import renpy

    renpy.host_build = True
    try:
        renpy.config.performance_test = False
    except Exception:
        pass

    try:
        import renpy_main_host

        renpy_main_host.install(renpy)
        rec("main_host installed")
    except Exception as e:
        rec(f"main_host: {e}")

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
        args = renpy.arguments.bootstrap()
        renpy.game.args = args
        rec("args command={} basedir={}".format(getattr(args, "command", None), basedir))
    except Exception as e:
        rec(f"args fail: {e}")
        rec(traceback.format_exc())

    _pre_main_host_stubs()

    state = {
        "phase": "boot",
        "main_menu": False,
        "left_main_menu": False,
        "started": False,
        "say_seen": False,
        "choice_seen": False,
        "ctc_seen": False,
        "text_renders": 0,
        "blit_renders": 0,
        "slow_true_renders": 0,
        "partial_blits": 0,
        "full_blits": 0,
        "injects": 0,
        "text_cps": None,
        "textshaders_forced_none": None,
        "rt_samples": [],
        "photo": None,
        "error": "",
        "context_current": None,
    }

    # Hook Text.render_blits / Text.render to count progressive path usage.
    try:
        import renpy.text.text as text_mod

        _orig_render = text_mod.Text.render
        _orig_render_blits = text_mod.Text.render_blits

        def _hooked_render(self, width, height, st, at):
            state["text_renders"] = int(state["text_renders"]) + 1
            try:
                if getattr(self, "slow", False):
                    state["slow_true_renders"] = int(state["slow_true_renders"]) + 1
            except Exception:
                pass
            return _orig_render(self, width, height, st, at)

        def _hooked_render_blits(self, render, layout, st):
            state["blit_renders"] = int(state["blit_renders"]) + 1
            try:
                blits = layout.blits_typewriter(st)
                # blits is list of (x,y,w,h,...) or similar; count partial vs full
                if blits:
                    # if any blit width < full layout width → progressive mid
                    try:
                        full_w = getattr(layout, "size", None)
                        if full_w is None:
                            full_w = getattr(layout, "width", None)
                        if isinstance(full_w, (tuple, list)):
                            full_w = full_w[0]
                        max_right = 0
                        for b in blits:
                            # blit objects or tuples
                            if hasattr(b, "x") and hasattr(b, "w"):
                                max_right = max(max_right, int(b.x) + int(b.w))
                            elif isinstance(b, (tuple, list)) and len(b) >= 4:
                                max_right = max(max_right, int(b[0]) + int(b[2]))
                        if full_w and max_right > 0 and max_right < int(full_w) * 0.95:
                            state["partial_blits"] = int(state["partial_blits"]) + 1
                        else:
                            state["full_blits"] = int(state["full_blits"]) + 1
                    except Exception:
                        state["full_blits"] = int(state["full_blits"]) + 1
            except Exception:
                pass
            return _orig_render_blits(self, render, layout, st)

        text_mod.Text.render = _hooked_render  # type: ignore
        text_mod.Text.render_blits = _hooked_render_blits  # type: ignore
        rec("Text.render + render_blits hooks installed")
    except Exception as e:
        state["error"] = f"text_hook_fail:{e}"
        rec(state["error"])
        rec(traceback.format_exc())

    def injector():
        try:
            rec(f"waiting main_menu game={game}")
            for i in range(500):
                try:
                    if bool(getattr(renpy.store, "main_menu", False)):
                        state["main_menu"] = True
                        rec("main_menu at tick=%d" % i)  # noqa: UP031
                        break
                except Exception:
                    pass
                time.sleep(0.05)
            if not state["main_menu"]:
                state["error"] = (state["error"] + "|main_menu_timeout").strip("|")
                rec("main_menu timeout")
                _request_quit()
                return

            # Let Movie + chrome paint
            time.sleep(2.5)
            try:
                prefs = getattr(renpy.game, "preferences", None)
                if prefs is not None:
                    if hasattr(prefs, "transitions"):
                        # keep transitions enabled for later Step 4; do not force 0
                        if int(getattr(prefs, "transitions", 0) or 0) < 2:
                            prefs.transitions = 2
                    # Keep product cps (default 50) for progressive observation.
                    # Do NOT set text_cps=0 — that would skip progressive path.
                    state["text_cps"] = getattr(prefs, "text_cps", None)
                rec("prefs text_cps={!r} transitions={!r}".format(
                    state["text_cps"],
                    getattr(prefs, "transitions", None) if prefs is not None else None,
                ))
            except Exception as e:
                rec(f"prefs soft: {e}")

            try:
                # Confirm host textshaders force is live
                import renpy as _r

                state["textshaders_forced_none"] = bool(getattr(_r, "host_build", False))
            except Exception:
                pass

            state["phase"] = "injecting_start"
            rec("begin Start inject (Enter only; focus seed first Button)")
            K_RETURN = 13
            for i in range(40):
                renpy_host.inject_key(K_RETURN, True, "\r")
                renpy_host.inject_key(K_RETURN, False, "\r")
                state["injects"] = int(state["injects"]) + 2
                try:
                    mm = getattr(renpy.store, "main_menu", None)
                    if i % 5 == 0:
                        rec(
                            "pulse#%d main_menu=%r text_renders=%s blit=%s partial=%s"  # noqa: UP031
                            % (
                                i,
                                mm,
                                state["text_renders"],
                                state["blit_renders"],
                                state["partial_blits"],
                            )
                        )
                    if mm is False:
                        state["left_main_menu"] = True
                        state["started"] = True
                        rec("left main_menu at pulse#%d" % i)  # noqa: UP031
                        break
                except Exception as e:
                    rec(f"status: {e}")
                time.sleep(0.25)

            # Fallback: stock Start via behavior.run / JumpOutException (not renpy.run).
            # First product menu is deep in prologue.rpy (~line 686); this gate only
            # needs Start→say. choice/ctc remain human Start→long dialogue authority.
            if not state["left_main_menu"]:
                state["phase"] = "start_fallback"
                rec("Enter failed; trying behavior.run(Start) / activate_main_menu_start")
                try:
                    import interact_helpers as ih

                    try:
                        ih.activate_main_menu_start("start")
                    except Exception as e:
                        # JumpOutException is expected CONTROL path; other errors fall through
                        rec(f"activate_main_menu_start: {type(e).__name__}:{e}")
                    time.sleep(1.0)
                    mm = getattr(renpy.store, "main_menu", None)
                    if mm is False:
                        state["left_main_menu"] = True
                        state["started"] = True
                        rec("left main_menu via activate_main_menu_start")
                except Exception as e:
                    rec(f"start fallback activate: {e}")
                    rec(traceback.format_exc())
                if not state["left_main_menu"]:
                    try:
                        import renpy.game as rgame
                        from renpy.display import behavior

                        Start = getattr(renpy.store, "Start", None)
                        if Start is not None:
                            try:
                                behavior.run(Start())
                                rec("behavior.run(Start()) invoked")
                            except rgame.CONTROL_EXCEPTIONS:
                                rec("behavior.run(Start) raised CONTROL (expected)")
                                raise
                        else:
                            raise rgame.JumpOutException("start")
                    except Exception as e:
                        rec(f"start fallback behavior: {e}")
                        rec(traceback.format_exc())
                    time.sleep(1.0)
                    try:
                        mm = getattr(renpy.store, "main_menu", None)
                        if mm is False:
                            state["left_main_menu"] = True
                            state["started"] = True
                            rec("left main_menu via behavior.run fallback")
                    except Exception as e:
                        rec(f"post-fallback status: {e}")

            state["phase"] = "observing_say"
            observe_until = time.time() + 18.0
            photo_written = False
            while time.time() < observe_until:
                try:
                    if _get_screen("say") is not None:
                        state["say_seen"] = True
                    if _get_screen("choice") is not None:
                        state["choice_seen"] = True
                    if _get_screen("ctc") is not None:
                        state["ctc_seen"] = True
                    mm = getattr(renpy.store, "main_menu", None)
                    if mm is False:
                        state["left_main_menu"] = True
                        state["started"] = True
                    try:
                        ctx = renpy.game.context()
                        state["context_current"] = getattr(ctx, "current", None)
                    except Exception:
                        pass
                except Exception as e:
                    rec(f"observe soft: {e}")

                # Advance dialogue a few times so typewriter + next lines exercise
                renpy_host.inject_key(K_RETURN, True, "\r")
                renpy_host.inject_key(K_RETURN, False, "\r")
                state["injects"] = int(state["injects"]) + 2

                if state["say_seen"] and not photo_written:
                    try:
                        rt = _sample_rt()
                        state["rt_samples"].append(
                            {
                                "mean": rt.get("mean"),
                                "pure_frac": rt.get("pure_frac"),
                                "featureless_black": rt.get("featureless_black"),
                                "low_mean": rt.get("low_mean"),
                                "low_pure_frac": rt.get("low_pure_frac"),
                                "ok": rt.get("ok"),
                                "error": rt.get("error"),
                            }
                        )
                        rec(
                            "rt ok={} mean={} pure={:.3f} low_mean={} featureless={}".format(
                                rt.get("ok"),
                                tuple(round(x, 1) for x in (rt.get("mean") or (0, 0, 0))),
                                float(rt.get("pure_frac") or 0),
                                tuple(round(x, 1) for x in (rt.get("low_mean") or (0, 0, 0))),
                                rt.get("featureless_black"),
                            )
                        )
                        # evidence strip of lower band
                        if rt.get("ok"):
                            try:
                                rw, rh, rgba = renpy_host.read_game_rt_rgba()
                                p = photo_dir / "step3-say-mid-band.png"
                                state["photo"] = _write_png_strip(p, rgba, rw, rh)
                                photo_written = True
                                rec("photo={}".format(state["photo"]))
                            except Exception as e:
                                rec(f"photo soft: {e}")
                    except Exception as e:
                        rec(f"rt sample soft: {e}")

                # Early stop when we have say + progressive blits evidence
                if (
                    state["say_seen"]
                    and (int(state["partial_blits"]) >= 1 or int(state["blit_renders"]) >= 1)
                    and photo_written
                ):
                    rec("early stop: say + blits + photo")
                    break
                time.sleep(0.35)

            # Final RT sample even if say never seen
            if not state["rt_samples"]:
                try:
                    rt = _sample_rt()
                    state["rt_samples"].append(
                        {
                            "mean": rt.get("mean"),
                            "pure_frac": rt.get("pure_frac"),
                            "featureless_black": rt.get("featureless_black"),
                            "low_mean": rt.get("low_mean"),
                            "low_pure_frac": rt.get("low_pure_frac"),
                            "ok": rt.get("ok"),
                            "error": rt.get("error"),
                        }
                    )
                except Exception as e:
                    rec(f"final rt soft: {e}")

            state["phase"] = "quitting"
            rec(
                "request_quit left_mm={} say={} blit={} partial={} text_renders={}".format(
                    state["left_main_menu"],
                    state["say_seen"],
                    state["blit_renders"],
                    state["partial_blits"],
                    state["text_renders"],
                )
            )
        except Exception as e:
            state["error"] = f"{type(e).__name__}: {e}"
            rec("injector exc: {}".format(state["error"]))
            rec(traceback.format_exc())
        finally:
            _request_quit()

    threading.Thread(target=injector, daemon=True).start()

    import renpy.main as renpy_main

    rec("entering renpy.main.main()")
    try:
        renpy_main.main()
        rec("main returned")
    except BaseException as e:
        rec(f"main exit {type(e).__name__}: {e}")

    # Verdict: entry reconfirm helper
    # PASS path = left main_menu + say screen + (blit path or text renders) + non-featureless RT
    rt_ok = False
    featureless = True
    last_rt = state["rt_samples"][-1] if state["rt_samples"] else {}
    if last_rt.get("ok"):
        rt_ok = True
        featureless = bool(last_rt.get("featureless_black"))

    engine_path_ok = (
        bool(state["left_main_menu"])
        and bool(state["say_seen"])
        and (int(state["blit_renders"]) >= 1 or int(state["text_renders"]) >= 1)
        and rt_ok
        and not featureless
    )
    progressive_hint = int(state["partial_blits"]) >= 1 or int(state["slow_true_renders"]) >= 1

    # Entry gate semantics for reopen Step 3:
    # - live_fail_reconfirm=True  → product Start→say FAILED (surgery may proceed)
    # - live_fail_reconfirm=False → product path appears usable (do NOT thrash)
    live_fail = not engine_path_ok
    ok = True  # gate itself completed; ok reflects harness success, not AC pass
    reason_parts = []
    if not state["left_main_menu"]:
        reason_parts.append("never_left_main_menu")
    if not state["say_seen"]:
        reason_parts.append("say_screen_not_seen")
    if int(state["blit_renders"]) < 1 and int(state["text_renders"]) < 1:
        reason_parts.append("no_text_render")
    if not rt_ok:
        reason_parts.append("rt_not_ok")
    if featureless:
        reason_parts.append("rt_featureless_black")
    if state["error"]:
        reason_parts.append("error=" + state["error"])
    if engine_path_ok and not progressive_hint:
        reason_parts.append("say_ok_but_no_partial_blits_observed")
    reason = "product_say_usable" if engine_path_ok else ("|".join(reason_parts) if reason_parts else "fail")

    body = [
        "gate=hmc_say_product_reconfirm",
        f"ok={ok}",
        f"engine_path_ok={engine_path_ok}",
        f"live_fail_reconfirm={live_fail}",
        f"progressive_hint={progressive_hint}",
        "path_kind=product_huangmeic_start_say",
        f"game={game}",
        "left_main_menu={}".format(state["left_main_menu"]),
        "started={}".format(state["started"]),
        "say_seen={}".format(state["say_seen"]),
        "choice_seen={}".format(state["choice_seen"]),
        "ctc_seen={}".format(state["ctc_seen"]),
        "text_renders={}".format(state["text_renders"]),
        "blit_renders={}".format(state["blit_renders"]),
        "slow_true_renders={}".format(state["slow_true_renders"]),
        "partial_blits={}".format(state["partial_blits"]),
        "full_blits={}".format(state["full_blits"]),
        "text_cps={}".format(state["text_cps"]),
        "textshaders_forced_none={}".format(state["textshaders_forced_none"]),
        "injects={}".format(state["injects"]),
        "context_current={!r}".format(state["context_current"]),
        "rt_samples={}".format(len(state["rt_samples"])),
        "last_rt_mean={}".format(last_rt.get("mean")),
        "last_rt_pure_frac={}".format(last_rt.get("pure_frac")),
        "last_rt_featureless={}".format(last_rt.get("featureless_black")),
        "last_rt_low_mean={}".format(last_rt.get("low_mean")),
        "photo={}".format(state["photo"]),
        "phase={}".format(state["phase"]),
        f"reason={reason}",
        "notes=ENTRY helper for reopen Step 3. live_fail_reconfirm=True means surgery allowed; False means do not thrash green typewriter gates. Human remains AC authority.",
    ]
    body.extend(lines[-120:])
    text = "\n".join(body) + "\n"
    out.write_text(text)
    art.write_text(text)
    _log(f"WROTE {out} engine_path_ok={engine_path_ok} live_fail={live_fail} reason={reason}")
    _request_quit()


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
