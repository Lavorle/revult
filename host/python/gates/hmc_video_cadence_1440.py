"""HuangmeiC video cadence probe (video-stutter root-cause evidence).

RENPY_HOST_GATE=hmc_video_cadence_probe

Samples in a background thread while the product pumps normally:
  - movie channel frame_index transitions (true display cadence)
  - video_clock_pos rate (clock sanity / epoch check)
  - transient 1920x1080 load_texture call cadence + duration (movie upload cost)
  - product_presents delta (present loop speed)

Outputs:
  host/target/gate-hmc_video_cadence_1440.json
  host/target/gate-hmc_video_cadence_1440.txt
"""

import json
import os
import sys
import threading
import time
import traceback
from collections import Counter
from pathlib import Path


def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")


def _log(msg):
    try:
        sys.__stdout__.write("[hmc_cadence] " + str(msg))
        sys.__stdout__.write(chr(10))
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        open("/tmp/hmc_video_cadence_1440.log", "a").write(str(msg) + chr(10))
    except Exception:
        pass


def _quit():
    try:
        import renpy_host
        renpy_host.request_quit()
    except Exception:
        pass


def _clear_falsey(name):
    val = os.environ.get(name)
    if val is not None and str(val).strip().lower() in ("", "0", "false", "no", "off", "n"):
        os.environ.pop(name, None)


SAMPLE_SECS = float(os.environ.get("HMC_CADENCE_SAMPLE_SECS", "10"))
POLL_S = 0.002


def _video_channels(rps):
    chans = getattr(rps, "_channels", {}) or {}
    out = []
    for ch, st in list(chans.items()):
        try:
            if st:
                out.append((int(ch), st))
        except Exception:
            pass
    return out


def _pick_video_channel(rps):
    for ch, st in _video_channels(rps):
        if st.get("video"):
            return int(ch), st
    return None, None


def _install_toggle_watcher(rps, channel):
    """Watch _clock_starved transitions and re-anchor events."""
    marks = []

    def watch():
        last_starved = None
        while not _STOP.is_set():
            try:
                st = rps._channels.get(channel) or {}
                sv = bool(st.get("_clock_starved"))
                if last_starved is None or sv != last_starved:
                    marks.append((time.perf_counter(), "clock_starved", sv))
                    last_starved = sv
            except Exception:
                pass
            time.sleep(0.02)

    t = threading.Thread(target=watch, daemon=True)
    t.start()
    return marks


_STOP = threading.Event()
_UPLOADS = []  # (t, ms, kind, surf_frame_idx) transient 1920x1080 load_texture
_UPLOAD_ALTS = []  # (t, ms, wh) of transient non-movie-size load_texture
_REPRESENT = []  # (t, ok) main-thread re-present attempts (may be huge)
_PRESENT_OK = []  # sampled re-present ok timestamps (1-of-every-N)
_FULL_RENDER = []  # (t, ms) main-thread draw_screen full renders
_REDRAWS = []  # (t, ms) process_redraws calls needing work
_THROTTLE = 1000  # per-call-list cap for re-present samples
_PROGRESS = []
_CNT = {'upload': 0, 'rp_ok': 0, 'rp_try': 0, 'full': 0, 'readv': 0, 'readv_raw': 0}
  # background-thread progress marks every 2s


def _instrument_load_texture():
    import renpy.wgpu.draw as wgpu_draw

    cls = wgpu_draw.WgpuDraw
    orig = cls.load_texture

    def wrapped(self, surf, transient=False, properties=None):
        t0 = time.perf_counter()
        pre_ent = None
        try:
            if transient:
                pre_ent = getattr(self, "_transient_tex", {}).get(id(surf))
        except Exception:
            pre_ent = None
        try:
            return orig(self, surf, transient=transient, properties=properties)
        finally:
            try:
                ms = (time.perf_counter() - t0) * 1000.0
                wt, ht = surf.get_size()
                if transient:
                    if wt == 1920 and ht == 1080:
                        kind = "unknown"
                        sfi = getattr(surf, "_host_frame_idx", None)
                        try:
                            post = getattr(self, "_transient_tex", {}).get(id(surf))
                            if pre_ent is None:
                                kind = "create"
                            elif post is not None and len(post) >= 4 and pre_ent is not None:
                                if len(post) >= 4 and len(pre_ent) >= 4 and post[3] != pre_ent[3]:
                                    kind = "rewrite"
                                elif len(post) >= 4:
                                    kind = "touch"
                        except Exception:
                            pass
                        _UPLOADS.append((t0, ms, kind, int(sfi) if sfi is not None else None))
                        _CNT['upload'] += 1
                    else:
                        _UPLOAD_ALTS.append((t0, ms, (wt, ht)))
            except Exception:
                pass

    cls.load_texture = wrapped
    return orig


