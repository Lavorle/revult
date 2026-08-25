"""HuangmeiC main-menu video soak probe (WP0 dual-window baseline).

Gate: RENPY_HOST_GATE=hmc_menu_video_soak_probe

Dual window after main_menu ready:
  - early: 0..3s
  - late:  3..15s (AC-M-soak warm residual)

Metrics:
  host p99 inter-present gaps (take_inter_present_gaps_ms)
  frame_index advance / unique samples
  path_cache frame count + inflight + ready_full
  product_fps / host_frames
  stall >= 2s present gap

JSON: host/target/gate-hmc_menu_video_soak_probe.json
Note: no from __future__; host run_file prepends imports.
"""

import json
import os
import sys
import threading
import time
import traceback
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


def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")


def _log(msg):
    try:
        sys.__stdout__.write("[hmc_menu_video_soak] " + str(msg))
        sys.__stdout__.write(chr(10))
        sys.__stdout__.flush()
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        open("/tmp/hmc_menu_video_soak_probe.log", "a").write(str(msg) + chr(10))  # noqa: SIM115
    except Exception:  # noqa: BLE001, S110
        pass


def _quit():
    try:
        import renpy_host
        renpy_host.request_quit()
    except Exception:  # noqa: BLE001, S110
        pass


def _clear_falsey(name):
    val = os.environ.get(name)
    if val is not None and str(val).strip().lower() in ("", "0", "false", "no", "off", "n"):
        os.environ.pop(name, None)


def _stubs():
    import types

    try:
        import renpy.audio as a
        import renpy.audio.renpysound_host as h

        sys.modules["renpy.audio.renpysound"] = h
        a.renpysound = h
    except Exception as e:  # noqa: BLE001
        _log(f"sound {e}")
    try:
        import host_pygame
        import host_pygame.locals as loc
        from host_pygame import scrap

        if not hasattr(host_pygame, "constants"):
            host_pygame.constants = loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules["renpy.pygame.scrap"] = scrap
        sys.modules["pygame.scrap"] = scrap
        import renpy.pygame as rpg

        if not hasattr(rpg, "constants"):
            rpg.constants = host_pygame.constants
        try:
            rpg.scrap = scrap
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            rpg.import_as_pygame()
        except Exception:  # noqa: BLE001, S110
            pass
    except Exception as e:  # noqa: BLE001
        _log(f"pygame {e}")
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
    except Exception as e:  # noqa: BLE001
        _log(f"uguu {e}")
    try:
        import renpy_ecsign_host as e

        sys.modules["renpy.ecsign"] = e
        import renpy

        renpy.ecsign = e
    except Exception as e:  # noqa: BLE001
        _log(f"ecsign {e}")


def _product_presents():
    import renpy_host
    if hasattr(renpy_host, "product_presents"):
        return int(renpy_host.product_presents())
    return -1


def _frame_count():
    import renpy_host
    if hasattr(renpy_host, "frame_count"):
        return int(renpy_host.frame_count())
    return -1


def _take_host_gaps():
    try:
        import renpy_host
        take = getattr(renpy_host, "take_inter_present_gaps_ms", None)
        if take is not None:
            return [float(x) for x in list(take())]
        peek = getattr(renpy_host, "inter_present_gaps_ms", None)
        if peek is not None:
            return [float(x) for x in list(peek())]
    except Exception:  # noqa: BLE001, S110
        pass
    return []


def _p99(gaps):
    if not gaps:
        return None
    sg = sorted(gaps)
    idx = min(len(sg) - 1, round(0.99 * (len(sg) - 1)))
    return float(sg[idx])


def _movie_path_candidates():
    base = _base()
    env = os.environ.get("RENPY_HOST_MOVIE_PATH")
    out = []
    if env:
        out.append(env)
    out.extend(
        [
            "video/main_menu@2.webm",
            "video/main_menu.webm",
            str(base / "host" / "playtests" / "HuangmeiC" / "game" / "video" / "main_menu@2.webm"),
            str(base / "host" / "playtests" / "HuangmeiC" / "game" / "video" / "main_menu.webm"),
        ]
    )
    return out


