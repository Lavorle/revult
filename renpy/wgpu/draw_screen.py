"""draw_screen — screen/present mixin extracted from draw.py."""
from __future__ import annotations

import time as _time

from .draw_debug import (
    _BAKE_ENABLED,
    _WALKSTAT_ON,    _UI_TRACE_LOGGED,
    _draw_screen_lock,
    _host_draw_fail,
    _phase0_due_frame,
    _phase0_log,
    _phase0_signals_enabled,
    _safe_print,
    _ui_trace_once,
    bake_attn,
    bake_frame_flush as _bake_frame_flush_orig,
)
from .host_bridge import host_env_bool
from .host_texture import HostTexture

# Resolved lazily (draw_surftree imports this module) — cache the accessor so
# the per-frame WALKSTAT tail costs one global lookup, not an import.
_SM_COST_FN = None


def _sm_cost_accessor():
    """``draw_surftree._sm_frame_cost`` or None when the mixin is unavailable."""
    global _SM_COST_FN
    fn = _SM_COST_FN
    if fn is None:
        try:
            from .draw_surftree import _sm_frame_cost as fn
        except Exception:  # noqa: BLE001 -- instrumentation only
            return None
        _SM_COST_FN = fn
    return fn


def bake_frame_flush(now: float = 0.0) -> None:
    """hmc_fix3 F3: bake PHASE0 cadence + rev-cache drain verbatim here.

    The product frame tail calls ``bake_frame_flush`` (draw_debug) once per
    frame; ``renpy/wgpu/draw_debug.py`` (the PHASE0 line body owner) is
    outside the editable file set, so the reverse-bake-cache window drain
    rides the same cadence by rebinding THIS module-local name only —
    ``renpy.wgpu.draw_debug.bake_frame_flush`` and every other reference
    stay the original object, so renpy's module backup pickle contract
    (identity-by-name resolution) is untouched. The drain emits its own
    window line through the same ``_phase0_log`` formatter.
    """
    try:
        from .draw_surftree import _keys_frame_flush

        # hmc_fix4 A: close the key-derivation window BEFORE the shared
        # bake-line tail peeks it, so a key derived and dropped inside this
        # product frame is attributed to the very frame it was derived in
        # (``keys_derived`` / ``keys_wasted`` land in the same PHASE0 line).
        _keys_frame_flush()
    except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
        pass
    try:
        _bake_frame_flush_orig(now)
    finally:
        try:
            from .draw_surftree import _rev_cache_drain_emit

            _rev_cache_drain_emit(now)
        except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
            pass


# Process-static env gates: read once at import (the probe loops re-imported
# nothing). Surgical: avoids per-draw os.environ reads on the hot path.
_LEGACY_FLUSH = host_env_bool("RENPY_HOST_LEGACY_FLUSH")


def _RENPY_HOST():
    """Lazy renpy_host handle via a mutable holder (assignment-free global).

    A plain `global X; if X is None: X = ...` pattern triggers UnboundLocal
    on the first read; keep every cached value in a list cell instead.
    """
    cell = _RENPY_HOST_CELL
    obj = cell[0]
    if obj is None:
        try:
            import renpy_host  # type: ignore
            obj = renpy_host
        except Exception:
            obj = False
        cell[0] = obj
    return obj if obj is not False else None


def _DRAW_MODELS_FN():
    cell = _DRAW_MODELS_CELL
    fn = cell[0]
    if fn is None:
        host = _RENPY_HOST()
        fn = getattr(host, "draw_models", None) if host is not None else None
        if fn is None:
            fn = False
        cell[0] = fn
    return fn if fn is not False else None


def _END_FRAME_PRESENT_OPTS_FN():
    cell = _END_OPTS_CELL
    fn = cell[0]
    if fn is None:
        host = _RENPY_HOST()
        fn = getattr(host, "end_frame_present_opts", None) if host is not None else None
        if fn is None:
            fn = False
        cell[0] = fn
    return fn if fn is not False else None


_RENPY_HOST_CELL = [None]
_DRAW_MODELS_CELL = [None]
_END_OPTS_CELL = [None]