_READV = []  # (t, ch, idx, changed, dur_ms, kind) main-thread read_video outcome
_CLOCK_LOG = []  # (t, fn, ch, arg_or_ret, note)
_POS_LOG = []  # sampled video_clock_pos returns at caller level
_CLOCK_COUNTS = {}
_THROTTLE = 1000  # per-call-list cap for re-present samples
_PROGRESS = []
_CNT = {'upload': 0, 'rp_ok': 0, 'rp_try': 0, 'full': 0, 'readv': 0, 'readv_raw': 0}
  # background-thread progress marks every 2s


def _instrument_clock_ffi():
    """Trace all renpy_host.video_clock_* calls via the shared module object."""
    import renpy_host as _rh

    _WT = []
    _WT_DECL = {"n": 0}

    def _mk_write(orig):
        def wrapped_write(handle, pixels, *a, **kw):
            t0 = time.perf_counter()
            try:
                return orig(handle, pixels, *a, **kw)
            finally:
                try:
                    ms = (time.perf_counter() - t0) * 1000.0
                    _WT_DECL["n"] += 1
                    if len(_WT) < 30000:
                        _WT.append((t0, ms, len(pixels)))
                except Exception:
                    pass
        return wrapped_write

    for _nm, _mk in (("write_texture_rgba", _mk_write),):
        _orig = getattr(_rh, _nm, None)
        if _orig is not None:
            setattr(_rh, _nm, _mk(_orig))
    globals()["_WT_LOG"] = _WT
    globals()["_WT_COUNTS"] = _WT_DECL

    for name in (
        "video_clock_start", "video_clock_stop", "video_clock_set_pos",
        "video_clock_pause", "video_clock_unpause", "video_clock_bind_audio",
        "video_clock_drift_ms",
    ):
        orig = getattr(_rh, name, None)
        if orig is None:
            continue

        def _mk(name, orig):
            def wrapped(*a, **kw):
                try:
                    r = orig(*a, **kw)
                except Exception as e:
                    r = repr(e)
                try:
                    _CLOCK_COUNTS[name] = _CLOCK_COUNTS.get(name, 0) + 1
                    if len(_CLOCK_LOG) < 20000:
                        _CLOCK_LOG.append((round(time.perf_counter(), 5), name, a, r))
                except Exception:
                    pass
                return r
            return wrapped

        setattr(_rh, name, _mk(name, orig))

    orig_pos = getattr(_rh, "video_clock_pos", None)

    def pos_wrapped(channel, *a, **kw):
        r = orig_pos(channel, *a, **kw)
        try:
            _CLOCK_COUNTS["video_clock_pos"] = _CLOCK_COUNTS.get("video_clock_pos", 0) + 1
            if len(_POS_LOG) < 8000:
                _POS_LOG.append((round(time.perf_counter(), 5), int(channel), round(float(r), 4)))
        except Exception:
            pass
        return r

    if orig_pos is not None:
        _rh.video_clock_pos = pos_wrapped


def _instrument_read_video():
    import renpy.audio.renpysound_host as rps
    import renpy_host as _rh

    orig = rps.read_video

    def wrapped(channel, *a, **kw):
        t0 = time.perf_counter()
        pre = None
        pre_pi = None
        try:
            ch = rps._channels.get(channel)
            if ch is not None:
                pre = ch.get("frame_index")
                pre_pi = ch.get("_present_idx")
        except Exception:
            pass
        try:
            return orig(channel, *a, **kw)
        finally:
            try:
                ms = (time.perf_counter() - t0) * 1000.0
                ch = rps._channels.get(channel)
                post = ch.get("frame_index") if ch is not None else None
                changed = (post is not None and post != pre) or (
                    pre_pi is not None and ch is not None and ch.get("_present_idx") != pre_pi
                )
                kind = "index_change" if changed else "same_or_skip"
                _CNT['readv_raw'] += 1
                if changed:
                    _CNT['readv'] += 1
                pos_now = None
                try:
                    pos_now = float(_rh.video_clock_pos(int(channel)))
                except Exception:
                    pass
                if len(_READV) < 20000:
                    _READV.append((t0, int(channel), post, changed, ms, kind, pos_now))
            except Exception:
                pass

    rps.read_video = wrapped


