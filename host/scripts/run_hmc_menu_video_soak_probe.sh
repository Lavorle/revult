#!/usr/bin/env bash
set -uo pipefail
ROOT=/mnt/nvme1n1p2/revult
cd "$ROOT"
pkill -f '/host/target/release/renpy-host' 2>/dev/null || true
sleep 0.5
export RENPY_HOST_BASE="$ROOT"
export RENPY_HOST_GAME="$ROOT/host/playtests/HuangmeiC"
export HUANGMEIC_GAME_SRC="${HUANGMEIC_GAME_SRC:-/mnt/nvme0n1p2/@home/isah1221/huangmeic/recovered_project}"
export CARGO_TARGET_DIR="$ROOT/host/target"
export PYTHONPATH="$ROOT/host/python/gates:$ROOT/host/python${PYTHONPATH:+:$PYTHONPATH}"
export RUST_LOG="info,wgpu_hal=off,wgpu_core=off,naga=off"
export RENPY_HOST_GATE=hmc_menu_video_soak_probe
export RENPY_HOST_PHASE0_SIGNALS=1
export RENPY_HOST_SMOKE_SECS=60
export RENPY_HOST_MANGOHUD=off
export RENPY_PERFORMANCE_TEST=0
export RENPY_HOST_BUILD=1
export DISPLAY=:0
export RENPY_HOST_MOVIE_W=1920 RENPY_HOST_MOVIE_H=1080
export RENPY_HOST_MOVIE_LAYOUT_W=1920 RENPY_HOST_MOVIE_LAYOUT_H=1080
export RENPY_HOST_MOVIE_MAX_FRAMES=360 RENPY_HOST_MOVIE_CHUNK_FRAMES=90
export RENPY_HOST_MOVIE_KICKSTART_FRAMES=30 RENPY_HOST_MOVIE_MIN_PLAYABLE=8
export RENPY_HOST_MOVIE_RSS_MB=4096 RENPY_HOST_MOVIE_FPS=30
export RENPY_HOST_MOVIE_PRESENT=1b RENPY_HOST_MOVIE_LAYOUT_CACHE=1
export RENPY_HOST_MOVIE_PUBLISH_CAP=8 RENPY_HOST_MOVIE_CONTINUE_PUBLISH=1
export RENPY_HOST_MOVIE_RING_FRAMES=0 RENPY_HOST_MOVIE_CONTINUE_KICKSTART=64
export RENPY_HOST_MOVIE_LIVE_CAP=0 RENPY_HOST_MOVIE_RING_SWAP_EVERY=8
export RENPY_HOST_MOVIE_DECODE_MODE=auto
export RENPY_HOST_MOVIE_PIPE_QUEUE=180
export RENPY_HOST_WARM_MENU_VIDEO=1
unset RENPY_SKIP_MAIN_MENU RENPY_SKIP_SPLASHSCREEN 2>/dev/null || true
: > /tmp/hmc_menu_video_soak_probe.log
: > "$ROOT/host/target/gate-hmc_menu_video_soak_probe.txt"
: > "$ROOT/host/target/gate-hmc_menu_video_soak_probe.json"
mkdir -p .omx/tmp/wp0
ls "$RENPY_HOST_GAME/game" | head
echo "=== launching menu video soak probe ==="
"$ROOT/host/target/release/renpy-host" "$RENPY_HOST_GAME" > .omx/tmp/wp0/menu-video-soak-stdout.log 2> .omx/tmp/wp0/menu-video-soak-stderr.log
echo "exit=$?"
echo "--- gate artifact ---"
cat host/target/gate-hmc_menu_video_soak_probe.txt 2>/dev/null || echo "no artifact"
echo "--- json summary ---"
python3 -c "import json; from pathlib import Path; p=Path('host/target/gate-hmc_menu_video_soak_probe.json'); d=json.loads(p.read_text()) if p.exists() and p.stat().st_size else None; print('no json' if not d else 'ok');
import sys
" 2>/dev/null || true
python3 <<'PY2'
import json
from pathlib import Path
p = Path("host/target/gate-hmc_menu_video_soak_probe.json")
if not p.exists() or p.stat().st_size == 0:
    print("no json")
else:
    d = json.loads(p.read_text())
    print("ok", d.get("ok"), "measured", d.get("measured"), "pass", d.get("pass"))
    print("main_menu", d.get("main_menu"))
    print("ac_m_soak", d.get("ac_m_soak"))
    print("ac_z", d.get("ac_z"))
    print("early_p99", d.get("early_p99_inter_present_ms"), "late_p99", d.get("late_p99_inter_present_ms"), "delta", d.get("p99_delta_late_minus_early"))
    for h in d.get("h_rank_hints") or []:
        print("H", h.get("id"), h.get("severity_hint"), h.get("evidence"))
    for k, v in (d.get("windows") or {}).items():
        print(
            "w", k,
            "fps=", v.get("product_fps"),
            "p99=", v.get("p99_inter_present_ms"),
            "max_gap=", v.get("max_inter_present_ms"),
            "adv=", v.get("frame_index_advances"),
            "nframes=", v.get("path_cache_nframes_end"),
            "inflight=", v.get("path_cache_inflight_end"),
            "host-prod=", v.get("host_frames_minus_product"),
            "stall=", v.get("stall_ge_2s"),
        )
PY2
echo "--- log tail ---"
tail -100 /tmp/hmc_menu_video_soak_probe.log 2>/dev/null || true
echo "--- stderr tail ---"
tail -50 .omx/tmp/wp0/menu-video-soak-stderr.log 2>/dev/null || true