def _path_cache_snapshot():
    snap = {
        "nframes": None,
        "ready_full": None,
        "inflight": None,
        "target_frames": None,
        "path": None,
        "helper": None,
    }
    try:
        from renpy.audio import renpysound_host as rps
    except Exception as e:  # noqa: BLE001
        snap["error"] = f"{type(e).__name__}: {e}"
        return snap

    keys = _movie_path_candidates()
    for key in keys:
        try:
            if hasattr(rps, "path_cache_frame_count"):
                n = int(rps.path_cache_frame_count(key))
                if n > 0 or snap["nframes"] is None:
                    snap["nframes"] = n
                    snap["path"] = key
                    snap["helper"] = "path_cache_frame_count"
            if hasattr(rps, "path_cache_ready_full"):
                rf = bool(rps.path_cache_ready_full(key))
                if rf or snap["ready_full"] is None:
                    snap["ready_full"] = rf
        except Exception:  # noqa: BLE001, S110
            pass

    try:
        cache = getattr(rps, "_PATH_FRAME_CACHE", None) or {}
        if isinstance(cache, dict) and cache:
            best = None
            best_n = -1
            for k, entry in cache.items():
                try:
                    n = len(entry.get("frames") or [])
                except Exception:  # noqa: BLE001
                    n = 0
                if n > best_n:
                    best_n = n
                    best = (k, entry)
            if best is not None:
                k, entry = best
                snap["path"] = snap["path"] or k
                if snap["nframes"] is None:
                    snap["nframes"] = best_n
                snap["inflight"] = bool(entry.get("inflight"))
                snap["target_frames"] = entry.get("target_frames")
                if snap["ready_full"] is None:
                    snap["ready_full"] = bool(entry.get("ready_full"))
                snap["helper"] = (snap.get("helper") or "") + "+_PATH_FRAME_CACHE"
    except Exception as e:  # noqa: BLE001
        snap["cache_error"] = f"{type(e).__name__}: {e}"
    return snap


def _movie_frame_index_samples(max_channels=16):
    samples = []
    try:
        from renpy.audio import renpysound_host as rps
        chans = getattr(rps, "_channels", {}) or {}
        for ch, st in list(chans.items()):
            if int(ch) >= max_channels:
                continue
            try:
                idx = st.get("frame_index")
                local = st.get("frame_index_local")
                nfr = len(st.get("play_frames") or st.get("frames") or [])
                base = st.get("base_index")
                total = st.get("total_decoded")
                if idx is not None or nfr > 0:
                    samples.append(
                        {
                            "channel": int(ch),
                            "frame_index": int(idx) if idx is not None else None,
                            "frame_index_local": int(local) if local is not None else None,
                            "nframes": int(nfr),
                            "base_index": int(base) if base is not None else None,
                            "total_decoded": int(total) if total is not None else None,
                        }
                    )
            except Exception:  # noqa: BLE001, S110
                pass
    except Exception:  # noqa: BLE001, S110
        pass
    return samples


def _best_frame_index(samples):
    best = None
    for s in samples:
        idx = s.get("frame_index")
        if idx is None:
            continue
        if best is None or int(idx) > int(best):
            best = int(idx)
    return best