class ScreenMixin:
    _draw_batch: list
    _draw_screen_depth: int
    _clip_rect: object
    virtual_size: tuple[int, int]
    physical_size: tuple[int, int]
    drawable_size: tuple[int, int]
    _rtt_free: dict
    _rtt_prev_frame: list
    _rtt_curr_frame: list

    def _dm(self, pipeline, mesh, texture=None, texture1=None, uniforms=None, texture2=None):
        """Queue a draw_model for the current product frame (ordered stream).

        Painter-order mechanism (batched commits, exact tree order): the
        stream in ``self._draw_batch`` keeps elements in tree order —

        - 6-tuples ``(pipeline, mesh, texture, texture1, uniforms, texture2)``
          = uniform / multi-texture draws (committed later, aggregated);
        - ``("GRP", snapshot)`` boundaries. Every draw that lands while the
          instance group still holds unconsumed quads appends a boundary whose
          snapshot records the per-key cumulative instance count at this
          exact point. ``_flush_draw_batch`` drains exactly that slice from
          ``_instance_group`` at its stream position, so grouped
          solid/textured quads never overtake a uniform draw queued after
          them — nor does a later batched draw underpaint earlier group quads
          (HuangmeiC active nav tab: the image_dissolve pill must cover its
          own text labels).

        No immediate per-draw commit anymore: previously every uniform draw
        paid one FCI + global lock because the boundary commit destroyed
        contiguous run batching; the stream runner keeps identical painter
        order while merging contiguous 6-tuple runs into one ``draw_models``
        call.

        Safety valve: ``RENPY_HOST_LEGACY_FLUSH=1`` selects the old
        conservative path (seam group flush + immediate per-draw commit, one
        FCI per uniform draw) if the streaming path ever regresses.
        """
        if not _LEGACY_FLUSH:
            grp = getattr(self, "_instance_group", None)
            if (
                grp is not None
                and not grp.empty()
                and not getattr(self, "_in_instance_flush", False)
            ):
                try:
                    snap = grp.snapshot()
                except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    _host_draw_fail("dm.snapshot", e)
                    snap = None
                if snap:
                    # Boundary: remember the exact so-far slice of the group;
                    # the runner drains it before anything appended after.
                    self._draw_batch.append(("GRP", snap))
            self._draw_batch.append(
                (int(pipeline), int(mesh), texture, texture1, uniforms, texture2)
            )
        else:
            # Conservative legacy seam: commit both pending streams at every
            # boundary — group first (queued earlier), then everything batched
            # before this call — then append this draw and commit it
            # immediately. Contiguous instanced runs still batch; every
            # uniform draw pays one sequence-preserving commit (order >
            # call count).
            grp = getattr(self, "_instance_group", None)
            if grp is not None and not getattr(self, "_in_instance_flush", False):
                try:
                    if not grp.empty():
                        # Ordered-stream flush (delegates to the runner);
                        # post-run the group is empty.
                        grp.flush(self)
                except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    _host_draw_fail("dm.instance_flush", e)
            self._draw_batch.append(
                (int(pipeline), int(mesh), texture, texture1, uniforms, texture2)
            )
            # Immediate seam commit: a tree-ordered later instanced draw must
            # never overtake this uniform draw because of the deferred batch.
            self._flush_draw_batch()

    def _flush_draw_batch(self):
        """Ordered-stream commit: merge uniform-run batches with group drains.

        Runner over the accumulated stream (cleared up-front):

        - Contiguous 6-tuple runs aggregate into ONE host ``draw_models`` call
          (list order == tree order).
        - ``("GRP", snap)`` boundary at position i: commit the pending run
          first, then ``_instance_group.drain_upto(snap)`` — exactly the
          instances queued before the boundary, from the per-key offset, one
          ``draw_instances`` per live key. Instances queued after stay put.
        - Tail: commit the last run, then ``drain_all()`` — the group
          remainder. For ``_end_frame_present`` this is the final commit; the
          follow-up ``_flush_instance_group`` is a no-op because drains are
          idempotent by per-key offset.
        """
        batch = self._draw_batch
        grp = getattr(self, "_instance_group", None)
        if not batch and (grp is None or grp.empty()):
            return
        self._draw_batch = []
        was_flush = getattr(self, "_in_instance_flush", False)
        self._in_instance_flush = True
        try:
            if batch:
                seg: list = []

                def _seg_commit():
                    """One draw_models call for the contiguous run (fallback loop)."""
                    if not seg:
                        return
                    try:
                        dm = _DRAW_MODELS_FN()
                        if dm is not None:
                            dm(seg)
                            return
                        host = _RENPY_HOST()
                        if host is not None:
                            for item in seg:
                                host.draw_model(*item)
                            return
                    except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        _host_draw_fail("flush_draw_batch", e)
                    finally:
                        del seg[:]

                def _commit_residue():
                    """Commit quads appended during a guarded drain (hermetic
                    fallback path: ``_InstanceGroup._emit`` -> ``_flush_instance_fallback``
                    -> ``_dm``). They must be committed at the current stream
                    position — right after their boundary — before any later
                    stream element; without this they would sink below (or,
                    via a nested full runner, jump above) the group's tail."""
                    residue = self._draw_batch
                    if not residue:
                        return
                    self._draw_batch = []
                    try:
                        dm = _DRAW_MODELS_FN()
                        if dm is not None:
                            dm(residue)
                            return
                        host = _RENPY_HOST()
                        if host is not None:
                            for item in residue:
                                host.draw_model(*item)
                            return
                    except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        _host_draw_fail("flush_draw_batch.residue", e)
                    finally:
                        del residue[:]

                for item in batch:
                    if (
                        isinstance(item, tuple)
                        and item
                        and isinstance(item[0], str)
                        and item[0] == "GRP"
                    ):
                        # Painter-order boundary: draws queued before it go out
                        # first, then exactly its group slice (and any quads
                        # the hermetic fallback appended mid-drain).
                        _seg_commit()
                        try:
                            if grp is not None:
                                grp.drain_upto(item[1], self)
                        except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                            _host_draw_fail("flush_draw_batch.grp", e)
                        _commit_residue()
                    else:
                        seg.append(item)
                _seg_commit()
            # Tail: the group remainder (covers frames whose last draws were
            # grouped, and empty-stream tails so nested-bake seams commit all
            # pending grouped quads into the still-open parent target).
            if grp is not None:
                try:
                    grp.drain_all(self)
                except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    _host_draw_fail("flush_draw_batch.grp_all", e)
            # Residue after the tail drain (hermetic fallback quads must not
            # leak into the next frame's stream past a present).
            try:
                residue = self._draw_batch
                if residue:
                    self._draw_batch = []
                    dm = _DRAW_MODELS_FN()
                    if dm is not None:
                        dm(residue)
                    else:
                        host = _RENPY_HOST()
                        if host is not None:
                            for item in residue:
                                host.draw_model(*item)
            except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                _host_draw_fail("flush_draw_batch.residue", e)
        finally:
            self._in_instance_flush = was_flush

    def _end_frame_present(self, full_canvas: bool = False):
        """Flush batched draw_model cmds then present (product + nested RTT)."""
        # Final tail commit in painter order: the ordered stream already
        # drains group boundaries at their exact positions and the runner tail
        # drain_all commits the group remainder — so here the stream is fully
        # drained (draws + group). The legacy seam flush below is a no-op
        # (drains are idempotent by per-key offset — nothing double-submits).
        self._flush_draw_batch()
        try:
            grp_flush = getattr(self, "_flush_instance_group", None)
            if grp_flush is not None:
                grp_flush()
        except Exception:
            pass
        _opts = _END_FRAME_PRESENT_OPTS_FN()
        if _opts is not None:
            _opts(bool(full_canvas))
        else:
            _RENPY_HOST().end_frame_present()

    def draw_screen(self, surftree, flip=True):


        """
        Walk a Render-like surftree and emit draw_model batches.

        Duck-types nodes rather than requiring Cython Render/GL2Model:

        - Model-like: ``mesh`` (handle / MeshData / True), optional ``texture`` /
          ``textures``, ``vertices``+``indices``, ``color``, ``shaders``
        - Render-like: ``children`` list of ``(child, xo, yo, …)`` or bare nodes;
          optional ``cached_model``, ``blits``, ``mesh``
        - Surface-like: ``get_size()`` + pixel buffer → textured quad
        - int: already-uploaded texture handle → full-window textured quad

        Offsets ``xo``/``yo`` are virtual-pixel (top-left origin) and map into NDC
        via ``virtual_size``. Clear is encoded in host ``end_frame_present``.

        Frame order matches GL2: prepare (load_all_textures) → begin_frame →
        draw → present → invalidate frame-local prepare marks.

        Critical: always pair begin_frame with end_frame_present. If a prior
        draw left the host ``in_frame`` (exception between begin/end), the next
        begin_frame nests and end_frame_present **drops** product cmds — the
        classic permanent ``arena_rt_clear`` failure mode.

        Also serializes concurrent force-redraw vs interact present (threading
        RLock) so two end_frame_present calls cannot interleave into pure
        arena-clear black after chrome thrash (confirm_alone_2 residual).
        """
        # Reentrant product present: nested calls (e.g. mid-draw side effect)
        # must not open a second host frame or wipe the outer cmds.
        lock = _draw_screen_lock()
        if not lock.acquire(blocking=True, timeout=30.0):
            _host_draw_fail("draw_screen", RuntimeError("draw_screen lock timeout"))
            return
        try:
            depth = int(getattr(self, "_draw_screen_depth", 0) or 0)
            if depth > 0:
                # Nested: only walk into the already-open host frame if any.
                try:
                    import renpy_host  # type: ignore

                    if surftree is not None and hasattr(renpy_host, "in_frame"):
                        try:
                            if renpy_host.in_frame():
                                self._draw_node(surftree, 0.0, 0.0)
                        except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                            _host_draw_fail("draw_screen.nested", e)
                except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    _host_draw_fail("draw_screen.nested_import", e)
                return
            self._draw_screen_depth = depth + 1
            try:
                self._draw_screen_body(surftree, flip=flip)
            finally:
                self._draw_screen_depth = 0
        finally:
            try:
                lock.release()
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass


    def _walk_tree_shelf(self, node, depth, out, maxdepth=3):
        """Depth-capped child-count skeleton of the render tree (diagnostics)."""
        if node is None or depth > maxdepth:
            return
        n = 0
        kinds = []
        try:
            for _c, _x, _y in self._iter_children(node):
                n += 1
                if len(kinds) < 6:
                    kinds.append(type(_c).__name__[:8])
        except Exception:
            return
        out.append(
            "%s%s(%d ch=[%s]%s%s)"
            % (
                " " * depth,
                type(node).__name__[:8],
                n,
                ",".join(kinds),
                ", layer=" if getattr(node, "layer_name", None) else "",
                getattr(node, "layer_name", None) or "",
            )
        )
        try:
            for _c, _x, _y in self._iter_children(node):
                self._walk_tree_shelf(_c, depth + 1, out, maxdepth)
        except Exception:
            return

    def _draw_screen_body(self, surftree, flip=True):
        """Inner product present (caller holds ``_DRAW_SCREEN_LOCK`` + depth)."""
        try:
            import renpy_host  # type: ignore

            frame_t0 = _time.monotonic() if _phase0_signals_enabled() else None
            prepare_ms = 0.0
            draw_ms = 0.0
            present_ms = 0.0
            invalidate_ms = 0.0

            self._ensure_pipes()
            # Recover from a stuck nested/in_frame host state (no-op when clean).
            self._recover_frame_state()
            if surftree is not None:
                try:
                    _p0 = _time.monotonic() if frame_t0 is not None else None
                    self.load_all_textures(surftree)
                    if _p0 is not None:
                        prepare_ms = (_time.monotonic() - _p0) * 1000.0
                except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    _host_draw_fail("load_all_textures", e)
            # Prepare may have nested RTT frames; ensure we are not nested before
            # the product present so cmds are not discarded as "nested non-target".
            self._recover_frame_state()
            renpy_host.begin_frame()
            self._draw_batch = []
            # Walk-stat counters: per-product-frame nodes/bakes/fail list, so a
            # "frame lost a subtree prefix" flake can be attributed to (a) the
            # walker visiting too few nodes, (b) an RTT bake path, or (c)
            # swallowed per-node exceptions (dedup-free via _HOST_FRAME_FAILS).
            from .draw_debug import (
                _FRAME_SKIPS,
                _HOST_FRAME_FAILS,
                _HOST_FRAME_FAILS_ENABLED as _wd_enabled,
            )

            self._frame_nodes = 0
            self._frame_bakes = 0
            del _HOST_FRAME_FAILS[:]
            del _FRAME_SKIPS[:]
            walk_t0 = _time.monotonic() if _wd_enabled else None
            # Fresh clip stack each product present (axis-aligned mesh crop / polygon stencil).
            self._clip_rect = None
            self._clip_poly = None
            # Full-canvas marker for this frame (transition composite bake /
            # fullscreen movie quad): lets the host also treat this frame as a
            # "complete" frame when it has fewer cmds than the last full render.
            self._frame_full_canvas = False
            walk_ok = True
            try:
                if surftree is not None:
                    _d0 = _time.monotonic() if frame_t0 is not None else None
                    self._draw_node(surftree, 0.0, 0.0)
                    if _d0 is not None:
                        draw_ms = (_time.monotonic() - _d0) * 1000.0
            except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                walk_ok = False
                _host_draw_fail("draw_node", e)
            finally:
                self._clip_rect = None
                self._clip_poly = None
                # cmd list after a walk exception: encode_pass Clears then draws
                # only what was queued → prefs chrome holes / hover flicker.
                # reset_frame_state drops cmds without encoding so the last good
                # game RT remains (empty-present no-op path).
                try:
                    _pr0 = _time.monotonic() if frame_t0 is not None else None
                    if walk_ok:
                        self._end_frame_present(
                            bool(getattr(self, "_frame_full_canvas", False))
                        )
                    else:
                        reset = getattr(renpy_host, "reset_frame_state", None)
                        if reset is not None:
                            reset()
                        else:
                            # Fallback: still close the frame (may present partial).
                            self._end_frame_present()
                    if _pr0 is not None:
                        present_ms = (_time.monotonic() - _pr0) * 1000.0
                except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    _host_draw_fail("end_frame_present", e)
                    try:
                        reset = getattr(renpy_host, "reset_frame_state", None)
                        if reset is not None:
                            reset()
                    except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        pass
                # Flush meshes that were evicted from the Python cache while still
                # referenced by this frame's draw cmds (deferred in _mesh_quad_ndc).
                # Phase 0d: bake-attribution frame flush — one throttled
                # (0.5 s) PHASE0_SIGNAL summarizing bakes_in_frame by tag/node
                # class/size. Diagnostic only; no-ops when instruments are off.
                bake_frame_flush()
                try:
                    self._flush_deferred_meshes()
                except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    _host_draw_fail("flush_deferred_meshes", e)
                # Recycle previous-frame RTTs after present so live handles
                # from this frame remain valid for one more present.
                try:
                    self._recycle_frame_rtts()
                except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    _host_draw_fail("recycle_frame_rtts", e)
                # Walk-stat: one line per product frame (O(1)), thread-tagged so
                # a nested probe-thread present is distinguishable from the
                # product loop; cmd_count comes from the host's last successful
                # product encode (0 until the first full present).
                if walk_t0 is not None:
                    try:
                        import threading as _th

                        tn = _th.current_thread().name or ""
                        th = "probe" if "prefs" in tn or "thrash" in tn else "main"
                        cc = -1
                        try:
                            cc = int(renpy_host.frame_cmd_count())
                        except Exception:
                            cc = -1
                        # Root children type skeleton: tells whether the RENDER
                        # tree itself lost the chrome subtree (walk nodes tiny
                        # AND root children tiny => render_screen prune) or the
                        # walker pruned a complete tree (nodes tiny, ch large).
                        ch = []
                        try:
                            for _c, _x, _y in self._iter_children(surftree):
                                ch.append(type(_c).__name__)
                                if len(ch) >= 16:
                                    break
                        except Exception:
                            ch = ["<err>"]
                        _smf = None
                        try:
                            _acc = _sm_cost_accessor()
                            if _acc is not None:
                                _smf = _acc(self)
                        except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
                            _smf = None
                        _smtxt = ""
                        if _smf is not None:
                            try:
                                # Per-frame stable-model-cache ledger (see
                                # draw_surftree._SM_F_* index contract):
                                # sm_ms = the frame's own accounting cost.
                                _smtxt = (
                                    " sm_ms=%.3f sm_tail_ms=%.3f sm_cnt_ms=%.3f"
                                    " sm_fp_ms=%.3f sm_bake_ms=%.3f sm_alive_ms=%.3f"
                                    " sm_calls=%d sm_cnt_n=%d sm_fp_n=%d sm_hit=%d"
                                    " sm_miss=%d sm_cnt_none=%d sm_key_none=%d"
                                    " sm_gate=%d sm_pins=%d sm_budget=%d sm_fp_n_none=%d"
                                    " sm_neg=%d sm_small=%d sm_acct_cap=%d sm_fp_budget=%d"
                                    % (
                                        (
                                            float(_smf[0])
                                            + float(_smf[1])
                                            + float(_smf[2])
                                            + float(_smf[3])
                                        )
                                        * 1000.0,
                                        float(_smf[4]) * 1000.0,
                                        float(_smf[0]) * 1000.0,
                                        float(_smf[1]) * 1000.0,
                                        float(_smf[2]) * 1000.0,
                                        float(_smf[3]) * 1000.0,
                                        int(_smf[6]),
                                        int(_smf[7]),
                                        int(_smf[8]),
                                        int(_smf[11]),
                                        int(_smf[12]),
                                        int(_smf[9]),
                                        int(_smf[10]),
                                        int(_smf[13]),
                                        int(_smf[14]),
                                        int(_smf[15]),
                                        int(_smf[16]),
                                        int(_smf[17]),
                                        int(_smf[18]),
                                        int(_smf[19]),
                                        int(getattr(self, "_sm_fp_budget", 0)),
                                    )
                                )
                            except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
                                _smtxt = ""
                        _safe_print(
                            "[WALKSTAT] th=%s nodes=%d bakes=%d fails=%d walk_ok=%d "
                            "cmds=%d nodes_ms=%.1f root=%s ch_n=%d ch=%s%s"
                            % (
                                th,
                                int(self._frame_nodes),
                                int(self._frame_bakes),
                                len(_HOST_FRAME_FAILS),
                                int(walk_ok),
                                cc,
                                (_time.monotonic() - walk_t0) * 1000.0,
                                type(surftree).__name__ if surftree is not None else "None",
                                len(ch),
                                ",".join(ch) if ch else "-",
                                _smtxt,
                            )
                        )
                        for _lf in _HOST_FRAME_FAILS[:6]:
                            _safe_print("[WALKSTAT_FAIL] %s %s: %s" % (_lf[0], _lf[1], _lf[2]))
                        for _sk in _FRAME_SKIPS[:10]:
                            _safe_print("[WALKSKIP] %s %s" % (_sk[0], _sk[1]))
                        # Tree shelf (depth≤3) every frame — cheap (~tens of
                        # nodes) and lets us tell a render-tree prune from a
                        # walker skip by printing node counts per level.
                        shelf = []
                        try:
                            self._walk_tree_shelf(surftree, 0, shelf)
                        except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
                            pass
                        if shelf:
                            _safe_print("[SHELF] %s" % " | ".join(shelf))
                    except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        pass
            # Do NOT call renpy_host.request_redraw() after every product present.
            # GL2 flip() only swaps buffers; continuous request_redraw wakes the
            # host event loop every frame and pairs with about_to_wait's redraw
            # to create a busy present/wake storm. Product redraw is already
            # driven by interact needs_redraw / video.playing / REDRAW timers.
            # Frame-local prepare marks: style/hover must re-prepare next frame.
            if surftree is not None:
                try:
                    _i0 = _time.monotonic() if frame_t0 is not None else None
                    self._invalidate_prepared(surftree)
                    if _i0 is not None:
                        invalidate_ms = (_time.monotonic() - _i0) * 1000.0
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
            if frame_t0 is not None:
                total_ms = (_time.monotonic() - frame_t0) * 1000.0
                # Always emit stall frames; throttle normal samples to interval.
                if total_ms >= 50.0 or _phase0_due_frame():
                    try:
                        fc = int(renpy_host.frame_count()) if hasattr(renpy_host, "frame_count") else -1
                    except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        fc = -1
                    tag = "STALL " if total_ms >= 50.0 else ""
                    _phase0_log(
                        f"{tag}draw_frame_ms={total_ms:.3f} prepare_ms={prepare_ms:.3f} "
                        f"draw_ms={draw_ms:.3f} present_ms={present_ms:.3f} "
                        f"invalidate_ms={invalidate_ms:.3f} flip={int(bool(flip))} "
                        f"host_frames={fc}"
                    )
            # Phase 1: arena thrash probe (once). Prefer existing Rust counters.
            if host_env_bool("RENPY_HOST_UI_TRACE") and "arena_count" not in _UI_TRACE_LOGGED:
                try:
                    sc = (
                        int(renpy_host.sample_texture_count())
                        if hasattr(renpy_host, "sample_texture_count")
                        else -1
                    )
                    ol = (
                        int(renpy_host.texture_order_len())
                        if hasattr(renpy_host, "texture_order_len")
                        else -1
                    )
                    _ui_trace_once(
                        "arena_count",
                        f"sample_texture_count={sc} texture_order_len={ol} cap=8192",
                    )
                except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    _ui_trace_once(
                        "arena_count",
                        f"arena counter read fail err={type(e).__name__}:{e}",
                    )
        except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            _host_draw_fail("draw_screen", e)



    def _recover_frame_state(self):
        """Pop any stuck nested host frames so the next present is top-level.

        Host ``end_frame_present`` with a non-empty ``frame_cmd_stack`` and no
        ``active_target`` discards cmds (nested non-target path). Product
        draw_screen must therefore never start while nested. We best-effort
        close leftover frames; missing APIs are ignored.

        Prefer ``reset_frame_state`` when available — after flowchart mesh RTT
        thrash the stack can be half-popped with in_frame false while cmds still
        land outside begin_frame (confirm AC-Nav residual).
        """
        try:
            import renpy_host  # type: ignore

            # Preferred: explicit depth/reset if host exposes them.
            if hasattr(renpy_host, "reset_frame_state"):
                renpy_host.reset_frame_state()
                return
            depth = 0
            if hasattr(renpy_host, "frame_depth"):
                try:
                    depth = int(renpy_host.frame_depth())
                except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    depth = 0
            # Fallback: attempt a few end_frame_present calls when in_frame-like
            # helpers exist, else a single defensive end (ignored if not in frame).
            n = max(depth, 0)
            if n == 0 and hasattr(renpy_host, "in_frame"):
                try:
                    if renpy_host.in_frame():
                        n = 1
                except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    n = 0
            # Always try at least nothing; if we know we're nested, drain.
            for _ in range(min(n, 8)):
                try:
                    self._end_frame_present()
                except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    break
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass


    def render_to_texture(self, what, alpha=True, properties=None, oversample=1.0, _dest_size=None):
        """
        Render `what` into an offscreen RTT and return its texture handle.

        `what` may be:
          - int / texture handle already on GPU → returned as-is
          - HostTexture → handle returned as-is
          - Render-like tree (children / mesh) → full tree walk into RTT
          - Surface-like with get_size() + pixels → uploaded and drawn into RTT
          - (width, height) size tuple → empty clear RTT of that size
          - object with .width/.height and optional .texture handle

        Uses create_render_texture + begin_target / end_target + draw_model.
        Nested begin_frame is safe under host frame_cmd_stack **only when**
        every begin_frame is paired with end_frame_present (try/finally below).
        `properties` / `oversample` accepted for GL2Draw call-site parity.

        hmc_fix3 F2: ``_dest_size`` (optional, walker-internal) shrinks ONLY
        the RTT texture size of the tree-walk pass to the dest box while the
        inner ``virtual_size`` stays the child's logical coordinate span, so
        the whole child content is rastered once at dest resolution instead of
        being baked at child size and downsampled by the following dest quad.
        Unknown / invalid / enlarging ``_dest_size`` values keep the old
        child-size bake byte-identically (conservative fallback). All other
        render_to_texture calls (dissolve_mid slots, size-tuple RTT, leaf
        passes) never pass ``_dest_size`` and are unaffected.
        """
        try:
            import renpy_host  # type: ignore

            self._ensure_pipes()

            # Already a GPU handle.
            if isinstance(what, int) and not isinstance(what, bool):
                return what
            if isinstance(what, HostTexture):
                return what.handle if what.handle > 0 else what

            def _rtt_pass(w, h, draw_fn, node=None, where_tag="rtt_pass"):
                """begin_target → begin_frame → draw_fn → end_frame → end_target.

                RTTs are borrowed from the size-keyed freelist (see
                ``_acquire_rtt``) so mesh-bake / dissolve thrash does not
                allocate a new full-screen target every call.
                """
                # M1 T3: isolate instance groups between parent and RTT
                if _WALKSTAT_ON:
                    try:
                        self._frame_bakes += 1
                    except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        pass
                # Phase 0d bake-attribution stamp (diagnostic only, no semantics).
                bake_attn(self, where_tag, node, w, h)
                _grp = getattr(self, "_instance_group", None)
                if _grp is not None:
                    try:
                        # flush parent pending into parent frame before nesting
                        if not _grp.empty():
                            _grp.flush(self)
                        _grp.push()
                    except Exception:
                        pass
                # Pending parent batch items precede this RTT's content in
                # tree order — commit them into the parent target first.
                self._flush_draw_batch()
                rtt = self._acquire_rtt(w, h)
                renpy_host.begin_target(rtt)
                renpy_host.begin_frame()
                # Scope the full-canvas frame marker to this bake: a composite
                # leaf inside a nested bake must not leak into the enclosing
                # product frame (its bake covers the RTT box, not the product
                # canvas), so those product frames keep the honest sparse guard.
                old_full = getattr(self, "_frame_full_canvas_hop", None)
                prev_full = getattr(self, "_frame_full_canvas", False)
                self._frame_full_canvas_hop = True
                self._frame_full_canvas = False
                try:
                    draw_fn()
                finally:
                    self._frame_full_canvas = bool(prev_full)
                    self._frame_full_canvas_hop = old_full
                    try:
                        self._end_frame_present()
                    except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        _host_draw_fail("render_to_texture.end_frame", e)
                    try:
                        renpy_host.end_target()
                    except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        _host_draw_fail("render_to_texture.end_target", e)
                    # restore parent instance group
                    if _grp is not None:
                        try:
                            _grp.pop()
                        except Exception:
                            pass
                return rtt
            # Size-only → empty RTT.
            if isinstance(what, (tuple, list)) and len(what) >= 2 and all(
                isinstance(x, (int, float)) for x in what[:2]
            ):
                return _rtt_pass(
                    what[0], what[1], lambda: None, node=None, where_tag="rtt_size"
                )

            # Object exposing a pre-built texture handle (leaf, no children needed).
            tex = getattr(what, "texture", None)
            children = list(self._iter_children(what)) if what is not None else []
            if isinstance(tex, HostTexture):
                tex = tex.handle if tex.handle > 0 else None
            if isinstance(tex, int) and not isinstance(tex, bool) and tex > 0 and not children:
                w = int(getattr(what, "width", 0) or getattr(what, "w", 0) or 0)
                h = int(getattr(what, "height", 0) or getattr(what, "h", 0) or 0)
                if w <= 0 or h <= 0:
                    try:
                        w, h = what.get_size()
                    except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        w, h = self.virtual_size
                handle = tex
                return _rtt_pass(
                    w,
                    h,
                    lambda: self._dm(self._tex_pipe, self._quad_mesh, handle),
                    node=what,
                    where_tag="rtt_texleaf",
                )

            # Surface-like leaf (no children): upload + draw into RTT.
            if (hasattr(what, "get_size") or hasattr(what, "_pixels")) and not children:
                try:
                    w, h = what.get_size()
                except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    w, h = self._node_size(what)
                w, h = max(1, int(w)), max(1, int(h))
                src = self.load_texture(what, transient=True)
                handle = src.handle if isinstance(src, HostTexture) else src
                return _rtt_pass(
                    w,
                    h,
                    lambda: self._dm(self._tex_pipe, self._quad_mesh, handle),
                    node=what,
                    where_tag="rtt_surfleaf",
                )

            # Render/Model tree → walk into RTT (covers mesh=True + children).
            # GL2 always prepares before RTT (gl2draw.pyx:1173+). Without this,
            # nested scene Renders used as dissolve slots draw blank/black.
            try:
                self.load_all_textures(what)
            except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                _host_draw_fail("rtt.load_all_textures", e)
            # Nested prepare may leave host frame stack dirty; drain before RTT
            # ONLY when we are not already inside a product/parent frame.
            # Mid-draw _child_to_texture → render_to_texture is common for mesh
            # containers; calling reset_frame_state there wipes the outer
            # begin_frame so every subsequent draw_model is dropped
            # ("draw_model outside begin_frame") and the game RT stays arena-clear
            # despite prepared dock HostTextures (H-Idle-A′ / AC-Idle residual).
            try:
                in_f = False
                if hasattr(renpy_host, "in_frame"):
                    try:
                        in_f = bool(renpy_host.in_frame())
                    except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        in_f = False
                if not in_f:
                    self._recover_frame_state()
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass

            w, h = self._node_size(what)
            if w <= 0 or h <= 0:
                w, h = self.virtual_size

            # hmc_fix3 F2: dest-size RTT override (shrink only). ``_draw_tree``
            # below never changes its logical coordinates — it keeps mapping the
            # child's own (w, h) virtual span onto the full NDC canvas, so the
            # smaller target rasterizes exactly the same content at dest
            # resolution. The bake is smaller only; UV / quad / stencil math is
            # untouched (vertex positions are NDC, canvas size is irrelevant).
            _sw, _sh = w, h
            if _dest_size is not None:
                try:
                    _dh_size = (int(_dest_size[0]), int(_dest_size[1]))
                except Exception:
                    _dh_size = (0, 0)
                if (
                    _dh_size[0] > 0
                    and _dh_size[1] > 0
                    and (_dh_size[0] < w or _dh_size[1] < h)
                ):
                    _sw = max(1, min(int(w), _dh_size[0]))
                    _sh = max(1, min(int(h), _dh_size[1]))

            old_vs = self.virtual_size

            def _draw_tree():
                old_clip = self._clip_rect
                old_poly = getattr(self, "_clip_poly", None)
                try:
                    self.virtual_size = (w, h)
                    # RTT-local coords: do not inherit product absolute clip AABB or polygon.
                    self._clip_rect = None
                    self._clip_poly = None
                    self._draw_node(what, 0.0, 0.0)
                finally:
                    self.virtual_size = old_vs
                    self._clip_rect = old_clip
                    self._clip_poly = old_poly

            return _rtt_pass(_sw, _sh, _draw_tree, node=what, where_tag="rtt_tree")
        except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            _host_draw_fail("render_to_texture", e)
            return what

    # --- Shader / uniform packing (named-pipeline honesty) ---------------------

__all__ = ["ScreenMixin"]
