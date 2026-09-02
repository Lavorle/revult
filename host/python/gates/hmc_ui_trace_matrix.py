"""
Phase 1 UI evidence matrix gate (settings + confirm + chat).

Gate name: hmc_ui_trace_matrix  (RENPY_HOST_GATE=hmc_ui_trace_matrix)

Opens product screens that exercise missing-text surfaces under
RENPY_HOST_UI_TRACE=1 once-logs. Not a product fix — instrumentation only.

Targets:
  - preferences  (system settings)
  - confirm      (confirm settings / dialog)
  - chat         (phone chat bubble text)

Writes: host/target/gate-hmc_ui_trace_matrix.txt
"""

import os
import sys
import threading
import time
import traceback
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---


def _base():
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path("/mnt/nvme1n1p2/revult")


def _log(msg):
    try:
        sys.__stdout__.write(f"[hmc_ui_trace] {msg}\n")
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        open("/tmp/hmc_ui_trace_matrix.log", "a").write(msg + "\n")
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
    import types

    try:
        import renpy.audio as _ra
        import renpy.audio.renpysound_host as _rs_host

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
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
        except Exception:
            pass
    except Exception as e:
        _log(f"pygame soft-fail: {e}")

    try:
        import renpy_uguu_host as u

        sys.modules["renpy.uguu.uguu"] = u
        sys.modules["renpy.uguu.gl"] = u
        pkg = sys.modules.get("renpy.uguu") or types.ModuleType("renpy.uguu")
        pkg.__path__ = []
        sys.modules["renpy.uguu"] = pkg
        for n in dir(u):
            if n.startswith("GL_") or n in ("clear_errors", "get_error"):
                setattr(pkg, n, getattr(u, n))
        pkg.uguu = u
        pkg.gl = u
        import renpy

        renpy.uguu = pkg
    except Exception as e:
        _log(f"uguu soft-fail: {e}")

    try:
        import renpy_ecsign_host as e

        sys.modules["renpy.ecsign"] = e
        import renpy

        renpy.ecsign = e
    except Exception as e:
        _log(f"ecsign soft-fail: {e}")


def _get_screen(name):
    try:
        import renpy

        return renpy.display.screen.get_screen(name)
    except Exception:
        return None


def _hide_menus():
    import renpy

    for n in ("load", "preferences", "appreciation", "flowchart", "confirm", "save", "chat"):
        try:
            renpy.store.Hide(n)()
        except Exception:
            try:
                renpy.hide_screen(n)
            except Exception:
                pass


def _force_show_menu(name):
    import renpy

    try:
        action = renpy.store.ShowMenu(name)
        action()
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        return True, "ShowMenu()"
    except Exception as e:
        try:
            renpy.display.screen.show_screen(name)
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            return True, f"display.screen.show_screen:{e}"
        except Exception as e2:
            return False, f"ShowMenu fail {e} / {e2}"


def _force_confirm():
    import renpy

    try:
        try:
            m = getattr(renpy.store, "persistent", None)
            if m is not None:
                mapping = getattr(m, "preferences_confirm_requirement_mapping", None)
                if isinstance(mapping, dict):
                    mapping["quit"] = True
        except Exception:
            pass
        renpy.display.screen.show_screen(
            "confirm",
            message="Phase1 UI trace confirm 确认退出？",
            yes_action=[renpy.store.Hide("confirm")],
            no_action=[renpy.store.Hide("confirm")],
            confirm_type="quit",
        )
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        return True, "show_screen_confirm"
    except Exception as e:
        return False, f"confirm fail {e}"


def _force_chat():
    import renpy

    # message_entry: (sender, text, arguments); sender None = system line.
    history = [
        (None, "系统：会话开始", None),
        ("黄莓", "你好，这是历史气泡", None),
    ]
    messages = [
        ("晓海", "这是待发送的宿主气泡文字", None),
        ("夏玟", "另一条中文气泡", None),
    ]
    try:
        renpy.display.screen.show_screen(
            "chat",
            False,  # group
            "Phase1聊天",
            history,
            messages,
            _transient=True,
        )
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        return True, "show_screen_chat"
    except Exception as e:
        # Fallback: positional kwargs if screen signature differs.
        try:
            renpy.display.screen.show_screen(
                "chat",
                group=False,
                title="Phase1聊天",
                history=history,
                messages=messages,
                _transient=True,
            )
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            return True, f"show_screen_chat_kwargs:{e}"
        except Exception as e2:
            return False, f"chat fail {e} | {e2}"


