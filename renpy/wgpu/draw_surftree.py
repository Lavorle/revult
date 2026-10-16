"""
draw_surftree — geometry / UV / clip + full traversal ownership (T5).

SurftreeMixin is now the single owner for all surftree traversal helpers:
geometry/UV/clip, texture/mesh resolution, render/surface predicates,
dissolve/reverse, child iteration, node sizing and mesh baking.
Other mixins (Traversal/Model/Walk) retain thin re-export shims for 1 version.

Usage:
    from .draw_surftree import SurftreeMixin
    class WgpuDraw(RttPoolMixin, SurftreeMixin): ...
"""
from __future__ import annotations

import hashlib
import os
import time as _time
from collections.abc import Sequence  # noqa: F401
from typing import TYPE_CHECKING

from .draw_debug import (  # noqa: F401
    _BAKE_ENABLED,
    _WALKSTAT_ON,    _UI_TRACE_LOGGED,
    _host_draw_fail,
    _phase0_due_dissolve,
    _phase0_log,
    _ui_trace_once,
    trans_bake_cache_enabled,
    trans_cache_count,
    trans_cache_why,
    bake_attn,
)
from .host_bridge import host_env_bool
from .host_texture import HostTexture
from .draw_texture import (  # RTT-handle premultiplied registry (single owner)
    register_premultiplied_handle,
    unregister_premultiplied_handle,
)

# M1 T3 grouping import (private helper; used via WgpuDraw._instance_add path)
try:
    from .host_bridge import _InstanceGroup  # noqa: F401
except Exception:
    _InstanceGroup = None  # type: ignore

if TYPE_CHECKING:
    from .host_texture import HostTexture


def _collect_handle_ints(x, out) -> None:
    """Collect positive ints (texture handles) from a nested fp bits value."""
    if x is None or x is True or x is False:
        return
    if isinstance(x, int):
        if x > 0:
            out.add(int(x))
        return
    if isinstance(x, (tuple, list)):
        for e in x:
            _collect_handle_ints(e, out)


def _subtree_movie_veto_enabled() -> bool:
    """RENPY_HOST_SUBTREE_MOVIE_VETO (default on).

    On: only subtrees that actually sample a movie-written texture are
    uncacheable (per-subtree veto). Off: legacy coarse behavior — the global
    movie publish nonce invalidates every cached subtree (A/B switch + safety
    valve).
    """
    global _SUBTREE_VETO_CELL
    v = _SUBTREE_VETO_CELL[0]
    if v is None:
        raw = os.environ.get("RENPY_HOST_SUBTREE_MOVIE_VETO", "")
        v = str(raw).strip().lower() not in ("0", "false", "no", "off")
        _SUBTREE_VETO_CELL[0] = v
    return bool(v)