def _sample_window(label, seconds, poll_s=0.05):
    _take_host_gaps()
    t0 = time.monotonic()
    p0 = _product_presents()
    f0 = _frame_count()
    idx_samples = []
    cache_samples = []
    last_p = p0
    last_progress_t = t0
    stall_ge_2s = False
    poll_gaps = []
    last_t = t0

    while time.monotonic() - t0 < seconds:
        time.sleep(poll_s)
        now = time.monotonic()
        p = _product_presents()
        if p > last_p:
            gap = (now - last_t) * 1000.0
            poll_gaps.append(gap)
            last_t = now
            last_p = p
            last_progress_t = now
        if (now - last_progress_t) >= 2.0:
            stall_ge_2s = True
        if (len(idx_samples) == 0) or ((now - t0) >= len(idx_samples) * 0.2):
            samples = _movie_frame_index_samples()
            idx = _best_frame_index(samples)
            idx_samples.append(idx)
            if len(cache_samples) < 8 or (now - t0) > seconds * 0.9:
                cache_samples.append(_path_cache_snapshot())

    p1 = _product_presents()
    f1 = _frame_count()
    dt = time.monotonic() - t0
    product_fps = (p1 - p0) / dt if dt > 0 and p0 >= 0 and p1 >= p0 else -1.0
    host_gaps = _take_host_gaps()
    source = "host_present"
    gaps = host_gaps
    if len(gaps) < 2:
        source = "probe_poll_fallback"
        gaps = poll_gaps
    p99 = _p99(gaps)
    max_gap = float(max(gaps)) if gaps else 0.0
    valid_idx = [int(x) for x in idx_samples if x is not None]
    uniq = sorted(set(valid_idx))
    frame_advance = (max(valid_idx) - min(valid_idx)) if len(valid_idx) >= 2 else 0
    cache_end = cache_samples[-1] if cache_samples else _path_cache_snapshot()
    cache_start = cache_samples[0] if cache_samples else cache_end
    n0 = cache_start.get("nframes")
    n1 = cache_end.get("nframes")
    try:
        n0i = int(n0) if n0 is not None else None
        n1i = int(n1) if n1 is not None else None
        cache_growth = (n1i - n0i) if (n0i is not None and n1i is not None) else None
    except Exception:  # noqa: BLE001
        cache_growth = None

    return {
        "label": label,
        "seconds": round(dt, 3),
        "product_fps": round(product_fps, 2),
        "product_presents": (p1 - p0) if p0 >= 0 else -1,
        "host_frames": (f1 - f0) if f0 >= 0 else -1,
        "host_frames_minus_product": ((f1 - f0) - (p1 - p0)) if (f0 >= 0 and p0 >= 0) else None,
        "max_inter_present_ms": round(max_gap, 3),
        "p99_inter_present_ms": round(p99, 3) if p99 is not None else None,
        "gap_count": len(gaps),
        "gap_source": source,
        "host_gap_count": len(host_gaps),
        "poll_gap_count": len(poll_gaps),
        "stall_ge_2s": bool(stall_ge_2s),
        "frame_index_samples": valid_idx[:80],
        "frame_index_unique": len(uniq),
        "frame_index_min": min(valid_idx) if valid_idx else None,
        "frame_index_max": max(valid_idx) if valid_idx else None,
        "frame_index_advance": int(frame_advance),
        "frame_index_advances": bool(frame_advance > 0),
        "path_cache_start": cache_start,
        "path_cache_end": cache_end,
        "path_cache_growth": cache_growth,
        "path_cache_inflight_end": cache_end.get("inflight"),
        "path_cache_ready_full_end": cache_end.get("ready_full"),
        "path_cache_nframes_end": cache_end.get("nframes"),
    }