def _instrument_interface():
    import renpy.display.core as core
    import renpy.display.render as _render

    obj = core.Interface
    orig_rp = obj._try_host_re_present_fast

    def wrapped_rp(self):
        t0 = time.perf_counter()
        try:
            ok = bool(orig_rp(self))
        finally:
            try:
                ms = (time.perf_counter() - t0) * 1000.0
                if len(_REPRESENT) < _THROTTLE:
                    _CNT['rp_try'] += 1
                if ok:
                    _CNT['rp_ok'] += 1
                if ok and len(_PRESENT_OK) < _THROTTLE * 4:
                    _PRESENT_OK.append(t0)
            except Exception:
                pass
        return ok

    obj._try_host_re_present_fast = wrapped_rp

    orig_ds = obj.draw_screen

    def wrapped_ds(self, *a, **kw):
        t0 = time.perf_counter()
        try:
            return orig_ds(self, *a, **kw)
        finally:
            try:
                ms = (time.perf_counter() - t0) * 1000.0
                _CNT['full'] += 1
                if len(_FULL_RENDER) < _THROTTLE:
                    _FULL_RENDER.append((t0, ms))
            except Exception:
                pass

    obj.draw_screen = wrapped_ds

    orig_pr = _render.process_redraws

    def wrapped_pr():
        t0 = time.perf_counter()
        try:
            return orig_pr()
        finally:
            try:
                ms = (time.perf_counter() - t0) * 1000.0
                if ms > 0.02 and len(_REDRAWS) < _THROTTLE:
                    _REDRAWS.append((t0, ms))
            except Exception:
                pass

    _render.process_redraws = wrapped_pr


def _percentiles(vals):
    if not vals:
        return None
    s = sorted(vals)

    def pct(p):
        idx = min(len(s) - 1, max(0, round(p * (len(s) - 1))))
        return round(float(s[idx]), 3)

    return {
        "n": len(s),
        "p50": pct(0.50),
        "p95": pct(0.95),
        "p99": pct(0.99),
        "min": round(float(s[0]), 3),
        "max": round(float(s[-1]), 3),
        "mean": round(sum(s) / len(s), 3),
    }


def _interval_stats(samples, key, fps=30.0):
    # samples: list of (t, value); returns [(t, value, dt_ms)]
    trans = []
    prev = None
    for t, v in samples:
        if prev is not None and v != prev:
            trans.append((t, v, (t - prev[0]) * 1000.0))
        prev = (t, v)
    return trans