def _movie_tex_handles():
    """Handles currently carrying in-place movie pixels (or None when unknown).

    ``renpy.display.video`` keeps a bounded registry of the texture handles its
    publish paths rewrite in place. A subtree bake key containing one of those
    handles is NOT static even if the render tree looks identical, so it must
    not be cached. When the registry is unavailable (hermetic gates / older
    module) callers fall back to the coarse global publish nonce.
    """
    global _MOVIE_VIDEO_MOD, _MOVIE_SEQ_FAIL
    mod = _MOVIE_VIDEO_MOD
    if mod is not None:
        fn = getattr(mod, "movie_texture_handles", None)
        if fn is None:
            return None
        try:
            return fn()
        except Exception:
            return None
    if _MOVIE_SEQ_FAIL:
        return None
    try:
        import sys as _sys

        mod = _sys.modules.get("renpy.display.video")
        if mod is None:
            from renpy.display import video as mod  # type: ignore
        _MOVIE_VIDEO_MOD = mod
    except Exception:
        _MOVIE_SEQ_FAIL = True
        return None
    fn = getattr(mod, "movie_texture_handles", None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


# =============================================================================
# Transition bake cache (dissolve old-slot cross-frame RTT content cache).
# Evidence: /tmp/hmc_childtrust.txt — a dissolve mid frame bakes BOTH slots at
# full-screen resolution; the old slot's structural fingerprint (type tree +
# deep texture/mesh handles + uniforms minus u_renpy_dissolve + matrix bits)
# is 95.7–99.7% identical across the whole transition band, so its bake result
# can be pinned once and reused. Movie frames rewrite texture CONTENT in place
# on a stable handle, so the key is stamped with the global movie publish
# sequence from renpy.display.video._MOVIE_PUBLISH_SEQ — any successful movie
# frame publish invalidates every cached bake (correct direction).
# The pinned bake target comes from renpy_host.create_render_texture directly:
# it never enters _rtt_free / frame rings / pool membership, never participates
# in gen-stamp refusal, and its lifetime is owned entirely by the cache
# (destroyed via destroy_texture on eviction / kill_textures / clear).
# =============================================================================
_MOVIE_VIDEO_MOD = None
_SUBTREE_VETO_CELL = [None]
_MOVIE_SEQ_FAIL: bool = False
# Sentinel for the depth>5 unvisited-children probe (never a real child tag).
_KIDS_END = object()
# Fingerprint walk node budget (key construction cost ceiling).
# Upper bound on how many surftree nodes one _trans_fp_node walk may visit:
# at 512 nodes the Python walk cost stays well under the ~5 ms/frame bake it
# replaces (checked on the 60s headless TC=1 band; see report). Budget
# exhaustion still raises ValueError → key=None → no cache, the conservative
# direction has not regressed — it merely becomes reachable for much larger
# real trees than the old 64-node cap allowed (HuangmeiC recovery).
_TRANS_FP_MAX_NODES = 512


def _movie_publish_seq() -> int:
    """Snapshot of the global movie publish sequence (display.video.

    lazily resolves ``renpy.display.video``; hermetic gates may inject a stub
    module into ``sys.modules`` (product path imports the real one). Import
    failure → -1 constant — the nonce then simply stays flat (same as a no
    movie context) and product behavior is unaffected.
    """
    global _MOVIE_VIDEO_MOD, _MOVIE_SEQ_FAIL
    mod = _MOVIE_VIDEO_MOD
    if mod is not None:
        try:
            return int(getattr(mod, "_MOVIE_PUBLISH_SEQ", -1))
        except Exception:
            return -1
    if _MOVIE_SEQ_FAIL:
        return -1
    try:
        import sys as _sys

        mod = _sys.modules.get("renpy.display.video")
        if mod is None:
            from renpy.display import video as mod  # type: ignore
        _MOVIE_VIDEO_MOD = mod
        return int(getattr(mod, "_MOVIE_PUBLISH_SEQ", -1))
    except Exception:
        _MOVIE_SEQ_FAIL = True
        return -1


# =============================================================================
# hmc_fix3 F3 — reverse-scale child bake cache (generalized per-frame subtree
# content cache). The ``_trans_bake_key`` / ``_trans_cache_lookup`` /
# ``_trans_cache_store`` machinery above stays the single implementation; the
# dissolve old-slot entry point (draw_walk._draw_dissolve_mid) is unchanged and
# ``_draw_reverse`` becomes the second entry point through
# ``_child_bake_cache_fetch``. All of it lives in the SAME
# ``self._trans_bake_cache`` store, so the existing kill_textures / resize /
# destroy hooks clear reverse entries too — no new clear site.
# Gate: RENPY_HOST_REVERSE_BAKE_CACHE (independent OnceLock parse; default on;
# "0"/false/off/no → disabled, byte-identical pre-cache behavior). The
# RENPY_HOST_TRANS_BAKE_CACHE gate keeps its existing semantics.
# Telemetry: draw_debug.py is outside the editable file set, so the counters
# mirror ``trans_cache_count``'s contract here and a one-shot tail hook appends
# ``rev_cache_hit=N miss=N rev_key_fail=N`` to the existing bake PHASE0 line
# without touching that line's body or cadence.
# =============================================================================
_REVERSE_BAKE_CACHE_ON: bool | None = None
# Window counters (drained into the PHASE0 line tail on each emitted line).
_REV_CACHE_HITS = 0
_REV_CACHE_MISSES = 0
_REV_CACHE_KEY_FAILS = 0
# hmc_fix4 B: misses where the private pinned RTT was NOT allocated because the
# key had not been observed before (a fresh key every frame = a churning
# subtree, which can never hit; pinning it only buys a pinned-RTT create +
# destroy per frame). The normal pooled bake runs instead — identical pixels.
_REV_CACHE_DEFERS = 0
# Lifetime totals (offline greps / hermetic gates). Mirrors trans_cache_totals.
_REV_CACHE_HIT_TOTAL = 0
_REV_CACHE_MISS_TOTAL = 0
_REV_CACHE_KEY_FAIL_TOTAL = 0
_REV_CACHE_DEFER_TOTAL = 0


def reverse_bake_cache_enabled() -> bool:
    """RENPY_HOST_REVERSE_BAKE_CACHE — OnceLock parse; default on ("1")."""
    global _REVERSE_BAKE_CACHE_ON
    v = _REVERSE_BAKE_CACHE_ON
    if v is None:
        raw = os.environ.get("RENPY_HOST_REVERSE_BAKE_CACHE", "").strip().lower()
        v = raw not in ("0", "false", "off", "no")
        _REVERSE_BAKE_CACHE_ON = v
    return v


# =============================================================================
# hmc_fix4 B — reverse pin stability gate.
#
# Evidence (headless prefs-effect run with RENPY_HOST_REVKEY_TRACE=1): the
# candidate that churned one pinned RTT per frame (render 1920x255, dest
# 806x107) is the preferences page whose subtree carries the game's live
# PreviewSayText typewriter preview: its Text is re-rendered and re-uploaded
# every frame, so the fingerprint legitimately differs every frame —
#   body.kids[0].fp.kids[2].fp.kids[0].fp.kids.n          0 -> 4 -> 2
#   ...kids[0].fp.size  (surface handle)  2801 -> 2811 -> 2832
#   ...kids[1].offset  (0.0, 91.5) -> (2.25, 2.25)
# and the key can never repeat while the preview types. The key is therefore
# NOT noisy: it correctly reports changing content, and any attempt to strip
# the handle/dest bits would serve a stale bake (wrong pixels). What IS pure
# waste is the pin: on every miss the cache allocated a private, non-pool
# pinned RTT, baked into it and LRU-evicted it on the next frame's miss
# (measured pins created == destroyed per window; 0 hits).
#
# Fix: a key must be seen at least once before (within _REV_PIN_SEEN_TTL
# product frames) before a pinned RTT is allocated for it. A first-sight key
# returns "no cache" and the caller runs the normal pooled bake — the exact
# fallback the pin path already documents for a failed pin, so pixels are
# unchanged; a stable key pays one deferred miss and then hits for as long as
# it stays stable. Gate: RENPY_HOST_REV_PIN_STABLE (default on).
# =============================================================================
_REV_PIN_SEEN: dict = {}          # reverse key -> product frame generation
_REV_PIN_SEEN_TTL = 8             # frames a first-sight key stays "recent"
_REV_PIN_STABLE_ON: bool | None = None


def rev_pin_stable_enabled() -> bool:
    """RENPY_HOST_REV_PIN_STABLE — OnceLock parse; default on ("1")."""
    global _REV_PIN_STABLE_ON
    v = _REV_PIN_STABLE_ON
    if v is None:
        raw = os.environ.get("RENPY_HOST_REV_PIN_STABLE", "").strip().lower()
        v = raw not in ("0", "false", "off", "no")
        _REV_PIN_STABLE_ON = v
    return v


def _rev_pin_seen_before(key, gen: int) -> bool:
    """True when ``key`` was already derived within the last TTL frames."""
    try:
        prev = _REV_PIN_SEEN.get(key)
        _REV_PIN_SEEN[key] = int(gen)
        if prev is not None and (int(gen) - int(prev)) <= _REV_PIN_SEEN_TTL:
            return True
        if len(_REV_PIN_SEEN) > 512:
            cut = int(gen) - _REV_PIN_SEEN_TTL
            for k in [k for k, g in _REV_PIN_SEEN.items() if int(g) < cut]:
                _REV_PIN_SEEN.pop(k, None)
        return False
    except Exception:  # noqa: BLE001 -- gate failure → cache behaves as before
        return True


def _rev_cache_count(kind: str) -> None:
    """Record one reverse-cache decision (mirrors trans_cache_count contract)."""
    global _REV_CACHE_HITS, _REV_CACHE_MISSES, _REV_CACHE_KEY_FAILS, _REV_CACHE_DEFERS
    global _REV_CACHE_HIT_TOTAL, _REV_CACHE_MISS_TOTAL, _REV_CACHE_KEY_FAIL_TOTAL
    global _REV_CACHE_DEFER_TOTAL
    try:
        if kind == "hit":
            _REV_CACHE_HITS += 1
            _REV_CACHE_HIT_TOTAL += 1
        elif kind == "miss":
            _REV_CACHE_MISSES += 1
            _REV_CACHE_MISS_TOTAL += 1
        elif kind == "key_fail":
            _REV_CACHE_KEY_FAILS += 1
            _REV_CACHE_KEY_FAIL_TOTAL += 1
        elif kind == "defer":
            _REV_CACHE_DEFERS += 1
            _REV_CACHE_DEFER_TOTAL += 1
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
        pass


def rev_cache_window_drain() -> tuple[int, int, int, int]:
    """Since-last-drain (hit, miss, key_fail, pin_deferred) window counters."""
    return (
        int(_REV_CACHE_HITS),
        int(_REV_CACHE_MISSES),
        int(_REV_CACHE_KEY_FAILS),
        int(_REV_CACHE_DEFERS),
    )


def rev_cache_totals() -> tuple[int, int, int, int]:
    """Lifetime ``(hit, miss, key_fail, pin_deferred)`` totals for gates."""
    return (
        int(_REV_CACHE_HIT_TOTAL),
        int(_REV_CACHE_MISS_TOTAL),
        int(_REV_CACHE_KEY_FAIL_TOTAL),
        int(_REV_CACHE_DEFER_TOTAL),
    )


def _rev_cache_drain_emit(now: float = 0.0) -> None:
    """Drain + emit the reverse-bake-cache counters on the PHASE0 cadence.

    hmc_fix3 F3 telemetry: ``renpy/wgpu/draw_debug.py`` owns the
    ``bakes_in_frame=`` PHASE0 line body and is outside this change's editable
    file set, so the drain rides the SAME cadence (draw_screen's
    ``bake_frame_flush`` rebind, see below) and emits its own window line in
    the same PHASE0_SIGNAL format:

        PHASE0_SIGNAL t=... rev_cache_hit=N miss=N rev_key_fail=N rev=[hit=N miss=N key_fail=N]

    (window since previous drain, then lifetime bracket totals). Mirrors the
    ``trans_cache_count`` contract; no draw_debug attribute is rebound, so
    renpy's module backup pickle contract is untouched.
    """
    global _REV_CACHE_HITS, _REV_CACHE_MISSES, _REV_CACHE_KEY_FAILS, _REV_CACHE_DEFERS
    try:
        from .draw_debug import _phase0_log
    except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
        return
    try:
        h = int(_REV_CACHE_HITS)
        m = int(_REV_CACHE_MISSES)
        kf = int(_REV_CACHE_KEY_FAILS)
        df = int(_REV_CACHE_DEFERS)
        _REV_CACHE_HITS = 0
        _REV_CACHE_MISSES = 0
        _REV_CACHE_KEY_FAILS = 0
        _REV_CACHE_DEFERS = 0
        t, ms, kf2, df2 = rev_cache_totals()
        if reverse_bake_cache_enabled():
            _phase0_log(
                "rev_cache_hit=%d miss=%d rev_key_fail=%d rev_pin_defer=%d"
                " rev=[hit=%d miss=%d key_fail=%d defer=%d]"
                % (h, m, kf, df, t, ms, kf2, df2)
            )
    except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
        pass


# =============================================================================
# hmc_fix4 A — bake-key derivation telemetry + frame-local derivation memo.
#
# Problem (independent review /tmp/hmcsv/report.txt): the subtree bake key was
# derived at the ENTRY of every code path that *might* consult the bake cache,
# before the cheap qualification tests ran. 2939 key derivations were measured
# inside 30 effect samples against 103 cache lookups — 96.5% of the fingerprint
# walks (each up to 512 nodes, depth <= 5) never reached a cache decision and
# were thrown away un-consumed.
#
# Fix: the key is now derived LAZILY, immediately before the cache lookup
# (``_child_to_texture(_trans_key_fn=...)``), so a child resolved by any cheap
# fast path (``cached_texture`` / ``_extract_host_texture`` / full-pixel solid
# slot / surface-load) never pays for a fingerprint. The counters below prove
# it and guard against a regression:
#
#   keys_derived  _trans_bake_key invocations (one fingerprint walk each = the
#                 cost the report measured; directly comparable to 2939/3014)
#   keys_none     derivations that produced no key (designed conservative miss)
#   keys_used     derived keys a cache lookup actually consumed
#   keys_wasted   derived keys NO lookup consumed in their frame — target 0
#   keys_dedup    derivations skipped by the frame-local memo (a second consult
#                 of the SAME child slot inside one product frame reuses the
#                 key derived by the first — same frame, same tree state)
#
# Window counters ride the existing bake PHASE0 line tail (registered through
# draw_debug.register_bake_line_tail, exactly like the sm_* counters, so the
# draw_debug.py line body stays untouched). The frame boundary is the same
# drained once-per-product-frame site (draw_screen.bake_frame_flush rebind ->
# ``_rev_cache_drain_emit``), so a key derived and dropped inside one frame is
# attributed to THAT frame.
# =============================================================================
_KEYS_DERIVED = 0
_KEYS_DERIVED_TOTAL = 0
_KEYS_NONE = 0
_KEYS_NONE_TOTAL = 0
_KEYS_USED = 0
_KEYS_USED_TOTAL = 0
_KEYS_WASTED = 0
_KEYS_WASTED_TOTAL = 0
_KEYS_DEDUP = 0
_KEYS_DEDUP_TOTAL = 0
# id(key) -> key: strong reference to every derived-but-not-yet-consumed key,
# so an id can never be recycled onto a different key while it is pending.
_KEYS_PENDING: dict = {}
_KEYS_HOOKS_ON: bool = False
# Frame-local derivation memo: (id(child), dest_size, kind) -> key.
# ``_KEY_MEMO_GEN`` carries the product-frame identity (``_rtt_gen``) it was
# built for; a change of generation clears it.
_KEY_MEMO: dict = {}
_KEY_MEMO_GEN: list = [None]
# Memo "absent" sentinel (a real entry value is always a key tuple).
_KEYS_MEMO_MISS = object()


def keys_window() -> tuple[int, int, int, int, int]:
    """Since-last-drain ``(derived, wasted, none, used, dedup)``."""
    return (
        int(_KEYS_DERIVED),
        int(_KEYS_WASTED),
        int(_KEYS_NONE),
        int(_KEYS_USED),
        int(_KEYS_DEDUP),
    )


def keys_totals() -> tuple[int, int, int, int, int]:
    """Lifetime totals ``(derived, wasted, none, used, dedup)`` — gates/greps."""
    return (
        int(_KEYS_DERIVED_TOTAL),
        int(_KEYS_WASTED_TOTAL),
        int(_KEYS_NONE_TOTAL),
        int(_KEYS_USED_TOTAL),
        int(_KEYS_DEDUP_TOTAL),
    )


def _keys_begin() -> None:
    """Count one ``_trans_bake_key`` invocation (the measured fingerprint cost)."""
    global _KEYS_DERIVED, _KEYS_DERIVED_TOTAL
    try:
        _KEYS_DERIVED += 1
        _KEYS_DERIVED_TOTAL += 1
    except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
        pass


def _keys_note_key(key) -> None:
    """Record one invocation outcome: ``None`` → keys_none, else → pending."""
    global _KEYS_NONE, _KEYS_NONE_TOTAL
    try:
        if key is None:
            _KEYS_NONE += 1
            _KEYS_NONE_TOTAL += 1
        else:
            _KEYS_PENDING[id(key)] = key
    except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
        pass


def _keys_rekey(old, new) -> None:
    """Move a pending registration from a base key to its namespaced form.

    ``_reverse_bake_key`` builds ``base + ("rev", dw, dh)`` — a NEW tuple — so
    the pending entry created for ``base`` must follow the object that is
    actually handed to the lookup, or the key would be miscounted as wasted.
    """
    try:
        if _KEYS_PENDING.pop(id(old), None) is not None:
            _KEYS_PENDING[id(new)] = new
    except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
        pass


def _keys_count_consumed(key) -> None:
    """Mark one pending derived key as consumed by a cache lookup."""
    global _KEYS_USED, _KEYS_USED_TOTAL
    try:
        if _KEYS_PENDING.pop(id(key), None) is not None:
            _KEYS_USED += 1
            _KEYS_USED_TOTAL += 1
    except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
        pass


def _keys_count_dedup() -> None:
    """Record one derivative skipped by the frame-local memo."""
    global _KEYS_DEDUP, _KEYS_DEDUP_TOTAL
    try:
        _KEYS_DEDUP += 1
        _KEYS_DEDUP_TOTAL += 1
    except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
        pass


def _keys_frame_flush() -> None:
    """Product-frame boundary: pending-but-unconsumed keys become wasted."""
    global _KEYS_WASTED, _KEYS_WASTED_TOTAL
    try:
        n = len(_KEYS_PENDING)
        if n:
            _KEYS_WASTED += n
            _KEYS_WASTED_TOTAL += n
            _KEYS_PENDING.clear()
    except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
        pass


def _keys_window_drain() -> None:
    """Reset the emitted window (lifetime totals are never reset)."""
    global _KEYS_DERIVED, _KEYS_NONE, _KEYS_USED, _KEYS_WASTED, _KEYS_DEDUP
    _KEYS_DERIVED = 0
    _KEYS_NONE = 0
    _KEYS_USED = 0
    _KEYS_WASTED = 0
    _KEYS_DEDUP = 0


def _keys_line_tail(drain: bool = False) -> str:
    """PHASE0 bake-line tail: ``keys_derived=… keys_wasted=…`` (peek/drain)."""
    try:
        if not (_KEYS_DERIVED or _KEYS_WASTED or _KEYS_NONE or _KEYS_DEDUP):
            return ""
        s = (
            " keys_derived=%d keys_wasted=%d keys_none=%d keys_used=%d keys_dedup=%d"
            % (
                int(_KEYS_DERIVED),
                int(_KEYS_WASTED),
                int(_KEYS_NONE),
                int(_KEYS_USED),
                int(_KEYS_DEDUP),
            )
        )
        if drain:
            _keys_window_drain()
        return s
    except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
        return ""


def _keys_install_hooks() -> None:
    """Register the PHASE0 line tail once (lazy; draw_debug is the owner)."""
    global _KEYS_HOOKS_ON
    if _KEYS_HOOKS_ON:
        return
    _KEYS_HOOKS_ON = True
    try:
        from .draw_debug import register_bake_line_tail

        register_bake_line_tail(_keys_line_tail)
    except Exception:  # noqa: BLE001, S110 -- missing hook API → counters still count
        pass


def _keys_memo(inst) -> dict:
    """Frame-local derivation memo for ``inst`` (cleared per product frame).

    Keyed on ``_rtt_gen`` (the RTT pool's per-product-frame bake generation):
    a second consult of the same child slot inside one frame returns the key
    the first consult derived — identical tree state, so the fingerprint is
    identical by construction, and the walk is paid once instead of per
    consult.
    """
    memo = _KEY_MEMO
    try:
        gen = int(getattr(inst, "_rtt_gen", 0))
    except Exception:  # noqa: BLE001 -- no gen → single-frame semantics
        gen = 0
    if _KEY_MEMO_GEN[0] != gen:
        _KEY_MEMO_GEN[0] = gen
        memo.clear()
    return memo


# =============================================================================
# hmc_fix4 B diagnostic — reverse bake key instability trace.
#
# RENPY_HOST_REVKEY_TRACE=1: for the same reverse candidate (same Python child
# object, same node type/size) on two CONSECUTIVE product frames, print the
# differing fingerprint components with both values, so the per-frame key churn
# (measured: pins/frame 0.78–1.12 = one pinned RTT created + destroyed per
# frame) is attributed to a concrete component instead of guessed at.
# Bounded: at most _REVKEY_TRACE_MAX reported candidates, 3 reports each.
# =============================================================================
_REVKEY_TRACE_ON = host_env_bool("RENPY_HOST_REVKEY_TRACE")
_REVKEY_TRACE_MAX = 24
_REVKEY_TRACE_PER_IDENT = 3
_REVKEY_TRACE_PREV: dict = {}   # (typename, id(child)) -> (frame_gen, key)
_REVKEY_SLOT_PREV: dict = {}    # (class,w,h,dw,dh) -> (frame_gen, key)
_REVKEY_TRACE_N: dict = {}      # ident -> reports emitted
_REVKEY_TRACE_STAT: dict = {}   # ident -> [stable, unstable]
_REVKEY_TRACE_SEQ: dict = {}    # ident -> generations observed
_REVKEY_CUR_SET: set = set()    # keys derived in the current product frame
_REVKEY_PREV_SET: set = set()   # keys derived in the previous product frame
_REVKEY_ALL: set = set()        # every key derived in the trace window
_REVKEY_SHAPED: set = set()     # keys whose shape line was emitted
_REVKEY_CUR_GEN: list = [None]  # current product frame generation
_REVKEY_NFRAMES: list = [0]     # product frames observed with a reverse key
_KEY_FIELDS = ("ver", "nonce", "drawable", "layout", "body", "rev", "dest_w", "dest_h")
_BODY_FIELDS = ("class", "size", "matrix", "clip", "uniforms", "mesh", "tex", "kids")


def _fmt_v(v, cap: int = 90) -> str:
    try:
        s = repr(v)
    except Exception:  # noqa: BLE001 -- telemetry must never abort frames
        s = "<?>"
    return s if len(s) <= cap else s[:cap] + "…"


def _fp_entries_diff(ae, be, path, out, maxout, kids) -> None:
    """Compare two ``("k", entries)`` kid containers, tracking kid indices.

    ``kids`` grows by the kid index each time the walk descends into a child,
    so a reported difference can be re-walked on the live tree (see
    :func:`_revkey_chain`) and named ("Render(1920x255) -> Text(1261x3744)").
    """
    if len(out) >= maxout:
        return
    ea = ae[1] if isinstance(ae, tuple) and len(ae) > 1 else ()
    eb = be[1] if isinstance(be, tuple) and len(be) > 1 else ()
    if len(ea) != len(eb):
        out.append((path + ".n", len(ea), len(eb), tuple(kids)))
    if not (isinstance(ea, tuple) and isinstance(eb, tuple)):
        return
    for j in range(min(len(ea), len(eb))):
        ent_a, ent_b = ea[j], eb[j]
        pj = "%s[%d]" % (path, j)
        if not (isinstance(ent_a, tuple) and isinstance(ent_b, tuple) and len(ent_a) > 1 and len(ent_b) > 1):
            if ent_a != ent_b:
                out.append((pj, ent_a, ent_b, tuple(kids) + (j,)))
            continue
        if ent_a[0] != ent_b[0]:
            out.append((pj + ".offset", ent_a[0], ent_b[0], tuple(kids) + (j,)))
            if len(out) >= maxout:
                return
        if ent_a[1] != ent_b[1]:
            _fp_sub_diff(ent_a[1], ent_b[1], pj + ".fp", out, maxout, 0, tuple(kids) + (j,))
            if len(out) >= maxout:
                return


def _fp_sub_diff(a, b, path: str, out, maxout: int, depth: int = 0, kids=()) -> None:
    """Collect deep paths for the first ``maxout`` differences of two fp nodes."""
    if len(out) >= maxout:
        return
    if not (isinstance(a, tuple) and isinstance(b, tuple)):
        out.append((path, a, b, tuple(kids)))
        return
    if len(a) != len(b):
        out.append((path + ".len", len(a), len(b), tuple(kids)))
    for i in range(min(len(a), len(b))):
        if a[i] == b[i]:
            continue
        nm = _BODY_FIELDS[i] if (depth == 0 and i < len(_BODY_FIELDS)) else str(i)
        seg = "%s.%s" % (path, nm)
        if depth == 0 and i == 7 and isinstance(a[7], tuple) and isinstance(b[7], tuple):
            # kid container: entry-wise comparison tracks the kid index
            _fp_entries_diff(a[7], b[7], seg, out, maxout, kids)
        else:
            _fp_sub_diff(a[i], b[i], seg, out, maxout, depth + 1, kids)
        if len(out) >= maxout:
            return


def _revkey_diff(ka, kb, maxout: int = 8):
    """[(path, a, b, kid_indices)] for the differing components of two keys."""
    out = []
    try:
        n = max(len(ka), len(kb))
        for i in range(n):
            nm = _KEY_FIELDS[i] if i < len(_KEY_FIELDS) else ("#%d" % i)
            a = ka[i] if i < len(ka) else "<missing>"
            b = kb[i] if i < len(kb) else "<missing>"
            if a == b:
                continue
            if nm == "body":
                _fp_sub_diff(a, b, "body", out, maxout)
            else:
                out.append((nm, a, b, ()))
            if len(out) >= maxout:
                break
    except Exception:  # noqa: BLE001 -- diagnostic must never abort frames
        pass
    return out


def _revkey_chain(inst, child, kids, cap: int = 8) -> str:
    """Class/size chain from the candidate root down a kid-index path."""
    parts = []
    node = child
    try:
        for lvl, j in enumerate(kids):
            if lvl >= cap:
                parts.append("…")
                break
            try:
                w, h = inst._node_size(node, default=(0, 0))
            except Exception:  # noqa: BLE001
                w = h = 0
            parts.append("%s(%sx%s)#%d" % (type(node).__name__, int(w), int(h), id(node) % 100000))
            try:
                nxt = list(inst._iter_children(node))
            except Exception:  # noqa: BLE001
                nxt = []
            if j >= len(nxt):
                parts.append("!kid%d_of_%d" % (j, len(nxt)))
                return " -> ".join(parts)
            node = nxt[j][0]
        try:
            w, h = inst._node_size(node, default=(0, 0))
        except Exception:  # noqa: BLE001
            w = h = 0
        parts.append("*%s(%sx%s)#%d" % (type(node).__name__, int(w), int(h), id(node) % 100000))
    except Exception as e:  # noqa: BLE001
        parts.append("chain-err:%r" % (e,))
    return " -> ".join(parts)


def _revkey_shape(key) -> str:
    """Compact human description of a derived reverse key (first see only)."""
    try:
        body = key[4]
        f = ["%s" % (body[0],), "%sx%s" % (body[1][0], body[1][1])]
        f.append("mtx=%s" % (body[2][0] if isinstance(body[2], tuple) else body[2],))
        f.append("mesh=%s:%s" % (
            (body[5][0] if isinstance(body[5], tuple) else body[5]),
            (body[5][1] if isinstance(body[5], tuple) and len(body[5]) > 1 else "-")))
        _u = body[4]
        f.append("uni=%d" % (len(_u[1]) if isinstance(_u, tuple) and len(_u) > 1 else -1,))
        f.append("tex=%s" % (_fmt_v(body[6], 60),))
        _k = body[7]
        f.append("kids=%d" % (len(_k[1]) if isinstance(_k, tuple) and len(_k) > 1 else -1,))
        f.append("dest=%s,%s" % (key[6], key[7]))
        f.append("nonce=%s" % (key[1],))
        return " ".join(f)
    except Exception as e:  # noqa: BLE001
        return "shape-err:%r" % (e,)


def _revkey_frame_report() -> None:
    """Per-frame reverse-key set report (churn vs. fresh-candidate verdict)."""
    try:
        cur = _REVKEY_CUR_SET
        prev = _REVKEY_PREV_SET
        if not cur and not prev:
            return
        inter = len(cur & prev)
        _phase0_log(
            "[REVKEY_FRAME] gen=%d n=%d prev=%d inter=%d missing_from_now=%d"
            " n_dest=%d uniq_all=%d"
            % (
                int(_REVKEY_CUR_GEN[0]),
                len(cur),
                len(prev),
                inter,
                len(prev - cur),
                len({(k[6], k[7]) for k in cur}),
                len(_REVKEY_ALL),
            )
        )
        for k in tuple(cur)[:4]:
            if k in _REVKEY_SHAPED:
                continue
            _REVKEY_SHAPED.add(k)
            if len(_REVKEY_SHAPED) > 24:
                continue
            _phase0_log("[REVKEY_SHAPE] %s" % _revkey_shape(k))
    except Exception:  # noqa: BLE001 -- diagnostic must never abort frames
        pass


def _revkey_slot(inst, child, dest_size):
    """Slot identity: node class + box + dest box (survives object churn)."""
    try:
        _w, _h = inst._node_size(child, default=(0, 0))
    except Exception:  # noqa: BLE001
        _w = _h = 0
    _dw = int(dest_size[0]) if dest_size else -1
    _dh = int(dest_size[1]) if dest_size else -1
    return (type(child).__name__, int(_w), int(_h), _dw, _dh)


def _revkey_node_probe(inst, child, kids, cap: int = 8) -> str:
    """Full attribute dump of the nodes along a kid-index path (tracing only)."""
    try:
        mov = _movie_tex_handles()
    except Exception:  # noqa: BLE001
        mov = None
    out = []
    node = child
    try:
        lvls = [(-1, node)]
        for lvl, j in enumerate(kids):
            if lvl >= cap:
                break
            nxt = list(inst._iter_children(node))
            if j >= len(nxt):
                out.append("kid%d OUT OF RANGE (%d kids on %s)" % (j, len(nxt), type(node).__name__))
                break
            node = nxt[j][0]
            lvls.append((j, node))
        for lvl, nd in lvls:
            parts = ["L%d" % (lvl + 1), type(nd).__name__]
            for a in ("text", "texture", "textures", "mesh", "shaders", "uniforms",
                      "cached_texture", "cached_model", "nearest", "layer_name", "blits"):
                try:
                    v = getattr(nd, a, None)
                except Exception:  # noqa: BLE001
                    continue
                if v is None:
                    continue
                if a in ("texture", "textures", "cached_texture"):
                    if isinstance(v, (list, tuple)):
                        rr = []
                        for t in v:
                            tg = inst._trans_one_tex(t)
                            hh = tg[1] if isinstance(tg, tuple) and len(tg) > 1 else -1
                            rr.append("%s%s" % (tg, "|MOVIE" if (mov and hh in mov) else ""))
                        parts.append("%s=[%s]" % (a, ",".join(rr)))
                    else:
                        tg = inst._trans_one_tex(v)
                        hh = tg[1] if isinstance(tg, tuple) and len(tg) > 1 else -1
                        parts.append("%s=%s%s" % (a, _fmt_v(tg, 40),
                                                  "|MOVIE" if (mov and hh in mov) else ""))
                elif a == "mesh":
                    try:
                        parts.append("mesh=%s" % (_fmt_v(inst._trans_mesh_bits(nd), 50),))
                    except Exception:  # noqa: BLE001
                        parts.append("mesh=<?>")
                else:
                    parts.append("%s=%s" % (a, _fmt_v(v, 60)))
            try:
                parts.append("size=%r" % (inst._node_size(nd, default=(0, 0)),))
            except Exception:  # noqa: BLE001
                pass
            try:
                parts.append("nkids=%d" % (len(list(inst._iter_children(nd))),))
            except Exception:  # noqa: BLE001
                pass
            try:
                parts.append("mtx=%s" % (_fmt_v(inst._trans_matrix_bits(getattr(nd, "reverse", None)), 50),))
            except Exception:  # noqa: BLE001
                pass
            out.append(" ".join(parts))
    except Exception as e:  # noqa: BLE001
        out.append("probe-err:%r" % (e,))
    return " || ".join(out)


def _revkey_trace(inst, child, dest_size, key) -> None:
    """Diagnostic: consecutive-frame key comparison for one reverse candidate.

    Two pairings are kept because either can be the churn source:
      * OBJECT identity ``(class, id(child))`` — the same Render object,
      * SLOT identity ``(class, node box, dest box)`` — the same visual slot
        even when the Python object is recreated every frame.
    Plus a per-frame set intersection (``[REVKEY_FRAME]``) that separates
    "the same slot churns its key" from "every frame draws a fresh candidate".
    """
    if not _REVKEY_TRACE_ON or key is None:
        return
    try:
        gen = int(getattr(inst, "_rtt_gen", 0))
        if _REVKEY_CUR_GEN[0] != gen:
            _revkey_frame_report()
            _REVKEY_PREV_SET.clear()
            _REVKEY_PREV_SET.update(_REVKEY_CUR_SET)
            _REVKEY_CUR_SET.clear()
            _REVKEY_CUR_GEN[0] = gen
            _REVKEY_NFRAMES[0] += 1
        _REVKEY_CUR_SET.add(key)
        _REVKEY_ALL.add(key)

        slot = _revkey_slot(inst, child, dest_size)
        ident = (type(child).__name__, id(child))
        for tag, pair in (("slot", slot), ("obj", ident)):
            prev = (_REVKEY_SLOT_PREV if tag == "slot" else _REVKEY_TRACE_PREV).get(pair)
            store = _REVKEY_SLOT_PREV if tag == "slot" else _REVKEY_TRACE_PREV
            store[pair] = (gen, key)
            if prev is None:
                continue
            pgen, pkey = int(prev[0]), prev[1]
            if pgen == gen:
                continue
            stat = _REVKEY_TRACE_STAT.setdefault(pair, [0, 0])
            _REVKEY_TRACE_SEQ[pair] = _REVKEY_TRACE_SEQ.get(pair, 0) + 1
            if pkey == key:
                stat[0] += 1
                continue
            stat[1] += 1
            if tag == "obj" and _REVKEY_TRACE_N.get(pair, 0) == 0:
                # object-identity churn with the same key is impossible; only
                # report object pairing when the key really changed.
                pass
            nrep = _REVKEY_TRACE_N.get(pair, 0)
            if nrep >= _REVKEY_TRACE_PER_IDENT or len(_REVKEY_TRACE_N) > _REVKEY_TRACE_MAX:
                continue
            _REVKEY_TRACE_N[pair] = nrep + 1
            diffs = _revkey_diff(pkey, key)
            _phase0_log(
                "[REVKEY_DIFF] pair=%s %s slot=%r id=%d gen=%d..%d stable=%d unstable=%d cmp=%d"
                % (
                    tag, ident[0], slot, int(id(child) % 1000000), pgen, gen,
                    stat[0], stat[1], len(diffs),
                )
            )
            for path, a, b, _kids in diffs:
                _phase0_log(
                    "[REVKEY_CMP] pair=%s %s.%s prev=%s now=%s"
                    % (tag, ident[0], path, _fmt_v(a), _fmt_v(b))
                )
                if _kids:
                    _phase0_log(
                        "[REVKEY_CHAIN] kid_path=%r %s"
                        % (list(_kids), _revkey_chain(inst, child, _kids))
                    )
                    _phase0_log(
                        "[REVKEY_NODE] kid_path=%r %s"
                        % (list(_kids), _revkey_node_probe(inst, child, _kids))
                    )
            break
    except Exception:  # noqa: BLE001 -- diagnostic must never abort frames
        pass


def _revkey_trace_summary() -> str:
    """One-line stable/unstable summary for the trace run (end-of-run grep)."""
    if not _REVKEY_TRACE_ON:
        return ""
    try:
        st = sum(v[0] for v in _REVKEY_TRACE_STAT.values())
        un = sum(v[1] for v in _REVKEY_TRACE_STAT.values())
        return ("frames=%d pairs=%d stable=%d unstable=%d reported=%d uniq_keys=%d"
                % (int(_REVKEY_NFRAMES[0]), len(_REVKEY_TRACE_STAT), st, un,
                   len(_REVKEY_TRACE_N), len(_REVKEY_ALL)))
    except Exception:  # noqa: BLE001
        return ""


class _TransBakeUncacheable(Exception):
    """H2: subtree must not be fingerprint-cached (budget / >64-vert mesh).

    Raised inside the recursion and caught by ``_trans_bake_key`` so the
    affected key simply becomes None — no cache entry, every frame re-bakes
    through the normal path (never a stale hit).
    """


class _SmFpBudgetExceeded(_TransBakeUncacheable):
    """The stable-model-cache fingerprint hit a *frame* ceiling, not a verdict.

    Distinct from a plain ``_TransBakeUncacheable`` so the caller can tell a
    cut-off walk (says nothing about the subtree — never remembered across
    frames) from a real "this subtree can never be baked" verdict.
    """


def _trans_fp_attr(v):
    """Cache-key-safe bit for a clip-ish node attribute (best-effort)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return ("b", v)
    if isinstance(v, (int, float)):
        return ("n", round(float(v), 4))
    if isinstance(v, str):
        return ("s", v)
    try:
        n = len(v)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 -- opaque object: type tag only
        n = None
    return ("o", type(v).__name__, n)


# =============================================================================
# Stable subtree MODEL cache — display-list / pinned-RTT reuse.
#
# Problem (measured on the prefs tab-transition window): the engine already
# reuses its bake cache (bakes=0–1 per frame) but still walks the whole tree
# (722–1651 nodes/frame, 22–82 ms) because the walk itself is pure Python; the
# SDL/GL2 reference does the same walk in Cython. The fix is not a faster walk
# but *fewer* walks: a render-like subtree whose structural fingerprint is
# unchanged across frames is baked ONCE into a pinned, non-pool render texture
# and afterwards drawn as a single quad — the recursion into that subtree never
# happens again.
#
# Safety model (a miss is always allowed, a wrong pixel never is):
#  * Key completeness: the fingerprint covers exactly the state the walker
#    reads when drawing a node — type, width/height, alpha, over,
#    forward/reverse matrices, matrix_kind, xclipping/yclipping (+clip attrs),
#    the full uniforms dict (``u_renpy_dissolve`` INCLUDED: the bake applies the
#    node's own effect, so an animating dissolve uniform must invalidate it),
#    shaders, mesh (full <=64-vertex digest), texture/textures handles,
#    cached_texture/cached_model handles, color, ndc, pipeline, vertices/
#    indices, nearest, properties, operation*, cropped_width/height and the
#    child list (offsets + recursive fingerprints). Any attribute that cannot be
#    read, or whose value has a type we cannot turn into a stable bit, raises
#    ``_TransBakeUncacheable`` → no key → per-frame walk (never a stale hit).
#  * Position independence: the bake targets the node's own box and the result
#    is drawn at the node's current position, so a node that merely *moves*
#    still hits; the incoming clip context is re-applied on the quad, so it is
#    not part of the key.
#  * Eligibility: render-like node, subtree node count >= 64 (budgeted count
#    walk), box within the live surface bounds.
#  * Cross-frame stability: a key must be observed on 2 consecutive product
#    frames before the pinned RTT is reused (first frame bakes + pins, second
#    frame hits). A candidate whose key changes frame-to-frame is put on a
#    backoff list so a churning subtree never pays a bake per frame.
#  * Movie: subtrees sampling a texture the movie rewrites in place are
#    uncacheable (see _movie_tex_handles / _subtree_movie_veto_enabled), same
#    veto the dissolve/reverse bake caches use.
#  * Lifetime: entries live in their own store (``self._sm_cache``); pinned
#    RTTs come from ``renpy_host.create_render_texture_pinned`` so they never
#    enter the RTT freelist / frame rings. Eviction is LRU by BYTE budget
#    (<= _SM_BUDGET_FACTOR × drawable area × 4); evicted handles are deferred to
#    the next product frame before ``destroy_texture`` (never destroyed while
#    the current frame's frame_cmds still reference them), and kill_textures /
#    resize boundaries clear the whole store.
#
# Gate: RENPY_HOST_STATIC_MODEL_CACHE (OnceLock; **default ON** — the pinned-RTT
# re-premultiply that made it wrong for translucent subtrees is fixed by the
# straight-through-alpha RTT sampling path, see ``static_model_cache_enabled``).
# Explicit falsey ("0"/false/off/no) disables it ⇒ byte-identical per-node walk.
# Telemetry: window counters appended to the existing bake_frame_flush PHASE0
# line tail as ``sm_hit=N sm_miss=N sm_nodes_saved=N``.
# =============================================================================
_SM_MIN_NODES = 64            # eligibility floor: subtree node count (task contract)
_SM_FP_MAX_NODES = 8192       # fingerprint walk budget (key-cost ceiling)
_SM_FP_MAX_DEPTH = 24         # depth budget; deeper node WITH children → uncacheable
_SM_COUNT_MAX = 4096          # budgeted eligibility count walk ceiling
_SM_COUNT_FRAME_BUDGET = 20000  # total eligibility-count node visits per product frame
_SM_SEEN_CAP = 48             # stale-entry / churn bookkeeping table cap
_SM_BUDGET_FACTOR = 3         # byte budget = factor × drawable area × 4
_SM_FALLBACK_DRAWABLE = (2560, 1440)
_SM_UNSTABLE_BACKOFF = 45     # frames a key-churning candidate slot is skipped
_SM_NEG_TTL = 15              # frames a "small"/"uncacheable" verdict is remembered
_SM_FP_FRAME_BUDGET = 3072    # hard per-product-frame ceiling on fingerprint node visits
_SM_ACCT_FRAME_MS = 6.0       # per-frame accounting (count+fingerprint) time ceiling (ms)
_SM_FP_US_PER_NODE = 1.6      # per-node cost estimate used before a frame has samples
_SM_SMALL = -1                # _sm_count_nodes: subtree is below the eligibility floor
_SM_CHURN_STREAK = 3          # consecutive key changes before the backoff engages
# Absent-attribute sentinel: distinct from None (a real None attribute value).
_SM_ABSENT = object()

# Names the *specialised* fingerprint path (``_sm_fp_render_fast``) may assume
# are absent on the class. The fast path hard-codes the value the generic body
# would read for an absent attribute (``_SM_ABSENT`` → ``None``); a class that
# carries any of these names takes the generic path instead, so the fast body
# stays provably identical to the generic one (see ``_SM_FP_SELFCHECK``).
_SM_FAST_ABSENT = (
    "color",
    "matrixcolor",
    "position",
    "motion",
    "at",
    "textures",
    "texture",
    "ndc",
    "pipeline",
    "vertices",
    "indices",
    "xclip",
    "yclip",
    "xclip_rect",
    "yclip_rect",
)
# Empty texture-slot bits: exactly what ``_sm_tex_bits`` returns for a node with
# no ``texture``/``textures`` (two ``_sm_one_tex`` calls on absent values → 0).
_SM_TEX_EMPTY = (0, 0)
# Dev gate: recompute every fast fingerprint with the generic body and compare
# (mismatch ⇒ log + use the generic value). Off in product; costs ~2x when on.
_SM_FP_SELFCHECK = bool(os.environ.get("RENPY_HOST_SM_FP_SELFCHECK"))
_SM_FP_MISMATCH_TOTAL = 0   # dev: fast-vs-generic body mismatches under selfcheck

# Render attributes the draw path reads (present on the engine's Render cdef
# class → absence means "not a node the walker understands" → uncacheable).
_SM_RENDER_REQUIRED = (
    "alpha",
    "over",
    "forward",
    "reverse",
    "matrix_kind",
    "width",
    "height",
    "children",
    "mesh",
    "shaders",
    "uniforms",
    "xclipping",
    "yclipping",
    "nearest",
    "properties",
    "operation",
    "operation_complete",
    "operation_alpha",
    "operation_parameter",
    "cropped_width",
    "cropped_height",
    "cached_texture",
    "cached_model",
)
# Optional attributes: keyed when present (a fork/subclass that carries state
# under these names must not slip through unkeyed). This fork's Render defines
# none of them: color/matrixcolor are materialised as shaders+uniforms,
# position/motion/at are Transform/Displayable level and land in
# forward/reverse/size/child offsets (all keyed above).
_SM_RENDER_OPTIONAL = (
    "color",
    "matrixcolor",
    "position",
    "motion",
    "at",
    "textures",
    "texture",
    "ndc",
    "pipeline",
    "vertices",
    "indices",
)

_SM_RENDER_OPTIONAL_FULL = tuple(_SM_RENDER_OPTIONAL)
# Bare-handle / scalar leaf types (fast identity test in the hot path).
_SM_SCALAR_TYPES = frozenset((int, bool, float, str))
# Names that make a node "special" for the fingerprint (the walker routes them
# through the dissolve / imagedissolve / reverse bake paths). Set-membership
# inline equivalent of `_is_dissolve_node | _is_imagedissolve_node`.
_SM_EFFECT_SHADERS = frozenset(
    (
        "renpy.dissolve",
        "dissolve",
        "renpy.imagedissolve",
        "imagedissolve",
        "image_dissolve",
    )
)
_SM_EFFECT_UNIFORMS = (
    "u_renpy_dissolve",
    "u_renpy_dissolve_offset",
    "u_renpy_dissolve_multiplier",
    "u_transition",
    "u_animation",
)

_SM_CACHE_ON: bool | None = None

# Window counters (drained into the PHASE0 bake line tail).
_SM_HITS = 0
_SM_MISSES = 0
_SM_NODES_SAVED = 0
# Lifetime totals (hermetic gates / offline greps).
_SM_HIT_TOTAL = 0
_SM_MISS_TOTAL = 0
_SM_NODES_SAVED_TOTAL = 0
_SM_BAKE_TOTAL = 0
_SM_UNCACHEABLE_TOTAL = 0
_SM_KEY_FAIL_TOTAL = 0
_SM_CLEAR_TOTAL = 0
_SM_BYTES_HELD = 0
# Cost accounting (fingerprint / eligibility-count node visits + wall time).
_SM_FP_NODES = 0
_SM_CNT_NODES = 0
_SM_KEY_US = 0.0
_SM_CNT_US = 0.0
_SM_FP_NODES_TOTAL = 0
_SM_CNT_NODES_TOTAL = 0
_SM_KEY_US_TOTAL = 0.0
_SM_CNT_US_TOTAL = 0.0
# Why-histogram for conservative misses (diagnosis of "no candidates" runs).
_SM_WHY: dict[str, int] = {}
_SM_WHY_TOTAL: dict[str, int] = {}
# Live cache owners (weak refs) so the frame-boundary hook can reach them.
_SM_INSTANCES: list = []
_SM_HOOKS_ON = False


class _SmEntry:
    """One pinned static-subtree RTT (handle + geometry + LRU bookkeeping).

    ``frame`` is the last product frame in which this key was observed: a hit
    requires ``frame == current - 1`` (2 consecutive frames with the same key),
    so a single-frame blip can never be mistaken for a static subtree.
    """

    __slots__ = ("handle", "w", "h", "nbytes", "frame", "nodes")

    def __init__(self, handle, w, h, nbytes, frame, nodes):
        self.handle = int(handle)
        self.w = int(w)
        self.h = int(h)
        self.nbytes = int(nbytes)
        self.frame = int(frame)
        self.nodes = int(nodes)


def static_model_cache_enabled() -> bool:
    """RENPY_HOST_STATIC_MODEL_CACHE — OnceLock parse; **default ON**.

    Default ON is a measured verdict. Two facts back it:

    1. Correctness. The pinned-RTT round trip used to re-premultiply the baked
       content (`direct over dark bg = 140`, `through a pinned RTT = 77` for a 50%
       white quad), so a cached translucent subtree composited darker than the
       direct walk (prefs-page pills/panels: +48/255 on white areas). That is
       fixed by the straight-through-alpha RTT sampling path
       (`renpy.pma_texture` + the premultiplied-handle registry in
       ``draw_texture.py``): registered RTT handles are sampled with
       `return color;` instead of the premultiplying foot, so a cached subtree
       matches the direct walk. Effect-window A/B, settings page: on the RT
       cells that three independent OFF runs reproduce byte-identically
       (74.3% of the grid — the deterministic UI, video phase excluded),
       OFF-vs-ON MAE is 0.20/255 median (was a systematic +48/255 offset).
    2. Cost. The tree-node count inside the effect windows drops from p50 694 to
       p50 119 and the frame interval p50 from 17.1 ms to 13.1 ms.

    Disable with an explicit falsey value: RENPY_HOST_STATIC_MODEL_CACHE=0
    (also "false" / "off" / "no"). Unset, empty, or any other value → enabled.
    Disabled ⇒ byte-identical per-node walk behavior (the hook returns on the
    first byte of the cache check).
    """
    global _SM_CACHE_ON
    v = _SM_CACHE_ON
    if v is None:
        raw = os.environ.get("RENPY_HOST_STATIC_MODEL_CACHE")
        if raw is None:
            v = True
        else:
            s = str(raw).strip().lower()
            v = s not in ("0", "false", "off", "no")
        _SM_CACHE_ON = v
    return bool(v)


def _sm_cost(cnt_nodes: int, fp_nodes: int, cnt_s: float, key_s: float) -> None:
    """Accumulate cache cost accounting (eligibility count + fingerprint walk)."""
    global _SM_FP_NODES, _SM_CNT_NODES, _SM_KEY_US, _SM_CNT_US
    global _SM_FP_NODES_TOTAL, _SM_CNT_NODES_TOTAL, _SM_KEY_US_TOTAL, _SM_CNT_US_TOTAL
    try:
        _SM_FP_NODES += int(fp_nodes)
        _SM_CNT_NODES += int(cnt_nodes)
        _SM_KEY_US += float(key_s) * 1e6
        _SM_CNT_US += float(cnt_s) * 1e6
        _SM_FP_NODES_TOTAL += int(fp_nodes)
        _SM_CNT_NODES_TOTAL += int(cnt_nodes)
        _SM_KEY_US_TOTAL += float(key_s) * 1e6
        _SM_CNT_US_TOTAL += float(cnt_s) * 1e6
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort
        pass


# --- per-frame cost ledger ---------------------------------------------------
# The window counters above are throttled (0.5 s) and therefore can not answer
# "which single frame paid the accounting cost?". These slots are a per-draw,
# per-PRODUCT-FRAME ledger: ``_sm_f`` accumulates during the walk of one frame,
# ``_sm_frame_boundary`` snapshots it into ``_sm_pf`` and zeroes it, and the
# [WALKSTAT] line (draw_screen) reads ``_sm_pf`` so every frame carries its own
# eligibility-count / fingerprint / bake / liveness-probe cost. Index order is
# the contract shared with the WALKSTAT tail; keep it stable.
# UNIT CONTRACT: the float slots hold SECONDS (raw ``perf_counter`` deltas);
# the [WALKSTAT] tail converts to milliseconds when it prints them.
_SM_F_CNT_MS = 0     # float s: eligibility count walk (_sm_count_nodes)
_SM_F_FP_MS = 1      # float s: key/fingerprint walk (_sm_key)
_SM_F_BAKE_MS = 2    # float s: pinned bake + draw of a fresh entry
_SM_F_ALIVE_MS = 3   # float s: entry liveness probe on the reuse path
_SM_F_TAIL_MS = 4    # float s: gate/box/store/entry-lookup tail
_SM_F_REST_MS = 5    # float s: reserved (unwrapped residual)
_SM_F_CALLS = 6      # int: candidate slots that reached the accounting path
_SM_F_CNT_N = 7      # int: node visits inside count walks
_SM_F_FP_N = 8       # int: node visits inside fingerprint walks
_SM_F_CNT_NONE = 9   # int: candidates dropped by the eligibility floor/budget
_SM_F_KEY_NONE = 10  # int: candidates whose subtree proved uncacheable
_SM_F_HIT = 11       # int: entries served from the pinned RTT
_SM_F_MISS = 12      # int: candidates walked instead of served
_SM_F_GATE = 13      # int: candidates skipped by a churn gate (no accounting)
_SM_F_PINS = 14      # int: pinned RTTs baked this frame
_SM_F_BUDGET = 15    # int: eligibility-count budget left at frame end
_SM_F_FP_N_NONE = 16  # int: fp node visits inside walks that proved uncacheable
_SM_F_NEG = 17       # int: candidates skipped by a remembered negative verdict
_SM_F_SMALL = 18     # int: candidates proven below the eligibility floor
_SM_F_ACCT_CAP = 19  # int: candidates skipped because the frame's time ceiling hit

_SM_F_SLOTS = 20
_SM_F_FLOATS = 6


def _sm_fresh_f() -> list:
    return [0.0] * _SM_F_FLOATS + [0] * (_SM_F_SLOTS - _SM_F_FLOATS)


def _sm_reset_frame_cost(obj) -> None:
    """Zero the per-frame ledger on ``obj`` (and its snapshot slot)."""
    try:
        obj._sm_f = _sm_fresh_f()
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort
        pass


def _sm_frame_cost(obj):
    """Last completed product frame's ledger (list, index order above) or None.

    ``sum`` of the four float stages is the frame's stable-model-cache
    accounting cost; the counters tell whether the frame was a discovery burst
    (``CALLS``/``FP_N`` high) or an idle frame (all zero).
    """
    return getattr(obj, "_sm_pf", None)


def _sm_miss_count() -> None:
    """Count one stable-model-cache miss (subtree walked instead of reused)."""
    global _SM_MISSES, _SM_MISS_TOTAL
    try:
        _SM_MISSES += 1
        _SM_MISS_TOTAL += 1
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort
        pass


def sm_cache_why(reason: str) -> None:
    """Record one conservative-miss reason (diagnostics only, never raises)."""
    try:
        _SM_WHY[reason] = _SM_WHY.get(reason, 0) + 1
        _SM_WHY_TOTAL[reason] = _SM_WHY_TOTAL.get(reason, 0) + 1
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
        pass


def sm_cache_why_take() -> dict:
    """Drain the window why-histogram (top reasons go on the PHASE0 line)."""
    try:
        d = dict(_SM_WHY)
        _SM_WHY.clear()
        return d
    except Exception:  # noqa: BLE001, S110
        return {}


def sm_cache_window() -> tuple[int, int, int]:
    """Peek (hit, miss, nodes_saved) since the last drain."""
    return int(_SM_HITS), int(_SM_MISSES), int(_SM_NODES_SAVED)


def sm_cache_window_drain() -> tuple[int, int, int]:
    """Drain (hit, miss, nodes_saved) — the PHASE0 line tail calls this."""
    global _SM_HITS, _SM_MISSES, _SM_NODES_SAVED
    global _SM_FP_NODES, _SM_CNT_NODES, _SM_KEY_US, _SM_CNT_US
    h, m, n = int(_SM_HITS), int(_SM_MISSES), int(_SM_NODES_SAVED)
    _SM_HITS = 0
    _SM_MISSES = 0
    _SM_NODES_SAVED = 0
    _SM_FP_NODES = 0
    _SM_CNT_NODES = 0
    _SM_KEY_US = 0.0
    _SM_CNT_US = 0.0
    return h, m, n


def sm_cache_totals() -> tuple[int, int, int]:
    """Lifetime (hit, miss, nodes_saved) totals for hermetic gates."""
    return int(_SM_HIT_TOTAL), int(_SM_MISS_TOTAL), int(_SM_NODES_SAVED_TOTAL)


def sm_cache_diag() -> dict:
    """Lifetime diagnostics dict (gates / one-shot probes)."""
    return {
        "enabled": static_model_cache_enabled(),
        "hit": int(_SM_HIT_TOTAL),
        "miss": int(_SM_MISS_TOTAL),
        "nodes_saved": int(_SM_NODES_SAVED_TOTAL),
        "bakes": int(_SM_BAKE_TOTAL),
        "uncacheable": int(_SM_UNCACHEABLE_TOTAL),
        "key_fail": int(_SM_KEY_FAIL_TOTAL),
        "clear": int(_SM_CLEAR_TOTAL),
        "bytes_held": int(_SM_BYTES_HELD),
        "fp_nodes": int(_SM_FP_NODES_TOTAL),
        "cnt_nodes": int(_SM_CNT_NODES_TOTAL),
        "key_ms": float(_SM_KEY_US_TOTAL) / 1000.0,
        "cnt_ms": float(_SM_CNT_US_TOTAL) / 1000.0,
        "why": dict(_SM_WHY_TOTAL),
    }


def sm_cache_reset() -> None:
    """Test hook: clear lifetime diagnostics (never touches pinned handles)."""
    global _SM_HITS, _SM_MISSES, _SM_NODES_SAVED
    global _SM_HIT_TOTAL, _SM_MISS_TOTAL, _SM_NODES_SAVED_TOTAL
    global _SM_BAKE_TOTAL, _SM_UNCACHEABLE_TOTAL, _SM_KEY_FAIL_TOTAL, _SM_CLEAR_TOTAL
    _SM_HITS = _SM_MISSES = _SM_NODES_SAVED = 0
    _SM_HIT_TOTAL = _SM_MISS_TOTAL = _SM_NODES_SAVED_TOTAL = 0
    _SM_BAKE_TOTAL = _SM_UNCACHEABLE_TOTAL = _SM_KEY_FAIL_TOTAL = _SM_CLEAR_TOTAL = 0
    _SM_WHY.clear()
    _SM_WHY_TOTAL.clear()


def _sm_register(inst) -> None:
    """Track a live cache owner for the frame-boundary hook (weak, deduped)."""
    try:
        import weakref

        for ref in list(_SM_INSTANCES):
            obj = ref()
            if obj is None:
                _SM_INSTANCES.remove(ref)
            elif obj is inst:
                return
        _SM_INSTANCES.append(weakref.ref(inst))
    except Exception:  # noqa: BLE001, S110 -- instrumentation must never abort frames
        pass


def _sm_frame_boundary_hook() -> None:
    """Product-frame boundary: per-instance frame seq + deferred destroys."""
    for ref in list(_SM_INSTANCES):
        obj = ref()
        if obj is None:
            try:
                _SM_INSTANCES.remove(ref)
            except Exception:  # noqa: BLE001, S110
                pass
            continue
        try:
            obj._sm_frame_boundary()
        except Exception:  # noqa: BLE001, S110 -- must never abort a frame
            pass


def _sm_line_tail(drain: bool = False) -> str:
    """PHASE0 bake-line tail: ``sm_hit=N sm_miss=N sm_nodes_saved=N``.

    ``drain=False`` peeks (the flush may still decide to skip this window);
    ``drain=True`` resets the window counters after a line was emitted.
    """
    try:
        if not static_model_cache_enabled():
            return ""
        h, m, n = sm_cache_window()
        if not (h or m or n or _SM_FP_NODES or _SM_CNT_NODES):
            return ""
        why = sm_cache_why_take() if drain else dict(_SM_WHY)
        why_s = ""
        try:
            if why:
                parts = []
                for k, v in sorted(why.items(), key=lambda kv: (-kv[1], kv[0]))[:3]:
                    parts.append("%s=%d" % (str(k).replace(" ", "_"), v))
                why_s = " sm_why=" + ",".join(parts)
        except Exception:  # noqa: BLE001, S110
            why_s = ""
        if drain:
            sm_cache_window_drain()
        return (
            " sm_hit=%d sm_miss=%d sm_nodes_saved=%d sm_bytes=%d"
            " sm_fp_nodes=%d sm_cnt_nodes=%d sm_key_ms=%.2f sm_cnt_ms=%.2f%s"
            % (
                h,
                m,
                n,
                int(_SM_BYTES_HELD),
                int(_SM_FP_NODES),
                int(_SM_CNT_NODES),
                float(_SM_KEY_US) / 1000.0,
                float(_SM_CNT_US) / 1000.0,
                why_s,
            )
        )
    except Exception:  # noqa: BLE001, S110 -- telemetry must never abort frames
        return ""


def _sm_install_hooks() -> None:
    """Register the frame-boundary + PHASE0-tail hooks once (lazy, draw_debug)."""
    global _SM_HOOKS_ON
    if _SM_HOOKS_ON:
        return
    try:
        from .draw_debug import register_bake_line_tail, register_frame_boundary_hook

        register_frame_boundary_hook(_sm_frame_boundary_hook)
        register_bake_line_tail(_sm_line_tail)
        _SM_HOOKS_ON = True
    except Exception:  # noqa: BLE001, S110 -- missing hook API → cache still works
        pass


class SurftreeMixin:
    # Expected attributes on the concrete WgpuDraw (type hints only)
    virtual_size: tuple[int, int]  # type: ignore
    _clip_rect: object  # type: ignore
    _clip_poly: object  # type: ignore  # Optional[list[tuple[float,float]]] polygon when reverse non-identity
    _mesh_cache: dict  # type: ignore
    _mesh_cache_cap: int  # type: ignore
    _mesh_deferred_destroy: list[int]  # type: ignore

    _CLIP_BIG = 65536.0  # GL2 BIG_PIXELS — unbounded axis when only one flag set.

    def _virt_rect_to_ndc(self, x, y, w, h):
        """Virtual-pixel top-left rect → NDC axis-aligned (x0,y0,x1,y1)."""
        vw = float(self.virtual_size[0]) or 1.0
        vh = float(self.virtual_size[1]) or 1.0
        x0 = 2.0 * float(x) / vw - 1.0
        x1 = 2.0 * (float(x) + float(w)) / vw - 1.0
        # Virtual y grows downward; NDC y grows upward.
        y1 = 1.0 - 2.0 * float(y) / vh
        y0 = 1.0 - 2.0 * (float(y) + float(h)) / vh
        return x0, y0, x1, y1

    def _clip_intersect(self, a, b):
        """Intersect two AABBs (x0,y0,x1,y1). None with anything → other; empty → None."""
        if a is None:
            return b
        if b is None:
            return a
        x0 = max(float(a[0]), float(b[0]))
        y0 = max(float(a[1]), float(b[1]))
        x1 = min(float(a[2]), float(b[2]))
        y1 = min(float(a[3]), float(b[3]))
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1, y1)

    def _is_identity(self, rev, eps: float = 1e-6) -> bool:
        """Tolerance-based identity check (1e-6). Delegates to ReverseScaler for single source."""
        try:
            from .draw_walk import ReverseScaler
            return ReverseScaler.is_identity(rev, eps=eps)
        except Exception:
            if rev is None:
                return True
            try:
                a = float(getattr(rev, "xdx", 1.0) or 1.0)
                b = float(getattr(rev, "xdy", 0.0) or 0.0)
                c = float(getattr(rev, "ydx", 0.0) or 0.0)
                d = float(getattr(rev, "ydy", 1.0) or 1.0)
                tx = float(getattr(rev, "xdw", 0.0) or 0.0)
                ty = float(getattr(rev, "ydw", 0.0) or 0.0)
                return abs(a - 1.0) < eps and abs(d - 1.0) < eps and abs(b) < eps and abs(c) < eps and abs(tx) < eps and abs(ty) < eps
            except Exception:
                return False

    def _transform_quad(self, quad: list[tuple[float, float]], rev, ox: float = 0.0, oy: float = 0.0) -> list[tuple[float, float]]:
        """Transform a quad (4 points) by the inverse of rev (forward matrix).

        Reuses draw_walk.ReverseScaler.inverse_matrix for single source of truth.
        If rev is identity or inversion fails, returns quad unchanged.
        """
        if quad is None or rev is None or self._is_identity(rev):
            return list(quad) if quad is not None else []
        try:
            from .draw_walk import ReverseScaler
            fwd = ReverseScaler.inverse_matrix(rev)
            if fwd is None:
                return list(quad)
            out: list[tuple[float, float]] = []
            for (x, y) in quad:
                try:
                    # fwd may be Matrix with transform(x,y) or our light _Inv
                    if hasattr(fwd, "transform"):
                        nx, ny = fwd.transform(float(x), float(y))
                    else:
                        # fallback manual
                        nx = float(x) * float(getattr(fwd, "xdx", 1.0)) + float(y) * float(getattr(fwd, "xdy", 0.0)) + float(getattr(fwd, "xdw", 0.0))
                        ny = float(x) * float(getattr(fwd, "ydx", 0.0)) + float(y) * float(getattr(fwd, "ydy", 1.0)) + float(getattr(fwd, "ydw", 0.0))
                    out.append((float(nx), float(ny)))
                except Exception:
                    out.append((float(x), float(y)))
            return out
        except Exception:
            return list(quad)

    def _aabb_to_poly(self, aabb) -> list[tuple[float, float]] | None:
        if aabb is None:
            return None
        try:
            x0, y0, x1, y1 = aabb
            return [(float(x0), float(y0)), (float(x1), float(y0)), (float(x1), float(y1)), (float(x0), float(y1))]
        except Exception:
            return None

    def _poly_area(self, poly: list[tuple[float, float]]) -> float:
        if poly is None or len(poly) < 3:
            return 0.0
        area = 0.0
        n = len(poly)
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            area += float(x1) * float(y2) - float(x2) * float(y1)
        return abs(area) * 0.5

    def _intersect_poly(self, a: list[tuple[float, float]] | None, b: list[tuple[float, float]] | None) -> list[tuple[float, float]] | None:
        """Convex polygon intersection (Sutherland–Hodgman). None with anything -> other. Empty -> None."""
        if a is None:
            return list(b) if b is not None else None
        if b is None:
            return list(a) if a is not None else None
        if not a or not b:
            return None
        # Ensure copy
        subject = [ (float(x), float(y)) for x, y in a ]
        clip = [ (float(x), float(y)) for x, y in b ]
        if len(subject) < 3 or len(clip) < 3:
            return None
        # Sutherland–Hodgman: clip subject against each edge of clip (assume clip is convex CCW)
        def _inside(px, py, ax, ay, bx, by) -> bool:
            # cross (b-a) x (p-a) >= -eps  -> inside for CCW
            return (float(bx) - float(ax)) * (float(py) - float(ay)) - (float(by) - float(ay)) * (float(px) - float(ax)) >= -1e-6
        def _intersection(ax, ay, bx, by, cx, cy, dx, dy):
            # Line AB intersect CD
            denom = (float(ax) - float(bx)) * (float(cy) - float(dy)) - (float(ay) - float(by)) * (float(cx) - float(dx))
            if abs(denom) < 1e-9:
                return ((float(ax) + float(bx)) * 0.5, (float(ay) + float(by)) * 0.5)
            px = ((float(ax) * float(by) - float(ay) * float(bx)) * (float(cx) - float(dx)) - (float(ax) - float(bx)) * (float(cx) * float(dy) - float(cy) * float(dx))) / denom
            py = ((float(ax) * float(by) - float(ay) * float(bx)) * (float(cy) - float(dy)) - (float(ay) - float(by)) * (float(cx) * float(dy) - float(cy) * float(dx))) / denom
            # round to 0.01 grid like GL2 polygon
            px = round(px + 0.005, 2) - ( (round(px + 0.005, 2) % 0.01) if False else 0)
            # Simplified: just return px,py without grid snap for stability
            return (float(px), float(py))
        output = subject
        for i in range(len(clip)):
            if not output:
                return None
            input_list = output
            output = []
            ax, ay = clip[i - 1] if i > 0 else clip[-1]
            bx, by = clip[i]
            if not input_list:
                break
            # Sutherland edge loop
            prev = input_list[-1]
            prev_inside = _inside(prev[0], prev[1], ax, ay, bx, by)
            for cur in input_list:
                cur_inside = _inside(cur[0], cur[1], ax, ay, bx, by)
                if cur_inside:
                    if not prev_inside:
                        # entering: add intersection
                        ix, iy = _intersection(prev[0], prev[1], cur[0], cur[1], ax, ay, bx, by)
                        output.append((ix, iy))
                    output.append(cur)
                elif prev_inside:
                    # exiting: add intersection
                    ix, iy = _intersection(prev[0], prev[1], cur[0], cur[1], ax, ay, bx, by)
                    output.append((ix, iy))
                prev = cur
                prev_inside = cur_inside
        if not output or len(output) < 3:
            return None
        if self._poly_area(output) < 1e-6:
            return None
        return output

    def _clip_push_from_node(self, node, ox, oy):
        """
        If node has xclipping/yclipping, return (new_clip, empty).

        new_clip is the absolute virtual-pixel AABB to install for children /
        self-draw; empty is True when the intersect is vacant (caller must skip).
        When the node does not clip, returns (current, False) unchanged.

        Local clip box (GL2 parity):
          x: [0, width] if xclipping else [-BIG, +BIG]
          y: [0, height] if yclipping else [-BIG, +BIG]
        then shifted by (ox, oy) into absolute virtual coords and intersected
        with existing clip (rect or poly).

        When node.reverse is non-identity, returns a clipped polygon
        list[tuple[float,float]] of 4 points (rotated) rather than an AABB,
        preserving the AABB fast path for identity (zero overhead).
        """
        xclip = bool(getattr(node, "xclipping", False))
        yclip = bool(getattr(node, "yclipping", False))
        if not xclip and not yclip:
            # No new clip; keep current (poly preferred if present)
            cur_poly = getattr(self, "_clip_poly", None)
            if cur_poly is not None:
                return cur_poly, False
            return self._clip_rect, False

        big = self._CLIP_BIG
        try:
            nw = float(getattr(node, "width", 0) or getattr(node, "w", 0) or 0)
            nh = float(getattr(node, "height", 0) or getattr(node, "h", 0) or 0)
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame
            nw = nh = 0.0
        if nw <= 0 or nh <= 0:
            try:
                if hasattr(node, "get_size"):
                    gw, gh = node.get_size()
                    if nw <= 0:
                        nw = float(gw or 0)
                    if nh <= 0:
                        nh = float(gh or 0)
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame
                pass
        if nw <= 0:
            nw = big if not xclip else 0.0
        if nh <= 0:
            nh = big if not yclip else 0.0

        lx0 = 0.0 if xclip else -big
        ly0 = 0.0 if yclip else -big
        lx1 = float(nw) if xclip else big
        ly1 = float(nh) if yclip else big
        rev = getattr(node, "reverse", None)
        is_id = self._is_identity(rev)
        # Current clips
        cur_rect = getattr(self, "_clip_rect", None)
        cur_poly = getattr(self, "_clip_poly", None)
        if is_id:
            # AABB fast path — zero overhead
            local = (
                float(ox) + lx0,
                float(oy) + ly0,
                float(ox) + lx1,
                float(oy) + ly1,
            )
            # If current is poly, intersect poly with AABB (convert AABB to poly)
            if cur_poly is not None:
                local_poly = [(local[0], local[1]), (local[2], local[1]), (local[2], local[3]), (local[0], local[3])]
                inter = self._intersect_poly(cur_poly, local_poly)
                if inter is None:
                    return None, True
                return inter, False
            inter = self._clip_intersect(cur_rect, local)
            if inter is None:
                return None, True
            return inter, False
        else:
            # Polygon path: local quad absolute
            local_quad: list[tuple[float, float]] = [
                (float(ox) + lx0, float(oy) + ly0),
                (float(ox) + lx1, float(oy) + ly0),
                (float(ox) + lx1, float(oy) + ly1),
                (float(ox) + lx0, float(oy) + ly1),
            ]
            # Intersect in parent space before applying forward transform
            # Build parent polygon(s)
            if cur_poly is not None:
                parent_poly = cur_poly
            elif cur_rect is not None:
                parent_poly = self._aabb_to_poly(cur_rect)
            else:
                parent_poly = None
            if parent_poly is not None:
                # Intersect local quad with parent poly in parent space
                inter_parent = self._intersect_poly(parent_poly, local_quad)
                if inter_parent is None:
                    return None, True
                # Transform the intersected polygon to child space via forward
                poly = self._transform_quad(inter_parent, rev)
            else:
                # No parent clip: just transform local quad to child space
                poly = self._transform_quad(local_quad, rev)
            if poly is None or len(poly) < 3 or self._poly_area(poly) < 1e-6:
                return None, True
            return poly, False
    def _crop_virt_quad_uv(self, ox, oy, w, h, u0, v_bottom, u1, v_top):
        """
        Crop a virtual-pixel dest rect against ``self._clip_rect`` and remap UVs.

        Returns (x, y, cw, ch, u0', v_bottom', u1', v_top') or None if empty.

        UV convention matches ``_mesh_quad_ndc``: v_bottom is the larger image-v
        (bottom of virtual rect), v_top is the smaller image-v (top of virtual).
        Cropping the top edge increases oy and moves toward v_top; cropping the
        bottom edge decreases height and moves toward v_bottom.
        """
        if w <= 0 or h <= 0:
            return None
        x0 = float(ox)
        y0 = float(oy)
        x1 = float(ox) + float(w)
        y1 = float(oy) + float(h)
        clip = self._clip_rect
        if clip is None:
            return (x0, y0, float(w), float(h), float(u0), float(v_bottom), float(u1), float(v_top))

        cx0, cy0, cx1, cy1 = clip
        ix0 = max(x0, float(cx0))
        iy0 = max(y0, float(cy0))
        ix1 = min(x1, float(cx1))
        iy1 = min(y1, float(cy1))
        if ix1 <= ix0 or iy1 <= iy0:
            return None

        span_x = x1 - x0
        span_y = y1 - y0
        if span_x <= 0.0 or span_y <= 0.0:
            return None

        # Fractional edges of the crop inside the original dest rect.
        fx0 = (ix0 - x0) / span_x
        fx1 = (ix1 - x0) / span_x
        fy0 = (iy0 - y0) / span_y  # top edge (virtual y down)
        fy1 = (iy1 - y0) / span_y  # bottom edge

        u0f = float(u0)
        u1f = float(u1)
        vb = float(v_bottom)
        vt = float(v_top)
        # u interpolates left→right; v interpolates top(vt)→bottom(vb).
        nu0 = u0f + (u1f - u0f) * fx0
        nu1 = u0f + (u1f - u0f) * fx1
        nvt = vt + (vb - vt) * fy0
        nvb = vt + (vb - vt) * fy1
        return (ix0, iy0, ix1 - ix0, iy1 - iy0, nu0, nvb, nu1, nvt)

    def _remap_uv_frac(self, u0, v_bottom, u1, v_top, frac):
        """Remap UV by dest-crop fractions (fx0, fy0, fx1, fy1) from _crop_virt_quad_uv."""
        if frac is None:
            return float(u0), float(v_bottom), float(u1), float(v_top)
        fx0, fy0, fx1, fy1 = frac
        u0f = float(u0)
        u1f = float(u1)
        vb = float(v_bottom)
        vt = float(v_top)
        nu0 = u0f + (u1f - u0f) * float(fx0)
        nu1 = u0f + (u1f - u0f) * float(fx1)
        nvt = vt + (vb - vt) * float(fy0)
        nvb = vt + (vb - vt) * float(fy1)
        return nu0, nvb, nu1, nvt

    def _mesh_quad_ndc(
        self,
        x0=-1.0,
        y0=-1.0,
        x1=1.0,
        y1=1.0,
        color=(1.0, 1.0, 1.0, 1.0),
        u0=0.0,
        v0=1.0,
        u1=1.0,
        v1=0.0,
    ):
        """Upload an axis-aligned NDC quad (pos.xy, uv.xy, color.rgba).

        UV convention (full texture default): bottom v=1, top v=0 so virtual-y
        down maps to image-y down when the host samples with top-left origin.
        Subsurface callers pass u0/u1/v0/v1 covering only the rect.

        Results are cached by quantized geometry so identical quads (common for
        full-screen and button rects) reuse one mesh handle instead of leaking
        create_mesh allocations every draw.
        """
        import renpy_host  # type: ignore

        r, g, b, a = (list(color) + [1.0, 1.0, 1.0, 1.0])[:4]

        def _q(v, n=3):
            try:
                return round(float(v), n)
            except (TypeError, ValueError):
                return 0.0

        # Coarse color quantization: dissolve dual-draw animates vertex alpha
        # every frame; fine color keys would thrash create_mesh unbounded.
        key = (
            _q(x0),
            _q(y0),
            _q(x1),
            _q(y1),
            _q(u0),
            _q(v0),
            _q(u1),
            _q(v1),
            _q(r, 2),
            _q(g, 2),
            _q(b, 2),
            _q(a, 1),  # alpha to 0.1 steps
        )
        cached = self._mesh_cache.get(key)
        if cached is not None:
            # Host mesh FIFO can destroy handles while Python still caches them
            # (no mid-frame pin before touch_mesh/pin fix). Prefer alive reuse;
            # drop dead entries so we recreate rather than draw a missing mesh
            # id (encode_pass skip → prefs hover chrome holes).
            alive = True
            try:
                probe = getattr(renpy_host, "mesh_alive", None)
                if probe is not None:
                    try:
                        alive = bool(probe(int(cached)))
                    except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        # Probe exists but failed — treat as dead and recreate
                        # rather than risk encode_pass skip of a bad handle.
                        alive = False
            except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                alive = True  # binding absent / import path
            if alive:
                try:
                    touch = getattr(renpy_host, "touch_mesh", None)
                    if touch is not None:
                        touch(int(cached))
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
                return cached
            self._mesh_cache.pop(key, None)

        verts = [
            float(x0), float(y0), float(u0), float(v0), float(r), float(g), float(b), float(a),
            float(x1), float(y0), float(u1), float(v0), float(r), float(g), float(b), float(a),
            float(x1), float(y1), float(u1), float(v1), float(r), float(g), float(b), float(a),
            float(x0), float(y1), float(u0), float(v1), float(r), float(g), float(b), float(a),
        ]
        handle = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
        # Cap cache via GpuHandleCache single-point eviction; on overflow the
        # LRU key is evicted by the cache itself. Do NOT destroy_mesh mid-frame —
        # the handle may already be in host frame_cmds from an earlier leaf in
        # this same draw_screen walk (dialog_config dense tree residual). Queue
        # the evicted handle for destroy after end_frame_present instead.
        if len(self._mesh_cache) > self._mesh_cache._cap:
            try:
                old_key, old_h = next(iter(self._mesh_cache.items()))
                self._mesh_cache.pop(old_key, None)
                self._mesh_deferred_destroy.append(int(old_h))
            except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass
        self._mesh_cache.set(key, handle)
        return handle

    def _flush_deferred_meshes(self):
        """Destroy mesh handles queued during mid-frame cache eviction.

        Must run only after ``end_frame_present`` has drained ``frame_cmds``;
        earlier destroy would make encode_pass skip draw cmds that still hold
        those mesh ids (dense dialog_config panel residual).
        """
        try:
            pending = self._mesh_deferred_destroy
        except AttributeError:
            self._mesh_deferred_destroy = []
            return
        if not pending:
            return
        self._mesh_deferred_destroy = []
        try:
            import renpy_host  # type: ignore

            destroy = getattr(renpy_host, "destroy_mesh", None)
            if destroy is None:
                return
            for h in pending:
                try:
                    destroy(int(h))
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass

    def _host_tex_uv(self, ht: HostTexture):
        """Mesh UVs (u0, v_bottom, u1, v_top) for a HostTexture sub-rect.

        Full texture (x,y,w,h)=(0,0,width,height) → (0, 1, 1, 0).
        """
        pw = float(ht.width) or 1.0
        ph = float(ht.height) or 1.0
        u0 = float(ht.x) / pw
        u1 = float(ht.x + ht.w) / pw
        # Bottom of image rect → higher v (matches full-quad v0=1).
        v_bottom = float(ht.y + ht.h) / ph
        v_top = float(ht.y) / ph
        return u0, v_bottom, u1, v_top

    def _host_tex_is_full(self, ht: HostTexture) -> bool:
        return (
            ht.x == 0
            and ht.y == 0
            and ht.w == ht.width
            and ht.h == ht.height
        )

    def _resolve_texture(self, tex):
        """Normalize texture-like values to a host handle (int) or None."""
        ht = self._resolve_texture_full(tex)  # type: ignore[attr-defined]
        return ht.handle if ht is not None else None

    def _resolve_texture_full(self, tex):
        """Normalize texture-like values to HostTexture or None (keeps UV rect).

        Also runs present-path dead-handle recovery (class b) so surftree-held
        HostTextures whose GPU slots were destroyed still draw when pixel stash
        has the full-texture RGBA. Does not re-enter load_texture cache-miss.
        """
        if tex is None:
            return None
        # Local import to avoid circular at module load
        from .host_texture import HostTexture

        if isinstance(tex, HostTexture):
            if tex.handle <= 0:
                return None
            return self._ensure_host_texture_alive(tex)  # type: ignore[attr-defined]
        if isinstance(tex, int) and not isinstance(tex, bool):
            # Bare handle: unknown size; HostTexture(1x1 full) → UV 0–1.
            if tex <= 0:
                return None
            ht = HostTexture(tex, 1, 1)
            return self._ensure_host_texture_alive(ht)  # type: ignore[attr-defined]
        # Nested object with .handle / .texture
        for attr in ("handle", "texture"):
            inner = getattr(tex, attr, None)
            if isinstance(inner, HostTexture):
                if inner.handle <= 0:
                    return None
                return self._ensure_host_texture_alive(inner)  # type: ignore[attr-defined]
            if isinstance(inner, int) and not isinstance(inner, bool) and inner > 0:
                ht = HostTexture(inner, 1, 1)
                return self._ensure_host_texture_alive(ht)  # type: ignore[attr-defined]
        # Surface-like → upload (HostTexture or raw handle)
        if hasattr(tex, "get_size") or hasattr(tex, "_pixels"):
            h = self.load_texture(tex, transient=True)  # type: ignore[attr-defined]
            if isinstance(h, HostTexture):
                return h if h.handle > 0 else None
            if isinstance(h, int) and not isinstance(h, bool) and h > 0:
                try:
                    w, hh = tex.get_size()
                    return HostTexture(h, max(1, int(w)), max(1, int(hh)))
                except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    return HostTexture(h, 1, 1)
        return None

    def _resolve_mesh(self, node, x0=-1.0, y0=-1.0, x1=1.0, y1=1.0, color=None, uv=None):
        """
        Resolve mesh from a Model-like node.

        Accepts:
          - int handle
          - MeshData (vertices / indices attrs)
          - True / "quad" → unit or rect NDC quad
          - node.vertices (+ optional node.indices)

        ``uv`` is optional (u0, v_bottom, u1, v_top) for HostTexture sub-rects.
        Explicit node.vertices always win (caller-supplied UVs preserved).
        """
        import renpy_host  # type: ignore

        mesh = getattr(node, "mesh", None) if not isinstance(node, (int, float)) else None

        # Explicit vertices on the node win for one-shot models.
        verts = getattr(node, "vertices", None) if not isinstance(node, (int, float)) else None
        if verts is not None:
            idx = getattr(node, "indices", None)
            return renpy_host.create_mesh(list(verts), list(idx) if idx is not None else None)

        # bool is a subclass of int — treat True/"quad"/None as "make a rect quad"
        # before accepting integer mesh handles.
        if mesh is True or mesh == "quad" or mesh is None:
            col = color
            if col is None and not isinstance(node, (int, float, bool)):
                col = getattr(node, "color", None)
            if col is None:
                col = (1.0, 1.0, 1.0, 1.0)
            u0, v0, u1, v1 = (0.0, 1.0, 1.0, 0.0) if uv is None else uv
            # Prefer cached full-window unit quad when covering NDC fully white + full UV.
            if (
                x0 == -1.0
                and y0 == -1.0
                and x1 == 1.0
                and y1 == 1.0
                and tuple(col)[:4] == (1.0, 1.0, 1.0, 1.0)
                and (u0, v0, u1, v1) == (0.0, 1.0, 1.0, 0.0)
            ):
                self._ensure_pipes()  # type: ignore[attr-defined]
                return self._quad_mesh  # type: ignore[attr-defined]
            return self._mesh_quad_ndc(x0, y0, x1, y1, col, u0, v0, u1, v1)

        if isinstance(mesh, int) and not isinstance(mesh, bool) and mesh > 0:
            return mesh

        # MeshData-like (host MeshData / future pure-Python meshes).
        if mesh is not None and hasattr(mesh, "vertices"):
            idx = getattr(mesh, "indices", None)
            return renpy_host.create_mesh(
                list(mesh.vertices), list(idx) if idx is not None else None
            )

        # Cython Mesh2 from Model.render (texture_rectangle / rectangle): no
        # ``vertices`` attr — exposes get_points() / points / triangles. Product
        # Model multi-tex (HuangmeiC dissolve_transform) stores Mesh2 on the
        # Render; treat it as a full-UV NDC quad at the already-computed rect.
        if mesh is not None and (
            hasattr(mesh, "get_points") or hasattr(mesh, "points")
        ):
            col = color
            if col is None and not isinstance(node, (int, float, bool)):
                col = getattr(node, "color", None)
            if col is None:
                col = (1.0, 1.0, 1.0, 1.0)
            u0, v0, u1, v1 = (0.0, 1.0, 1.0, 0.0) if uv is None else uv
            return self._mesh_quad_ndc(x0, y0, x1, y1, col, u0, v0, u1, v1)

        return None




    # --- GL2-parity load_all_textures prepass ---------------------------------

    def _is_render_like(self, n):
        """True for Render-like nodes (have both children and mesh attrs)."""
        return hasattr(n, "children") and hasattr(n, "mesh")

    def _is_surface_like(self, n):
        """True for Surface-like pixel sources.

        Render also has get_size — callers must check ``_is_render_like`` first.
        """
        return hasattr(n, "get_size") and (
            hasattr(n, "_pixels")
            or hasattr(n, "get_buffer")
            or hasattr(n, "get_at")
        )

    def _is_imagedissolve_node(self, node):
        """True when a node is renpy.imagedissolve (3-tex control/bottom/top).

        Also matches product alias ``image_dissolve`` (HuangmeiC dissolve_transform).
        """
        if node is None:
            return False
        shaders = getattr(node, "shaders", None) or ()
        if any(
            s in ("renpy.imagedissolve", "imagedissolve", "image_dissolve")
            for s in shaders
        ):
            return True
        uniforms = getattr(node, "uniforms", None)
        if isinstance(uniforms, dict) and (  # noqa: SIM102 -- collapsed nested if would obscure product alias comment
            "u_renpy_dissolve_offset" in uniforms
            or "u_renpy_dissolve_multiplier" in uniforms
            # Product alias uniforms (HuangmeiC image_dissolve).
            # Either key alone suffices: ATL animates one uniform per
            # keyframe, and sibling sites (draw_pipeline.py,
            # draw_walk.py, _extract_host_texture below) all use OR.
            or ("u_transition" in uniforms or "u_animation" in uniforms)
        ):
            # ImageDissolve stamps offset/multiplier; plain Dissolve uses u_renpy_dissolve.
            if "u_renpy_dissolve" not in uniforms:
                return True
        op = getattr(node, "operation", None)
        if op in ("IMAGEDISSOLVE", "imagedissolve"):
            return True
        try:
            # renpy.display.render.IMAGEDISSOLVE == 2
            return int(op) == 2
        except (TypeError, ValueError):
            return False

    def _is_dissolve_node(self, node):
        """True when a Render/model should dual-draw as renpy.dissolve.

        Detects product Dissolve/Fade stamps: shader name, u_renpy_dissolve
        uniform, or operation_complete with DISSOLVE-like operation.
        ImageDissolve (3-tex) is excluded — handled by imagedissolve pipeline.
        """
        if node is None:
            return False
        if self._is_imagedissolve_node(node):
            return False
        shaders = getattr(node, "shaders", None) or ()
        if any(s in ("renpy.dissolve", "dissolve") for s in shaders):
            return True
        uniforms = getattr(node, "uniforms", None)
        if isinstance(uniforms, dict) and "u_renpy_dissolve" in uniforms:
            return True
        op_complete = getattr(node, "operation_complete", None)
        if op_complete is None:
            return False
        # operation may be an int enum (DISSOLVE=1 in render.pyx historically).
        op = getattr(node, "operation", None)
        if op is None:
            return False
        if op in ("DISSOLVE", "dissolve"):
            return True
        try:
            # renpy.display.render.DISSOLVE=1 (IMAGEDISSOLVE=2 handled above).
            return int(op) == 1
        except (TypeError, ValueError):
            return False

    def _dissolve_complete(self, node):
        """Return dissolve amount in [0,1], or None if not a dissolve amount.

        Priority:
          1. explicit ``u_renpy_dissolve`` uniform (authoritative mid-fade)
          2. ``operation_complete`` only when operation is DISSOLVE **and** the
             amount is clearly mid-range (0,1) — Render defaults complete=0.0
             on every node, so 0.0 alone is NOT "start of dissolve"
          3. shader-only ``renpy.dissolve`` shell (no uniform) → 1.0 (finished
             sticky shell; walk NEW/main_menu)

        AC-Idle: sticky renpy.dissolve with default complete=0 used to walk the
        empty OLD child → permanent arena clear while dock HostTextures lived
        under NEW.
        """
        if node is None:
            return None
        uniforms = getattr(node, "uniforms", None)
        if isinstance(uniforms, dict) and "u_renpy_dissolve" in uniforms:
            try:
                return max(0.0, min(1.0, float(uniforms["u_renpy_dissolve"])))
            except (TypeError, ValueError):
                return 1.0
        shaders = getattr(node, "shaders", None) or ()
        has_shader = any(s in ("renpy.dissolve", "dissolve") for s in shaders)
        op = getattr(node, "operation", None)
        op_c = getattr(node, "operation_complete", None)
        is_diss_op = False
        if op is not None:
            try:
                is_diss_op = op in ("DISSOLVE", "dissolve") or int(op) == 1
            except (TypeError, ValueError):
                is_diss_op = op in ("DISSOLVE", "dissolve")
        if is_diss_op and op_c is not None:
            try:
                c = float(op_c)
            except (TypeError, ValueError):
                c = None
            if c is not None:
                # Only trust mid-range amounts. Exact 0.0 is the Render default
                # and also "transition not started" — with a renpy.dissolve
                # shader still on the tree that almost always means a finished
                # sticky shell, not a real t=0 frame.
                if 0.001 < c < 0.999:
                    return max(0.0, min(1.0, c))
                if c >= 0.999:
                    return 1.0
                # c <= 0.001: fall through
        if has_shader:
            return 1.0
        return None

    def _reverse_axis_scale(self, node):
        """Return (xdx, ydy) for axis-aligned reverse, or None if not applicable.

        Used by Frame/Solid stretch and Text drawable oversample (``draw_to_virt``).
        """
        rev = getattr(node, "reverse", None)
        if rev is None:
            return None
        xdx = float(getattr(rev, "xdx", 1.0) or 1.0)
        ydy = float(getattr(rev, "ydy", 1.0) or 1.0)
        xdy = float(getattr(rev, "xdy", 0.0) or 0.0)
        ydx = float(getattr(rev, "ydx", 0.0) or 0.0)
        # Only axis-aligned scale; skip rotation/shear.
        if abs(xdy) > 1e-6 or abs(ydx) > 1e-6:
            return None
        return xdx, ydy

    def _node_needs_axis_scale(self, node, children):
        """True when node should apply reverse axis scale to children.

        Frame (imagelike) stamps ``reverse`` Matrix2D with non-identity xdx/ydy
        so source tiles fill dest border pieces. Solid uses the same path:
        ``solid_texture(10,10)`` + ``reverse = Matrix2D(W/10, 0, 0, H/10)``.

        Product Text stamps ``reverse = layout.reverse``:
        - ``draw_per_virt=1`` → IDENTITY (must never stretch typewriter mid-st)
        - ``draw_per_virt>1`` (maximize) → uniform ``1/oversample`` (drawable res)

        Both Frame/Solid and oversampled Text return True for non-identity reverse.
        Dest size must be ``child_size * |scale|`` (see draw path) — **not** always
        the full parent box. Stretching typewriter partials into the full text box
        after maximize reintroduced AC-T1 balloon glyphs.
        """
        sc = self._reverse_axis_scale(node)
        if sc is None:
            return False
        xdx, ydy = sc
        # Non-identity axis scale → apply reverse mapping.
        return abs(xdx - 1.0) > 1e-6 or abs(ydy - 1.0) > 1e-6

    def _reverse_dest_size(self, node, child, parent_size):
        """Dest size for a reverse-scaled child.

        Product reverse contracts:

        1. **Uniform scale-down** (``|xdx|<1`` and ``|ydy|<1``) — Text / image
           drawable oversample (``reverse = 1/os``), and Transform fit cover
           when the child is larger than the dest box (e.g. HuangmeiC splash
           3840×2160 under ``full_fill`` → reverse 0.5 into 1920×1080):

           - **Full** HostTexture (entire atlas UV): dest = reverse node layout
             box (``parent_size``). Main-menu overlay is a 1280×720 PNG with only
             the left 280px opaque; it must still cover the full virtual canvas
             so the strip reaches the bottom. Mapping ``child*inv_os`` when the
             texture was not re-uploaded at physical res shrank it (~853×480)
             and left a hole — user "overlay incomplete after maximize".
             Same rule covers true 2× full images under cover/oversample: a
             full 3840×2160 HostTexture under reverse 0.5 fills parent 1920×1080
             (not child*scale→1920 if parent wrong, not 3840 double-draw).

           - **Subsurface** HostTexture (typewriter mid-st): dest =
             ``child_size * |scale|`` so partial glyphs stay partial.
             **Do not** collapse this branch into parent_size — that reintroduces
             AC-T balloon after maximize (worker-3 typewriter path depends on it).

        2. **Scale-up / layout fill** (Solid 10×10→dest, Frame pieces):
           dest = reverse node layout box (``parent_size``).
        """
        sc = self._reverse_axis_scale(node)
        if sc is None:
            return parent_size
        xdx, ydy = sc
        pw, ph = int(parent_size[0]), int(parent_size[1])
        # Prefer parent layout box for scale-up / unknown.
        if not (abs(xdx) < 1.0 - 1e-6 and abs(ydy) < 1.0 - 1e-6):
            if host_env_bool("RENPY_HOST_UI_TRACE") and "reverse_branch" not in _UI_TRACE_LOGGED:
                _ui_trace_once(
                    "reverse_branch",
                    f"branch=scale_up_or_fill pw={pw} ph={ph} xdx={xdx} ydy={ydy} dw={max(1, pw)} dh={max(1, ph)}",
                )
            return max(1, pw), max(1, ph)

        # Uniform scale-down (oversample).
        ht = None
        try:
            ht = self._resolve_texture_full(child)
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            ht = None
        if ht is not None and self._host_tex_is_full(ht):
            # Full image/text line texture → fill reverse node's virtual box.
            if host_env_bool("RENPY_HOST_UI_TRACE") and "reverse_branch" not in _UI_TRACE_LOGGED:
                _ui_trace_once(
                    "reverse_branch",
                    f"branch=full_oversample pw={pw} ph={ph} xdx={xdx} ydy={ydy} "
                    f"ht=({getattr(ht, 'w', '?')},{getattr(ht, 'h', '?')}) dw={max(1, pw)} dh={max(1, ph)}",
                )
            return max(1, pw), max(1, ph)

        cw, ch = self._node_size(child, default=(0, 0))
        if cw <= 0 or ch <= 0:
            if host_env_bool("RENPY_HOST_UI_TRACE") and "reverse_branch" not in _UI_TRACE_LOGGED:
                _ui_trace_once(
                    "reverse_branch",
                    f"branch=unknown_child_fallback pw={pw} ph={ph} xdx={xdx} ydy={ydy} cw={cw} ch={ch}",
                )
            return max(1, pw), max(1, ph)
        # Partial UV (typewriter): map drawable partial → virtual partial.
        dw = max(1, round(abs(float(cw) * float(xdx))))
        dh = max(1, round(abs(float(ch) * float(ydy))))
        if host_env_bool("RENPY_HOST_UI_TRACE") and "reverse_branch" not in _UI_TRACE_LOGGED:
            _ui_trace_once(
                "reverse_branch",
                f"branch=subsurface_partial pw={pw} ph={ph} xdx={xdx} ydy={ydy} "
                f"cw={cw} ch={ch} dw={dw} dh={dh}",
            )
        return dw, dh

    # --- Full traversal ownership (T5): consolidated from Traversal/Model/Walk ---
    # These were previously scattered across draw_traversal/draw_model/draw_walk;
    # SurftreeMixin is now the single owner. Other mixins retain thin shims for
    # 1 version to keep WgpuDraw MRO stable and hermetic gates importable.

    def _extract_host_texture(self, child, depth=0):
        """Pull a pure HostTexture leaf from a subsurface/image Render without RTT."""
        if child is None or depth > 8:
            return None
        if isinstance(child, HostTexture):
            return child if child.handle > 0 else None
        if isinstance(child, int) and not isinstance(child, bool):
            return HostTexture(child, 1, 1) if child > 0 else None
        if self._rtt_cached_sample_ok(child):
            ct = getattr(child, "cached_texture", None)
            if isinstance(ct, HostTexture) and ct.handle > 0:
                return ct
            if isinstance(ct, int) and not isinstance(ct, bool) and ct > 0:
                w, h = self._node_size(child, default=(1, 1))
                return HostTexture(ct, max(1, w), max(1, h))
        if not self._is_render_like(child):
            return None
        if getattr(child, "mesh", None):
            return None
        if getattr(child, "reverse", None) is not None:
            return None
        if bool(getattr(child, "xclipping", False)) or bool(getattr(child, "yclipping", False)):
            return None
        shaders = getattr(child, "shaders", None) or ()
        if shaders:
            try:
                from renpy.wgpu.shaders import composition_mode
                if any(composition_mode(s) is None for s in shaders):
                    return None
            except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                return None
        uniforms = getattr(child, "uniforms", None)
        if isinstance(uniforms, dict) and (
            "u_renpy_matrixcolor" in uniforms
            or "u_renpy_blur_log2" in uniforms
            or "u_renpy_mask_multiplier" in uniforms
            or "u_renpy_mask_offset" in uniforms
            or "u_transition" in uniforms
            or "u_animation" in uniforms
        ):
            return None
        kids = list(self._iter_children(child))
        if len(kids) != 1:
            return None
        only, _xo, _yo = kids[0]
        return self._extract_host_texture(only, depth + 1)

    def _solid_reverse_slot_texture(self, child):
        """Solid reverse (10×10 + axis scale) → dissolve-slot HostTexture without RTT."""
        if child is None or not self._is_render_like(child):
            return None
        memo = getattr(child, "_wgpu_solid_slot", None)
        if isinstance(memo, HostTexture) and memo.handle > 0:
            return memo
        solid_node = child
        kids = list(self._iter_children(solid_node))
        if not self._node_needs_axis_scale(solid_node, kids):
            if (
                len(kids) == 1
                and not getattr(solid_node, "mesh", None)
                and getattr(solid_node, "reverse", None) is None
            ):
                only, xo, yo = kids[0]
                try:
                    if abs(float(xo)) > 1e-3 or abs(float(yo)) > 1e-3:
                        return None
                except (TypeError, ValueError):
                    return None
                if not self._is_render_like(only):
                    return None
                solid_node = only
                kids = list(self._iter_children(solid_node))
            else:
                return None
            if not self._node_needs_axis_scale(solid_node, kids):
                return None
        if len(kids) != 1:
            return None
        only, xo, yo = kids[0]
        try:
            if abs(float(xo)) > 1e-3 or abs(float(yo)) > 1e-3:
                return None
        except (TypeError, ValueError):
            return None
        if isinstance(only, HostTexture):
            leaf = only if only.handle > 0 else None
        else:
            if self._is_render_like(only) and getattr(only, "reverse", None) is not None:
                return None
            leaf = self._extract_host_texture(only)
        if leaf is None or leaf.handle <= 0:
            return None
        if not self._host_tex_is_full(leaf):
            return None
        nw, nh = self._node_size(child, default=(0, 0))
        if nw <= 0 or nh <= 0:
            return None
        try:
            child._wgpu_solid_slot = leaf  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
        return leaf

    # --- Dissolve transition old-slot bake cache ------------------------------

    _TRANS_BAKE_FP_MAX_NODES = _TRANS_FP_MAX_NODES  # module-level budget (key-cost ceiling)
    _TRANS_BAKE_CACHE_CAP = 4      # LRU pinned entries (task contract)

    def _trans_bake_key(self, child, count_uncacheable: bool = True):
        """Structural cache key for a per-frame subtree bake.

        key = ("trb1", _MOVIE_PUBLISH_SEQ, drawable_size, layout_virtual_size,
               fingerprint)
        with fingerprint = type tree (NodeClassName, depth ≤ 5, kid counts +
        child offsets) + deep texture/mesh handles + uniform bits minus
        ``u_renpy_dissolve`` + matrix2d/matrixcolor bits + node size.

        A node carrying a non-composition shader / movie-ish surface is keyed
        by its uniforms/maps; the movie publish seq covers in-place content
        rewrites. Returns None when the tree is too large to fingerprint or
        anything raised — callers treat None as cache-disabled (safe miss).

        ``count_uncacheable`` (hmc_fix3 F3): the dissolve old-slot entry point
        keeps the default True so its conservative misses still land in the
        shared ``cache_hit/miss/uncache`` PHASE0 counters; the reverse entry
        passes False (its own ``rev_*`` counters drain separately) so entry
        points never distort each other's telemetry.

        hmc_fix4 A: every invocation is counted by ``keys_derived`` (the cost
        the review measured); callers derive through this only when a cache
        decision is imminent, so ``keys_wasted`` stays ~0.
        """
        _keys_install_hooks()
        _keys_begin()
        try:
            _handles = set()
            cap = [self._TRANS_BAKE_FP_MAX_NODES, _handles]
            body = self._trans_fp_node(child, 0, cap)
            if body is None:
                _keys_note_key(None)
                return None
            drawable = tuple(int(x) for x in (getattr(self, "drawable_size", None) or (0, 0)))
            layout = tuple(int(x) for x in (getattr(self, "layout_virtual_size", None) or (0, 0)))
            # Per-subtree movie veto (coarse global nonce refined): a subtree
            # that samples a handle the movie rewrites in place can never be
            # cached; every other subtree stays cacheable even while a movie
            # plays elsewhere on screen. Registry unavailable -> legacy global
            # publication nonce (conservative, invalidates everything).
            _vids = _movie_tex_handles() if _subtree_movie_veto_enabled() else None
            if _vids is None:
                _nonce = _movie_publish_seq()
            elif _handles and (_handles & set(_vids)):
                raise _TransBakeUncacheable(
                    "trans fp movie texture inside subtree"
                )
            else:
                _nonce = 0
            _key = ("trb1", _nonce, drawable, layout, body)
            _keys_note_key(_key)
            return _key
        except _TransBakeUncacheable as e:
            # H2: conservative miss — depth/node budget exhausted or an
            # over-cap mesh; the subtree re-bakes every frame instead of
            # risking a stale pinned bake. Not a failure, a designed miss.
            if count_uncacheable:
                try:
                    trans_cache_count("uncacheable")
                except Exception:  # noqa: BLE001, S110
                    pass
            try:
                trans_cache_why(str(e))
            except Exception:  # noqa: BLE001, S110
                pass
            _keys_note_key(None)
            return None
        except Exception as e:  # noqa: BLE001 -- key failure must never abort the frame
            if count_uncacheable:
                try:
                    trans_cache_count("key_fail")
                except Exception:  # noqa: BLE001, S110
                    pass
            _host_draw_fail("trans_bake_key", e)
            _keys_note_key(None)
            return None

    def _reverse_bake_key(self, child, dest_size):
        """hmc_fix3 F3: generic helper — reverse-scale child cache key.

        Reuses the single derived-key implementation (``_trans_bake_key``)
        unchanged and namespaces the reverse entry with the dest box so two
        dest sizes of the same subtree never collide inside the shared store.
        Returns None when the subtree is unkeyable (conservative: no cache).

        hmc_fix4 A: a frame-local memo keyed by ``(id(child), dest box)`` serves
        the SECOND and later consults of the same child slot inside one product
        frame from the key the first consult derived (identical tree state, so
        identical fingerprint by construction) — the 512-node fingerprint walk
        is paid once per candidate per frame, not once per consult. Only
        non-None keys are memoized, so a key-less subtree keeps re-deriving and
        keeps counting its ``rev_key_fail`` exactly as before.
        """
        if child is None:
            return None
        try:
            if dest_size is None:
                _dw, _dh = self._node_size(child)
                if _dw <= 0 or _dh <= 0:
                    _dw, _dh = self.virtual_size
            else:
                _dw, _dh = int(dest_size[0]), int(dest_size[1])
        except Exception:  # noqa: BLE001 -- key failure must never abort the frame
            _rev_cache_count("key_fail")
            return None
        memo = _keys_memo(self)
        # The memo identity carries the instance id AND the walker geometry the
        # key is derived under (both are part of the key itself), so a second
        # WgpuDraw instance — or a resized one — can never be served a key that
        # was derived for another geometry.
        try:
            _dr = tuple(int(x) for x in (getattr(self, "drawable_size", None) or (0, 0)))
            _ly = tuple(int(x) for x in (getattr(self, "layout_virtual_size", None) or (0, 0)))
        except Exception:  # noqa: BLE001 -- geometry probe failure → plain identity
            _dr = _ly = (0, 0)
        _ident = (id(self), id(child), int(_dw), int(_dh), _dr, _ly)
        try:
            _hit = memo.get(_ident, _KEYS_MEMO_MISS)
            if _hit is not _KEYS_MEMO_MISS:
                _keys_count_dedup()
                return _hit
        except Exception:  # noqa: BLE001, S110 -- memo failure → re-derive
            memo = {}
        base = self._trans_bake_key(child, count_uncacheable=False)
        if base is None:
            _rev_cache_count("key_fail")
            return None
        try:
            _key = base + ("rev", int(_dw), int(_dh))
            _keys_rekey(base, _key)
            _revkey_trace(self, child, (_dw, _dh), _key)
            try:
                memo[_ident] = _key
            except Exception:  # noqa: BLE001, S110 -- memo is best-effort
                pass
            return _key
        except Exception:  # noqa: BLE001 -- key failure must never abort the frame
            _rev_cache_count("key_fail")
            return None

    def _child_bake_cache_fetch(self, child, key, w, h, kind="tr", dest_size=None):
        """hmc_fix3 F3: generic per-frame subtree bake content cache.

        One entry point shared by the candidates; today they are the dissolve
        old slot (legacy inline path, untouched) and ``_draw_reverse`` (this
        helper). On a derived-key hit the pinned private RTT is returned
        directly — no render_to_texture pass. On a miss the child is baked
        into a private non-pool RTT (``renpy_host.create_render_texture_pinned``
        when the host exposes it) and pinned under ``key``; a pin failure
        returns None and the normal pooled bake runs in the caller. The store
        is the SAME ``self._trans_bake_cache`` the dissolve path uses, so the
        existing ``_trans_cache_clear`` hooks (kill_textures / resize /
        destroy) evict reverse entries too — no second clear site.

        ``kind`` only selects the counter set ("tr" → shared trans counters,
        "rev" → the reverse-specific ``rev_cache_hit/miss`` window drained by
        the PHASE0 tail hook); key/lookup/store semantics are identical.
        """
        if key is None or child is None:
            return None
        _keys_count_consumed(key)
        try:
            tw, th = (int(w), int(h)) if (w and h) else self._node_size(child)
            if tw <= 0 or th <= 0:
                tw, th = self.virtual_size
            hit = self._trans_cache_lookup(key, tw, th)
            if hit is not None:
                if kind == "rev":
                    _rev_cache_count("hit")
                else:
                    trans_cache_count("hit")
                return hit
            if kind == "rev":
                _rev_cache_count("miss")
            else:
                trans_cache_count("miss")
            # hmc_fix4 B (reverse entry only): a first-sight key takes the
            # documented "pin failed" fallback — no private pinned RTT is
            # allocated, the caller's normal pooled bake runs (identical
            # pixels). Only a key that repeats within _REV_PIN_SEEN_TTL frames
            # proves the subtree is cacheable and gets pinned, so a churning
            # candidate (fresh key every frame) can never buy a per-frame
            # pinned-RTT create + destroy.
            if kind == "rev" and rev_pin_stable_enabled():
                try:
                    _gen = int(getattr(self, "_rtt_gen", 0))
                except Exception:  # noqa: BLE001 -- no generation → treat as stable
                    _gen = None
                if _gen is not None and not _rev_pin_seen_before(key, _gen):
                    _rev_cache_count("defer")
                    return None
            pinned = self._trans_cache_pinned_bake(child, key, tw, th)
            return pinned
        except Exception as e:  # noqa: BLE001 -- cache failure must not abort frame
            _host_draw_fail("child_bake_cache_fetch", e)
            return None

    def _trans_matrix_bits(self, rev):
        """Matrix2D/3D fingerprint bits: None→"n", identity→"i", else type+vals."""
        if rev is None:
            return "n"
        try:
            if self._is_identity(rev):
                return "i"
        except Exception:  # noqa: BLE001 -- wgpu host must not abort frame
            pass
        vals = []
        try:
            for attr in (
                "xdx", "xdy", "ydx", "ydy", "xdw", "ydw",
                "zdx", "zdy", "zdz", "zdw", "wdx", "wdy", "wdz", "wdw",
            ):
                v = getattr(rev, attr, None)
                if v is None:
                    continue
                vals.append(round(float(v), 5))
        except Exception:  # noqa: BLE001 -- value noise: uniform miss fallback
            vals = ["x"]
        return ("m", type(rev).__name__, tuple(vals))

    def _trans_one_tex(self, t):
        """Texture-like → handle tag or opaque type tag for the fingerprint."""
        if t is None:
            return 0
        if isinstance(t, HostTexture):
            h = int(getattr(t, "handle", 0) or 0)
            return ("ht", h) if h > 0 else 0
        if isinstance(t, int) and not isinstance(t, bool):
            return ("ht", t) if t > 0 else 0
        for attr in ("handle", "texture"):
            inner = getattr(t, attr, None)
            if isinstance(inner, HostTexture):
                h = int(getattr(inner, "handle", 0) or 0)
                return ("ht", h) if h > 0 else 0
            if isinstance(inner, int) and not isinstance(inner, bool) and inner > 0:
                return ("ht", inner)
        return ("x", type(t).__name__)

    def _trans_tex_handles(self, node, collect_cached: bool):
        """Deep texture handle bits for one node (cached_texture excluded).

        collect_cached=False at the slot child (depth 0): its cached_texture is
        the self-cycling bake handle (pool reel re-targets every frame —
        evidence hmc_childtrust §recycling) and must not churn the key.
        """
        th = []
        for attr in ("texture", "textures"):
            raw = getattr(node, attr, None)
            entries = raw if isinstance(raw, (list, tuple)) else (((raw,) if raw is not None else ()))
            for t in entries:
                if t is None:
                    th.append(0)
                    continue
                th.append(self._trans_one_tex(t))
        if collect_cached:
            ct = getattr(node, "cached_texture", None)
            th.append(self._trans_one_tex(ct) if ct is not None else 0)
        return tuple(th)

    def _trans_mesh_bits(self, node):
        """Mesh attribute fingerprint bits (mesh handles at depth ≥ 1).

        H2 (hmcreview): a full ≤64-vertex ("quad") mesh is hashed completely
        (blake2b 8-byte digest) so any vertex/color/uv drift is keyed; an
        over-cap mesh raises :class:`_TransBakeUncacheable` — the key becomes
        None and the subtree re-bakes every frame (conservative direction).
        """
        m = getattr(node, "mesh", None)
        if m is None:
            return None
        if isinstance(m, bool):
            return ("b", m)
        if isinstance(m, int) and not isinstance(m, bool):
            return ("h", int(m))
        if m == "quad":
            return "q"
        if hasattr(m, "vertices"):
            try:
                verts = list(m.vertices) or []
            except Exception:  # noqa: BLE001, S110
                verts = []
            # H2: content drift used to hide in the 16-float head; hash the
            # whole vertex blob (exact float repr → stable for identical
            # content, distinct for any change).
            if len(verts) > 64:
                # Content is effectively opaque to the key — do not cache.
                raise _TransBakeUncacheable(
                    "trans fingerprint mesh > 64 verts: not cacheable"
                )
            digest = hashlib.blake2b(digest_size=8)
            digest.update(",".join(repr(float(v)) for v in verts).encode("ascii", "replace"))
            return ("vh", len(verts), digest.hexdigest())
        if hasattr(m, "get_points") or hasattr(m, "points"):
            return ("p", type(m).__name__)
        return ("o", type(m).__name__)

    def _trans_fp_node(self, node, depth, cap):
        """Recursive fingerprint tuple; depth ≤ 5, bounded by cap[0] nodes.

        H2 (hmcreview): conservative fingerprint.
        - Depth budget exhausted (a subtree at depth > 5 exists): raise
          _TransBakeUncacheable only when that node still has children —
          their content can drift without entering the key, so the whole key
          becomes None (no cache entry, per-frame bake; never a stale bake).
          A depth>5 LEAF (scalar / terminal Render with no children) carries
          no unvisited state and must not disable the cache.
        - Node budget exhausted (cap[0] < 0): ValueError, caught by
          _trans_bake_key → None (same conservative outcome).
        - Per-layer clip state (xclipping/yclipping and any roughly-known
          clip attributes on the node) is part of the key: clipping changes
          what is actually drawn without touching handles/mats/uniforms.
        """
        if node is None or depth > 5:
            if node is not None and depth > 5:
                # H2: unvisited children test — only a depth>5 node that
                # still HAS children can hide drifting content; a terminal
                # leaf is fully represented by its own (earlier) key bits.
                try:
                    first_kid = next(iter(self._iter_children(node)), _KIDS_END)
                    has_kids = first_kid is not _KIDS_END
                    kid_desc = type(first_kid).__name__ if has_kids else "-"
                except Exception:  # noqa: BLE001 -- probe failure → conservative miss
                    has_kids = True
                    kid_desc = "!probe"
                if has_kids:
                    raise _TransBakeUncacheable(
                        f"trans fp deep node d={depth} {type(node).__name__} kid={kid_desc}"
                    )
            return None
        if isinstance(node, bool):
            return ("b", node)
        if isinstance(node, int) and not isinstance(node, bool):
            return ("t", int(node))
        if isinstance(node, float):
            return ("f", round(node, 4))
        if isinstance(node, str):
            return ("s", node)
        if isinstance(node, HostTexture):
            return self._trans_one_tex(node)
        cap[0] -= 1
        if cap[0] < 0:
            raise ValueError("trans fingerprint node budget exceeded")
        parts = [type(node).__name__]
        try:
            nw, nh = self._node_size(node, default=(0, 0))
        except Exception:  # noqa: BLE001, S110
            nw = nh = 0
        parts.append((int(nw), int(nh)))
        parts.append(self._trans_matrix_bits(getattr(node, "reverse", None)))
        # H2: clip state per layer (read the same attributes the walk's
        # _clip_push_from_node consults, plus defensively-known clip variants).
        try:
            xc = bool(getattr(node, "xclipping", False))
            yc = bool(getattr(node, "yclipping", False))
            cattrs = None
            if xc or yc:
                cattrs = tuple(
                    _trans_fp_attr(getattr(node, a, None))
                    for a in ("xclip", "yclip", "xclip_rect", "yclip_rect")
                )
            parts.append(("c", xc, yc, cattrs))
        except Exception:  # noqa: BLE001 -- clip probe failure → force miss
            raise _TransBakeUncacheable(
                "trans fingerprint clip-state probe failed"
            ) from None
        uniforms = getattr(node, "uniforms", None)
        if isinstance(uniforms, dict):
            ub = []
            for k in sorted(uniforms):
                if k == "u_renpy_dissolve":
                    continue
                v = uniforms[k]
                if isinstance(v, float):
                    ub.append((k, round(v, 4)))
                elif isinstance(v, int) and not isinstance(v, bool):
                    ub.append((k, int(v)))
                else:
                    ub.append((k, str(v)))
            parts.append(("u", tuple(ub)))
        else:
            parts.append(None)
        parts.append(self._trans_mesh_bits(node))
        # Evidence: exclude the slot child's (depth-0) texture set + the
        # self-cycling cached_texture bake handle everywhere.
        _th = self._trans_tex_handles(node, depth > 0)
        parts.append(_th)
        # Per-subtree movie veto input: every live handle this subtree samples.
        try:
            _sink = cap[1] if len(cap) > 1 else None
            if _sink is not None:
                for _e in _th:
                    _collect_handle_ints(_e, _sink)
        except Exception:  # noqa: BLE001, S110 -- veto input is best-effort
            pass
        kids = []
        for knd, kx, ky in self._iter_children(node):
            cap[0] -= 1
            if cap[0] < 0:
                raise ValueError("trans fingerprint node budget exceeded")
            try:
                off = (round(float(kx), 3), round(float(ky), 3))
            except (TypeError, ValueError):
                off = None
            kids.append((off, self._trans_fp_node(knd, depth + 1, cap)))
        parts.append(("k", tuple(kids)))
        return tuple(parts)

    def _trans_cache_state(self):
        st = getattr(self, "_trans_bake_cache", None)
        if st is None:
            st = {}
            self._trans_bake_cache = st  # key → (handle, w, h, drawable, layout)
        pend = getattr(self, "_trans_bake_cache_pending", None)
        if pend is None:
            pend = []
            self._trans_bake_cache_pending = pend  # destroys drained at next store
        return st, pend

    def _trans_cache_destroy_handle(self, handle) -> None:
        """Give a pinned private RTT back to the host + pool bookkeeping."""
        try:
            h = int(handle)
        except (TypeError, ValueError):
            return
        if h <= 0:
            return
        try:
            import renpy_host  # type: ignore

            renpy_host.destroy_texture(h)
        except Exception:  # noqa: BLE001, S110 -- destroy must never abort frames
            pass
        # Single destroy funnel for every pinned/private RTT (trans cache + SM
        # cache) — drop its premultiplied-handle registration here so a recycled
        # UV/texture slot can never inherit the classification.
        try:
            unregister_premultiplied_handle(h)
        except Exception:  # noqa: BLE001, S110 -- bookkeeping only
            pass
        try:
            self._pool_handles_set().discard(h)
        except Exception:  # noqa: BLE001, S110
            pass

    def _trans_cache_clear(self) -> None:
        """Destroy every pinned entry + pending destroys (kill_textures / resize)."""
        st, pend = self._trans_cache_state()
        try:
            trans_cache_count("clear")
        except Exception:  # noqa: BLE001, S110
            pass
        if st:
            for ent in st.values():
                try:
                    self._trans_cache_destroy_handle(ent[0])
                except Exception:  # noqa: BLE001, S110
                    pass
            st.clear()
        if pend:
            for h in list(pend):
                self._trans_cache_destroy_handle(h)
            pend.clear()

    def _trans_cache_lookup(self, key, w, h):
        """Return the pinned HostTexture for ``key`` when safely reusable.

        Defenses: non-pool handle (never freelist / frame rings / pool
        membership), liveness probe via renpy_host.texture_alive, and a
        per-entry drawable/layout stamp — a resize boundary drops the entry
        before content can leak across the geometry change.
        """
        st, pend = self._trans_cache_state()
        ent = st.get(key) if st else None
        if ent is None:
            return None
        handle, ew, eh, edraw, elay = ent
        # Geometry-change boundary: a stale entry can not survive a resize.
        drawable_now = getattr(self, "drawable_size", None) or (0, 0)
        layout_now = getattr(self, "layout_virtual_size", None) or (0, 0)
        try:
            dnow = (int(drawable_now[0]), int(drawable_now[1]))
            lnow = (int(layout_now[0]), int(layout_now[1]))
        except Exception:  # noqa: BLE001, S110
            return None
        if dnow != edraw or lnow != elay:
            self._trans_cache_clear()
            return None
        try:
            if (int(ew), int(eh)) != (int(w), int(h)):
                return None
        except Exception:  # noqa: BLE001, S110
            return None
        if self._rtt_hot_pool_handle(handle):
            # Pinned handles are non-pool by construction; a pool leak means
            # external state corruption — refuse + drop rather than sample it.
            self._trans_cache_clear()
            return None
        try:
            import renpy_host  # type: ignore

            alive = int(handle) > 0 and bool(renpy_host.texture_alive(int(handle)))
        except Exception:  # noqa: BLE001, S110 -- probe failure → conservative miss
            alive = False
        if not alive:
            self._trans_cache_destroy_handle(handle)
            st.pop(key, None)
            return None
        # Fresh on-use: re-insert as MRU so bounded eviction stays stable.
        st.pop(key, None)
        st[key] = ent
        return HostTexture(int(handle), int(ew), int(eh))

    def _trans_cache_store(self, key, handle, w, h) -> None:
        """Pin a freshly baked private RTT under ``key`` (LRU cap 4)."""
        st, pend = self._trans_cache_state()
        # Drain pending destroys queued from earlier frames: those handles
        # were last drawn in an already-drained product frame, so destroying
        # at the next bake keeps painter order and pool state intact
        # (never a mid-frame destroy of a handle still inside frame_cmds).
        if pend:
            for ph in list(pend):
                self._trans_cache_destroy_handle(ph)
            pend.clear()
        st.pop(key, None)
        while len(st) >= self._TRANS_BAKE_CACHE_CAP:
            try:
                ok, old = next(iter(st.items()))
            except StopIteration:
                break
            st.pop(ok, None)
            try:
                pend.append(int(old[0]))
            except Exception:  # noqa: BLE001, S110
                pass
        drawable = getattr(self, "drawable_size", None) or (0, 0)
        layout = getattr(self, "layout_virtual_size", None) or (0, 0)
        st[key] = (
            int(handle),
            int(w),
            int(h),
            (int(drawable[0]), int(drawable[1])),
            (int(layout[0]), int(layout[1])),
        )

    def _trans_cache_pinned_bake(self, child, key, w, h):
        """Old-slot bake into a PRIVATE non-pool RTT pinned under ``key``.

        Mirrors render_to_texture._rtt_pass exactly (instance-group isolation,
        parent batch flush, nested frame + full-canvas scoping, virtual/clip
        restore) except the target comes from the pinned/private RTT FFI
        (``renpy_host.create_render_texture_pinned`` when the host exposes it,
        else legacy ``create_render_texture``) — never handed to
        _acquire_rtt/_recycle_frame_rtts, never gen-stamped, never refused.
        Lifetime: pinned until eviction / clear. H3: the _grp.push→pop span is
        fully try/finally-wrapped; any exception path pops exactly once.
        """
        import renpy_host  # type: ignore

        self._ensure_pipes()  # type: ignore[attr-defined]
        if w <= 0 or h <= 0:
            w, h = self.virtual_size
        bake_attn(self, "trans_bake_pin", child, w, h)
        rtt = 0
        _grp = getattr(self, "_instance_group", None)
        # H3 (hmcreview): the _grp.push → _grp.pop span is the WHOLE scheme —
        # an early failure (create_render_texture* / begin_target / begin_frame
        # raising) previously skipped pop, pairing a child epoch against the
        # parent group and corrupting painter order until the next RTT push
        # restored it. Wrap the entire span in try/finally; the finally pop
        # must run on every exit path and must never itself raise.
        _grp_pushed = False
        try:  # instance-group epoch: push → pop strictly paired over this span
            if _grp is not None:
                try:
                    if not _grp.empty():
                        _grp.flush(self)
                    _grp.push()
                    _grp_pushed = True
                except Exception:  # noqa: BLE001, S110 -- push failure → no epoch to pop
                    _grp = None
                # Pending parent batch items precede the bake content in tree
                # order; commit them into the parent target first (painter order).
                self._flush_draw_batch()  # type: ignore[attr-defined]
            try:
                # H1: prefer the pinned/private FFI when the host exposes it —
                # the resulting RTT is excluded from arena reuse pools
                # (freelist hit / step-2 oldest-live re-target / per-size cap),
                # so a later same-size bake cannot re-target and overwrite the
                # pinned content. Missing function → keep the legacy path.
                _crt_pinned = getattr(renpy_host, "create_render_texture_pinned", None)
                if _crt_pinned is not None:
                    rtt = int(_crt_pinned(w, h))
                else:
                    rtt = int(renpy_host.create_render_texture(w, h))
                if rtt <= 0:
                    raise ValueError("trans_bake_pin: bad private RTT handle")
                # Render-texture pixels are premultiplied: classify the handle so
                # a later quad sampling it takes the passthrough pipeline.
                try:
                    register_premultiplied_handle(rtt)
                except Exception:  # noqa: BLE001, S110 -- bookkeeping only
                    pass
                # Non-pool validation (same as lookup): a pool member here
                # means external state corruption — refuse + drop rather than
                # bake into some other pass's target.
                if self._rtt_hot_pool_handle(rtt):
                    raise ValueError("trans_bake_pin: handle leaked into rtt pool")
                bw, bh = self._node_size(child)
                if bw <= 0 or bh <= 0:
                    bw, bh = self.virtual_size
                renpy_host.begin_target(rtt)
                renpy_host.begin_frame()
                old_vs = self.virtual_size
                old_clip = self._clip_rect
                old_poly = getattr(self, "_clip_poly", None)
                old_full = getattr(self, "_frame_full_canvas_hop", None)
                prev_full = getattr(self, "_frame_full_canvas", False)
                self._frame_full_canvas_hop = True
                self._frame_full_canvas = False
                try:
                    # Honest bake count: the pinned bake is a real RTT pass (so
                    # WALKSTAT bakes= stays truthful; hit frames simply skip it).
                    try:
                        self._frame_bakes = getattr(self, "_frame_bakes", 0) + 1
                    except Exception:  # noqa: BLE001, S110 -- counter is diagnostics-only
                        pass
                    try:
                        self.virtual_size = (bw, bh)
                        self._clip_rect = None
                        self._clip_poly = None
                        self._draw_node(child, 0.0, 0.0)  # type: ignore[attr-defined]
                    finally:
                        self.virtual_size = old_vs
                        self._clip_rect = old_clip
                        self._clip_poly = old_poly
                        try:
                            self._end_frame_present()  # type: ignore[attr-defined]
                        except Exception as e:  # noqa: BLE001 -- logged, frame order kept
                            _host_draw_fail("trans_bake_pin.end_frame", e)
                        try:
                            renpy_host.end_target()
                        except Exception as e:  # noqa: BLE001 -- logged
                            _host_draw_fail("trans_bake_pin.end_target", e)
                finally:
                    self._frame_full_canvas = bool(prev_full)
                    self._frame_full_canvas_hop = old_full
                self._trans_cache_store(key, rtt, int(w), int(h))
                return HostTexture(rtt, int(w), int(h))
            except Exception as e:  # noqa: BLE001 -- pin failure falls back to pooled path
                _host_draw_fail("trans_bake_pin", e)
                if rtt:
                    self._trans_cache_destroy_handle(rtt)
                    rtt = 0
                return None
        finally:
            # H3: pop runs on EVERY exit path — including the early-failure
            # returns above — restoring the parent epoch before the pooled
            # fallback bake runs. The pop itself must never raise.
            if _grp_pushed:
                try:
                    _grp.pop()
                except Exception as e:  # noqa: BLE001 -- log-only; propagate nothing
                    try:
                        _host_draw_fail("trans_bake_pin.grp_pop", e)
                    except Exception:  # noqa: BLE001, S110 -- DRAW_RAISE tolerated
                        pass

    # --- Stable subtree MODEL cache ------------------------------------------

    def _sm_state(self):
        """Lazily create + register the static-model-cache state on this draw."""
        st = getattr(self, "_sm_cache", None)
        if st is None:
            st = {}
            self._sm_cache = st              # key → _SmEntry
            self._sm_pending = []            # handles awaiting next-frame destroy
            self._sm_gate = {}               # id(node) → frame until which skipped
            self._sm_pos_gate = {}           # walk slot → frame until which skipped
            self._sm_cur_keys = {}           # id(node) → key seen this frame
            self._sm_prev_keys = {}          # id(node) → key seen last frame
            self._sm_cur_pos = {}            # walk slot → key seen this frame
            self._sm_prev_pos = {}           # walk slot → key seen last frame
            self._sm_frame = 0               # product-frame sequence
            self._sm_bytes = 0               # pinned bytes held
            self._sm_stamp = None            # (drawable, layout) geometry stamp
            self._sm_in_bake = False         # re-entrancy guard for our own bake
            self._sm_count_budget = _SM_COUNT_FRAME_BUDGET
            self._sm_fp_budget = _SM_FP_FRAME_BUDGET
            self._sm_neg = {}                # slot/nid → (until_frame, kind)
            self._sm_pol = {}                # class → fingerprint policy
            _sm_reset_frame_cost(self)
            self._sm_pf = _sm_fresh_f()
            _sm_install_hooks()
            _sm_register(self)
        return st

    def _sm_frame_boundary(self) -> None:
        """Product-frame boundary hook (draw_debug.bake_frame_flush).

        Advances the frame sequence, drains deferred destroys queued from the
        previous frame (never a mid-frame destroy of a handle the current
        frame's frame_cmds still reference) and rotates the churn-detection
        key tables.
        """
        try:
            self._sm_frame = int(getattr(self, "_sm_frame", 0)) + 1
            self._sm_count_budget = _SM_COUNT_FRAME_BUDGET
            self._sm_fp_budget = _SM_FP_FRAME_BUDGET
            # Per-frame cost ledger: publish the frame that just ended (the
            # [WALKSTAT] line for it is printed after this hook) and reset.
            _f = getattr(self, "_sm_f", None)
            if _f is not None:
                _f[_SM_F_BUDGET] = int(self._sm_count_budget)
                self._sm_pf = _f
                self._sm_f = _sm_fresh_f()
            pend = getattr(self, "_sm_pending", None)
            if pend:
                for h in list(pend):
                    self._trans_cache_destroy_handle(h)
                pend.clear()
            cur = getattr(self, "_sm_cur_keys", None)
            prev = getattr(self, "_sm_prev_keys", None)
            if prev is not None:
                prev.clear()
                if cur:
                    prev.update(cur)
            if cur:
                cur.clear()
            cur_pos = getattr(self, "_sm_cur_pos", None)
            prev_pos = getattr(self, "_sm_prev_pos", None)
            if prev_pos is not None:
                prev_pos.clear()
                if cur_pos:
                    prev_pos.update(cur_pos)
            if cur_pos:
                cur_pos.clear()
            f = int(self._sm_frame)
            for name in ("_sm_gate", "_sm_pos_gate"):
                g = getattr(self, name, None)
                if g:
                    for k in [k for k, until in g.items() if int(until) <= f]:
                        g.pop(k, None)
            neg = getattr(self, "_sm_neg", None)
            if neg:
                for k in [k for k, v in neg.items() if int(v[0]) <= f]:
                    neg.pop(k, None)
                while len(neg) > _SM_SEEN_CAP * 4:
                    try:
                        neg.pop(next(iter(neg)), None)
                    except StopIteration:
                        break
            self._sm_prune_seen()
        except Exception:  # noqa: BLE001, S110 -- boundary work must never abort
            pass

    def _sm_prune_seen(self) -> None:
        """Keep the churn tables bounded (FIFO on insertion order)."""
        try:
            for name in ("_sm_cur_keys", "_sm_cur_pos", "_sm_streak"):
                cur = getattr(self, name, None)
                if cur is not None and len(cur) > _SM_SEEN_CAP:
                    for k in list(cur)[: len(cur) - _SM_SEEN_CAP]:
                        cur.pop(k, None)
        except Exception:  # noqa: BLE001, S110
            pass

    def _sm_geometry_stamp(self):
        try:
            d = getattr(self, "drawable_size", None) or (0, 0)
            l = getattr(self, "layout_virtual_size", None) or self.virtual_size
            return (int(d[0]), int(d[1]), int(l[0]), int(l[1]))
        except Exception:  # noqa: BLE001, S110
            return None

    def _sm_budget_bytes(self) -> int:
        """LRU byte budget: <= _SM_BUDGET_FACTOR × drawable area × 4 bytes."""
        try:
            d = getattr(self, "drawable_size", None) or (0, 0)
            w, h = int(d[0]), int(d[1])
        except Exception:  # noqa: BLE001, S110
            w = h = 0
        if w <= 0 or h <= 0:
            try:
                l = getattr(self, "layout_virtual_size", None) or self.virtual_size
                w, h = int(l[0]), int(l[1])
            except Exception:  # noqa: BLE001, S110
                w = h = 0
        if w <= 0 or h <= 0:
            w, h = _SM_FALLBACK_DRAWABLE
        return max(1, int(_SM_BUDGET_FACTOR * w * h * 4))

    def _sm_count_nodes(self, node):
        """Budgeted subtree node count; only proves ``>= _SM_MIN_NODES``.

        Returns ``_SM_MIN_NODES`` as soon as the floor is reached (the exact
        count comes from the fingerprint walk that produces the key),
        ``_SM_SMALL`` when the subtree provably holds fewer nodes than the
        floor, and ``None`` when only the per-frame count budget ran out. That
        distinction matters: a budget verdict is a property of the *frame* and
        must never be remembered across frames, while a "small" verdict is a
        property of the subtree and may be cached (see ``_sm_neg``).

        The traversal is inlined instead of driven through ``_iter_children``:
        this loop runs for every candidate on every frame, and the generator
        (two ``getattr`` + two ``isinstance`` + a ``yield`` per node) was the
        dominant part of its cost. Visit order is identical to the generator's
        (children then blits, in order), so the subtree size it proves is
        unchanged.

        The budget is spent on *work* (visits + child pushes), not on visits
        alone: a single node with a very large fan-out used to push all of its
        children before the next budget check, so one call could cost tens of
        milliseconds while "visiting" well under the budget (measured: 6.4 ms
        for a 64-visit call on the settings page). Charging the pushes closes
        that hole and makes the per-frame count cost proportional to the
        budget.
        """
        budget = int(getattr(self, "_sm_count_budget", _SM_COUNT_FRAME_BUDGET))
        if budget <= 0:
            sm_cache_why("count_budget_exhausted")
            return None
        floor = _SM_MIN_NODES
        n = 0          # nodes discovered (the eligibility proof)
        work = 0       # budget units spent (visits + child pushes)
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur is None:
                continue
            n += 1
            work += 1
            if n >= floor:
                self._sm_count_budget = budget - work
                return floor
            if work >= budget:
                self._sm_count_budget = budget - work
                return None
            try:
                entries = getattr(cur, "children", None)
                if entries:
                    for entry in entries:
                        if entry is None:
                            continue
                        if type(entry) is tuple:
                            child = entry[0]
                        elif isinstance(entry, list):
                            child = entry[0]
                        else:
                            child = entry
                        if child is not None:
                            stack.append(child)
                            work += 1
                entries = getattr(cur, "blits", None)
                if entries:
                    for entry in entries:
                        if entry is None:
                            continue
                        if type(entry) is tuple:
                            child = entry[0]
                        elif isinstance(entry, list):
                            child = entry[0]
                        else:
                            child = entry
                        if child is not None:
                            stack.append(child)
                            work += 1
                if work >= budget:
                    self._sm_count_budget = budget - work
                    return None
            except Exception:  # noqa: BLE001 -- probe failure → conservative miss
                sm_cache_why("count_probe_failed")
                return None
        self._sm_count_budget = budget - work
        return _SM_SMALL

    # --- fingerprint ---------------------------------------------------------

    def _sm_matrix_bits(self, v, where):
        """Strict Matrix bits: None/identity/4x4-or-2D values, else uncacheable."""
        if v is None:
            return "n"
        try:
            if self._is_identity(v):
                return "i"
        except Exception:  # noqa: BLE001
            pass
        vals = []
        seen_any = False
        try:
            for attr in (
                "xdx", "xdy", "ydx", "ydy", "xdw", "ydw",
                "zdx", "zdy", "zdz", "zdw", "wdx", "wdy", "wdz", "wdw",
            ):
                if hasattr(v, attr):
                    seen_any = True
                    vals.append((attr, round(float(getattr(v, attr)), 6)))
        except Exception:  # noqa: BLE001 -- value noise: uniform miss fallback
            raise _TransBakeUncacheable("sm fp matrix %s unreadable" % where) from None
        if not seen_any:
            raise _TransBakeUncacheable(
                "sm fp matrix %s type %s" % (where, type(v).__name__)
            )
        return ("m", type(v).__name__, tuple(vals))

    def _sm_one_tex(self, t, where):
        """Strict texture-handle bits (unknown texture-like type → uncacheable)."""
        if t is None or t is _SM_ABSENT:
            return 0
        if isinstance(t, HostTexture):
            h = int(getattr(t, "handle", 0) or 0)
            if h <= 0:
                return 0
            return ("ht", h, int(t.x), int(t.y), int(t.w), int(t.h), int(t.width), int(t.height))
        if isinstance(t, int) and not isinstance(t, bool):
            return ("ht", int(t)) if t > 0 else 0
        for attr in ("handle", "texture"):
            inner = getattr(t, attr, None)
            if isinstance(inner, HostTexture):
                h = int(getattr(inner, "handle", 0) or 0)
                return ("ht", h) if h > 0 else 0
            if isinstance(inner, int) and not isinstance(inner, bool):
                return ("ht", int(inner)) if inner > 0 else 0
        raise _TransBakeUncacheable("sm fp %s texture type %s" % (where, type(t).__name__))

    def _sm_tex_bits(self, node):
        th = []
        raw = getattr(node, "texture", _SM_ABSENT)
        th.append(self._sm_one_tex(raw, "texture"))
        raws = getattr(node, "textures", _SM_ABSENT)
        if raws is _SM_ABSENT or raws is None:
            th.append(0)
        elif isinstance(raws, (list, tuple)):
            th.append(tuple(self._sm_one_tex(t, "textures") for t in raws))
        else:
            th.append(self._sm_one_tex(raws, "textures"))
        return tuple(th)

    def _sm_model_bits(self, node):
        """Bits for ``cached_model`` (texture slots + mesh) — never ignored."""
        cm = getattr(node, "cached_model", _SM_ABSENT)
        if cm is _SM_ABSENT or cm is None:
            return None
        return self._sm_model_shape_bits(cm)

    def _sm_model_shape_bits(self, cm):
        """``cached_model`` SHAPE bits: presence, slot count, mesh, shaders, uniforms.

        The model's texture *handles* are deliberately not part of the key:
        prepare rebuilds ``cached_model`` every frame and the pooled bake RTTs
        it samples are re-targeted every frame (the same reason the proven
        trans fingerprint keys no model handles). Soundness comes from the
        fact that the model is *derived* from the node's own keyed attributes
        (shaders/uniforms/mesh/size) plus its recursed children; the only
        outside source would be dissolve slot recovery, and dissolve /
        imagedissolve nodes are uncacheable by construction.
        """
        try:
            texs = getattr(cm, "textures", None)
            if isinstance(texs, (list, tuple)):
                n_slots = len(texs)
            else:
                n_slots = 1 if getattr(cm, "texture", None) is not None else 0
            return (
                type(cm).__name__,
                n_slots,
                self._sm_mesh_bits(cm),
                None
                if getattr(cm, "shaders", None) is None
                else self._sm_basic_bits(getattr(cm, "shaders"), "cm.shaders"),
                self._sm_uniform_bits(getattr(cm, "uniforms", None), "cm.uniforms"),
            )
        except _TransBakeUncacheable:
            raise
        except Exception:  # noqa: BLE001 -- probe failure → conservative miss
            raise _TransBakeUncacheable("sm fp cached_model probe failed") from None

    def _sm_digest_seq(self, seq, where):
        """Digest a numeric sequence (vertices/indices) — content-exact bits."""
        try:
            n = len(seq)
        except Exception:  # noqa: BLE001
            raise _TransBakeUncacheable("sm fp %s not a sequence" % where) from None
        if n > 4096:
            raise _TransBakeUncacheable("sm fp %s too long (%d)" % (where, n))
        try:
            d = hashlib.blake2b(digest_size=8)
            for v in seq:
                if v is None:
                    d.update(b"n;")
                elif isinstance(v, bool):
                    d.update(b"1;" if v else b"0;")
                elif isinstance(v, (int, float)):
                    d.update(repr(round(float(v), 6)).encode("ascii", "replace"))
                    d.update(b";")
                else:
                    raise _TransBakeUncacheable("sm fp %s element type %s" % (where, type(v).__name__))
            return ("d", n, d.hexdigest())
        except _TransBakeUncacheable:
            raise
        except Exception:  # noqa: BLE001
            raise _TransBakeUncacheable("sm fp %s unreadable" % where) from None

    def _sm_uniform_bits(self, v, where):
        """Uniform/property dict bits: basic values + matrices only.

        Hot path for the common ``{str: float}`` / ``{str: int}`` case: sorted
        keys (plain C sort, no per-key lambda/str) and an inline type dispatch,
        so a node carrying a handful of uniforms does not pay a Python call per
        uniform.
        """
        if v is None or v is _SM_ABSENT:
            return None
        if type(v) is not dict:
            raise _TransBakeUncacheable("sm fp %s not a dict (%s)" % (where, type(v).__name__))
        if not v:
            return 0
        if len(v) == 1:
            keys = list(v)
        else:
            try:
                keys = sorted(v)
            except Exception:  # noqa: BLE001 -- unsortable keys → no key
                raise _TransBakeUncacheable("sm fp %s keys unsortable" % where) from None
        out = []
        append = out.append
        for k in keys:
            val = v[k]
            tv = type(val)
            append(k)
            if tv is float:
                append(round(val, 5))
            elif tv is int or tv is str or tv is bool or val is None:
                append(val)
            else:
                append(self._sm_value_bits(val, "%s[%s]" % (where, k)))
        return ("u", tuple(out))

    def _sm_basic_bits(self, v, where):
        """Strict basic-value bits; unknown types → uncacheable."""
        if v is None or v is _SM_ABSENT:
            return None
        if isinstance(v, bool):
            return ("b", v)
        if isinstance(v, int):
            return ("n", int(v))
        if isinstance(v, float):
            return ("f", round(float(v), 6))
        if isinstance(v, str):
            return ("s", v)
        if isinstance(v, (tuple, list)):
            return ("t", tuple(self._sm_basic_bits(e, where) for e in v))
        raise _TransBakeUncacheable("sm fp %s value type %s" % (where, type(v).__name__))

    def _sm_value_bits(self, v, where):
        """Per-attribute bits: basic values, matrices, meshes, texture handles."""
        if v is _SM_ABSENT or v is None:
            return None
        if isinstance(v, (bool, int, float, str)):
            return self._sm_basic_bits(v, where)
        if where.endswith("mesh"):
            return self._sm_mesh_bits_by_value(v, where)
        try:
            if hasattr(v, "xdx"):
                return self._sm_matrix_bits(v, where)
        except Exception:  # noqa: BLE001
            pass
        if isinstance(v, (tuple, list)):
            return ("t", tuple(self._sm_value_bits(e, where) for e in v))
        if isinstance(v, dict):
            return self._sm_uniform_bits(v, where)
        raise _TransBakeUncacheable("sm fp %s value type %s" % (where, type(v).__name__))

    def _sm_mesh_bits_by_value(self, m, where):
        """Mesh bits for a mesh VALUE (``_trans_mesh_bits`` reads node.mesh)."""
        if m is None or m is _SM_ABSENT:
            return None
        if isinstance(m, bool):
            return ("b", m)
        if isinstance(m, int) and not isinstance(m, bool):
            return ("h", int(m))
        if m == "quad":
            return "q"
        if hasattr(m, "vertices"):
            try:
                verts = list(m.vertices) or []
            except Exception:  # noqa: BLE001, S110
                verts = []
            if len(verts) > 64:
                raise _TransBakeUncacheable("sm fp mesh > 64 verts")

            d = hashlib.blake2b(digest_size=8)
            d.update(",".join(repr(float(v)) for v in verts).encode("ascii", "replace"))
            return ("vh", len(verts), d.hexdigest())
        if hasattr(m, "get_points") or hasattr(m, "points"):
            return ("p", type(m).__name__)
        raise _TransBakeUncacheable("sm fp mesh type %s" % type(m).__name__)

    def _sm_fp_leaf(self, node):
        """Fingerprint for a non-Render leaf (``_draw_model_like`` inputs)."""
        if getattr(node, "_dissolve_origin", None) is not None:
            # Dissolve slot recovery reads the origin's children — state the
            # key cannot see → never cacheable.
            raise _TransBakeUncacheable("sm fp leaf with _dissolve_origin")
        try:
            get_size = hasattr(node, "get_size") or hasattr(node, "_pixels")
        except Exception:  # noqa: BLE001, S110
            get_size = False
        tex_id = getattr(node, "texture", _SM_ABSENT)
        texes = getattr(node, "textures", _SM_ABSENT)
        has_tex = (
            (tex_id is not _SM_ABSENT and tex_id is not None)
            or (texes is not _SM_ABSENT and texes is not None)
        )
        if get_size and not has_tex:
            # Surface-like leaf: the drawn pixels come from a content-derived
            # upload handle the key cannot see → never cacheable.
            raise _TransBakeUncacheable("sm fp surface-like leaf %s" % type(node).__name__)
        w_a = getattr(node, "width", _SM_ABSENT)
        w_b = getattr(node, "w", _SM_ABSENT)
        h_a = getattr(node, "height", _SM_ABSENT)
        h_b = getattr(node, "h", _SM_ABSENT)
        parts = [
            type(node).__name__,
            self._sm_tex_bits(node),
            self._sm_basic_bits(getattr(node, "color", _SM_ABSENT), "leaf.color"),
            self._sm_basic_bits(getattr(node, "ndc", _SM_ABSENT), "leaf.ndc"),
            self._sm_basic_bits(getattr(node, "shaders", _SM_ABSENT), "leaf.shaders"),
            self._sm_uniform_bits(getattr(node, "uniforms", _SM_ABSENT), "leaf.uniforms"),
            self._sm_basic_bits(getattr(node, "pipeline", _SM_ABSENT), "leaf.pipeline"),
            self._sm_basic_bits(getattr(node, "alpha", _SM_ABSENT), "leaf.alpha"),
            self._sm_basic_bits(getattr(node, "over", _SM_ABSENT), "leaf.over"),
            self._sm_basic_bits(getattr(node, "operation", _SM_ABSENT), "leaf.operation"),
            self._sm_basic_bits(
                getattr(node, "operation_complete", _SM_ABSENT), "leaf.operation_complete"
            ),
            self._sm_basic_bits(
                getattr(node, "operation_alpha", _SM_ABSENT), "leaf.operation_alpha"
            ),
            self._sm_model_bits(node),
            (
                self._sm_value_bits(w_a, "leaf.width"),
                self._sm_value_bits(w_b, "leaf.w"),
                self._sm_value_bits(h_a, "leaf.height"),
                self._sm_value_bits(h_b, "leaf.h"),
            ),
        ]
        mesh = getattr(node, "mesh", _SM_ABSENT)
        if mesh is _SM_ABSENT or mesh is None:
            parts.append(None)
        else:
            parts.append(self._sm_mesh_bits_by_value(mesh, "leaf.mesh"))
        for name in ("vertices", "indices", "texture1"):
            v = getattr(node, name, _SM_ABSENT)
            if v is _SM_ABSENT or v is None:
                parts.append(None)
            elif name == "texture1":
                parts.append(self._sm_one_tex(v, "leaf.texture1"))
            else:
                parts.append(self._sm_digest_seq(v, "leaf.%s" % name))
        if w_a is _SM_ABSENT and w_b is _SM_ABSENT:
            try:
                sw, sh = node.get_size()
                parts.append(("gs", int(sw), int(sh)))
            except Exception:  # noqa: BLE001 -- no size surface: matches walker default
                parts.append(None)
        else:
            parts.append(None)
        return tuple(parts)

    def _sm_pol_map(self):
        """Class → fingerprint-policy cache (lazily created; hot path lookup)."""
        pol = getattr(self, "_sm_pol", None)
        if pol is None:
            pol = {}
            self._sm_pol = pol
        return pol

    def _sm_class_policy(self, cls):
        """Per-class fingerprint policy: ``(mode, optional_names, has_blits)``.

        ``mode`` 0 — the class is a cdef class (the engine's ``Render``) whose
        attribute surface is fixed at class level: deciding once per class that
        every pixel-affecting attribute the walker reads is present lets the
        per-node hot path drop ~35 ``getattr`` probes per node. ``mode`` 2 — a
        plain-Python class (per-instance dict, e.g. CPython managed dict):
        the surface can only be probed per instance, so the slower path probes
        every required attribute and keys every optional name that exists.
        ``mode`` 3 — a cdef-like class missing a required attribute: unkeyable.
        Either way the contract "any attribute we cannot read, or any state we
        cannot see → uncacheable" holds: a class that does not expose the full
        surface is never keyed.
        """
        pol = self._sm_pol_map().get(cls)
        if pol is None:
            # __dictoffset__ < 0 ⇒ instances have a (managed) instance dict.
            if getattr(cls, "__dictoffset__", 0) != 0:
                pol = (2, _SM_RENDER_OPTIONAL_FULL, True, False)
            else:
                req_ok = True
                for name in _SM_RENDER_REQUIRED:
                    if not hasattr(cls, name):
                        req_ok = False
                        break
                opt_names = tuple(n for n in _SM_RENDER_OPTIONAL if hasattr(cls, n))
                # Specialised-path gate: cdef surface, no optional state names,
                # and none of the attributes the fast body hard-codes as absent.
                fast = bool(req_ok) and not opt_names
                if fast:
                    for name in _SM_FAST_ABSENT:
                        if hasattr(cls, name):
                            fast = False
                            break
                pol = (
                    0 if req_ok else 3,
                    opt_names,
                    hasattr(cls, "blits"),
                    fast,
                )
            self._sm_pol_map()[cls] = pol
        return pol

    def _sm_matrix_bits_fast(self, v):
        """Cheap matrix bits (values only; identity == its own value tuple)."""
        try:
            if hasattr(v, "zdx"):
                return (
                    v.xdx, v.xdy, v.ydx, v.ydy, v.xdw, v.ydw,
                    v.zdx, v.zdy, v.zdz, v.zdw, v.wdx, v.wdy, v.wdz, v.wdw,
                )
            return (v.xdx, v.xdy, v.ydx, v.ydy, v.xdw, v.ydw)
        except Exception:  # noqa: BLE001 -- unknown transform type → no key
            raise _TransBakeUncacheable("sm fp matrix type %s" % type(v).__name__) from None

    def _sm_fp_render(self, node, pol, depth, cap, pol_map=None):
        """Strict fingerprint for a Render-like node (every pixel input keyed).

        Hot path: direct attribute reads with a per-class policy (no per-attribute
        ``getattr`` probing), rounded/int-stabilised numeric fields (float noise
        from animation must not churn the key while a 1e-4 difference is still
        far below one 1/255 pixel step), one flat tuple, children iterated inline.
        """
        if pol_map is None:
            pol_map = self._sm_pol_map()
        cls = type(node)
        mode = pol[0]
        if mode == 3:
            raise _TransBakeUncacheable("sm fp class %s missing attrs" % cls.__name__)
        if mode == 2:
            # Instance-dict class: every required attribute must be readable.
            for name in _SM_RENDER_REQUIRED:
                try:
                    getattr(node, name)
                except Exception:  # noqa: BLE001 -- unreadable → no key
                    raise _TransBakeUncacheable("sm fp attr %s unreadable" % name) from None
        shaders = node.shaders
        uniforms = node.uniforms
        mesh = node.mesh
        op = node.operation
        op_complete = node.operation_complete
        if shaders or uniforms or op or op_complete:
            # Effect-bearing / dissolve-carrying nodes only: every detection path
            # of the walker's `_is_dissolve_node` / `_is_imagedissolve_node` goes
            # through shaders, uniforms or operation/operation_complete, and a
            # dissolve or imagedissolve subtree (or any node with the dual-slot
            # recovery pointer) belongs to the existing dissolve/reverse bake
            # paths → never cached here. The common check is set membership
            # (equivalent to the two predicates, see _SM_EFFECT_*); the full
            # predicates run only for the rare op-driven forms.
            special = False
            if shaders:
                for _s in shaders:
                    if _s in _SM_EFFECT_SHADERS:
                        special = True
                        break
            if not special and uniforms:
                for _k in _SM_EFFECT_UNIFORMS:
                    if _k in uniforms:
                        special = True
                        break
            if not special and (op or op_complete):
                special = bool(
                    self._is_dissolve_node(node) or self._is_imagedissolve_node(node)
                )
            if special:
                raise _TransBakeUncacheable("sm fp dissolve node %s" % cls.__name__)
            if getattr(node, "_dissolve_origin", None) is not None:
                raise _TransBakeUncacheable("sm fp node with _dissolve_origin")
        fwd = node.forward
        rev = node.reverse
        ct = node.cached_texture
        cm = node.cached_model
        props = node.properties
        nearest = node.nearest
        if type(nearest) is not bool and nearest is not None:
            nearest = self._sm_value_bits(nearest, "nearest")
        xc = node.xclipping
        yc = node.yclipping
        if xc or yc:
            clip_bits = (
                True,
                bool(xc),
                bool(yc),
                getattr(node, "xclip", None),
                getattr(node, "yclip", None),
                getattr(node, "xclip_rect", None),
                getattr(node, "yclip_rect", None),
            )
        else:
            clip_bits = False
        # children (inline mirror of _iter_children: children then blits)
        kids = []
        children = node.children
        if children:
            for entry in children:
                if entry is None:
                    continue
                if isinstance(entry, (tuple, list)):
                    nent = len(entry)
                    if nent > 2:
                        child, kx, ky = entry[0], entry[1], entry[2]
                    elif nent == 2:
                        child, kx, ky = entry[0], entry[1], 0.0
                    else:
                        continue
                else:
                    child, kx, ky = entry, 0.0, 0.0
                kids.append((kx, ky, self._sm_fp_node(child, depth + 1, cap, pol_map)))
        if pol[2]:
            blits = getattr(node, "blits", None) if mode == 2 else node.blits
            if blits:
                for entry in blits:
                    if entry is None:
                        continue
                    if isinstance(entry, (tuple, list)):
                        nent = len(entry)
                        if nent > 2:
                            child, kx, ky = entry[0], entry[1], entry[2]
                        elif nent == 2:
                            child, kx, ky = entry[0], entry[1], 0.0
                        else:
                            continue
                    else:
                        child, kx, ky = entry, 0.0, 0.0
                    kids.append((kx, ky, self._sm_fp_node(child, depth + 1, cap, pol_map)))
        opt_names = pol[1]
        if opt_names:
            opt = []
            for name in opt_names:
                v = getattr(node, name, None) if mode == 2 else getattr(node, name)
                if v is None:
                    opt.append(None)
                elif name in ("texture", "textures"):
                    opt.append(self._sm_tex_value_bits(v, name))
                else:
                    opt.append(self._sm_value_bits(v, name))
            opt_bits = tuple(opt)
        else:
            opt_bits = None
        return (
            cls.__name__,
            int(node.width),
            int(node.height),
            round(node.alpha, 4),
            round(node.over, 4),
            node.matrix_kind,
            0 if fwd is None else self._sm_matrix_bits_fast(fwd),
            0 if rev is None else self._sm_matrix_bits_fast(rev),
            clip_bits,
            None if uniforms is None else self._sm_uniform_bits(uniforms, "u"),
            None if shaders is None else self._sm_basic_bits(shaders, "shaders"),
            None if mesh is None else self._sm_mesh_bits_by_value(mesh, "mesh"),
            0 if ct is None else self._sm_one_tex(ct, "cached_texture"),
            None if cm is None else self._sm_model_shape_bits(cm),
            nearest,
            None if props is None else self._sm_uniform_bits(props, "properties"),
            (
                op,
                round(op_complete, 4),
                node.operation_alpha,
                node.operation_parameter,
            ),
            (int(node.cropped_width), int(node.cropped_height)),
            opt_bits,
            tuple(kids),
        )

    def _sm_tex_value_bits(self, v, where):
        if v is None:
            return 0
        if isinstance(v, (list, tuple)):
            return tuple(self._sm_one_tex(t, where) for t in v)
        return self._sm_one_tex(v, where)

    def _sm_fp_node(self, node, depth, cap, pol_map=None):
        """Recursive fingerprint: Render-like / leaf / bare handle.

        One budget decrement per visited node (the returned count is the number
        of ``_draw_node`` calls a full walk would have made for this subtree).
        ``pol_map`` is passed down so the hot path costs one dict lookup per
        node instead of one method call + lookup.
        """
        if pol_map is None:
            pol_map = self._sm_pol_map()
        if node is None:
            return None
        cap[0] -= 1
        if cap[0] < 0:
            raise _SmFpBudgetExceeded("sm fp node budget exceeded")
        t = type(node)
        if t in _SM_SCALAR_TYPES:
            return node
        if depth > _SM_FP_MAX_DEPTH:
            raise _TransBakeUncacheable("sm fp depth budget %s" % t.__name__)
        if t is HostTexture:
            return self._sm_one_tex(node, "node")
        cls = t
        pol = pol_map.get(cls)
        if pol is None:
            if t is HostTexture or isinstance(node, HostTexture):
                return self._sm_one_tex(node, "node")
            pol = self._sm_class_policy(cls)
        if pol[3]:
            return self._sm_fp_render_fast(node, depth, cap, pol_map, pol[2])
        if pol[0] <= 2:
            return self._sm_fp_render(node, pol, depth, cap, pol_map)
        # Unknown class: Render-like (children + mesh) but not a class surface
        # the walker understands → uncacheable. A child-bearing unknown is
        # uncacheable too; a childless one is fingerprinted as a model leaf.
        if hasattr(node, "mesh") or hasattr(node, "children"):
            raise _TransBakeUncacheable("sm fp unknown render-like class %s" % cls.__name__)
        try:
            first = next(iter(self._iter_children(node)), None)
        except Exception:  # noqa: BLE001 -- probe failure → conservative miss
            raise _TransBakeUncacheable("sm fp child probe failed") from None
        if first is not None:
            raise _TransBakeUncacheable("sm fp non-render container %s" % cls.__name__)
        return self._sm_fp_leaf(node)

    def _sm_fp_render_fast(self, node, depth, cap, pol_map, has_blits):
        """Specialised ``_sm_fp_render`` for the engine's cdef ``Render`` surface.

        Same 20-slot body, byte for byte, as the generic path — but the class
        policy (``pol[3]``) has already proved that ``color``/``ndc``/
        ``pipeline``/``texture(s)``/``w``/``h``/``vertices``/``indices``/
        ``texture1``/``xclip*``/``yclip*`` and the whole optional-name tuple are
        absent on this class, so the body can hard-code the constants the
        generic path would compute (``None`` / ``(0, 0)``) and read the rest
        inline. Only the calls that carry state — dissolve detection, uniforms,
        shaders, mesh, cached model/texture, matrix bits, children — remain, and
        the two matrix reads plus the child recursion are inlined.

        Cost matters here: this body runs once per node per frame for every
        cache candidate, and the walk is ~1.7k nodes on the settings page. The
        generic body makes ~9.5 Python calls per node; this one makes ~2.

        ``_SM_FP_SELFCHECK`` (dev) recomputes the generic body and compares: on
        mismatch the generic value wins (and the reason is recorded), so the
        specialised path can never introduce a divergent key silently.
        """
        fpn = self._sm_fp_node
        shaders = node.shaders
        uniforms = node.uniforms
        mesh = node.mesh
        op = node.operation
        op_complete = node.operation_complete
        if shaders or uniforms or op or op_complete:
            special = False
            if shaders:
                for _s in shaders:
                    if _s in _SM_EFFECT_SHADERS:
                        special = True
                        break
            if not special and uniforms:
                for _k in _SM_EFFECT_UNIFORMS:
                    if _k in uniforms:
                        special = True
                        break
            if not special and (op or op_complete):
                special = bool(
                    self._is_dissolve_node(node) or self._is_imagedissolve_node(node)
                )
            if special:
                raise _TransBakeUncacheable("sm fp dissolve node %s" % type(node).__name__)
            if getattr(node, "_dissolve_origin", None) is not None:
                raise _TransBakeUncacheable("sm fp node with _dissolve_origin")
        fwd = node.forward
        rev = node.reverse
        ct = node.cached_texture
        cm = node.cached_model
        props = node.properties
        nearest = node.nearest
        if type(nearest) is not bool and nearest is not None:
            nearest = self._sm_value_bits(nearest, "nearest")
        xc = node.xclipping
        yc = node.yclipping
        if xc or yc:
            # xclip / yclip / xclip_rect / yclip_rect are absent on this class
            # (gate) → the generic body reads None for all four.
            clip_bits = (True, bool(xc), bool(yc), None, None, None, None)
        else:
            clip_bits = False
        kids = []
        append = kids.append
        children = node.children
        if children:
            for entry in children:
                if entry is None:
                    continue
                if type(entry) is tuple:
                    nent = len(entry)
                    if nent > 2:
                        child = entry[0]
                        kx = entry[1]
                        ky = entry[2]
                    elif nent == 2:
                        child = entry[0]
                        kx = entry[1]
                        ky = 0.0
                    else:
                        continue
                elif isinstance(entry, list):
                    nent = len(entry)
                    if nent > 2:
                        child = entry[0]
                        kx = entry[1]
                        ky = entry[2]
                    elif nent == 2:
                        child = entry[0]
                        kx = entry[1]
                        ky = 0.0
                    else:
                        continue
                else:
                    child = entry
                    kx = 0.0
                    ky = 0.0
                append((kx, ky, fpn(child, depth + 1, cap, pol_map)))
        if has_blits:
            blits = node.blits
            if blits:
                for entry in blits:
                    if entry is None:
                        continue
                    if type(entry) is tuple:
                        nent = len(entry)
                        if nent > 2:
                            child = entry[0]
                            kx = entry[1]
                            ky = entry[2]
                        elif nent == 2:
                            child = entry[0]
                            kx = entry[1]
                            ky = 0.0
                        else:
                            continue
                    elif isinstance(entry, list):
                        nent = len(entry)
                        if nent > 2:
                            child = entry[0]
                            kx = entry[1]
                            ky = entry[2]
                        elif nent == 2:
                            child = entry[0]
                            kx = entry[1]
                            ky = 0.0
                        else:
                            continue
                    else:
                        child = entry
                        kx = 0.0
                        ky = 0.0
                    append((kx, ky, fpn(child, depth + 1, cap, pol_map)))
        # Matrix bits, inlined: one hasattr + 14 reads (3D) or 6 reads (2D).
        if fwd is None:
            fwd_bits = 0
        else:
            try:
                fwd_bits = (
                    fwd.xdx, fwd.xdy, fwd.ydx, fwd.ydy, fwd.xdw, fwd.ydw,
                    fwd.zdx, fwd.zdy, fwd.zdz, fwd.zdw, fwd.wdx, fwd.wdy, fwd.wdz, fwd.wdw,
                )
            except AttributeError:
                try:
                    fwd_bits = (fwd.xdx, fwd.xdy, fwd.ydx, fwd.ydy, fwd.xdw, fwd.ydw)
                except Exception:  # noqa: BLE001 -- unknown transform type → no key
                    raise _TransBakeUncacheable(
                        "sm fp matrix type %s" % type(fwd).__name__
                    ) from None
            except Exception:  # noqa: BLE001 -- unreadable transform → no key
                raise _TransBakeUncacheable(
                    "sm fp matrix type %s" % type(fwd).__name__
                ) from None
        if rev is None:
            rev_bits = 0
        else:
            try:
                rev_bits = (
                    rev.xdx, rev.xdy, rev.ydx, rev.ydy, rev.xdw, rev.ydw,
                    rev.zdx, rev.zdy, rev.zdz, rev.zdw, rev.wdx, rev.wdy, rev.wdz, rev.wdw,
                )
            except AttributeError:
                try:
                    rev_bits = (rev.xdx, rev.xdy, rev.ydx, rev.ydy, rev.xdw, rev.ydw)
                except Exception:  # noqa: BLE001 -- unknown transform type → no key
                    raise _TransBakeUncacheable(
                        "sm fp matrix type %s" % type(rev).__name__
                    ) from None
            except Exception:  # noqa: BLE001 -- unreadable transform → no key
                raise _TransBakeUncacheable(
                    "sm fp matrix type %s" % type(rev).__name__
                ) from None
        w_a = node.width
        h_a = node.height
        body = (
            type(node).__name__,
            int(w_a),
            int(h_a),
            round(node.alpha, 4),
            round(node.over, 4),
            node.matrix_kind,
            fwd_bits,
            rev_bits,
            clip_bits,
            None if uniforms is None else self._sm_uniform_bits(uniforms, "u"),
            None if shaders is None else self._sm_basic_bits(shaders, "shaders"),
            None if mesh is None else self._sm_mesh_bits_by_value(mesh, "mesh"),
            0 if ct is None else self._sm_one_tex(ct, "cached_texture"),
            None if cm is None else self._sm_model_shape_bits(cm),
            nearest,
            None if props is None else self._sm_uniform_bits(props, "properties"),
            (op, round(op_complete, 4), node.operation_alpha, node.operation_parameter),
            (int(node.cropped_width), int(node.cropped_height)),
            None,
            tuple(kids),
        )
        if _SM_FP_SELFCHECK:
            ref = self._sm_fp_render(
                node, (0, (), has_blits, False), depth, [cap[0]], pol_map
            )
            if ref != body:
                global _SM_FP_MISMATCH_TOTAL
                try:
                    _SM_FP_MISMATCH_TOTAL += 1
                except Exception:  # noqa: BLE001, S110
                    pass
                try:
                    sm_cache_why("fp_selfcheck_mismatch")
                except Exception:  # noqa: BLE001, S110
                    pass
                return ref
        return body

    def _sm_key(self, node, allowance=None):
        """(key, node_count, budget_limited) for ``node``.

        ``key is None`` ⇒ the subtree can never be served from a pinned RTT.
        ``budget_limited`` is True when the walk was cut off by the per-frame
        node ceiling rather than by a property of the subtree: such a verdict
        must not be remembered across frames (see the negative memory in
        ``_sm_try_draw``). ``allowance`` caps the number of visited nodes for
        this call (``None`` ⇒ the per-candidate ceiling only) and the nodes
        actually visited are charged to the frame's fingerprint budget.
        """
        cap_nodes = _SM_FP_MAX_NODES
        if allowance is not None:
            allowance = int(allowance)
            if allowance <= 0:
                sm_cache_why("fp_frame_budget")
                return None, 0, True
            if allowance < cap_nodes:
                cap_nodes = allowance
        limited = False
        try:
            handles = set()
            cap = [cap_nodes, handles, 0]
            try:
                body = self._sm_fp_node(node, 0, cap, self._sm_pol_map())
            except _SmFpBudgetExceeded:
                sm_cache_why("fp_frame_budget")
                return None, max(1, cap_nodes - max(0, int(cap[0]))), True
            finally:
                _spent = cap_nodes - max(0, int(cap[0]))
                if _spent > 0:
                    try:
                        self._sm_fp_budget = max(
                            0, int(getattr(self, "_sm_fp_budget", _SM_FP_FRAME_BUDGET)) - _spent
                        )
                    except Exception:  # noqa: BLE001, S110
                        pass
            if body is None:
                sm_cache_why("fp_none")
                return None, 0, limited
            nodes = max(1, int(cap_nodes) - int(cap[0]))
            drawable = tuple(int(x) for x in (getattr(self, "drawable_size", None) or (0, 0)))
            layout = tuple(int(x) for x in (getattr(self, "layout_virtual_size", None) or (0, 0)))
            _vids = _movie_tex_handles() if _subtree_movie_veto_enabled() else None
            if _vids is None:
                _nonce = _movie_publish_seq()
            else:
                if handles and (handles & set(_vids)):
                    sm_cache_why("movie_texture_in_subtree")
                    return None, 0, limited
                _nonce = 0
            return ("sm1", _nonce, drawable, layout, body), nodes, limited
        except _TransBakeUncacheable as e:
            global _SM_UNCACHEABLE_TOTAL
            try:
                _SM_UNCACHEABLE_TOTAL += 1
            except Exception:  # noqa: BLE001, S110
                pass
            try:
                sm_cache_why(str(e)[:48])
            except Exception:  # noqa: BLE001, S110
                pass
            # Visited-node count is returned on the refusal path too: the
            # per-frame ledger must see the cost of a walk that proved the
            # subtree uncacheable (it is charged to sm_fp_n_none).
            return None, max(1, int(cap_nodes) - max(0, int(cap[0]))), limited
        except Exception as e:  # noqa: BLE001 -- key failure must never abort the frame
            global _SM_KEY_FAIL_TOTAL
            try:
                _SM_KEY_FAIL_TOTAL += 1
            except Exception:  # noqa: BLE001, S110
                pass
            _host_draw_fail("sm_key", e)
            return None, 0, False

    # --- store / eviction ----------------------------------------------------

    def _sm_entry_alive(self, ent) -> bool:
        handle = int(ent.handle)
        if handle <= 0:
            return False
        if self._rtt_hot_pool_handle(handle):
            self._sm_clear()
            return False
        try:
            import renpy_host  # type: ignore

            return bool(renpy_host.texture_alive(int(handle)))
        except Exception:  # noqa: BLE001, S110 -- probe failure → conservative miss
            return False

    def _sm_retire(self, ent, defer: bool = True) -> None:
        """Drop one entry; its pinned handle is destroyed on the next frame."""
        global _SM_BYTES_HELD
        try:
            h = int(ent.handle)
        except Exception:  # noqa: BLE001, S110
            return
        try:
            self._sm_bytes = max(0, int(getattr(self, "_sm_bytes", 0)) - int(ent.nbytes))
            _SM_BYTES_HELD = max(0, int(_SM_BYTES_HELD) - int(ent.nbytes))
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            if defer:
                pend = getattr(self, "_sm_pending", None)
                if pend is None:
                    pend = []
                    self._sm_pending = pend
                pint = int(h)
                if pint > 0 and pint not in pend:
                    pend.append(pint)
            else:
                self._trans_cache_destroy_handle(h)
        except Exception:  # noqa: BLE001, S110 -- destroy must never abort frames
            pass

    def _sm_store(self, key, handle, w, h, nodes, frame):
        """Pin a fresh private RTT under ``key`` (LRU eviction by byte budget)."""
        global _SM_BYTES_HELD
        st = self._sm_state()
        try:
            nbytes = int(w) * int(h) * 4
        except Exception:  # noqa: BLE001, S110
            nbytes = 0
        budget = self._sm_budget_bytes()
        if nbytes > budget:
            # A single bake larger than the whole byte budget would evict
            # everything else on every frame → refuse (normal walk).
            sm_cache_why("entry_over_budget")
            return None
        ent = _SmEntry(int(handle), int(w), int(h), nbytes, int(frame), int(nodes))
        st.pop(key, None)
        while st and (int(getattr(self, "_sm_bytes", 0)) + nbytes) > budget:
            try:
                old_key, old = next(iter(st.items()))
            except StopIteration:
                break
            st.pop(old_key, None)
            self._sm_retire(old, defer=True)
        st[key] = ent
        try:
            self._sm_bytes = int(getattr(self, "_sm_bytes", 0)) + nbytes
            _SM_BYTES_HELD = int(_SM_BYTES_HELD) + nbytes
        except Exception:  # noqa: BLE001, S110
            pass
        return ent

    def _sm_clear(self) -> None:
        """Destroy every pinned entry + pending destroys (kill_textures/resize)."""
        global _SM_CLEAR_TOTAL, _SM_BYTES_HELD
        try:
            _SM_CLEAR_TOTAL += 1
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            st = getattr(self, "_sm_cache", None)
            if st:
                for ent in list(st.values()):
                    try:
                        self._trans_cache_destroy_handle(int(ent.handle))
                    except Exception:  # noqa: BLE001, S110
                        pass
                st.clear()
            self._sm_bytes = 0
            _SM_BYTES_HELD = 0
            pend = getattr(self, "_sm_pending", None)
            if pend:
                for h in list(pend):
                    self._trans_cache_destroy_handle(h)
                pend.clear()
            for name in (
                "_sm_gate",
                "_sm_pos_gate",
                "_sm_cur_keys",
                "_sm_prev_keys",
                "_sm_cur_pos",
                "_sm_prev_pos",
                "_sm_streak",
                "_sm_neg",
            ):
                d = getattr(self, name, None)
                if d:
                    d.clear()
        except Exception:  # noqa: BLE001, S110 -- cache cleanup must never abort
            pass

    # --- bake / draw ---------------------------------------------------------

    def _sm_fp_allowance(self, acct_ms: float) -> int:
        """Per-candidate fingerprint node ceiling for this frame.

        Hard bound: ``sum(allowed nodes) <= _SM_FP_FRAME_BUDGET`` per product
        frame, further trimmed by the accounting time left in the frame
        (``_SM_ACCT_FRAME_MS`` minus what the frame already spent), using the
        frame's own measured per-node cost. A candidate that would need more
        than its allowance is cut off mid-walk and simply not cached this
        frame — the walk then draws it as usual, so nothing about the rendered
        pixels depends on this bound; only *when* (or whether) a subtree gets
        a pinned RTT does.
        """
        try:
            left = int(getattr(self, "_sm_fp_budget", _SM_FP_FRAME_BUDGET))
        except Exception:  # noqa: BLE001, S110
            left = _SM_FP_FRAME_BUDGET
        if left <= 0:
            return 0
        _f = getattr(self, "_sm_f", None)
        if _f is None:
            return min(left, _SM_FP_MAX_NODES)
        rem_ms = float(_SM_ACCT_FRAME_MS) - float(acct_ms)
        if rem_ms <= 0.0:
            return 0
        fpn = int(_f[_SM_F_FP_N])
        us = (_f[_SM_F_FP_MS] * 1e6 / fpn) if fpn >= 32 else float(_SM_FP_US_PER_NODE)
        if us < 0.2:
            us = 0.2
        allow = int((rem_ms * 1000.0) / us)
        if allow > left:
            allow = left
        if allow > _SM_FP_MAX_NODES:
            allow = _SM_FP_MAX_NODES
        return allow if allow > 0 else 0

    def _sm_bake_box(self, node):
        """Bake box (w, h) or (0, 0) when the box would not fit the surface."""
        try:
            nw, nh = self._node_size(node, default=(0, 0))
            nw, nh = int(nw), int(nh)
        except Exception:  # noqa: BLE001, S110
            return 0, 0
        if nw <= 0 or nh <= 0:
            return 0, 0
        try:
            from .rtt_pool import _clamp_rtt_size

            d = getattr(self, "drawable_size", None) or (0, 0)
            l = getattr(self, "layout_virtual_size", None) or self.virtual_size
            cw, ch = _clamp_rtt_size(nw, nh, int(l[0]), int(d[0]), int(l[1]), int(d[1]))
        except Exception:  # noqa: BLE001 -- unknown bounds → conservative miss
            return 0, 0
        if int(cw) != nw or int(ch) != nh:
            sm_cache_why("box_over_surface")
            return 0, 0
        return nw, nh

    def _sm_bake_pin(self, node, key, w, h, nodes, frame):
        """Bake ``node`` into a pinned non-pool RTT and pin it under ``key``.

        Mirrors ``_trans_cache_pinned_bake`` (instance-group isolation, parent
        batch flush, nested frame + full-canvas scoping, virtual/clip restore)
        with the byte-budget store of the stable model cache.
        """
        global _SM_BAKE_TOTAL
        try:
            import renpy_host  # type: ignore

            self._ensure_pipes()  # type: ignore[attr-defined]
            bake_attn(self, "sm_pin", node, w, h)
            rtt = 0
            _grp = getattr(self, "_instance_group", None)
            _grp_pushed = False
            self._sm_in_bake = True
            try:
                try:
                    # Product prepare (`load_all_textures(surftree)`) already ran
                    # for this frame before the walk; re-walking the subtree here
                    # would double the bake frame's cost. Only prepare when this
                    # node was NOT prepared this frame (stale/missing tick), which
                    # keeps child textures guaranteed without the redundant walk.
                    if getattr(node, "loaded", None) != (int(getattr(self, "_frame_gen", 0)) + 1):
                        self.load_all_textures(node)
                except Exception as e:  # noqa: BLE001 -- logged; bake still runs
                    _host_draw_fail("sm_pin.load_all_textures", e)
                if _grp is not None:
                    try:
                        if not _grp.empty():
                            _grp.flush(self)
                        _grp.push()
                        _grp_pushed = True
                    except Exception:  # noqa: BLE001, S110 -- push failure → no epoch
                        _grp = None
                    self._flush_draw_batch()  # type: ignore[attr-defined]
                try:
                    _crt_pinned = getattr(renpy_host, "create_render_texture_pinned", None)
                    if _crt_pinned is not None:
                        rtt = int(_crt_pinned(w, h))
                    else:
                        rtt = int(renpy_host.create_render_texture(w, h))
                    if rtt <= 0:
                        raise ValueError("sm_pin: bad private RTT handle")
                    # Render-texture pixels are premultiplied: classify the handle
                    # so _sm_draw_entry's quad samples it passthrough.
                    try:
                        register_premultiplied_handle(rtt)
                    except Exception:  # noqa: BLE001, S110 -- bookkeeping only
                        pass
                    if self._rtt_hot_pool_handle(rtt):
                        raise ValueError("sm_pin: handle leaked into rtt pool")
                    renpy_host.begin_target(rtt)
                    renpy_host.begin_frame()
                    old_vs = self.virtual_size
                    old_clip = self._clip_rect
                    old_poly = getattr(self, "_clip_poly", None)
                    old_full = getattr(self, "_frame_full_canvas_hop", None)
                    prev_full = getattr(self, "_frame_full_canvas", False)
                    self._frame_full_canvas_hop = True
                    self._frame_full_canvas = False
                    try:
                        try:
                            self._frame_bakes = getattr(self, "_frame_bakes", 0) + 1
                        except Exception:  # noqa: BLE001, S110 -- diagnostics only
                            pass
                        try:
                            self.virtual_size = (int(w), int(h))
                            self._clip_rect = None
                            self._clip_poly = None
                            self._draw_node(node, 0.0, 0.0)  # type: ignore[attr-defined]
                        finally:
                            self.virtual_size = old_vs
                            self._clip_rect = old_clip
                            self._clip_poly = old_poly
                            try:
                                self._end_frame_present()  # type: ignore[attr-defined]
                            except Exception as e:  # noqa: BLE001 -- logged
                                _host_draw_fail("sm_pin.end_frame", e)
                            try:
                                renpy_host.end_target()
                            except Exception as e:  # noqa: BLE001 -- logged
                                _host_draw_fail("sm_pin.end_target", e)
                    finally:
                        self._frame_full_canvas = bool(prev_full)
                        self._frame_full_canvas_hop = old_full
                    ent = self._sm_store(key, rtt, w, h, nodes, frame)
                    if ent is None:
                        # Refused (over budget): give the RTT straight back and
                        # let the normal per-node walk run this frame.
                        self._trans_cache_destroy_handle(rtt)
                        return None
                    try:
                        _SM_BAKE_TOTAL += 1
                    except Exception:  # noqa: BLE001, S110
                        pass
                    return ent
                except Exception as e:  # noqa: BLE001 -- pin failure → normal walk
                    _host_draw_fail("sm_pin", e)
                    if rtt:
                        self._trans_cache_destroy_handle(rtt)
                    return None
            finally:
                self._sm_in_bake = False
                if _grp_pushed:
                    try:
                        _grp.pop()
                    except Exception as e:  # noqa: BLE001 -- log-only
                        try:
                            _host_draw_fail("sm_pin.grp_pop", e)
                        except Exception:  # noqa: BLE001, S110
                            pass
        except Exception as e:  # noqa: BLE001 -- cache failure must not abort frame
            self._sm_in_bake = False
            _host_draw_fail("sm_bake_pin", e)
            return None

    def _sm_draw_entry(self, ent, ctx) -> None:
        """Draw one cached bake as a real subtree (single quad at its place)."""
        try:
            ht = HostTexture(int(ent.handle), int(ent.w), int(ent.h))
            self._draw_texture_at(ht, ctx.ox, ctx.oy, (int(ent.w), int(ent.h)))  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001 -- draw failure logged once
            _host_draw_fail("sm_draw_entry", e)

    def _sm_try_draw(self, node, ctx) -> bool:
        """Stable subtree model cache entry point (``WalkMixin._draw_node_inner_body``).

        Returns True when ``node`` was drawn from a cached pinned RTT — the
        recursion into its children must not happen then.
        """
        global _SM_HITS, _SM_MISSES, _SM_NODES_SAVED
        global _SM_HIT_TOTAL, _SM_MISS_TOTAL, _SM_NODES_SAVED_TOTAL
        on = _SM_CACHE_ON
        if on is None:
            on = static_model_cache_enabled()
        if not on:
            return False
        try:
            if getattr(self, "_sm_in_bake", False):
                # Never nest our own pinned bake inside an RTT pass.
                return False
            if not (hasattr(node, "children") and hasattr(node, "mesh")):
                return False
            # Preserve the existing bake/leaf paths byte-for-byte: nodes that
            # already carry a bake handle are owned by _draw_cached /
            # _draw_reverse / the ctex leaf paths.
            if getattr(node, "cached_model", None) is not None:
                return False
            if getattr(node, "cached_texture", None) is not None:
                return False
            if self._is_dissolve_node(node) or self._is_imagedissolve_node(node):
                return False
            st = self._sm_state()
            _smf = getattr(self, "_sm_f", None)
            if _smf is None:
                _smf = _sm_fresh_f()
                self._sm_f = _smf
            stamp = self._sm_geometry_stamp()
            if stamp != getattr(self, "_sm_stamp", None):
                # Resize / layout change: a pinned bake can not survive it.
                self._sm_clear()
                self._sm_stamp = stamp
            frame = int(getattr(self, "_sm_frame", 0))
            nid = id(node)
            # Candidate slot identity: the engine rebuilds the tree per frame
            # during a transition, so a node-id-only churn memory would miss
            # exactly the animated candidates we must not bake every frame.
            # The walk position of a slot is stable across rebuilds.
            try:
                slot = (round(float(ctx.ox), 1), round(float(ctx.oy), 1), type(node).__name__)
            except Exception:  # noqa: BLE001 -- unusable ctx offset → no slot gate
                slot = None
            gate = getattr(self, "_sm_gate", None)
            pos_gate = getattr(self, "_sm_pos_gate", None)
            if gate is not None and int(gate.get(nid, 0)) > frame:
                sm_cache_why("churn_gated_node")
                _sm_miss_count()
                _smf[_SM_F_GATE] += 1
                return False
            if slot is not None and pos_gate is not None and int(pos_gate.get(slot, 0)) > frame:
                sm_cache_why("churn_gated_slot")
                _sm_miss_count()
                _smf[_SM_F_GATE] += 1
                return False
            # Negative memory (sound: it can only DELAY a bake, never serve a
            # stale RTT). Two verdicts are remembered per candidate identity
            # for _SM_NEG_TTL frames:
            #   * "small"       — the subtree provably holds < _SM_MIN_NODES
            #                     nodes, so neither the count nor the
            #                     fingerprint walk can pay off;
            #   * "uncacheable" — the fingerprint proved the subtree can never
            #                     be served from a pinned RTT (dissolve node,
            #                     surface-like leaf, ...), so every frame used
            #                     to re-walk it just to re-learn that.
            # A verdict reached only because the per-frame budget ran out is
            # NOT remembered (it says nothing about the subtree).
            neg = self._sm_neg
            nkey = slot if slot is not None else nid
            v = neg.get(nkey)
            if v is not None:
                if int(v[0]) > frame:
                    _smf[_SM_F_NEG] += 1
                    _sm_miss_count()
                    return False
                neg.pop(nkey, None)
            # Accounting ceiling: this frame has already spent its count +
            # fingerprint budget of wall time, so no further candidate is
            # evaluated (the leftover nodes fall back to the plain walk).
            _acct_ms = (_smf[_SM_F_CNT_MS] + _smf[_SM_F_FP_MS]) * 1000.0
            if _acct_ms >= _SM_ACCT_FRAME_MS:
                sm_cache_why("acct_frame_budget")
                _smf[_SM_F_ACCT_CAP] += 1
                return False
            _t0 = _time.perf_counter()
            _b0 = int(getattr(self, "_sm_count_budget", _SM_COUNT_FRAME_BUDGET))
            cnt = self._sm_count_nodes(node)
            _t1 = _time.perf_counter()
            _smf[_SM_F_CALLS] += 1
            _smf[_SM_F_CNT_N] += _b0 - int(getattr(self, "_sm_count_budget", _b0))
            _smf[_SM_F_CNT_MS] += _t1 - _t0
            if cnt is None:
                _smf[_SM_F_CNT_NONE] += 1
                return False
            if cnt == _SM_SMALL:
                # Subtree below the eligibility floor: remember it briefly so
                # the next frames skip the count walk as well.
                _smf[_SM_F_SMALL] += 1
                neg[nkey] = (frame + _SM_NEG_TTL, 1)
                return False
            key, nodes, limited = self._sm_key(node, self._sm_fp_allowance(_acct_ms))
            _t2 = _time.perf_counter()
            _smf[_SM_F_FP_N] += int(nodes)
            _smf[_SM_F_FP_MS] += _t2 - _t1
            _sm_cost(cnt, nodes, (_t1 - _t0), (_t2 - _t1))
            if key is None:
                _smf[_SM_F_KEY_NONE] += 1
                _smf[_SM_F_FP_N_NONE] += int(nodes)
                if int(nodes) > 1 and not limited:
                    # The refusal is a property of the subtree, not of the
                    # frame: do not pay for the same walk every frame. A walk
                    # cut off by the frame ceiling is NOT remembered.
                    neg[nkey] = (frame + _SM_NEG_TTL, 2)
                return False
            nw, nh = self._sm_bake_box(node)
            if nw <= 0 or nh <= 0:
                return False
            cur = self._sm_cur_keys
            prev = self._sm_prev_keys
            cur_pos = getattr(self, "_sm_cur_pos", None)
            prev_pos = getattr(self, "_sm_prev_pos", None)
            ent = st.get(key)
            if ent is not None and int(ent.frame) == frame - 1:
                # 2nd consecutive frame with the same key → reuse is allowed.
                _t3 = _time.perf_counter()
                _alive = self._sm_entry_alive(ent)
                _t4 = _time.perf_counter()
                _smf[_SM_F_ALIVE_MS] += _t4 - _t3
                if _alive:
                    ent.frame = frame
                    st.pop(key, None)
                    st[key] = ent
                    self._sm_draw_entry(ent, ctx)
                    _smf[_SM_F_TAIL_MS] += _time.perf_counter() - _t4
                    _smf[_SM_F_HIT] += 1
                    _SM_HITS += 1
                    _SM_HIT_TOTAL += 1
                    _SM_NODES_SAVED += max(0, int(ent.nodes) - 1)
                    _SM_NODES_SAVED_TOTAL += max(0, int(ent.nodes) - 1)
                    cur[nid] = key
                    if cur_pos is not None and slot is not None:
                        cur_pos[slot] = key
                    return True
                sm_cache_why("entry_dead_handle")
                st.pop(key, None)
                self._sm_retire(ent, defer=False)
            prev_key = prev.get(nid)
            cur[nid] = key
            prev_slot_key = None if (prev_pos is None or slot is None) else prev_pos.get(slot)
            if cur_pos is not None and slot is not None:
                cur_pos[slot] = key
            changed = (prev_key is not None and prev_key != key) or (
                prev_slot_key is not None and prev_slot_key != key
            )
            # Churn detection: ONE key change is a legitimate content change
            # (page switch, new text); a *run* of changes means the candidate is
            # animating, and baking it every frame would cost more than the walk
            # it replaces. Back off after _SM_CHURN_STREAK consecutive changes.
            streak = getattr(self, "_sm_streak", None)
            if streak is None:
                streak = {}
                self._sm_streak = streak
            skey = slot if slot is not None else nid
            if changed:
                n_streak = int(streak.get(skey, 0)) + 1
                streak[skey] = n_streak
            else:
                n_streak = 0
                streak.pop(skey, None)
            if n_streak >= _SM_CHURN_STREAK:
                if gate is not None:
                    gate[nid] = frame + _SM_UNSTABLE_BACKOFF
                if pos_gate is not None and slot is not None:
                    pos_gate[slot] = frame + _SM_UNSTABLE_BACKOFF
                streak.pop(skey, None)
                sm_cache_why("churn_backoff")
                _sm_miss_count()
                _smf[_SM_F_MISS] += 1
                return False
            if ent is not None:
                # Same key seen, but not on the previous frame: stale entry.
                sm_cache_why("stale_entry_rebake")
                st.pop(key, None)
                self._sm_retire(ent, defer=True)
            _t5 = _time.perf_counter()
            baked = self._sm_bake_pin(node, key, nw, nh, nodes, frame)
            _t6 = _time.perf_counter()
            _smf[_SM_F_BAKE_MS] += _t6 - _t5
            if baked is None:
                sm_cache_why("bake_refused")
                _smf[_SM_F_MISS] += 1
                return False
            self._sm_draw_entry(baked, ctx)
            _smf[_SM_F_PINS] += 1
            _smf[_SM_F_MISS] += 1
            _sm_miss_count()
            return True
        except Exception as e:  # noqa: BLE001 -- cache failure must never abort frame
            _host_draw_fail("sm_try_draw", e)
            return False

    def kill_textures(self):
        """Surftree pre-hook: clear the transition bake cache before the
        Texture mixin destroys everything. Pinned private RTTs are invisible
        to the RTT pool (never in _rtt_free / frame rings), so they must be
        destroyed here when kill_textures runs (resize / quit / display reset
        boundaries all funnel into draw.kill_textures). TextureMixin
        .kill_textures is next in MRO; explicit delegation keeps the product
        destruction order intact with no draw.py call-site change.
        """
        try:
            # hmc_fix3 F3: reverse-cache entries share this store, so clear on
            # either gate being enabled (whichever created entries is covered).
            if trans_bake_cache_enabled() or reverse_bake_cache_enabled():
                self._trans_cache_clear()
        except Exception:  # noqa: BLE001, S110 -- cache cleanup must never abort kill
            pass
        try:
            # Stable subtree model cache: pinned RTTs + pending destroys share
            # the same lifecycle boundary (resize / quit / display reset).
            if static_model_cache_enabled():
                self._sm_clear()
        except Exception:  # noqa: BLE001, S110 -- cache cleanup must never abort kill
            pass
        try:
            from .draw_texture import TextureMixin

            TextureMixin.kill_textures(self)
        except Exception as e:  # noqa: BLE001 -- delegate failure logged (re-raises under DRAW_RAISE)
            _host_draw_fail("kill_textures", e)

    def _child_to_texture(
        self, child, _trans_key=None, _trans_kind="tr", _dest_size=None, _trans_key_fn=None
    ):
        """Convert a mesh-child to HostTexture for model texture slots.

        ``_trans_key`` (optional) engages the per-frame subtree bake content
        cache (hmc_fix3 F3): on structural-key hit the pinned private RTT is
        returned directly — no render_to_texture pass is issued. On miss the
        child is baked into a private RTT (pin) when possible; a pin failure
        falls through to the normal pooled path below, unchanged. ctx/uv/
        position math is never touched — only the sampled texture content is
        substituted (identical source w/h ↔ HostTexture shape).

        ``_trans_key_fn`` (hmc_fix4 A, optional) is the LAZY form of
        ``_trans_key``: it is invoked only when the walk has passed every cheap
        qualification test above and is about to make a cache decision, so a
        child resolved by ``cached_texture`` / ``_extract_host_texture`` /
        full-pixel solid slot / surface load never pays for a subtree
        fingerprint walk (measured: 95–96.5% of all derived keys were thrown
        away un-consumed before this change).

        ``_trans_kind`` selects the counter set: the dissolve old-slot entry
        point keeps kind "tr" (shared trans counters, cadence unchanged); the
        reverse-scale entry point uses ``_reverse_bake_key`` + kind "rev"
        (independent ``rev_cache_hit/miss`` window, RENPY_HOST_REVERSE_BAKE_
        CACHE gated in ``_draw_reverse`` itself).

        ``_dest_size`` (hmc_fix3 F2, walker-internal) shrinks the RTT bake to
        the dest box while the child's logical coordinates stay the span
        (bake crop-out at dest resolution, see render_to_texture); None keeps
        the old child-size bake.
        """
        if child is None:
            return None
        if isinstance(child, HostTexture):
            return child if child.handle > 0 else None
        if isinstance(child, int) and not isinstance(child, bool):
            return HostTexture(child, 1, 1) if child > 0 else None
        if self._is_render_like(child):
            ct = getattr(child, "cached_texture", None)
            if self._rtt_cached_sample_ok(child):
                if isinstance(ct, HostTexture) and ct.handle > 0:
                    return ct
                if isinstance(ct, int) and not isinstance(ct, bool) and ct > 0:
                    w, h = self._node_size(child)
                    return HostTexture(ct, w, h)
            pure = self._extract_host_texture(child)
            if pure is not None:
                return pure
            solid_slot = self._solid_reverse_slot_texture(child)
            if solid_slot is not None:
                return solid_slot
            if not getattr(child, "loaded", False):
                try:
                    self.load_all_textures(child)
                except Exception as e:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    _host_draw_fail("child_to_texture.load_all_textures", e)
                pure = self._extract_host_texture(child)
                if pure is not None:
                    return pure
                solid_slot = self._solid_reverse_slot_texture(child)
                if solid_slot is not None:
                    return solid_slot
                ct = getattr(child, "cached_texture", None)
                if self._rtt_cached_sample_ok(child):
                    if isinstance(ct, HostTexture) and ct.handle > 0:
                        return ct
                    if isinstance(ct, int) and not isinstance(ct, bool) and ct > 0:
                        w, h = self._node_size(child)
                        return HostTexture(ct, w, h)
            # Per-frame subtree content cache (hmc_fix3 F3): dissolve old slot
            # (kind "tr", byte-identical counters/cadence) and reverse scale
            # (kind "rev") share the same store + clear hooks.
            # hmc_fix4 A: the key is derived HERE (after every cheap path above
            # has declined), so only a child that really needs the cache pays
            # for the fingerprint walk; ``_trans_key_fn`` is the lazy deriver.
            if _trans_key is None and _trans_key_fn is not None:
                try:
                    _trans_key = _trans_key_fn()
                except Exception as e:  # noqa: BLE001 -- key failure → normal bake
                    _host_draw_fail("child_to_texture.trans_key_fn", e)
                    _trans_key = None
            if _trans_key is not None:
                try:
                    tw, th = self._node_size(child)
                    if tw <= 0 or th <= 0:
                        tw, th = self.virtual_size
                    if _dest_size is not None:
                        try:
                            _ds0, _ds1 = (int(_dest_size[0]), int(_dest_size[1]))
                        except Exception:  # noqa: BLE001 -- bad dest → node-size cache shape
                            _ds0 = _ds1 = 0
                        if _ds0 > 0 and _ds1 > 0:
                            tw, th = _ds0, _ds1
                    hit = self._child_bake_cache_fetch(
                        child, _trans_key, tw, th, kind=_trans_kind
                    )
                    if hit is not None:
                        return hit
                    # Fetch miss / private pin failed → the normal pooled
                    # bake below still runs (unchanged).
                except Exception as e:  # noqa: BLE001 -- cache failure must not abort frame
                    _host_draw_fail("child_to_texture.trans_cache", e)
            # Phase 0d bake-attribution: this is the mesh-children → texture
            # seam; nested render_to_texture lands in an RTT bake here.
            if _BAKE_ENABLED:
                _cw, _ch = self._node_size(child)
                bake_attn(self, "child_tex", child, _cw, _ch)
            handle = self.render_to_texture(child, _dest_size=_dest_size)
            ht = None
            if isinstance(handle, HostTexture):
                ht = handle if handle.handle > 0 else None
            elif isinstance(handle, int) and not isinstance(handle, bool) and handle > 0:
                if _dest_size is not None:
                    try:
                        _lab = (max(1, int(_dest_size[0])), max(1, int(_dest_size[1])))
                    except Exception:  # noqa: BLE001 -- fall back to node size label
                        _lab = None
                    if _lab is not None:
                        w, h = _lab
                    else:
                        w, h = self._node_size(child)
                else:
                    w, h = self._node_size(child)
                ht = HostTexture(handle, w, h)
            if ht is not None:
                try:
                    # Stamp the bake generation so next-frame pool handles
                    # (freelist re-target) are refused by _rtt_cached_sample_ok.
                    # hmc_fix3 F2: a dest-size bake's pixel rect differs from the
                    # child's logical node box, so don't claim this handle under
                    # the child's cached_texture (other consumers label with node
                    # size); the exact child-size bake keeps the old stamp.
                    if _dest_size is None:
                        child.cached_texture = ht
                        child._cached_texture_gen = int(getattr(self, "_rtt_gen", 0))
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
            return ht
        if self._is_surface_like(child):
            tex = self.load_texture(child)
            if isinstance(tex, HostTexture):
                return tex if tex.handle > 0 else None
            if isinstance(tex, int) and not isinstance(tex, bool) and tex > 0:
                try:
                    w, h = child.get_size()
                    return HostTexture(tex, max(1, int(w)), max(1, int(h)))
                except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    return HostTexture(tex, 1, 1)
            return None
        if hasattr(child, "get_size") or hasattr(child, "_pixels"):
            try:
                tex = self.load_texture(child)
                if isinstance(tex, HostTexture):
                    return tex if tex.handle > 0 else None
                if isinstance(tex, int) and not isinstance(tex, bool) and tex > 0:
                    try:
                        w, h = child.get_size()
                        return HostTexture(tex, max(1, int(w)), max(1, int(h)))
                    except Exception:  # noqa: BLE001 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                        return HostTexture(tex, 1, 1)
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass
        # Phase 0d bake-attribution: generic child → bake tail branch.
        if _BAKE_ENABLED:
            _cw, _ch = self._node_size(child)
            bake_attn(self, "child_tex", child, _cw, _ch)
        try:
            handle = self.render_to_texture(child, _dest_size=_dest_size)
            ht = None
            if isinstance(handle, HostTexture):
                ht = handle if handle.handle > 0 else None
            elif isinstance(handle, int) and not isinstance(handle, bool) and handle > 0:
                if _dest_size is not None:
                    try:
                        w, h = max(1, int(_dest_size[0])), max(1, int(_dest_size[1]))
                    except Exception:  # noqa: BLE001 -- fall back to node size label
                        w, h = self._node_size(child)
                else:
                    w, h = self._node_size(child)
                ht = HostTexture(handle, w, h)
            if ht is not None and self._is_render_like(child):
                try:
                    # hmc_fix3 F2: keep the exact child-size stamp semantics —
                    # a dest-size bake is not claimed under cached_texture.
                    if _dest_size is None:
                        child.cached_texture = ht
                        # Stamp the bake generation so next-frame pool handles
                        # (freelist re-target) are refused by _rtt_cached_sample_ok.
                        child._cached_texture_gen = int(getattr(self, "_rtt_gen", 0))
                except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                    pass
            return ht
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
        return None

    def _make_model_leaf(self, w, h, textures, mesh_obj=None, shaders=None, uniforms=None):
        """Lightweight model leaf for prepared ``cached_model`` (GL2Model stand-in)."""
        class _ModelLeaf:
            pass
        leaf = _ModelLeaf()
        leaf.width = int(w)
        leaf.height = int(h)
        leaf.texture = textures[0] if textures else None
        leaf.textures = list(textures) if textures else None
        leaf.mesh = True if mesh_obj is None else mesh_obj
        if shaders:
            leaf.shaders = tuple(shaders)
        else:
            leaf.shaders = ("renpy.texture",) if textures else ("renpy.solid",)
        leaf.color = None
        leaf.vertices = None
        leaf.indices = None
        leaf.pipeline = None
        leaf.uniforms = uniforms
        leaf.ndc = None
        leaf.children = None
        leaf.cached_model = None
        leaf.blits = None
        return leaf

    def _iter_children(self, node):
        """Yield (child, xo, yo) from Render-like children / blits."""
        children = getattr(node, "children", None)
        if children:
            for entry in children:
                if entry is None:
                    continue
                if isinstance(entry, (tuple, list)):
                    child = entry[0]
                    xo = float(entry[1]) if len(entry) > 1 else 0.0
                    yo = float(entry[2]) if len(entry) > 2 else 0.0
                    yield child, xo, yo
                else:
                    yield entry, 0.0, 0.0
        blits = getattr(node, "blits", None)
        if blits:
            for entry in blits:
                if entry is None:
                    continue
                if isinstance(entry, (tuple, list)):
                    child = entry[0]
                    xo = float(entry[1]) if len(entry) > 1 else 0.0
                    yo = float(entry[2]) if len(entry) > 2 else 0.0
                    yield child, xo, yo
                else:
                    yield entry, 0.0, 0.0

    def _node_size(self, node, default=None):
        """Best-effort (w, h) from a Render/Model/Surface-like node."""
        if default is None:
            default = self.virtual_size
        if isinstance(node, HostTexture):
            try:
                return max(1, int(node.w)), max(1, int(node.h))
            except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
                pass
        w = float(getattr(node, "width", 0) or getattr(node, "w", 0) or 0)
        h = float(getattr(node, "height", 0) or getattr(node, "h", 0) or 0)
        if w > 0 and h > 0:
            return max(1, int(w)), max(1, int(h))
        try:
            if hasattr(node, "get_size"):
                gw, gh = node.get_size()
                if gw and gh:
                    return max(1, int(gw)), max(1, int(gh))
        except Exception:  # noqa: BLE001, S110 -- wgpu host must not abort frame — residual logged via _host_draw_fail/_phase0_log where needed
            pass
        dw = int(default[0]) if default and len(default) > 0 else 0
        dh = int(default[1]) if default and len(default) > 1 else 0
        if dw <= 0 or dh <= 0:
            return 0, 0
        return max(1, dw), max(1, dh)

    def _bake_mesh_children(self, node, children):
        """Bake mesh children into an RTT leaf (surftree-owned)."""
        if _WALKSTAT_ON:
            try:
                self._frame_bakes = getattr(self, "_frame_bakes", 0) + 1
            except Exception:  # noqa: BLE001, S110 -- counter is diagnostics-only
                pass
        try:
            import renpy_host  # type: ignore
            self._ensure_pipes()
            w, h = self._node_size(node)
            if w <= 0 or h <= 0:
                w, h = self.virtual_size
            # Phase 0d bake-attribution: whole mesh-child subtree baked into
            # an RTT here (nested begin_target frame, one bake per call).
            bake_attn(self, "mesh_children", node, w, h)
            # Tile-isolate the instance group around the nested bake (same
            # contract as render_to_texture._rtt_pass): pending parent quads
            # flush into the parent target BEFORE begin_target (painter order),
            # and bake-target quads never merge into the parent frame's group
            # map (they would be committed into the wrong parent target).
            _grp = getattr(self, "_instance_group", None)
            if _grp is not None:
                try:
                    if not _grp.empty():
                        _grp.flush(self)
                    _grp.push()
                except Exception:
                    _grp = None
                # Pending parent batch items precede the bake content in tree
                # order; commit them into the parent target first.
                self._flush_draw_batch()
            try:
                rtt = self._acquire_rtt(w, h)
                renpy_host.begin_target(rtt)
                renpy_host.begin_frame()
                old_vs = self.virtual_size
                old_clip = self._clip_rect
                old_poly = getattr(self, "_clip_poly", None)
                try:
                    self.virtual_size = (w, h)
                    self._clip_rect = None
                    self._clip_poly = None
                    for child, cx, cy in children:
                        self._draw_node(child, float(cx), float(cy))
                finally:
                    self.virtual_size = old_vs
                    self._clip_rect = old_clip
                    self._clip_poly = old_poly
                    try:
                        self._end_frame_present()
                        renpy_host.end_target()
                    except Exception as e:
                        _host_draw_fail("mesh_bake.end_frame", e)
            finally:
                if _grp is not None:
                    try:
                        _grp.pop()
                    except Exception:
                        pass
            class _BakedLeaf:
                pass
            leaf = _BakedLeaf()
            leaf.width = w
            leaf.height = h
            leaf.texture = int(rtt)
            leaf.mesh = True
            leaf.shaders = ("renpy.texture",)
            leaf.color = None
            leaf.textures = None
            leaf.vertices = None
            leaf.indices = None
            leaf.pipeline = None
            leaf.uniforms = None
            leaf.ndc = None
            leaf.children = None
            leaf.cached_model = None
            leaf.blits = None
            return leaf
        except Exception as e:
            _host_draw_fail("mesh_bake", e)
            return None


__all__ = ["SurftreeMixin"]