def _force_product_redraw():
    """Product present after ShowMenu when nested interact frames=1.

    Mirrors hmc_nav_structure_probe: rebuild root → render_screen →
    load_all_textures → draw_screen so FTFont.draw / load_texture fire.
    """
    import renpy

    try:
        import interact_helpers as ih
    except Exception as e:
        return f"no_ih:{e}"
    try:
        ready, why, iface = ih.interface_ready()
        if not ready or iface is None:
            return f"iface:{why}"
        root = ih._rebuild_product_root(iface)
        if root is None:
            return "root_absent"
        w = int(getattr(renpy.config, "screen_width", 1920) or 1920)
        h = int(getattr(renpy.config, "screen_height", 1080) or 1080)
        surftree = renpy.display.render.render_screen(root, w, h)
        draw = getattr(renpy.display, "draw", None)
        if draw is None or not hasattr(draw, "draw_screen"):
            return "no_draw"
        try:
            if hasattr(draw, "load_all_textures"):
                draw.load_all_textures(surftree)
        except Exception as e:
            return f"load_fail:{e}"
        draw.draw_screen(surftree, flip=True)
        try:
            iface.surftree = surftree
        except Exception:
            pass
        return "rebuild_render_screen"
    except Exception as e:
        return f"redraw_exc:{e}"


def _walk_text_stats(node, acc=None, depth=0, budget=None):
    """Count Text / HostTexture leaves for matrix structure evidence."""
    if acc is None:
        acc = {
            "n_text": 0,
            "n_host_tex": 0,
            "n_dead": 0,
            "n_alive": 0,
            "text_samples": [],
            "n_nodes": 0,
        }
    if budget is None:
        budget = [4000]
    if depth > 40 or budget[0] <= 0 or node is None:
        return acc
    budget[0] -= 1
    acc["n_nodes"] += 1
    try:
        import renpy_host  # type: ignore

        tname = type(node).__name__
        # Text displayable
        try:
            from renpy.text.text import Text as _Text
        except Exception:
            _Text = ()
        if _Text and isinstance(node, _Text):
            acc["n_text"] += 1
            if len(acc["text_samples"]) < 6:
                try:
                    s = getattr(node, "text", None)
                    if isinstance(s, list):
                        s = "".join(str(x) for x in s[:4])
                    acc["text_samples"].append(str(s)[:40] if s is not None else tname)
                except Exception:
                    acc["text_samples"].append(tname)
        # HostTexture-like
        handle = getattr(node, "handle", None)
        if handle is None:
            handle = getattr(node, "texture", None)
        if isinstance(handle, int) and handle > 0:
            acc["n_host_tex"] += 1
            alive = True
            try:
                if hasattr(renpy_host, "texture_alive"):
                    alive = bool(renpy_host.texture_alive(int(handle)))
            except Exception:
                alive = True
            if alive:
                acc["n_alive"] += 1
            else:
                acc["n_dead"] += 1
        # children
        children = getattr(node, "children", None) or getattr(node, "blits", None)
        if children:
            for ch in list(children)[:64]:
                if isinstance(ch, (list, tuple)) and ch:
                    _walk_text_stats(ch[0], acc, depth + 1, budget)
                else:
                    _walk_text_stats(ch, acc, depth + 1, budget)
        for attr in ("render", "child", "raw_child", "focus", "layer"):
            try:
                ch = getattr(node, attr, None)
                if ch is not None and ch is not node:
                    _walk_text_stats(ch, acc, depth + 1, budget)
            except Exception:
                pass
    except Exception:
        pass
    return acc


