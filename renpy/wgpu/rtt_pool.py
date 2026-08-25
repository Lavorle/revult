"""
rtt_pool — RTT freelist / frame recycle extracted from draw.py.

Usage as mixin:

    from .rtt_pool import RttPoolMixin
    class WgpuDraw(RttPoolMixin):
        ...

State attributes expected on `self` (initialized in WgpuDraw.__init__):
    _rtt_free: dict[(w,h), list[int]]
    _rtt_prev_frame: list[(handle,w,h)]
    _rtt_curr_frame: list[(handle,w,h)]
    _rtt_pool_cap: int
    drawable_size, virtual_size, layout_virtual_size

Re-exported methods keep `WgpuDraw._acquire_rtt` etc public.
"""
from __future__ import annotations


class RttPoolMixin:
    # type hints for attributes owned by WgpuDraw; not initialized here
    _rtt_free: dict[tuple[int, int], list[int]]  # type: ignore
    _rtt_prev_frame: list[tuple[int, int, int]]  # type: ignore
    _rtt_curr_frame: list[tuple[int, int, int]]  # type: ignore
    _rtt_pool_cap: int  # type: ignore
    drawable_size: tuple[int, int]  # type: ignore
    virtual_size: tuple[int, int]  # type: ignore
    layout_virtual_size: tuple[int, int]  # type: ignore
    texture_cache: dict  # type: ignore

    def get_texture_size(self):
        free_n = sum(len(v) for v in self._rtt_free.values())
        live_n = len(self._rtt_prev_frame) + len(self._rtt_curr_frame)
        return (live_n + free_n, len(self.texture_cache))

    def _acquire_rtt(self, w, h):
        """Borrow or create a render texture of size (w, h); tracked for recycle.

        Bounds live RTTs per size to ``_rtt_pool_cap``. When the cap is hit
        mid-prepare (before any product present can freelist), **reuse** the
        oldest live handle of that size in place instead of allocating. This
        is required for HuangmeiC splash/dissolve: frames=1 for many seconds
        while nested mesh_bake thrash would otherwise allocate thousands of
        1920×1080 targets and OOM the process.
        """
        import renpy_host  # type: ignore

        w = max(1, int(w))
        h = max(1, int(h))
        # Feel residual H3: reverse-inflated RTT sizes (e.g. 2219x5567).
        # Cap strictly to live drawable / layout virtual. Never use temporary
        # mesh-bake virtual_size (it is set to the RTT size itself and would
        # pass reverse-inflated requests through).
        try:
            dw = max(1, int(self.drawable_size[0]))
            dh = max(1, int(self.drawable_size[1]))
            lv = getattr(self, "layout_virtual_size", None) or self.virtual_size
            lw = max(1, int(lv[0]))
            lh = max(1, int(lv[1]))
            # Strict ceiling: min(layout, drawable). Large windows must not pass
            # reverse-inflated RTT requests; small windows must not allocate
            # above the live surface.
            hard_w = min(lw, dw)
            hard_h = min(lh, dh)
            if hard_w < 1:
                hard_w = max(lw, dw, 1)
            if hard_h < 1:
                hard_h = max(lh, dh, 1)
            w = min(w, hard_w)
            h = min(h, hard_h)
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            w = min(w, 1920)
            h = min(h, 1080)
        key = (w, h)
        free = self._rtt_free.get(key)
        if free:
            handle = int(free.pop())
            self._rtt_curr_frame.append((handle, w, h))
            return handle

        live_same = [t for t in self._rtt_curr_frame if t[1] == w and t[2] == h]
        if len(live_same) >= self._rtt_pool_cap:
            # Reuse oldest live RTT of this size (overwrite). Safe for bake
            # targets that are re-drawn every call; concurrent unique content
            # is limited to _rtt_pool_cap simultaneous slots.
            old_handle, _, _ = live_same[0]
            # Rotate tracking: drop first occurrence, re-append as newest.
            dropped = False
            new_curr = []
            for t in self._rtt_curr_frame:
                if not dropped and t[0] == old_handle and t[1] == w and t[2] == h:
                    dropped = True
                    continue
                new_curr.append(t)
            new_curr.append((old_handle, w, h))
            self._rtt_curr_frame = new_curr
            return int(old_handle)

        handle = int(renpy_host.create_render_texture(w, h))
        self._rtt_curr_frame.append((handle, w, h))
        return handle

    def _release_rtt_now(self, handle, w=None, h=None):
        """Return a short-lived RTT (e.g. is_pixel_opaque) to the freelist/destroy."""
        if handle is None:
            return
        try:
            handle = int(handle)
        except (TypeError, ValueError):
            return
        if handle <= 0:
            return
        # Drop from curr/prev tracking if present (avoid double-free on recycle).
        self._rtt_curr_frame = [
            t for t in self._rtt_curr_frame if t[0] != handle
        ]
        self._rtt_prev_frame = [
            t for t in self._rtt_prev_frame if t[0] != handle
        ]
        key = None
        if w is not None and h is not None:
            key = (max(1, int(w)), max(1, int(h)))
        if key is not None:
            bucket = self._rtt_free.setdefault(key, [])
            if len(bucket) < self._rtt_pool_cap:
                bucket.append(handle)
                return
        try:
            import renpy_host  # type: ignore

            renpy_host.destroy_texture(handle)
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass

    def _recycle_frame_rtts(self):
        """End-of-frame: freelist previous frame RTTs; promote current → previous.

        Keeps RTTs alive for one extra frame so late same-frame / next-frame
        reads of a just-baked handle remain valid, then reuses or destroys.
        """
        try:
            import renpy_host  # type: ignore
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            renpy_host = None  # type: ignore

        for handle, w, h in self._rtt_prev_frame:
            key = (w, h)
            bucket = self._rtt_free.setdefault(key, [])
            if len(bucket) < self._rtt_pool_cap:
                bucket.append(handle)
            elif renpy_host is not None:
                try:
                    renpy_host.destroy_texture(handle)
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
        self._rtt_prev_frame = self._rtt_curr_frame
        self._rtt_curr_frame = []

    def _destroy_all_rtts(self):
        """Destroy freelist + tracked frame RTTs (kill_textures / resize)."""
        try:
            import renpy_host  # type: ignore
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            renpy_host = None  # type: ignore

        handles = []
        for bucket in self._rtt_free.values():
            handles.extend(bucket)
        handles.extend(h for h, _w, _h in self._rtt_prev_frame)
        handles.extend(h for h, _w, _h in self._rtt_curr_frame)
        self._rtt_free.clear()
        self._rtt_prev_frame = []
        self._rtt_curr_frame = []
        if renpy_host is None:
            return
        for handle in handles:
            try:
                renpy_host.destroy_texture(int(handle))
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass


__all__ = ["RttPoolMixin"]