def _rank_h(early, late):
    hints = []
    early.get("p99_inter_present_ms")
    l_p99 = late.get("p99_inter_present_ms")
    growth = late.get("path_cache_growth")
    if growth is None:
        try:
            n_e = int((early.get("path_cache_end") or {}).get("nframes") or 0)
            n_l = int((late.get("path_cache_end") or {}).get("nframes") or 0)
            growth = n_l - n_e
        except Exception:  # noqa: BLE001
            growth = None
    inflight_late = bool(late.get("path_cache_inflight_end"))
    n_late = late.get("path_cache_nframes_end")
    try:
        n_late_i = int(n_late) if n_late is not None else None
    except Exception:  # noqa: BLE001
        n_late_i = None
    host_over_prod = late.get("host_frames_minus_product")
    advances = bool(late.get("frame_index_advances"))
    stall = bool(late.get("stall_ge_2s") or early.get("stall_ge_2s"))
    late_bad = (l_p99 is not None and l_p99 > 66.0) or (not advances) or stall

    if growth is not None and growth > 0 and late_bad:
        sev = "high" if (n_late_i is not None and n_late_i > 120) else "med"
        hints.append({
            "id": "H1",
            "title": "path-cache full-RGBA growth past warm prefix",
            "severity_hint": sev,
            "evidence": f"path_cache_growth={growth} nframes_late={n_late} p99_late={l_p99}",
        })
    else:
        hints.append({
            "id": "H1",
            "title": "path-cache full-RGBA growth past warm prefix",
            "severity_hint": "low" if not late_bad else "med",
            "evidence": f"growth={growth} n_late={n_late} late_bad={late_bad}",
        })

    if inflight_late and late_bad:
        hints.append({
            "id": "H2",
            "title": "background ffmpeg continue-fill after warm",
            "severity_hint": "high",
            "evidence": f"inflight_late={inflight_late} nframes={n_late} p99_late={l_p99}",
        })
    else:
        hints.append({
            "id": "H2",
            "title": "background ffmpeg continue-fill after warm",
            "severity_hint": "med" if inflight_late else "low",
            "evidence": f"inflight_late={inflight_late} growth={growth}",
        })

    if host_over_prod is not None and host_over_prod > max(30, (late.get("product_presents") or 0) * 0.5):
        hints.append({
            "id": "H3",
            "title": "write_texture / present thrash (host_frames > product)",
            "severity_hint": "high" if late_bad else "med",
            "evidence": "host_minus_product={} host_frames={} product={}".format(
                host_over_prod, late.get("host_frames"), late.get("product_presents")),
        })
    else:
        hints.append({
            "id": "H3",
            "title": "write_texture / present thrash (host_frames > product)",
            "severity_hint": "low",
            "evidence": f"host_minus_product={host_over_prod}",
        })

    ratio = None
    try:
        hf = float(late.get("host_frames") or 0)
        pp = float(late.get("product_presents") or 0)
        if pp > 0:
            ratio = hf / pp
    except Exception:  # noqa: BLE001
        ratio = None
    if ratio is not None and ratio > 1.8 and late_bad:
        hints.append({
            "id": "H4",
            "title": "busy-wake host_frames residual",
            "severity_hint": "med",
            "evidence": f"host/product ratio={ratio:.2f}",
        })
    else:
        hints.append({
            "id": "H4",
            "title": "busy-wake host_frames residual",
            "severity_hint": "low",
            "evidence": f"host/product ratio={ratio}",
        })

    if (l_p99 is not None and l_p99 > 66.0) or stall or (not advances):
        sev = "high" if (stall or not advances or (l_p99 is not None and l_p99 > 120)) else "med"
        hints.append({
            "id": "H5",
            "title": "present starvation / large inter-present gaps",
            "severity_hint": sev,
            "evidence": "p99_late={} stall={} advances={} max_gap={}".format(
                l_p99, stall, advances, late.get("max_inter_present_ms")),
        })
    else:
        hints.append({
            "id": "H5",
            "title": "present starvation / large inter-present gaps",
            "severity_hint": "low",
            "evidence": f"p99_late={l_p99} advances={advances}",
        })

    order = {"high": 0, "med": 1, "low": 2}
    hints.sort(key=lambda h: (order.get(h.get("severity_hint"), 9), h.get("id")))
    return hints