def _leaf_font_probe():
    """Direct FTFont host leaf probe (CJK sample) — fills alpha/face matrix row."""
    info = {
        "ok": False,
        "alpha_nonzero": 0,
        "face": None,
        "fallback": False,
        "handle": None,
        "alive": None,
        "err": None,
    }
    try:
        from renpy.pygame.surface import Surface
        from renpy_text_ftfont_host import FTFace, FTFont
        from renpy_text_ftfont_host import init as ft_init

        ft_init()
        # Prefer a game CJK face if present.
        base = _base()
        candidates = [
            base / "host" / "playtests" / "HuangmeiC" / "game" / "fonts" / "SourceHanSansCN-Bold.otf",
            base / "host" / "playtests" / "HuangmeiC" / "game" / "fonts" / "HarmonyOS_Sans_SC_Regular.ttf",
            Path("/usr/share/fonts/google-droid-sans-fonts/DroidSansFallback.ttf"),
            Path("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc"),
        ]
        face = None
        face_path = None
        for p in candidates:
            try:
                if p.is_file():
                    with open(p, "rb") as fh:
                        face = FTFace(fh, 0, str(p))
                    face_path = str(p)
                    break
            except Exception:
                continue
        if face is None:
            face = FTFace(_io_bytes := __import__("io").BytesIO(b""), 0, "empty")
            info["fallback"] = True
        info["face"] = face_path or "default"
        # Detect load_default fallback: FTFace stores _pil default when truetype fails.
        try:
            font = FTFont(face, 28, False, False, 0, True, False, "auto")
        except Exception as e:
            info["err"] = f"FTFont:{e}"
            return info
        surf = Surface((256, 64))
        # Build glyphs via font.glyphs then draw.
        glyphs = font.glyphs("确认设置聊天黄莓")
        font.draw(surf, 0, 0, (255, 255, 255, 255), glyphs, False, False, (0, 0, 0, 255))
        px = getattr(surf, "_pixels", b"")
        nonzero = 0
        try:
            for i in range(3, len(px), 4):
                if px[i]:
                    nonzero += 1
        except Exception:
            pass
        info["alpha_nonzero"] = nonzero
        # Upload through product draw path if available.
        try:
            import renpy

            draw = getattr(renpy.display, "draw", None)
            if draw is not None and hasattr(draw, "load_texture"):
                ht = draw.load_texture(surf)
                handle = int(getattr(ht, "handle", 0) or 0)
                info["handle"] = handle
                try:
                    import renpy_host  # type: ignore

                    if handle > 0 and hasattr(renpy_host, "texture_alive"):
                        info["alive"] = bool(renpy_host.texture_alive(handle))
                    else:
                        info["alive"] = handle > 0
                except Exception:
                    info["alive"] = handle > 0
        except Exception as e:
            info["err"] = f"upload:{e}"
        info["ok"] = nonzero > 0 and (info.get("handle") or 0) > 0
        return info
    except Exception as e:
        info["err"] = f"{type(e).__name__}:{e}"
        return info


