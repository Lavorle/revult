"""Wgpu constants — single source of truth (T1 stub, T6 full)."""

from __future__ import annotations

# T1 placeholder — will be expanded in T6 to the full table.
# Kept as single source to avoid magic literals scattered in draw/rtt_pool/video/text.
PIL_PADDING = 4
ISO_BASIS_X = 0.866
ISO_BASIS_Y = 0.5

# ── FFMPEG decode (video.py defaults, T5) ──
FFMPEG_CHUNK_FRAMES = 20
FFMPEG_KICKSTART_FRAMES = 8
FFMPEG_TIMEOUT_BASE = 30.0
FFMPEG_TIMEOUT_PER_FRAME = 0.15

# ── Present / draw (T6) ──
PRESENT_LOCK_TIMEOUT = 30.0
AUTO_MIPMAP_THRESH = 0.75
