# HuangmeiC playtest basedir

In-repo `RENPY_HOST_GAME` basedir for HuangmeiC recovered assets on renpy-host/wgpu.

## Source assets

| Item | Value |
|------|-------|
| Default source | `HUANGMEIC_GAME_SRC=/mnt/nvme0n1p2/@home/isah1221/huangmeic/recovered_project` |
| Runtime game/ | multi-symlink overlay (gitignored; created by the launcher) |

Override the source with `HUANGMEIC_GAME_SRC` if the recovered tree lives elsewhere.

**`recovered_project` is READ-ONLY. Do not mutate it.**

## Runtime multi-symlink overlay

The launcher does **not** point `game` at recovered_project as a single symlink.
Instead it builds a real `game/` directory (same pattern as the recovery validation
project) that:

| Path under `game/` | Kind | Target / contents |
|--------------------|------|-------------------|
| `audio/` `fonts/` `gui/` `images/` `scripts/` `video/` | symlink | `$HUANGMEIC_GAME_SRC/<name>` |
| `script_version.txt` | file (host-owned) | `(8, 5, 3)` |
| `zz_host_atl_uniforms.rpy` (and other host injects) | file (host-owned) | copied from `host_overlay/` |
| `cache/` `saves/` `saves_2/` | local dirs | writable runtime state |

### `host_overlay/`

Tracked host-only injects live under `host_overlay/` (not under recovered_project).
The launcher copies them into `game/` on each overlay ensure/`--relink`. Use this for
compat shims (e.g. early `add_uniform` for HuangmeiC `dissolve_transform` ATL props).

### Why `script_version.txt`?

HuangmeiC was built against **Ren'Py 8.5.3**, which auto-scans `game/audio/` at
init priority 0 and fills `store.audio.*` from basenames. Upstream Ren'Py 8.6
defaults `config.late_audio_scan = True` (scan at init 1900). Without a
script_version ≤ `(8, 5, 99)`, this line in `options.rpy` fails before the late
scan runs:

```renpy
define config.main_menu_music = audio.main_menu_theme
```

`00compat._set_script_version((8, 5, 3))` restores early audio scan + other 8.5.x
compat knobs. The file is injected here so recovered_project stays immutable.

- `game/` is listed in `.gitignore` and is **not** committed.
- Pass `--relink` to force rebuild of asset links and restore `script_version.txt`.

Do not hand-edit the overlay; use the launcher.

## Usage

From the repo root:

```bash
# Smoke: process up N seconds, no crash → exit 0 (default N=30)
./host/scripts/run_huangmeic_playtest.sh --smoke

# Smoke with explicit duration
./host/scripts/run_huangmeic_playtest.sh --smoke 60

# Interactive / normal play
./host/scripts/run_huangmeic_playtest.sh --normal
```

### Defaults

- **Release binary** (`cargo build -p renpy-host --release`) is the default.
- **MangoHud is required always** — the launcher wraps the host with `mangohud`.

### Smoke pass criteria

- Process stays up for N seconds after start with no crash → exit 0.
- The smoke clock is **host-internal and starts after process start**. A first release cargo build can take a long time *before* that clock begins; build time is not counted against the smoke window.

### MangoHud notes

The MangoHud overlay is a best-effort residual:

- Wrap is **required** (launcher always invokes via `mangohud`).
- A visible HUD may need extra tweaks such as `--dlsym` or `MANGOHUD_CONFIG` adjustments depending on the environment.
