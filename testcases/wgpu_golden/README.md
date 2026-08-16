# wgpu_golden — pixel baselines for host goldens (AC6)

Format of `baseline.rgba` / `actual.rgba`:

```
magic:  b"RGBA"
width:  u32 LE
height: u32 LE
pixels: width*height*4 bytes, Rgba8Unorm, tight rows (no padding)
```

## Suite G01–G08 (Phase 9)

| ID | Dir | Gate | Scene |
|----|-----|------|-------|
| G01 | `G01_solid_image/` | `g01` | solid rect + textured checker (geometry, texture, PMA) |
| G02 | `G02_text/` | `g02` | dialogue box + bitmap text atlas upload |
| G03 | `G03_dissolve/` | `g03` | dissolve pipeline full-frame-ish quad |
| G04 | `G04_blur/` | `g04` | blur pipeline, blur_log2=2, checkerboard |
| G05 | `G05_movie/` | `g05` | video texture (fixed synthetic gradient frame) |
| G06 | `G06_live2d/` | `g06` | Live2D idle sample (fixed pose multi-mesh + mask RTT) |
| G07 | `G07_model/` | `g07` | procedural assimp-style cube + textured quad |
| G08 | `G08_mask/` | `g08` | dual-texture alpha mask |

## Metric (plan §6.1 / AC6)

- mean absolute error ≤ 2/255
- max channel delta ≤ 16
- capture: **pre-present** game RT via `read_game_rt_rgba`

## Bootstrap

First run with missing `baseline.rgba` writes the baseline and logs
`baseline written`. Re-baseline only with explicit changelog.

## Run

```bash
cd host
export RENPY_HOST_BASE=$(cd .. && pwd)
export PYTHONPATH=$RENPY_HOST_BASE/host/python/gates

# Full Phase 9 CI (build + ldd + G01–G08 + regressions)
bash scripts/phase9_gates.sh

# Or individual goldens
for g in g01 g02 g03 g04 g05 g06 g07 g08; do
  RENPY_HOST_GATE=$g cargo run -p renpy-host || exit 1
done
```

Shared harness: `host/python/gates/golden_mae.py`.