def probe():
    import renpy

    out_txt = _base() / "host" / "target" / "gate-hmc_menu_video_soak_probe.txt"
    out_json = _base() / "host" / "target" / "gate-hmc_menu_video_soak_probe.json"
    lines = []
    report = {
        "gate": "hmc_menu_video_soak_probe",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "windows": {},
        "ac_m_soak": {},
        "ac_z": {},
        "h_rank_hints": [],
    }

    deadline = time.time() + 90.0
    while time.time() < deadline:
        try:
            if bool(getattr(renpy.store, "main_menu", False)):
                break
        except Exception:  # noqa: BLE001, S110
            pass
        time.sleep(0.2)

    mm = False
    try:
        mm = bool(getattr(renpy.store, "main_menu", False))
    except Exception:  # noqa: BLE001
        mm = False
    report["main_menu"] = mm
    lines.append(f"main_menu={mm}")
    _log(f"main_menu={mm} frames={_frame_count()} product={_product_presents()}")

    time.sleep(0.3)
    report["path_cache_pre"] = _path_cache_snapshot()
    lines.append("path_cache_pre={}".format(report["path_cache_pre"]))

    early = _sample_window("early_0_3s", 3.0)
    report["windows"]["early_0_3s"] = early
    lines.append(f"early={early}")
    _log("early p99={} advances={} nframes={} inflight={}".format(
        early.get("p99_inter_present_ms"),
        early.get("frame_index_advances"),
        early.get("path_cache_nframes_end"),
        early.get("path_cache_inflight_end"),
    ))

    late = _sample_window("late_3_15s", 12.0)
    report["windows"]["late_3_15s"] = late
    lines.append(f"late={late}")
    _log("late p99={} advances={} nframes={} inflight={} host_minus_product={}".format(
        late.get("p99_inter_present_ms"),
        late.get("frame_index_advances"),
        late.get("path_cache_nframes_end"),
        late.get("path_cache_inflight_end"),
        late.get("host_frames_minus_product"),
    ))

    l_p99 = late.get("p99_inter_present_ms")
    advances = bool(late.get("frame_index_advances"))
    stall = bool(late.get("stall_ge_2s") or early.get("stall_ge_2s"))
    report["ac_m_soak"] = {
        "warm_window_s": "3..15",
        "p99_inter_present_ms": l_p99,
        "p99_le_66": (l_p99 is not None and float(l_p99) <= 66.0),
        "frame_index_advances": advances,
        "frame_index_advance": late.get("frame_index_advance"),
        "pass": bool(l_p99 is not None and float(l_p99) <= 66.0 and advances and (not stall)),
    }
    report["ac_z"] = {
        "stall_ge_2s": stall,
        "hang": False,
        "crash": False,
    }
    lines.append("ac_m_soak={}".format(report["ac_m_soak"]))
    lines.append("ac_z={}".format(report["ac_z"]))

    e_p99 = early.get("p99_inter_present_ms")
    report["early_p99_inter_present_ms"] = e_p99
    report["late_p99_inter_present_ms"] = l_p99
    report["p99_delta_late_minus_early"] = (
        round(float(l_p99) - float(e_p99), 3)
        if (l_p99 is not None and e_p99 is not None)
        else None
    )

    hints = _rank_h(early, late)
    report["h_rank_hints"] = hints
    for i, h in enumerate(hints, 1):
        lines.append("H%d_rank=%s severity=%s evidence=%s" % (i, h["id"], h["severity_hint"], h["evidence"]))  # noqa: UP031
        _log("H rank {} {} {}".format(h["id"], h["severity_hint"], h["evidence"]))

    report["ok"] = True
    report["measured"] = bool(mm)
    report["pass"] = bool(report["ac_m_soak"].get("pass") and not stall)
    lines.append("ok={}".format(report["ok"]))
    lines.append("measured={}".format(report["measured"]))
    lines.append("pass={}".format(report["pass"]))

    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    out_json.write_text(json.dumps(report, indent=2, default=str) + chr(10), encoding="utf-8")
    _log("wrote {} pass={}".format(out_json, report["pass"]))
    time.sleep(0.3)
    _quit()


def main():
    open("/tmp/hmc_menu_video_soak_probe.log", "w").write("start" + chr(10))  # noqa: SIM115
    base = _base()
    game = os.environ.get("RENPY_HOST_GAME") or str(base / "host" / "playtests" / "HuangmeiC")
    os.environ["RENPY_HOST_BASE"] = str(base)
    os.environ["RENPY_HOST_BUILD"] = "1"
    os.environ["RENPY_HOST_GAME"] = game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    os.environ.setdefault("RENPY_HOST_PHASE0_SIGNALS", "1")
    _clear_falsey("RENPY_SKIP_MAIN_MENU")
    _clear_falsey("RENPY_SKIP_SPLASHSCREEN")
    for path in (str(base / "host" / "python" / "gates"), str(base / "host" / "python")):
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        _stubs()
        import bootstrap as boot

        for name, call in (
            ("import_renpy", boot.stage_import_renpy),
            ("import_all", boot.stage_import_all),
            ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
        ):
            good, missing, error, _extra = call()
            _log(f"stage {name} good={good} missing={missing} error={error!r}")
            if not good:
                _log(f"bootstrap fail {name}")
                _quit()
                return
        import renpy

        renpy.host_build = True
        try:
            import renpy_main_host
            renpy_main_host.install(renpy)
        except Exception as e:  # noqa: BLE001
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
                except Exception:  # noqa: BLE001, S110
                    pass
            renpy.game.args = renpy.arguments.bootstrap()
        except Exception as e:  # noqa: BLE001
            _log(f"args fail {e}")
            _quit()
            return
        threading.Thread(target=probe, daemon=True).start()
        try:
            _log("entering renpy.main.main()")
            renpy.main.main()
        except SystemExit:
            pass
        except Exception as e:  # noqa: BLE001
            _log(f"main exc {e}")
            _log(traceback.format_exc())
        finally:
            _quit()
    except Exception as e:  # noqa: BLE001
        _log(f"main outer exc {e}")
        _log(traceback.format_exc())
        _quit()


if __name__ == "__main__":
    main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