def run():
    base = _base()
    out = base / "host" / "target" / "gate-hmc_ui_trace_matrix.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    state = {
        "phase": "boot",
        "main_menu": False,
        "results": [],
        "errors": [],
    }

    def rec(m):
        lines.append(m)
        _log(m)

    # Force UI_TRACE on for this gate (matrix collection).
    os.environ["RENPY_HOST_UI_TRACE"] = "1"
    rec("RENPY_HOST_UI_TRACE={}".format(os.environ.get("RENPY_HOST_UI_TRACE")))

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

    for name, call in (
        ("import_renpy", boot.stage_import_renpy),
        ("import_all", boot.stage_import_all),
        ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ):
        good, _miss, err, _extra = call()
        rec(f"stage {name} good={good} err={err!r}")
        if not good:
            out.write_text(f"gate=hmc_ui_trace_matrix\nok=False\nerror={err}\n")
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

    # Map AC1 surfaces → open strategy.
    targets = [
        ("settings", "preferences", "menu"),
        ("confirm", "confirm", "overlay"),
        ("chat", "chat", "overlay"),
    ]

    def injector():
        rec("waiting main_menu")
        # Up to ~60s: Movie decode + nested pump can delay store.main_menu.
        for i in range(1200):
            try:
                if bool(getattr(renpy.store, "main_menu", False)):
                    state["main_menu"] = True
                    rec("main_menu at tick=%d" % i)
                    break
            except Exception:
                pass
            # Soft alternate: main_menu screen already present.
            try:
                if _get_screen("main_menu") is not None:
                    state["main_menu"] = True
                    rec("main_menu screen present at tick=%d" % i)
                    break
            except Exception:
                pass
            time.sleep(0.05)
        if not state["main_menu"]:
            state["errors"].append("main_menu_timeout")
            rec("main_menu timeout")
            _finish()
            _request_quit()
            return

        # Let main-menu chrome + movie settle so reverse/arena probes fire.
        time.sleep(2.0)
        try:
            pinfo = _force_product_redraw()
            rec(f"main_menu redraw={pinfo}")
        except Exception as e:
            rec(f"main_menu redraw fail: {e}")

        for tname, screen_name, kind in targets:
            state["phase"] = f"nav_{tname}"
            rec(f"=== target {tname} ({screen_name}/{kind}) ===")
            entry = {
                "name": tname,
                "screen": screen_name,
                "opened": False,
                "open_via": None,
                "error": None,
                "redraw": None,
                "struct": {},
                "leaf": {},
            }
            try:
                _hide_menus()
                time.sleep(0.2)
                if tname == "confirm":
                    ok_open, via = _force_confirm()
                elif tname == "chat":
                    ok_open, via = _force_chat()
                else:
                    ok_open, via = _force_show_menu(screen_name)
                entry["open_via"] = via
                if not ok_open:
                    entry["error"] = via
                    state["results"].append(entry)
                    rec(f"open FAIL {via}")
                    continue

                opened = False
                for _j in range(40):
                    if _get_screen(screen_name) is not None:
                        opened = True
                        break
                    time.sleep(0.1)
                entry["opened"] = opened
                rec(f"opened={opened} via={via}")
                # Hold open so draw_screen once-logs can fire for this surface.
                time.sleep(0.5)
                entry["redraw"] = _force_product_redraw()
                rec("redraw={}".format(entry["redraw"]))
                time.sleep(0.4)
                entry["redraw2"] = _force_product_redraw()
                rec("redraw2={}".format(entry["redraw2"]))
                # Structure walk on live surftree after redraw.
                try:
                    import renpy

                    st = getattr(renpy.display.interface, "surftree", None)
                    entry["struct"] = _walk_text_stats(st)
                    rec(
                        "struct n_text={} n_host_tex={} n_alive={} n_dead={} samples={}".format(
                            entry["struct"].get("n_text"),
                            entry["struct"].get("n_host_tex"),
                            entry["struct"].get("n_alive"),
                            entry["struct"].get("n_dead"),
                            entry["struct"].get("text_samples"),
                        )
                    )
                except Exception as e:
                    entry["struct"] = {"err": str(e)}
                    rec(f"struct fail: {e}")
                # Direct leaf font probe once (same for all targets; still per-row evidence).
                try:
                    entry["leaf"] = _leaf_font_probe()
                    rec(
                        "leaf ok={} alpha_nz={} handle={} alive={} face={} err={}".format(
                            entry["leaf"].get("ok"),
                            entry["leaf"].get("alpha_nonzero"),
                            entry["leaf"].get("handle"),
                            entry["leaf"].get("alive"),
                            entry["leaf"].get("face"),
                            entry["leaf"].get("err"),
                        )
                    )
                except Exception as e:
                    entry["leaf"] = {"err": str(e)}
                    rec(f"leaf fail: {e}")
                time.sleep(0.3)
            except Exception as e:
                entry["error"] = str(e)
                rec(f"target exc: {e}")
                rec(traceback.format_exc())
            state["results"].append(entry)

        _finish()
        time.sleep(0.3)
        _request_quit()

    def _finish():
        opened = [r["name"] for r in state["results"] if r.get("opened")]
        failed = [r["name"] for r in state["results"] if not r.get("opened")]
        body = [
            "gate=hmc_ui_trace_matrix",
            "ok=%s" % (state["main_menu"] and len(opened) >= 2),
            "main_menu={}".format(state["main_menu"]),
            "opened={}".format(",".join(opened)),
            "failed={}".format(",".join(failed)),
            "ui_trace={}".format(os.environ.get("RENPY_HOST_UI_TRACE")),
            "phase={}".format(state["phase"]),
            "errors={}".format(";".join(state["errors"])),
        ]
        for r in state["results"]:
            st = r.get("struct") or {}
            lf = r.get("leaf") or {}
            body.append(
                "result name={} screen={} opened={} via={} redraw={} "
                "n_text={} n_host_tex={} n_alive={} n_dead={} "
                "leaf_ok={} leaf_alpha_nz={} leaf_handle={} leaf_alive={} leaf_face={} "
                "err={}".format(
                    r.get("name"),
                    r.get("screen"),
                    r.get("opened"),
                    r.get("open_via"),
                    r.get("redraw"),
                    st.get("n_text"),
                    st.get("n_host_tex"),
                    st.get("n_alive"),
                    st.get("n_dead"),
                    lf.get("ok"),
                    lf.get("alpha_nonzero"),
                    lf.get("handle"),
                    lf.get("alive"),
                    lf.get("face"),
                    r.get("error") or lf.get("err") or st.get("err"),
                )
            )
        text = "\n".join(body) + "\n"
        out.write_text(text)
        rec(f"WROTE {out}")
        rec(text)

    t = threading.Thread(target=injector, name="hmc-ui-trace", daemon=True)
    t.start()

    import renpy.main as renpy_main

    rec("entering renpy.main.main()")
    try:
        renpy_main.main()
        rec("main returned")
    except BaseException as e:
        rec(f"main exit {type(e).__name__}: {e}")
        rec(traceback.format_exc())

    # Ensure artifact even if injector raced quit.
    try:
        if not out.exists() or out.stat().st_size == 0:
            _finish()
    except Exception:
        try:
            _finish()
        except Exception:
            pass


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
