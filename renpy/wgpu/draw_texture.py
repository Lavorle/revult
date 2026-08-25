"""draw_texture — texture/cache mixin extracted from draw.py."""
from __future__ import annotations

import os
import time as _time

from .draw_debug import (
    _UI_TRACE_LOGGED,
    _host_draw_fail,
    _phase0_due_write,
    _phase0_log,
    _phase0_signals_enabled,
    _ui_trace_once,
)
from .host_texture import HostTexture, _surf_fingerprint


class TextureMixin:
    texture_cache: dict
    _transient_tex: dict
    _handle_pixels: dict
    _handle_pixels_cap: int
    _handle_remap: dict
    _rtt_free: dict
    _rtt_prev_frame: list
    _rtt_curr_frame: list
    _mesh_cache: dict
    _mesh_deferred_destroy: list
    _quad_mesh: object

    def _stash_handle_pixels(self, handle, w, h, pixels, *, transient=False):
        try:
            handle = int(handle or 0)
        except Exception:
            return
        if handle <= 0:
            return
        try:
            w, h = int(w), int(h)
        except Exception:
            return
        if w <= 0 or h <= 0:
            return
        if transient and (w * h) >= (1280 * 720):
            return
        if (w * h) <= 1:
            return
        need = w * h * 4
        if not isinstance(pixels, (bytes, bytearray)) or len(pixels) < need:
            return
        store = getattr(self, "_handle_pixels", None)
        if store is None:
            self._handle_pixels = {}
            store = self._handle_pixels
        cap = int(getattr(self, "_handle_pixels_cap", 2048) or 2048)
        if len(store) >= cap and handle not in store:
            self._evict_handle_pixels(store, cap)
        store[handle] = (w, h, bytes(pixels[:need]))

    def _evict_handle_pixels(self, store, cap):
        if not store:
            return
        drop_n = max(1, int(cap) // 8)
        pin_px = 1920 * 1080  # keep panels ≤ full HD; drop > HD first
        chrome_px = 256 * 256  # always try to keep tiny Solid / icon chrome
        def _area(ent):
            try:
                return int(ent[0]) * int(ent[1])
            except Exception:
                return 0
        items = list(store.items())
        oversize = [(k, _area(v)) for k, v in items if _area(v) > pin_px]
        oversize.sort(key=lambda kv: kv[1], reverse=True)
        dropped = 0
        for k, _a in oversize:
            if dropped >= drop_n:
                break
            store.pop(k, None)
            dropped += 1
        if dropped >= drop_n:
            return
        mid = [(k, _area(v)) for k, v in store.items() if _area(v) > chrome_px]
        mid.sort(key=lambda kv: kv[1], reverse=True)
        for k, _a in mid:
            if dropped >= drop_n:
                break
            store.pop(k, None)
            dropped += 1
        if dropped >= drop_n:
            return
        for k in list(store.keys()):
            if dropped >= drop_n:
                break
            if k in store:
                store.pop(k, None)
                dropped += 1

    def _forget_handle_pixels(self, handle):
        try:
            handle = int(handle or 0)
        except Exception:
            return
        if handle <= 0:
            return
        store = getattr(self, "_handle_pixels", None)
        if store is not None:
            store.pop(handle, None)
        remap = getattr(self, "_handle_remap", None)
        if remap is not None:
            remap.pop(handle, None)
            dead = [k for k, v in remap.items() if int(v) == handle]
            for k in dead:
                remap.pop(k, None)

    def _ensure_host_texture_alive(self, ht):
        if ht is None:
            return None
        try:
            old = int(getattr(ht, "handle", 0) or 0)
        except Exception:
            return ht
        if old <= 0:
            return ht
        remap = getattr(self, "_handle_remap", None)
        if remap is None:
            self._handle_remap = {}
            remap = self._handle_remap
        seen = set()
        cur = old
        while cur in remap and cur not in seen:
            seen.add(cur)
            try:
                cur = int(remap[cur])
            except Exception:
                break
        if cur != old and cur > 0:
            try:
                import renpy_host  # type: ignore
                alive_mapped = True
                if hasattr(renpy_host, "texture_alive"):
                    alive_mapped = bool(renpy_host.texture_alive(int(cur)))
                if alive_mapped:
                    ht.handle = int(cur)
                    ht.texture = int(cur)
                    return ht
            except Exception:
                pass
        try:
            import renpy_host  # type: ignore
            alive = True
            if hasattr(renpy_host, "texture_alive"):
                alive = bool(renpy_host.texture_alive(int(cur if cur > 0 else old)))
            if alive:
                if hasattr(renpy_host, "touch_texture"):
                    try:
                        renpy_host.touch_texture(int(ht.handle))
                    except Exception:
                        pass
                return ht
            store = getattr(self, "_handle_pixels", None) or {}
            ent = store.get(old) or store.get(cur)
            if ent is None and remap:
                for k, v in list(remap.items()):
                    try:
                        if int(v) in (old, cur) and k in store:
                            ent = store.get(k)
                            break
                    except Exception:
                        continue
            if ent is None:
                ent = self._recover_pixels_for_dead_handle(old, cur, ht)
            if ent is None:
                if (
                    os.environ.get("RENPY_HOST_UI_TRACE") == "1"
                    and "dead_present" not in _UI_TRACE_LOGGED
                ):
                    _ui_trace_once(
                        "dead_present",
                        f"handle={old} alive=0 size=({getattr(ht, 'w', '?')},"
                        f"{getattr(ht, 'h', '?')}) recover=no_pixels",
                    )
                return ht
            sw, sh, pixels = ent
            try:
                new_h = int(renpy_host.create_texture_rgba(int(sw), int(sh), pixels))
            except Exception as e:
                if (
                    os.environ.get("RENPY_HOST_UI_TRACE") == "1"
                    and "dead_present" not in _UI_TRACE_LOGGED
                ):
                    _ui_trace_once(
                        "dead_present",
                        f"handle={old} alive=0 recover=create_fail "
                        f"err={type(e).__name__}:{e}",
                    )
                return ht
            if new_h <= 0:
                return ht
            remap[old] = int(new_h)
            if cur != old:
                remap[cur] = int(new_h)
            store.pop(old, None)
            store.pop(cur, None)
            store[int(new_h)] = (int(sw), int(sh), pixels)
            try:
                for k, (fp, h, tw, th) in list(self.texture_cache.items()):
                    try:
                        if int(h) in (old, cur):
                            self.texture_cache[k] = (fp, int(new_h), tw, th)
                    except Exception:
                        continue
            except Exception:
                pass
            try:
                from renpy.display import im  # type: ignore
                cache = getattr(im, "cache", None)
                if cache is not None:
                    try:
                        _lock = getattr(cache, "lock", None)
                        it = list(getattr(cache, "cache", {}).values())
                    except Exception:
                        it = []
                    for ce in it:
                        tex = getattr(ce, "texture", None)
                        if isinstance(tex, HostTexture):
                            try:
                                if int(getattr(tex, "handle", 0) or 0) in (old, cur):
                                    tex.handle = int(new_h)
                                    tex.texture = int(new_h)
                            except Exception:
                                pass
                        elif isinstance(tex, int) and not isinstance(tex, bool):
                            try:
                                if int(tex) in (old, cur):
                                    ce.texture = HostTexture(
                                        int(new_h),
                                        int(getattr(ht, "width", sw) or sw),
                                        int(getattr(ht, "height", sh) or sh),
                                    )
                            except Exception:
                                pass
            except Exception:
                pass
            ht.handle = int(new_h)
            ht.texture = int(new_h)
            if hasattr(renpy_host, "touch_texture"):
                try:
                    renpy_host.touch_texture(int(new_h))
                except Exception:
                    pass
            if (
                os.environ.get("RENPY_HOST_UI_TRACE") == "1"
                and "dead_present" not in _UI_TRACE_LOGGED
            ):
                _ui_trace_once(
                    "dead_present",
                    f"handle={old} alive=0 size=({getattr(ht, 'w', '?')},"
                    f"{getattr(ht, 'h', '?')}) recover=ok new_handle={int(new_h)}",
                )
            return ht
        except Exception as e:
            if (
                os.environ.get("RENPY_HOST_UI_TRACE") == "1"
                and "dead_present" not in _UI_TRACE_LOGGED
            ):
                _ui_trace_once(
                    "dead_present",
                    f"texture_alive probe fail handle={old} err={type(e).__name__}:{e}",
                )
            return ht

    def _recover_pixels_for_dead_handle(self, old, cur, ht):
        try:
            from renpy.display import im  # type: ignore
        except Exception:
            return None
        cache = getattr(im, "cache", None)
        if cache is None:
            return None
        entries = None
        try:
            lock = getattr(cache, "lock", None)
            if lock is not None:
                with lock:
                    entries = list(getattr(cache, "cache", {}).values())
            else:
                entries = list(getattr(cache, "cache", {}).values())
        except Exception:
            try:
                entries = list(getattr(cache, "cache", {}).values())
            except Exception:
                return None
        want = set()
        try:
            want.add(int(old))
            want.add(int(cur))
        except Exception:
            pass
        for ce in entries:
            try:
                tex = getattr(ce, "texture", None)
                th = None
                if isinstance(tex, HostTexture):
                    th = int(getattr(tex, "handle", 0) or 0)
                elif isinstance(tex, int) and not isinstance(tex, bool):
                    th = int(tex)
                if th is None or th not in want:
                    continue
                surf = getattr(ce, "surf", None)
                if surf is None:
                    what = getattr(ce, "what", None)
                    if what is not None and hasattr(what, "load"):
                        try:
                            surf = what.load()
                        except Exception:
                            surf = None
                if surf is None:
                    continue
                try:
                    sw, sh = surf.get_size()
                    sw, sh = int(sw), int(sh)
                except Exception:
                    continue
                if sw <= 0 or sh <= 0:
                    continue
                try:
                    bounds = getattr(ce, "bounds", None)
                    if (
                        bounds is not None
                        and len(bounds) >= 4
                        and tuple(bounds) != (0, 0, sw, sh)
                        and hasattr(surf, "subsurface")
                    ):
                        sub = surf.subsurface(tuple(bounds[:4]))
                        if sub is not None:
                            surf = sub
                            sw, sh = surf.get_size()
                            sw, sh = int(sw), int(sh)
                except Exception:
                    pass
                pixels = getattr(surf, "_pixels", None)
                if pixels is None and hasattr(surf, "get_buffer"):
                    try:
                        pixels = bytes(surf.get_buffer())
                    except Exception:
                        pixels = None
                if not isinstance(pixels, (bytes, bytearray)):
                    continue
                need = sw * sh * 4
                if len(pixels) < need:
                    continue
                return (sw, sh, bytes(pixels[:need]))
            except Exception:
                continue
        return None

    def load_texture(self, surf, transient=False, properties=None):
        try:
            import renpy_host  # type: ignore
            w, h = surf.get_size()
            w, h = max(1, int(w)), max(1, int(h))
            need = w * h * 4
            key = id(surf)
            if transient:
                frame_idx = getattr(surf, "_host_frame_idx", None)
                tent = self._transient_tex.get(key)
                if (
                    frame_idx is not None
                    and tent is not None
                    and len(tent) >= 4
                ):
                    thandle, tw, th, tfp = tent[0], tent[1], tent[2], tent[3]
                    last_idx = tent[4] if len(tent) >= 5 else None
                    if (
                        tw == w
                        and th == h
                        and thandle
                        and last_idx is not None
                        and int(last_idx) == int(frame_idx)
                    ):
                        alive = True
                        if hasattr(renpy_host, "texture_alive"):
                            try:
                                alive = bool(renpy_host.texture_alive(int(thandle)))
                            except Exception:
                                alive = True
                        if alive:
                            if hasattr(renpy_host, "touch_texture"):
                                try:
                                    renpy_host.touch_texture(int(thandle))
                                except Exception:
                                    pass
                            return HostTexture(thandle, w, h)
            if hasattr(surf, "_pixels"):
                raw = surf._pixels
                pixels = bytes(raw) if not isinstance(raw, (bytes, bytearray)) else bytes(raw)
            else:
                try:
                    pixels = bytes(surf.get_buffer())
                except Exception:
                    pixels = b""
            empty_pad_input = False
            if len(pixels) < need:
                if len(pixels) == 0:
                    empty_pad_input = True
                    pixels = bytes(need)  # transparent black
                else:
                    pixels = pixels + bytes(need - len(pixels))
            elif len(pixels) > need:
                pixels = pixels[:need]
            self._load_empty_pad_input = empty_pad_input and (not transient)
            fp = _surf_fingerprint(pixels, w, h)
            if (
                os.environ.get("RENPY_HOST_UI_TRACE") == "1"
                and "empty_upload" not in _UI_TRACE_LOGGED
                and (w * h) > 4
            ):
                try:
                    any_a = False
                    for i in range(3, len(pixels), 4):
                        if pixels[i]:
                            any_a = True
                            break
                    if not any_a:
                        self._ui_trace_blank_pixels = (w, h, bool(empty_pad_input))
                except Exception:
                    pass
            def _alive(handle):
                if not handle:
                    return False
                if not hasattr(renpy_host, "texture_alive"):
                    return True
                try:
                    return bool(renpy_host.texture_alive(int(handle)))
                except Exception:
                    return True
            def _trace_empty_upload(handle, tag):
                blank = getattr(self, "_ui_trace_blank_pixels", None)
                if blank is None and int(handle or 0) != 0:
                    return
                if int(handle or 0) == 0:
                    _ui_trace_once(
                        "empty_upload",
                        f"class=c handle=0 tag={tag} size=({w},{h}) pixels_len={len(pixels)} need={need}",
                    )
                    return
                if blank is not None:
                    bw, bh, was_empty = blank
                    _ui_trace_once(
                        "empty_upload",
                        f"class=a blank_success handle={int(handle)} tag={tag} "
                        f"size=({bw},{bh}) all_alpha_zero=1 was_empty_pad={int(bool(was_empty))}",
                    )
                    try:
                        del self._ui_trace_blank_pixels
                    except Exception:
                        self._ui_trace_blank_pixels = None
            def _touch(handle):
                if not handle or not hasattr(renpy_host, "touch_texture"):
                    return
                try:
                    renpy_host.touch_texture(int(handle))
                except Exception:
                    pass
            if transient:
                if os.environ.get("RENPY_HOST_MOVIE_ASSERT", "").strip() in (
                    "1",
                    "true",
                    "yes",
                ):
                    logged = getattr(self, "_ac2_tex_sizes_logged", None)
                    if logged is None:
                        logged = set()
                        self._ac2_tex_sizes_logged = logged
                    present_1b = os.environ.get(
                        "RENPY_HOST_MOVIE_PRESENT", "1b"
                    ).strip().lower() not in ("1a", "layout", "s1")
                    half_bleed = (w, h) in ((960, 540), (1280, 720))
                    layout_ok = (w, h) == (1920, 1080)
                    if (layout_ok or half_bleed) and (w, h) not in logged:
                        logged.add((w, h))
                        try:
                            import sys
                            print(
                                f"AC2_HOSTTEX transient size=({w}, {h})"
                                f"{' present_1b=1' if (half_bleed and present_1b) else ''}",
                                file=sys.stderr,
                                flush=True,
                            )
                            if half_bleed and not present_1b:
                                print(
                                    f"AC2_WARN HostTexture size=({w}, {h}) "
                                    f"expected layout (1920, 1080) — half-bleed risk",
                                    file=sys.stderr,
                                    flush=True,
                                )
                        except Exception:
                            pass
                tent = self._transient_tex.get(key)
                if tent is not None:
                    thandle, tw, th, tfp = tent[0], tent[1], tent[2], tent[3]
                    if tw == w and th == h and thandle and _alive(thandle):
                        if tfp is not None and tfp == fp:
                            _touch(thandle)
                            return HostTexture(thandle, w, h)
                        wrote = False
                        wt_ms = 0.0
                        if hasattr(renpy_host, "write_texture_rgba"):
                            try:
                                _wt0 = _time.monotonic() if _phase0_signals_enabled() else None
                                renpy_host.write_texture_rgba(int(thandle), pixels)
                                if _wt0 is not None:
                                    wt_ms = (_time.monotonic() - _wt0) * 1000.0
                                wrote = True
                            except Exception:
                                wrote = False
                        if wrote:
                            if _phase0_due_write():
                                _phase0_log(
                                    f"write_texture_ms={wt_ms:.3f} size={w}x{h} "
                                    f"path=transient_rewrite handle={int(thandle)}"
                                )
                            frame_idx = getattr(surf, "_host_frame_idx", None)
                            self._transient_tex[key] = (
                                thandle, w, h, fp, frame_idx
                            )
                            self._stash_handle_pixels(
                                thandle, w, h, pixels, transient=True
                            )
                            return HostTexture(thandle, w, h)
                        try:
                            renpy_host.destroy_texture(thandle)
                        except Exception:
                            pass
                        self._forget_handle_pixels(thandle)
                    elif thandle:
                        try:
                            renpy_host.destroy_texture(thandle)
                        except Exception:
                            pass
                        self._forget_handle_pixels(thandle)
                    self._transient_tex.pop(key, None)
                handle = renpy_host.create_texture_rgba(w, h, pixels)
                frame_idx = getattr(surf, "_host_frame_idx", None)
                self._transient_tex[key] = (handle, w, h, fp, frame_idx)
                if len(self._transient_tex) > 64:
                    for old_k in list(self._transient_tex.keys())[
                        : max(0, len(self._transient_tex) - 32)
                    ]:
                        old = self._transient_tex.pop(old_k, None)
                        if old is not None:
                            try:
                                renpy_host.destroy_texture(int(old[0]))
                            except Exception:
                                pass
                            try:
                                self._forget_handle_pixels(int(old[0]))
                            except Exception:
                                pass
                self._stash_handle_pixels(handle, w, h, pixels, transient=True)
                _trace_empty_upload(handle, "transient_create")
                return HostTexture(handle, w, h)
            empty_input = bool(getattr(self, "_load_empty_pad_input", False))
            self._load_empty_pad_input = False
            if key in self.texture_cache:
                old_fp, handle, old_w, old_h = self.texture_cache[key]
                if not _alive(handle):
                    self.texture_cache.pop(key, None)
                elif old_fp is not None and old_fp == fp and (old_w, old_h) == (w, h):
                    _touch(handle)
                    return HostTexture(handle, w, h)
                else:
                    wrote = False
                    wt_ms = 0.0
                    if (
                        handle
                        and (old_w, old_h) == (w, h)
                        and hasattr(renpy_host, "write_texture_rgba")
                    ):
                        try:
                            _wt0 = _time.monotonic() if _phase0_signals_enabled() else None
                            renpy_host.write_texture_rgba(int(handle), pixels)
                            if _wt0 is not None:
                                wt_ms = (_time.monotonic() - _wt0) * 1000.0
                            wrote = True
                        except Exception:
                            wrote = False
                    if wrote:
                        if _phase0_due_write():
                            _phase0_log(
                                f"write_texture_ms={wt_ms:.3f} size={w}x{h} "
                                f"path=cache_rewrite handle={int(handle)}"
                            )
                        self.texture_cache[key] = (fp, handle, w, h)
                        self._stash_handle_pixels(
                            handle, w, h, pixels, transient=False
                        )
                        _trace_empty_upload(handle, "cache_rewrite")
                        return HostTexture(handle, w, h)
                    try:
                        renpy_host.destroy_texture(handle)
                    except Exception:
                        pass
                    self._forget_handle_pixels(handle)
                    self.texture_cache.pop(key, None)
            handle = renpy_host.create_texture_rgba(w, h, pixels)
            if not empty_input:
                self.texture_cache[key] = (fp, handle, w, h)
                self._stash_handle_pixels(handle, w, h, pixels, transient=False)
            else:
                _trace_empty_upload(handle, "cache_create_empty_pad_nocache")
            if not empty_input:
                _trace_empty_upload(handle, "cache_create")
            return HostTexture(handle, w, h)
        except Exception as e:
            _host_draw_fail("load_texture", e)
            try:
                w, h = surf.get_size()
                w, h = max(1, int(w)), max(1, int(h))
            except Exception:
                w, h = 1, 1
            try:
                import renpy_host  # type: ignore
                transparent_bytes = bytes(w * h * 4)
                handle = renpy_host.create_texture_rgba(w, h, transparent_bytes)
                _ui_trace_once(
                    "empty_upload",
                    f"class=a blank_success handle={int(handle)} tag=placeholder_exc "
                    f"size=({w},{h}) all_alpha_zero=1 was_empty_pad=1",
                )
                return HostTexture(handle, w, h)
            except Exception:
                _ui_trace_once(
                    "empty_upload",
                    f"class=c handle=0 tag=placeholder_fail size=({w},{h})",
                )
                return HostTexture(0, w, h)

    def ready_one_texture(self):
        return False

    def solid_texture(self, w, h, color):
        from renpy.pygame.surface import Surface
        s = Surface((max(1, int(w)), max(1, int(h))))
        if len(color) == 3:
            color = (*color, 255)
        s.fill(color)
        return self.load_texture(s)

    def kill_textures(self):
        try:
            from renpy.display import im
            im.cache.clear()
        except Exception:
            pass
        try:
            import renpy_host  # type: ignore
            for _fp, handle, _tw, _th in list(self.texture_cache.values()):
                try:
                    renpy_host.destroy_texture(handle)
                except Exception:
                    pass
            for tent in list(self._transient_tex.values()):
                handle = tent[0] if tent else 0
                _w = tent[1] if tent and len(tent) > 1 else 0
                _h = tent[2] if tent and len(tent) > 2 else 0
                _fp = tent[3] if tent and len(tent) > 3 else None
                try:
                    renpy_host.destroy_texture(int(handle))
                except Exception:
                    pass
        except Exception:
            pass
        self.texture_cache.clear()
        self._transient_tex.clear()
        try:
            self._handle_remap = {}
        except Exception:
            pass
        try:
            self._destroy_all_rtts()
        except Exception:
            pass
        try:
            import renpy_host  # type: ignore
            for handle in list(self._mesh_cache.values()):
                try:
                    renpy_host.destroy_mesh(int(handle))
                except Exception:
                    pass
            for handle in list(getattr(self, "_mesh_deferred_destroy", None) or []):
                try:
                    renpy_host.destroy_mesh(int(handle))
                except Exception:
                    pass
        except Exception:
            pass
        self._mesh_cache.clear()
        self._mesh_deferred_destroy = []
        self._quad_mesh = None
__all__ = ["TextureMixin"]
