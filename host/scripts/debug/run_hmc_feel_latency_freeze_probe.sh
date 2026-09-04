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
export RENPY_HOST_GATE=hmc_feel_latency_freeze_probe
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
: > /tmp/hmc_feel_latency_freeze_probe.log
: > "$ROOT/host/target/gate-hmc_feel_latency_freeze_probe.txt"
: > "$ROOT/host/target/gate-hmc_feel_latency_freeze_probe.json"
mkdir -p .omx/tmp/wp0
ls "$RENPY_HOST_GAME/game" | head
echo "=== launching feel latency probe ==="
"$ROOT/host/target/release/renpy-host" "$RENPY_HOST_GAME" > .omx/tmp/wp0/feel-latency-stdout.log 2> .omx/tmp/wp0/feel-latency-stderr.log
echo "exit=$?"
echo "--- gate artifact ---"
cat host/target/gate-hmc_feel_latency_freeze_probe.txt 2>/dev/null || echo "no artifact"
echo "--- json summary ---"
python3 -c "import json; from pathlib import Path; p=Path('host/target/gate-hmc_feel_latency_freeze_probe.json'); d=json.loads(p.read_text()) if p.exists() and p.stat().st_size else None; print('no json' if not d else 'ok');
import sys
" 2>/dev/null || true
python3 <<'PY2'
import json
from pathlib import Path
p = Path("host/target/gate-hmc_feel_latency_freeze_probe.json")
if not p.exists() or p.stat().st_size == 0:
    print("no json")
else:
    d = json.loads(p.read_text())
    print("ok", d.get("ok"), "measured", d.get("measured"))
    print("ac_t", d.get("ac_t_pass_lt_200ms"), "max_fi", d.get("first_interactive_max_ms"))
    print("ac_f", d.get("ac_f_proxy_p99_le_66"), "p99_max", d.get("p99_inter_present_max_ms"))
    print("ac_p99", d.get("ac_p99_pass_le_8_3"), "mm", d.get("ac_p99_main_menu_ms"), "prefs", d.get("ac_p99_prefs_idle_ms"))
    print("ac_z", d.get("ac_z"))
    for h in d.get("h_rank_hints") or []:
        print("H", h.get("id"), h.get("severity_hint"), h.get("evidence"))
    for k, v in (d.get("measurements") or {}).items():
        print("m", k, "fi=", v.get("first_interactive_ms"), "stall=", v.get("stall_ge_2s"), "hang=", v.get("hang_suspect"), "ready=", v.get("target_ready_end"))
    for k, v in (d.get("continuous") or {}).items():
        print("c", k, "fps=", v.get("product_fps"), "p99=", v.get("p99_inter_present_ms"), "max_gap=", v.get("max_inter_present_ms"))
    print("take_focuses", d.get("take_focuses_repro"))
PY2
echo "--- log tail ---"
tail -100 /tmp/hmc_feel_latency_freeze_probe.log 2>/dev/null || true
echo "--- stderr tail ---"
tail -50 .omx/tmp/wp0/feel-latency-stderr.log 2>/dev/null || true