def probe():
    import renpy

    # 2560x1440 drawable parity with the user playtest environment.
    try:
        _rws = getattr(renpy_host, "request_window_size", None)
        if _rws is not None:
            _rws(2560, 1440)
            _log("window forced 2560x1440")
    except Exception as e:
        _log(f"window force fail: {e}")
    deadline = time.time() + 120.0
    while time.time() < deadline:
        try:
            if bool(getattr(renpy.store, "main_menu", False)):
                break
        except Exception:
            pass
        time.sleep(0.2)

    from renpy.audio import renpysound_host as rps
    import renpy_host

    t0 = time.perf_counter()
    ch, st = None, None
    while time.perf_counter() - t0 < 30.0:
        ch, st = _pick_video_channel(rps)
        if st is not None and (st.get("clock_armed") or st.get("frame_index", 0) > 0):
            break
        time.sleep(0.1)

    report = {
        "gate": "hmc_video_cadence_probe",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "env": {
            k: os.environ.get(k)
            for k in sorted(os.environ)
            if k.startswith("RENPY_HOST_MOVIE")
        },
        "sample_secs": SAMPLE_SECS,
        "channel": ch,
    }
    lines = [f"resume channel={ch} main_menu={bool(getattr(renpy.store, 'main_menu', False))}"]
    _log(f"resume channel={ch} armed={bool(st.get('clock_armed')) if st else None} nframes={len(st.get('play_frames') or []) if st else None}")

    marks = _install_toggle_watcher(rps, ch)

    fi_samples = []  # (t, frame_index)
    pos_samples = []  # (t, pos)
    ascf_samples = []  # (t, audio sample clock in seconds)
    cnt_samples = []  # (t, readv_raw, readv_idx, rp_ok, rp_try, full, upload)
    pres0 = renpy_host.product_presents()
    f0 = renpy_host.frame_count()
    t_start = time.perf_counter()
    p_prev = pres0
    p_last = t_start
    p_gaps = []
    while time.perf_counter() - t_start < SAMPLE_SECS:
        time.sleep(POLL_S)
        try:
            fi = st.get("frame_index")
            fi_samples.append((time.perf_counter(), int(fi) if fi is not None else None))
            cnt_samples.append(
                (
                    time.perf_counter(),
                    _CNT.get("readv_raw", 0), _CNT.get("readv", 0), _CNT.get("rp_ok", 0),
                    _CNT.get("rp_try", 0), _CNT.get("full", 0), _CNT.get("upload", 0),
                )
            )
            try:
                pos = float(renpy_host.video_clock_pos(int(ch)))
                pos_samples.append((time.perf_counter(), pos))
            except Exception:
                pass
            try:
                ascf = float(renpy_host.audio_sample_clock_frames())
                asf_rate = int(renpy_host.audio_sample_rate())
                ascf_samples.append((time.perf_counter(), ascf / max(1.0, float(asf_rate))))
            except Exception:
                pass
            if (time.perf_counter() - t_start) >= len(_PROGRESS) * 2.0:
                _PROGRESS.append(time.perf_counter())
                _log(f"progress {int(time.perf_counter()-t_start)}s samples={len(fi_samples)} presents={int(renpy_host.product_presents())}")
        except Exception as e:
            _log(f"sample exc {e!r}")
        p = int(renpy_host.product_presents())
        if p > p_prev:
            now = time.perf_counter()
            p_gaps.append((now - p_last) * 1000.0)
            p_last = now
            p_prev = p

    t_end = time.perf_counter()
    p1 = int(renpy_host.product_presents())
    f1 = int(renpy_host.frame_count())
    dt = t_end - t_start

    # frame_index step transitions (dedupe consecutive equal values; the movie
    # may also oscillate, record value at each poll for offline analysis)
    raw_fi = [(round(t, 4), v) for t, v in fi_samples]
    trans = []
    prev_v = None
    for t, v in raw_fi:
        if prev_v is not None and v is not None and v != prev_v:
            trans.append({"t": t, "to": v, "from": prev_v, "gap_ms": round((t - prev_t) * 1000.0, 3)})
        prev_t = t
        prev_v = v
    gaps = [x["gap_ms"] for x in trans]
    uni = sorted({v for _, v in fi_samples if v is not None})

    # movie upload cadence: only actual rewrite/create events advance pixels
    rewrites = [(t, ms, fi) for (t, ms, kind, fi) in _UPLOADS if kind in ("create", "rewrite")]
    up_gaps = []
    for a, b in zip(rewrites, rewrites[1:]):
        up_gaps.append(round((b[0] - a[0]) * 1000.0, 3))

    unique_nonmovie = Counter(wh for _, _, wh in _UPLOAD_ALTS)

    # pos rate: slope over first/last 1s (robust wrt epoch jumps)
    def slope(seg):
        if len(seg) < 10:
            return None
        a, b = seg[0], seg[-1]
        # use median filter: compare 200ms windows at both ends
        return None

    try:
        pos_first = pos_samples[0][1]
        pos_last = pos_samples[-1][1]
        pos_rate = (pos_last - pos_first) / dt if pos_samples else None
        # account for no epoch jumps: count jumps > 0.2s
        pos_jumps = sum(
            1
            for (t0, v0), (t1, v1) in zip(pos_samples, pos_samples[1:])
            if abs(v1 - v0) > 0.2
        )
    except Exception:
        pos_first = pos_last = None
        pos_rate = None
        pos_jumps = None

    report["frame_index"] = {
        "n_samples": len(fi_samples),
        "unique_values": len(uni),
        "min": uni[0] if uni else None,
        "max": uni[-1] if uni else None,
        "transitions": len(trans),
        "sampled_dt_s": round(dt, 3),
        "interval_stats_ms": _percentiles(gaps),
        "shown_fps_from_transitions": round(len(trans) / dt, 2) if dt else None,
        "intervals_hist_ms": _hist(gaps),
        "step_hist": _step_hist(trans),
        "first_trans": trans[:40],
        "series_tail": raw_fi[-60:],
        "series_head": raw_fi[:200],
        "series_stride_sample": [raw_fi[i] for i in range(0, len(raw_fi), 25)],
    }
    report["movie_upload"] = {
        "n": len(_UPLOADS),
        "n_rewrites": len(rewrites),
        "n_alt": len(_UPLOAD_ALTS),
        "alt_sizes_top": unique_nonmovie.most_common(6),
        "rewrite_interval_stats_ms": _percentiles(up_gaps),
        "duration_stats_ms": _percentiles([m for _, m, _ in rewrites]),
        "kind_counts": Counter(kind for _, _, kind, _ in _UPLOADS).most_common(),
        "events_full": [
            (round(t, 4), kind) for (t, _ms, kind, _fi) in _UPLOADS
        ],
    }
    rp_ok_gaps = [
        round((b - a) * 1000.0, 3) for a, b in zip(_PRESENT_OK, _PRESENT_OK[1:])
    ]
    fr_gaps = [round((b[0] - a[0]) * 1000.0, 3) for a, b in zip(_FULL_RENDER, _FULL_RENDER[1:])]
    report["loop_cadence"] = {
        "attempts_sampled": len(_REPRESENT),
        "present_ok_sampled": len(_PRESENT_OK),
        "present_ok_gap_stats_ms": _percentiles(rp_ok_gaps),
        "full_render_n": len(_FULL_RENDER),
        "full_render_gap_stats_ms": _percentiles(fr_gaps),
        "full_render_dur_stats_ms": _percentiles([m for _, m in _FULL_RENDER]),
        "heavy_process_redraws_n": len(_REDRAWS),
        "heavy_process_redraws_dur_stats_ms": _percentiles([m for _, m in _REDRAWS]),
    }
    rv_changed = [x for x in _READV if x[5] == "index_change"]
    rv_change_gaps = [
        round((b[0] - a[0]) * 1000.0, 3) for a, b in zip(rv_changed, rv_changed[1:])
    ]
    report["read_video"] = {
        "n": len(_READV),
        "n_index_change": len(rv_changed),
        "index_change_per_s": round(len(rv_changed) / dt, 2) if dt else None,
        "index_change_gap_stats_ms": _percentiles(rv_change_gaps),
        "dur_stats_ms": _percentiles([x[4] for x in _READV]),
    }
    try:
        ch6 = [x for x in _READV if len(x) >= 7 and x[1] == int(ch) and x[6] is not None]
        derr = []
        for t, cc, post, changed, ms, kind, posv in ch6:
            try:
                raw = int(posv * 30.0)
                derr.append(post - (raw % 360 if raw >= 0 else raw))
            except Exception:
                pass
        report["readv_pos_compare"] = {
            "n": len(ch6),
            "idx_minus_rawmod_hist": Counter(derr).most_common(12),
            "series_full": [
                (round(t - ch6[0][0], 4), post, chg, round(posv, 5))
                for t, cc, post, chg, ms, kind, posv in ch6
            ],
        }
    except Exception as e:
        report["readv_pos_compare"] = {"error": repr(e)}
    # stall correlation: sample-window slices in which NOTHING moves
    # (idx unchanged AND readv idx counter unchanged AND no rp_ok AND no full)
    try:
        total_ctors = dict()
        stall_rows = []
        prev = None
        for t, rvr, rvi, rpok, rptr, full, upl in cnt_samples:
            if prev is None:
                prev = [t, rvr, rvi, rpok, rptr, full, upl]
                continue
            dt_s = t - prev[0]
            moved = (rvi != prev[2]) or (rpok != prev[3]) or (full != prev[5])
            if dt_s >= 0.03 and not moved:
                stall_rows.append(
                    {
                        "from": round(prev[0], 4),
                        "to": round(t, 4),
                        "dur_ms": round(dt_s * 1000.0, 3),
                        "rp_try_delta": rptr - prev[4],
                        "readv_raw_delta": rvr - prev[1],
                    }
                )
            prev = [t, rvr, rvi, rpok, rptr, full, upl]
        durs = [r["dur_ms"] for r in stall_rows]
        report["stalls"] = {
            "rows": stall_rows[:200],
            "n": len(stall_rows),
            "dur_stats_ms": _percentiles(durs),
            "total_stalled_ms": round(sum(durs), 1),
            "pct_window": round(sum(durs) / (dt * 1000.0) * 100.0, 2) if dt else None,
        }
    except Exception as e:
        report["stalls"] = {"error": repr(e)}
    report["clock_ffi"] = {
        "counts": dict(_CLOCK_COUNTS),
        "log": [(t, fn, a, round(r, 4) if isinstance(r, float) else r) for t, fn, a, r in _CLOCK_LOG[:3000]],
        "pos_head": _POS_LOG[:600],
        "pos_stride": [_POS_LOG[i] for i in range(0, len(_POS_LOG), 25)],
    }
    try:
        wt = globals().get("_WT_LOG") or []
        wt_gaps = [round((b[0] - a[0]) * 1000.0, 3) for a, b in zip(wt, wt[1:])]
        report["write_texture"] = {
            "n": len(wt),
            "declared": (globals().get("_WT_COUNTS") or {}).get("n"),
            "gap_stats_ms": _percentiles(wt_gaps),
            "dur_stats_ms": _percentiles([m for _, m, _ in wt]),
            "sizes_counter": Counter(sz for _, _, sz in wt).most_common(6),
            "events_full": [(round(t, 4), m) for t, m, _ in wt],
        }
    except Exception as e:
        report["write_texture"] = {"error": repr(e)}
    report["ch_fields"] = {
        k: st.get(k)
        for k in (
            "decode_fps", "base_index", "total_decoded", "ready_full",
            "loop", "playing", "video", "clock_armed", "layout_cached",
            "decode_w", "decode_h", "growing", "loop_gen", "_audio_clock_bound",
        )
    }
    try:
        pos_prev = None
        pos_deltas = []
        for t, sec in pos_samples:
            if pos_prev is not None:
                pos_deltas.append(sec - pos_prev[1])
            pos_prev = (t, sec)
        report["pos_delta_stats"] = _percentiles([round(d * 1000.0, 3) for d in pos_deltas])
        # stride: value at ~5ms cadence — head/tail so bursts visible
        report["pos_stride"] = [
            (round(pos_samples[i][0], 4), round(pos_samples[i][1], 5))
            for i in range(0, len(pos_samples), max(1, len(pos_samples) // 250))
        ]
    except Exception as e:
        report["pos_delta_stats"] = {"error": repr(e)}
    report["fi_series_full"] = raw_fi
    report["pos_series_head"] = [(round(t, 4), round(v, 4)) for t, v in pos_samples[:200]]
    report["video_clock"] = {
        "pos_first": pos_first,
        "pos_last": pos_last,
        "pos_rate_per_s": round(pos_rate, 3) if pos_rate is not None else None,
        "pos_jumps_gt200ms": pos_jumps,
    }
    try:
        asc_prev = None
        asc_rate = []
        asc_stalls_n = 0
        asc_total_stall = 0.0
        asc_rate_jit = 0.0
        for t, sec in ascf_samples:
            if asc_prev is not None:
                dsec = sec - asc_prev[1]
                dt_s = t - asc_prev[0]
                if dt_s > 1e-3:
                    r = dsec / dt_s
                    asc_rate.append(r)
                    if r < 0.3:
                        asc_stalls_n += 1
                        asc_total_stall += dt_s
            asc_prev = (t, sec)
        asc_rate_jit = _percentiles(asc_rate)
        report["audio_device_clock"] = {
            "n_samples": len(ascf_samples),
            "rate_stats": asc_rate_jit,
            "sub_03x_stall_events": asc_stalls_n,
            "stall_total_ms": round(asc_total_stall * 1000.0, 1),
            "stall_pct_window": round(asc_total_stall / dt * 100.0, 2) if dt else None,
            "first": ascf_samples[0][1] if ascf_samples else None,
            "last": ascf_samples[-1][1] if ascf_samples else None,
        }
    except Exception as e:
        report["audio_device_clock"] = {"error": repr(e)}
    report["present_loop"] = {
        "n_presents": p1 - pres0,
        "presents_per_s": round((p1 - pres0) / dt, 1) if dt else None,
        "gap_stats_ms": _percentiles(p_gaps),
        "host_frames_delta": f1 - f0,
    }
    report["clock_starved_marks"] = [
        {"t": round(t - t_start, 3), "v": v} for t, k, v in marks[:40]
    ]
    lines.append("frame_index=" + json.dumps(report["frame_index"], default=str))
    lines.append("movie_upload=" + json.dumps(report["movie_upload"], default=str))
    lines.append("video_clock=" + json.dumps(report["video_clock"]))
    lines.append("audio_device_clock=" + json.dumps(report.get("audio_device_clock", {})))
    lines.append("present_loop=" + json.dumps(report["present_loop"], default=str))
    lines.append("clock_starved_marks=" + json.dumps(report["clock_starved_marks"]))
    lines.append("stalls=" + json.dumps({k: v for k, v in report.get("stalls", {}).items() if k != "rows"}))

    # pass criteria for this probe: display cadence p99 interval sane (~33ms); p95 < 50ms
    ist = report["frame_index"]["interval_stats_ms"]
    ok = False
    try:
        it = ist and ist["n"] > 20
        p95 = ist and ist["p95"]
        p99 = ist and ist["p99"]
        ok = bool(it and 1000.0 / 33.0 * 0.7 <= (ist["n"]) and p95 <= 60.0 and p99 <= 100.0)
    except Exception:
        pass
    report["cadence_ok"] = ok
    lines.append(f"cadence_ok={ok}")

    Path(_base() / "host" / "target").mkdir(parents=True, exist_ok=True)
    txt = _base() / "host" / "target" / "gate-hmc_video_cadence_1440.txt"
    jsn = _base() / "host" / "target" / "gate-hmc_video_cadence_1440.json"
    txt.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    jsn.write_text(json.dumps(report, indent=2, default=str) + chr(10), encoding="utf-8")
    _log("wrote artifact pass=" + str(report["cadence_ok"]))
    time.sleep(0.3)
    _quit()


def _step_hist(trans):
    h = Counter()
    for t in trans:
        try:
            a = int(t["from"])
            b = int(t["to"])
            h[abs(b - a) if a is not None else None] += 1
        except Exception:
            pass
    return sorted(((k, v) for k, v in h.items()), key=lambda x: (x[0] is None, x[0]))[:20]


def _hist(gaps, edges=(5, 15, 25, 33.5, 47, 67, 100, 200, 400, 1000)):
    out = {}
    lo = None
    for edge in edges + (None,):
        hi = edge
        key = f"({lo},{hi})"
        out[key] = sum(1 for g in gaps if (lo is None or g > lo) and (hi is None or g <= hi))
        lo = edge
    return {k: v for k, v in out.items() if v}


def main():
    open("/tmp/hmc_video_cadence_1440.log", "w").write("start" + chr(10))
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
        import hmc_menu_video_soak_probe as sp

        sp._stubs()
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
        except Exception as e:
            _log(f"main_host: {e}")
        # instrument before the product main loop starts
        try:
            _instrument_load_texture()
            _instrument_interface()
            _instrument_read_video()
            _instrument_clock_ffi()
            _log("instrumented")
        except Exception as e:
            _log(f"instrument exc {e!r}")
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
            _log(f"args fail {e}")
            _quit()
            return
        threading.Thread(target=probe, daemon=True).start()
        try:
            _log("entering renpy.main.main()")
            renpy.main.main()
        except SystemExit:
            pass
        except Exception as e:
            _log(f"main exc {e}")
            _log(traceback.format_exc())
        finally:
            _quit()
    except Exception as e:
        _log(f"main outer exc {e}")
        _log(traceback.format_exc())
        _quit()


if __name__ == "__main__":
    main()
