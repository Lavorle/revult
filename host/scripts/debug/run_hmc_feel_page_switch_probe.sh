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
export RENPY_HOST_GATE=hmc_feel_page_switch_probe
export RENPY_HOST_PHASE0_SIGNALS=1
export RENPY_HOST_SMOKE_SECS=180
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
export RENPY_HOST_MOVIE_PUBLISH_CAP=4 RENPY_HOST_MOVIE_CONTINUE_PUBLISH=1
export RENPY_HOST_MOVIE_RING_FRAMES=90 RENPY_HOST_MOVIE_CONTINUE_KICKSTART=64
export RENPY_HOST_WARM_MENU_VIDEO=1
unset RENPY_SKIP_MAIN_MENU RENPY_SKIP_SPLASHSCREEN 2>/dev/null || true
: > /tmp/hmc_feel_page_switch_probe.log
mkdir -p .omx/tmp/wp0 host/target
"$ROOT/host/target/release/renpy-host" "$RENPY_HOST_GAME" > .omx/tmp/wp0/page-switch-stdout.log 2> .omx/tmp/wp0/page-switch-stderr.log
echo "exit=$?"
python3 - <<'PY2'
import json
from pathlib import Path
p = Path("host/target/gate-hmc_feel_page_switch_probe.json")
if p.exists() and p.stat().st_size:
    d = json.loads(p.read_text())
    print("page_switch ok", d.get("ok"))
else:
    t = Path("host/target/gate-hmc_feel_page_switch_probe.txt")
    print(t.read_text()[:2000] if t.exists() else "no artifact")
PY2

