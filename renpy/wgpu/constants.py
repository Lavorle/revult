"""Wgpu constants — single source of truth (T6 full table).

Every numeric literal that previously lived as a magic value inside draw.py /
rtt_pool.py / video.py / text.py / shaders compositor is declared here once,
with a ``# src: <file:line>`` provenance comment pointing at the call site it
replaces. Keep this the ONLY place these numbers are written.
"""

from __future__ import annotations

# ── Draw caches / caps (draw.py) ──
HANDLE_PIXELS_CAP = 2048  # src: draw.py:98 — dead-handle pixel recovery stash cap
MESH_CACHE_CAP = 4096  # src: draw.py:122 — geometry-keyed mesh cache cap
RTT_FREELIST_CAP = 8  # src: draw.py:112 — free RTT handles kept per (w,h) size
RTT_POOL_MAX_PER_SIZE = 16  # src: rtt_pool.py (per-size live cap; T8 extract helper)

# ── Golden / max texture sizes (draw.py / draw_texture.py) ──
GOLDEN_FALLBACK_W = 1920  # src: draw.py:351 / draw_texture.py:334 — AC2 layout size
GOLDEN_FALLBACK_H = 1080  # src: draw.py:351 / draw_texture.py:334 — AC2 layout size
MAX_TEX_W = 7680  # src: draw.py:425 — desktop-ish max width when host has no monitor query
MAX_TEX_H = 4320  # src: draw.py:426 — desktop-ish max height when host has no monitor query

# ── Present / draw (draw_screen.py / draw_traversal.py) ──
PRESENT_LOCK_TIMEOUT = 30.0  # src: draw_screen.py:96 / draw_traversal.py:371 — product draw lock

# ── FFMPEG decode (video.py defaults, T5) ──
FFMPEG_CHUNK_FRAMES = 20  # src: video.py — progressive decode chunk frame count
FFMPEG_KICKSTART_FRAMES = 8  # src: video.py — kickstart (publish-every-frame) frames
FFMPEG_TIMEOUT_BASE = 30.0  # src: video.py:55 — decode chunk timeout floor (seconds)
FFMPEG_TIMEOUT_PER_FRAME = 0.15  # src: video.py — decode chunk timeout per frame (seconds)

# ── Present / draw (T6) ──
AUTO_MIPMAP_THRESH = 0.75  # src: draw.py:250 — draw_per_virt below which auto-mipmap engages

# ── Text / iso basis ──
PIL_PADDING = 4  # src: text.py:40 — glyph bitmap padding (px) each side
ISO_BASIS_X = 0.866  # src: draw_walk.py:10 — isometric tile projection X basis (cos30)
ISO_BASIS_Y = 0.5  # src: draw_walk.py:11 — isometric tile projection Y basis (sin30/2)

# ── Instance ring / perf gate (M1 Wave32 Phase 1a) ──
INSTANCE_RING_INIT = 4096  # src: arena.rs:16 — instance ring initial capacity (M1 Wave32)
PERF_GATE_THRESH = 10  # src: arena.rs:22 / host_bridge.get_frame_stats — draw_calls/quads perf gate threshold

# ── Grouped instancing (M1 T3) ──
INSTANCE_GROUP_MIN = 2  # src: host_bridge._InstanceGroup — minimum quads to emit grouped draw_instances (else single instance path)
INSTANCE_GROUP_MAX_BATCH = 8192  # src: arena.draw_instances — cap per draw_instances call to bound ring growth
