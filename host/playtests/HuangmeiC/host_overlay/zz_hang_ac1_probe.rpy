# Hang-lead AC1 probe (env-gated). Copied into game/ by playtest overlay only.
# Does not mutate recovered_project. Enable with RENPY_HOST_AC1_PROBE=1.

init python hide:
    import os
    import sys
    import threading
    import time

    if os.environ.get("RENPY_HOST_AC1_PROBE", "").strip() not in ("1", "true", "yes"):
        pass
    else:

        def _ac1_log(msg):
            try:
                print(
                    "PHASE0_SIGNAL t=%.3f AC1_PROBE %s" % (time.monotonic(), msg),
                    file=sys.stderr,
                    flush=True,
                )
            except Exception:
                pass

        def _ac1_probe():
            t_thread = time.monotonic()
            _ac1_log("thread_start")

            mm_t0 = None
            for _ in range(600):
                try:
                    import renpy

                    if bool(getattr(renpy.store, "main_menu", False)):
                        mm_t0 = time.monotonic()
                        _ac1_log("main_menu_true dt_thread=%.3f" % (mm_t0 - t_thread))
                        break
                except Exception as e:
                    _ac1_log("wait_err %r" % (e,))
                time.sleep(0.1)
            else:
                _ac1_log("FAIL no main_menu within 60s")
                if os.environ.get("RENPY_HOST_AC1_PROBE_QUIT", "").strip() in ("1", "true", "yes"):
                    try:
                        import renpy_host

                        renpy_host.request_quit()
                    except Exception:
                        pass
                return

            # Settle after Movie decode path.
            time.sleep(0.5)

            try:
                import renpy
                import renpy_host
            except Exception as e:
                _ac1_log("FAIL import %r" % (e,))
                return

            # Scale virtual 1920×1080 dock coords → physical inject coords.
            try:
                draw = renpy.display.draw
                phys = tuple(getattr(draw, "physical_size", (1920, 1080)) or (1920, 1080))
                virt = tuple(getattr(draw, "virtual_size", (1920, 1080)) or (1920, 1080))
            except Exception:
                phys, virt = (1920, 1080), (1920, 1080)
            try:
                win = tuple(renpy_host.window_size())
            except Exception:
                win = phys
            _ac1_log("sizes phys=%r virt=%r win=%r" % (phys, virt, win))

            vw = float(virt[0]) or 1920.0
            vh = float(virt[1]) or 1080.0
            pw = float(phys[0] or win[0] or 1920)
            ph = float(phys[1] or win[1] or 1080)
            sx = pw / vw
            sy = ph / vh

            # Virtual dock button centers (6-btn no-continue + 7-btn with continue).
            vpts = [
                ("start", 370, 1018),
                ("load", 606, 1018),
                ("extra", 842, 1018),
                ("flowchart", 1078, 1018),
                ("config", 1314, 1018),
                ("exit", 1550, 1018),
                ("start7", 252, 1018),
                ("continue7", 488, 1018),
                ("load7", 724, 1018),
                ("extra7", 960, 1018),
                ("flowchart7", 1196, 1018),
                ("config7", 1432, 1018),
                ("exit7", 1668, 1018),
            ]
            # Also try a few y offsets in case dock padding differs.
            y_offs = (0, -20, -40, 10)

            def _focus_name():
                try:
                    f = renpy.display.focus.get_focused()
                    if f is None:
                        return None
                    return str(f)[:160]
                except Exception as e:
                    return "err:%s" % e

            def _mouse_pos():
                try:
                    import renpy.pygame as rpg

                    return tuple(rpg.mouse.get_pos())
                except Exception:
                    try:
                        import host_pygame.mouse as m

                        return tuple(m.get_pos())
                    except Exception as e:
                        return ("err", str(e))

            first_hover_t = None
            hits = 0
            for name, vx, vy in vpts:
                for yo in y_offs:
                    px = int(vx * sx)
                    py = int((vy + yo) * sy)
                    try:
                        # Motion + no real button press.
                        renpy_host.inject_mouse(px, py, 0, False)
                    except Exception as e:
                        _ac1_log("inject_fail %s %r" % (name, e))
                        continue
                    # Also drive focus path directly with *virtual* coords (hang AC1
                    # cares that input path is live; focus API proves hoverability).
                    try:
                        renpy.display.focus.mouse_handler(None, int(vx), int(vy + yo), default=False)
                    except Exception as e:
                        _ac1_log("mouse_handler_err %s %r" % (name, e))
                    time.sleep(0.05)
                    foc = _focus_name()
                    mpos = _mouse_pos()
                    now = time.monotonic()
                    if foc and not str(foc).startswith("err:"):
                        hits += 1
                        if first_hover_t is None:
                            first_hover_t = now
                            _ac1_log(
                                "first_focus name=%s vxy=%d,%d pxy=%d,%d yo=%d "
                                "dt_main_menu=%.3f focus=%r mouse=%r"
                                % (name, vx, vy, px, py, yo, first_hover_t - mm_t0, foc, mpos)
                            )
                        else:
                            _ac1_log(
                                "hover name=%s vxy=%d,%d pxy=%d,%d yo=%d focus=%r mouse=%r"
                                % (name, vx, vy, px, py, yo, foc, mpos)
                            )
                        break  # next button
                else:
                    _ac1_log(
                        "miss name=%s vxy=%d,%d focus=%r mouse=%r"
                        % (name, vx, vy, _focus_name(), _mouse_pos())
                    )

            # Keyboard smoke: Escape/arrow should not hang.
            try:
                renpy_host.inject_key(275, True, "")  # RIGHT
                renpy_host.inject_key(275, False, "")
                renpy_host.inject_key(276, True, "")  # LEFT
                renpy_host.inject_key(276, False, "")
                _ac1_log("keys_injected focus=%r" % (_focus_name(),))
            except Exception as e:
                _ac1_log("keys_fail %r" % (e,))

            # Idle ≥30 s with periodic re-hover (AC1: no kill).
            idle_end = time.monotonic() + 32.0
            sweeps = 0
            while time.monotonic() < idle_end:
                sweeps += 1
                for name, vx, vy in vpts[:6]:
                    px = int(vx * sx)
                    py = int(vy * sy)
                    try:
                        renpy_host.inject_mouse(px, py, 0, False)
                        renpy.display.focus.mouse_handler(None, int(vx), int(vy), default=False)
                    except Exception:
                        pass
                    time.sleep(0.05)
                try:
                    mm = bool(getattr(renpy.store, "main_menu", False))
                except Exception:
                    mm = None
                _ac1_log(
                    "idle_sweep n=%d main_menu=%s focus=%r hits=%d remaining=%.1f"
                    % (sweeps, mm, _focus_name(), hits, idle_end - time.monotonic())
                )
                time.sleep(2.0)

            _ac1_log(
                "DONE first_hover_dt=%s hits=%d sweeps=%d"
                % (
                    ("%.3f" % (first_hover_t - mm_t0)) if first_hover_t and mm_t0 else "none",
                    hits,
                    sweeps,
                )
            )

            if os.environ.get("RENPY_HOST_AC1_PROBE_QUIT", "").strip() in ("1", "true", "yes"):
                try:
                    renpy_host.request_quit()
                    _ac1_log("request_quit")
                except Exception as e:
                    _ac1_log("request_quit_fail %r" % (e,))

        try:
            t = threading.Thread(target=_ac1_probe, name="hang-ac1-probe", daemon=True)
            t.start()
            _ac1_log("spawned")
        except Exception as e:
            _ac1_log("spawn_fail %r" % (e,))
