"""draw_texture — texture/cache mixin extracted from draw.py."""
from __future__ import annotations

import time as _time
from enum import Enum

from .draw_debug import (
    _UI_TRACE_LOGGED,
    _host_draw_fail,
    _phase0_due_write,
    _phase0_log,
    _phase0_signals_enabled,
    _ui_trace_once,
)
from .host_bridge import host_env_bool
from .host_texture import HostTexture, _surf_fingerprint

try:
    from .host_texture import AliveState as HandleState  # type: ignore
except Exception:  # noqa: BLE001
    class HandleState(Enum):
        ALIVE = 1
        REMAPPED = 2
        DEAD_RECOVER = 3


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
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            return
        if handle <= 0:
            return
        try:
            w, h = int(w), int(h)
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        # T7: _handle_pixels is a GpuHandleCache; size-aware eviction keeps small
        # UI chrome and drops oversize frames first (was _evict_handle_pixels).
        store = self._handle_pixels
        val = (w, h, bytes(pixels[:need]))
        if handle not in store and len(store) >= store._cap:
            store.evict_some(max(1, store._cap // 8), area_fn=lambda e: int(e[0]) * int(e[1]),
                             keep_below=256 * 256)
        store.set(handle, val)

    def _evict_handle_pixels(self, store, cap):
        # Retained for legacy callers / signature parity; delegates to size-aware
        # GpuHandleCache eviction (keeps small chrome, drops oversize first).
        if not getattr(store, "evict_some", None):
            return
        store.evict_some(max(1, int(cap) // 8), area_fn=lambda e: int(e[0]) * int(e[1]),
                         keep_below=256 * 256)

    def _forget_handle_pixels(self, handle):
        try:
            h = int(handle or 0)
            if h > 0:
                store = getattr(self, "_handle_pixels", None)
                if store is not None and hasattr(store, "pop"):
                    store.pop(h, None)
                remap = getattr(self, "_handle_remap", None)
                if remap is not None and hasattr(remap, "pop"):
                    remap.pop(h, None)
        except Exception:  # noqa: BLE001
            pass

    def _log_once(self, key: str, exc: Exception) -> None:
        _host_draw_fail(key, exc)

    def _set_handle(self, ht, value):
        v = int(value)
        if hasattr(ht, "handle"):
            ht.handle = v
        if hasattr(ht, "texture"):
            ht.texture = v

    def _ensure_host_texture_alive(self, ht, w: int | None = None, h: int | None = None):
        if ht is None:
            return None
        try:
            handle = int(getattr(ht, "handle", 0) or 0)
            if handle == 0 and isinstance(ht, int) and not isinstance(ht, bool):
                handle = int(ht)
        except Exception:  # noqa: BLE001
            return None
        if handle <= 0:
            return ht
        # State 1: ALIVE probe — single lookup via texture_alive
        try:
            import renpy_host  # type: ignore

            alive_fn = getattr(renpy_host, "texture_alive", None)
            if alive_fn is not None:
                is_alive = bool(alive_fn(int(handle)))
            else:
                is_alive = True
            if is_alive:
                if hasattr(renpy_host, "touch_texture"):
                    try:
                        renpy_host.touch_texture(int(handle))
                    except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        pass
                return ht
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
        # State 2: REMAPPED — single lookup, no chain walk
        remap = self._handle_remap
        try:
            remapped = remap.get(handle)
        except Exception:
            remapped = None
        if remapped is not None:
            try:
                import renpy_host  # type: ignore

                alive_fn = getattr(renpy_host, "texture_alive", None)
                if alive_fn is not None:
                    is_alive = bool(alive_fn(int(remapped)))
                else:
                    is_alive = True
                if is_alive:
                    self._set_handle(ht, remapped)
                    return ht
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass
        # State 3: DEAD_RECOVER — single _handle_pixels lookup + create_texture_rgba
        store = self._handle_pixels
        try:
            pix_ent = store.get(handle)
        except Exception:
            pix_ent = None
        if pix_ent is not None:
            try:
                import renpy_host  # type: ignore

                if isinstance(pix_ent, (tuple, list)) and len(pix_ent) == 3:
                    sw, sh, pixels = pix_ent
                else:
                    pixels = pix_ent
                    sw = w if w is not None else int(getattr(ht, "w", 1) or 1)
                    sh = h if h is not None else int(getattr(ht, "h", 1) or 1)
                cw = int(w) if w is not None else int(sw)
                ch = int(h) if h is not None else int(sh)
                if cw <= 0 or ch <= 0:
                    return None
                new_h = int(renpy_host.create_texture_rgba(int(cw), int(ch), pixels))
                if new_h <= 0:
                    return None
                # remap: handle both GpuHandleCache.set and plain dict
                try:
                    remap.set(handle, int(new_h))  # type: ignore[attr-defined]
                except AttributeError:
                    remap[handle] = int(new_h)  # type: ignore[index]
                self._set_handle(ht, new_h)
                # keep capacity semantics via stash
                try:
                    if isinstance(pixels, (bytes, bytearray)):
                        store.set(int(new_h), (int(cw), int(ch), bytes(pixels[: int(cw * ch * 4)])))  # type: ignore[attr-defined]
                    else:
                        store.set(int(new_h), (int(cw), int(ch), pixels))  # type: ignore[attr-defined]
                except AttributeError:
                    # plain dict fallback
                    if isinstance(pixels, (bytes, bytearray)):
                        store[int(new_h)] = (int(cw), int(ch), bytes(pixels[: int(cw * ch * 4)]))  # type: ignore[index]
                    else:
                        store[int(new_h)] = (int(cw), int(ch), pixels)  # type: ignore[index]
                try:
                    store.pop(handle, None)  # type: ignore[attr-defined]
                except AttributeError:
                    try:
                        store.pop(handle, None)
                    except Exception:
                        pass
                return ht
            except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                self._log_once("dead_recover", e)
                return None
        return None

    def _recover_pixels_for_dead_handle(self, old, cur, ht):
        # Legacy shim: im.cache full traversal deleted (slop). Single-lookup via _handle_pixels.
        store = self._handle_pixels
        ent = store.get(int(old))
        if ent is None:
            ent = store.get(int(cur))
        return ent

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
                            except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                                alive = True
                        if alive:
                            if hasattr(renpy_host, "touch_texture"):
                                try:
                                    renpy_host.touch_texture(int(thandle))
                                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                                    pass
                            return HostTexture(thandle, w, h)
            if hasattr(surf, "_pixels"):
                raw = surf._pixels
                pixels = bytes(raw)
            else:
                try:
                    pixels = bytes(surf.get_buffer())
                except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                host_env_bool("RENPY_HOST_UI_TRACE")
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
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
            def _alive(handle):
                if not handle:
                    return False
                if not hasattr(renpy_host, "texture_alive"):
                    return True
                try:
                    return bool(renpy_host.texture_alive(int(handle)))
                except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                    except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        self._ui_trace_blank_pixels = None
            def _touch(handle):
                if not handle or not hasattr(renpy_host, "touch_texture"):
                    return
                try:
                    renpy_host.touch_texture(int(handle))
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
            if transient:
                if host_env_bool("RENPY_HOST_MOVIE_ASSERT"):
                    logged = getattr(self, "_ac2_tex_sizes_logged", None)
                    if logged is None:
                        logged = set()
                        self._ac2_tex_sizes_logged = logged
                    present_1b = True
                    try:
                        import os as _os
                        present_1b = _os.environ.get(
                            "RENPY_HOST_MOVIE_PRESENT", "1b"
                        ).strip().lower() not in ("1a", "layout", "s1")
                    except Exception:
                        pass
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
                        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                            except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                            pass
                        self._forget_handle_pixels(thandle)
                    elif thandle:
                        try:
                            renpy_host.destroy_texture(thandle)
                        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                                pass
                            try:
                                self._forget_handle_pixels(int(old[0]))
                            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
                    except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            _host_draw_fail("load_texture", e)
            try:
                w, h = surf.get_size()
                w, h = max(1, int(w)), max(1, int(h))
            except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
            except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
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
        # T5: im.cache coupling removed — host_texture stash (GpuHandleCache) is now single owner.
        # Legacy ``im.cache.clear()`` is no longer driven from wgpu; present-path dead-handle
        # recovery via _handle_pixels handles surftree-held HostTextures after FIFO eviction.
        try:
            import renpy_host  # type: ignore
            for _fp, handle, _tw, _th in list(self.texture_cache.values()):
                try:
                    renpy_host.destroy_texture(handle)
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
            for tent in list(self._transient_tex.values()):
                handle = tent[0] if tent else 0
                _w = tent[1] if tent and len(tent) > 1 else 0
                _h = tent[2] if tent and len(tent) > 2 else 0
                _fp = tent[3] if tent and len(tent) > 3 else None
                try:
                    renpy_host.destroy_texture(int(handle))
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
        self.texture_cache.clear()
        self._transient_tex.clear()
        try:
            self._handle_remap.clear()
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
        try:
            self._destroy_all_rtts()
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
        try:
            import renpy_host  # type: ignore
            for handle in list(self._mesh_cache.values()):
                try:
                    renpy_host.destroy_mesh(int(handle))
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
            for handle in list(getattr(self, "_mesh_deferred_destroy", None) or []):
                try:
                    renpy_host.destroy_mesh(int(handle))
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
        self._mesh_cache.clear()
        self._mesh_deferred_destroy = []
        self._quad_mesh = None
__all__ = ["TextureMixin"]
