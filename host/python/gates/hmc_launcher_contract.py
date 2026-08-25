#!/usr/bin/env python3
"""Hermetic contract checks for the HuangmeiC launcher and wrapper."""  # noqa: EXE001

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LAUNCHER = ROOT / "host/scripts/run_huangmeic_playtest.sh"
DEFAULT_WRAPPER = ROOT / "host/run_huangmeic_playtest.sh"

MOVIE_ENV = {
    "RENPY_HOST_MOVIE_W",
    "RENPY_HOST_MOVIE_H",
    "RENPY_HOST_MOVIE_LAYOUT_W",
    "RENPY_HOST_MOVIE_LAYOUT_H",
    "RENPY_HOST_MOVIE_FPS",
    "RENPY_HOST_MOVIE_PRESENT",
    "RENPY_HOST_MOVIE_MAX_FRAMES",
    "RENPY_HOST_MOVIE_CHUNK_FRAMES",
    "RENPY_HOST_MOVIE_KICKSTART_FRAMES",
    "RENPY_HOST_MOVIE_MIN_PLAYABLE",
    "RENPY_HOST_MOVIE_LAYOUT_CACHE",
    "RENPY_HOST_MOVIE_RSS_MB",
}
RUNTIME_COMMANDS = (
    "chmod",
    "cmp",
    "cp",
    "find",
    "ln",
    "mangohud",
    "mkdir",
    "mktemp",
    "mv",
    "python3",
    "readlink",
    "realpath",
    "rm",
    "stat",
    "tr",
)


class GateFailure(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


def decode_nul(data: bytes) -> list[str]:
    items = data.split(b"\0")
    if items and items[-1] == b"":
        items.pop()
    return [os.fsdecode(item) for item in items]


def executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def link_command(directory: Path, name: str) -> None:
    target = shutil.which(name)
    require(target is not None, f"required command is unavailable: {name}")
    (directory / name).symlink_to(Path(target).resolve())


def tree_manifest(root: Path) -> tuple[tuple[object, ...], ...]:
    records: list[tuple[object, ...]] = []
    for path in sorted(root.rglob("*"), key=lambda item: os.fsencode(str(item.relative_to(root)))):
        info = path.lstat()
        relative = os.fsencode(str(path.relative_to(root)))
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISREG(info.st_mode):
            records.append((relative, "file", mode, info.st_size, info.st_mtime_ns, path.read_bytes()))
        elif stat.S_ISDIR(info.st_mode):
            records.append((relative, "dir", mode, info.st_size, info.st_mtime_ns))
        elif stat.S_ISLNK(info.st_mode):
            records.append((relative, "symlink", mode, info.st_size, info.st_mtime_ns, os.readlink(path)))
        else:
            records.append((relative, "other", mode, info.st_size, info.st_mtime_ns))
    return tuple(records)


class Harness:
    def __init__(self, launcher: Path, wrapper: Path) -> None:
        self.launcher = launcher
        self.wrapper = wrapper
        self.temp = tempfile.TemporaryDirectory(prefix="hmc-launcher-contract-")
        self.root = Path(self.temp.name)
        self.tools = self.root / "tools"
        self.build_tools = self.root / "build-tools"
        self.target = self.root / "cargo-target"
        self.game = self.root / "basedir"
        self.cargo_capture = self.root / "cargo.argv"
        self.host_capture = self.root / "host.argv"
        self.env_capture = self.root / "host.env"
        self.mangohud_capture = self.root / "mangohud.argv"
        self.forbidden_capture = self.root / "forbidden-runtime-command"

        self.tools.mkdir()
        self.build_tools.mkdir()
        (self.target / "release").mkdir(parents=True)
        (self.target / "debug").mkdir(parents=True)
        (self.game / "game").mkdir(parents=True)
        (self.game / "game/script_version.txt").write_text("(8, 5, 3)\n", encoding="utf-8")

        for name in ("bash", "basename", "dirname", "python3", "realpath", "sed"):
            link_command(self.tools, name)
        for name in ("bash", "basename", "dirname"):
            link_command(self.build_tools, name)

        cargo_body = r'''#!/bin/bash
set -eu
printf '%s\0' "$PWD" "$CARGO_TARGET_DIR" "$@" >"$HMC_CARGO_CAPTURE"
'''
        executable(self.tools / "cargo", cargo_body)
        executable(self.build_tools / "cargo", cargo_body)

        host_body = r'''#!/bin/bash
set -eu
printf '%s\0' "$@" >"$HMC_HOST_CAPTURE"
/usr/bin/env -0 >"$HMC_ENV_CAPTURE"
'''
        executable(self.target / "release/renpy-host", host_body)
        executable(self.target / "debug/renpy-host", host_body)

        forbidden_body = r'''#!/bin/bash
set -eu
printf '%s\n' "${0##*/}" >>"$HMC_FORBIDDEN_CAPTURE"
exit 97
'''
        for name in RUNTIME_COMMANDS:
            executable(self.build_tools / name, forbidden_body)

    def close(self) -> None:
        self.temp.cleanup()

    def install_mangohud(self) -> None:
        executable(
            self.tools / "mangohud",
            r'''#!/bin/bash
set -eu
printf '%s\0' "$@" >"$HMC_MANGOHUD_CAPTURE"
[[ "${1-}" == "--dlsym" ]] && shift
exec "$@"
''',
        )

    def environment(self, *, build_only_path: bool = False, **updates: str | None) -> dict[str, str]:
        env = os.environ.copy()
        for name in tuple(env):
            if name.startswith(("RENPY_HOST_", "HMC_")) or name in {"CARGO_TARGET_DIR", "HUANGMEIC_GAME_SRC", "PYTHONPATH", "RENPY_PERFORMANCE_TEST", "RENPY_SKIP_MAIN_MENU", "RENPY_SKIP_SPLASHSCREEN", "RUST_LOG"}:
                env.pop(name, None)
        env.update(
            PATH=str(self.build_tools if build_only_path else self.tools),
            CARGO_TARGET_DIR=str(self.target),
            RENPY_HOST_GAME=str(self.game),
            HMC_CARGO_CAPTURE=str(self.cargo_capture),
            HMC_HOST_CAPTURE=str(self.host_capture),
            HMC_ENV_CAPTURE=str(self.env_capture),
            HMC_MANGOHUD_CAPTURE=str(self.mangohud_capture),
            HMC_FORBIDDEN_CAPTURE=str(self.forbidden_capture),
        )
        for key, value in updates.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def run(
        self,
        *args: str,
        wrapper: bool = False,
        build_only_path: bool = False,
        **updates: str | None,
    ) -> subprocess.CompletedProcess[bytes]:
        for capture in (
            self.cargo_capture,
            self.host_capture,
            self.env_capture,
            self.mangohud_capture,
            self.forbidden_capture,
        ):
            capture.unlink(missing_ok=True)
        command = self.wrapper if wrapper else self.launcher
        return subprocess.run(
            [str(command), *args],
            env=self.environment(build_only_path=build_only_path, **updates),
            capture_output=True,
            timeout=20,
            check=False,
        )

    def cargo_argv(self) -> list[str]:
        require(self.cargo_capture.exists(), "cargo was not invoked")
        return decode_nul(self.cargo_capture.read_bytes())

    def host_argv(self) -> list[str]:
        require(self.host_capture.exists(), "host was not invoked")
        return decode_nul(self.host_capture.read_bytes())

    def child_env(self) -> dict[str, str]:
        require(self.env_capture.exists(), "host environment was not captured")
        return dict(item.split("=", 1) for item in decode_nul(self.env_capture.read_bytes()))

    def mangohud_argv(self) -> list[str]:
        require(self.mangohud_capture.exists(), "MangoHud was not invoked")
        return decode_nul(self.mangohud_capture.read_bytes())


def run_success(result: subprocess.CompletedProcess[bytes], label: str) -> None:
    require(
        result.returncode == 0,
        f"{label} failed rc={result.returncode}\nstdout={os.fsdecode(result.stdout)}\nstderr={os.fsdecode(result.stderr)}",
    )


def run_failure(result: subprocess.CompletedProcess[bytes], label: str) -> None:
    require(
        result.returncode != 0,
        f"{label} unexpectedly succeeded\nstdout={os.fsdecode(result.stdout)}\nstderr={os.fsdecode(result.stderr)}",
    )


def test_source_purity(launcher: Path) -> None:
    text = launcher.read_text(encoding="utf-8")
    required_functions = (
        "_parse_args",
        "_validate_launcher_env",
        "_build_host",
        "_ensure_default_game_overlay",
        "_sync_host_overlay",
        "_launch_host",
        "main",
    )
    for name in required_functions:
        require(f"{name}()" in text, f"missing source-testable function {name}")
    final_guard = '''if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi'''
    require(text.rstrip().endswith(final_guard), "main guard is not the final launcher construct")

    with tempfile.TemporaryDirectory(prefix="hmc-launcher-source-") as temporary:
        root = Path(temporary)
        tree = root / "tree"
        fail_path = root / "fail-path"
        tree.mkdir()
        fail_path.mkdir()
        (tree / "plain file").write_bytes(b"unchanged\n")
        (tree / ".hidden").write_bytes(b"hidden\n")
        (tree / "link").symlink_to("plain file")
        marker = root / "external-command-ran"
        for name in (
            "basename",
            "cargo",
            "dirname",
            "env",
            "find",
            "mangohud",
            "pwd",
            "python3",
            "realpath",
            "sed",
        ):
            executable(
                fail_path / name,
                "#!/bin/bash\nprintf '%s\\n' \"${0##*/}\" >>\"$HMC_SOURCE_MARKER\"\nexit 96\n",
            )
        manifest_before = tree_manifest(tree)
        probe = r'''
set -euo pipefail
shopt -s dotglob nullglob
trap ':' USR1
umask 027
set -- 'one two' '' '--three' '*'
snapshot() {
  local output="$1"
  shift
  {
    builtin pwd -P
    /usr/bin/env -0
    set +o
    shopt -p || :
    umask
    trap -p
    printf 'arg:%s\0' "$@"
    /usr/bin/find "$HMC_SOURCE_TREE" -printf '%P\0%y\0%m\0%s\0%T@\0%l\0' | /usr/bin/sort -z
  } >"$output"
}
before="$HMC_SOURCE_ROOT/before"
after="$HMC_SOURCE_ROOT/after"
snapshot "$before" "$@"
saved_path="$PATH"
source_forbidden() {
  printf '%s\n' "$1" >>"$HMC_SOURCE_MARKER"
  return 96
}
for name in cd command export pwd set shopt trap umask unset; do
  eval "$name() { source_forbidden '$name'; }"
done
PATH="$HMC_FAIL_PATH"
. "$HMC_LAUNCHER"
PATH="$saved_path"
for name in cd command export pwd set shopt trap umask unset; do
  builtin unset -f "$name"
done
builtin unset -f source_forbidden
snapshot "$after" "$@"
/usr/bin/cmp -s "$before" "$after"
declare -F _parse_args _validate_launcher_env _build_host _ensure_default_game_overlay _sync_host_overlay _launch_host main >/dev/null
'''
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", probe],
            env={
                "PATH": "/usr/bin:/bin",
                "HMC_LAUNCHER": str(launcher),
                "HMC_SOURCE_ROOT": str(root),
                "HMC_SOURCE_TREE": str(tree),
                "HMC_FAIL_PATH": str(fail_path),
                "HMC_SOURCE_MARKER": str(marker),
            },
            capture_output=True,
            timeout=10,
            check=False,
        )
        run_success(result, "source-purity probe")
        marker_text = marker.read_text(encoding="utf-8") if marker.exists() else ""
        require(not marker.exists(), f"sourcing executed external command(s): {marker_text}")
        require(tree_manifest(tree) == manifest_before, "sourcing changed the temporary filesystem")


def test_help_and_modes(harness: Harness) -> None:
    outputs: list[bytes] = []
    for wrapper in (False, True):
        result = harness.run("--help", wrapper=wrapper)
        run_success(result, "wrapper help" if wrapper else "canonical help")
        require(not harness.cargo_capture.exists(), "help invoked cargo")
        outputs.append(result.stdout)
    require(outputs[0] == outputs[1], "wrapper and canonical help differ")
    help_text = os.fsdecode(outputs[0])
    for token in (
        "--normal",
        "--smoke[=N]",
        "--build-only",
        "--debug",
        "--release",
        "auto|required|off",
    ):
        require(token in help_text, f"help omitted {token}")

    result = harness.run("--normal", RENPY_HOST_MANGOHUD="off")
    run_success(result, "explicit normal mode")
    require("RENPY_HOST_SMOKE_SECS" not in harness.child_env(), "normal mode leaked smoke deadline")

    result = harness.run("--smoke", RENPY_HOST_MANGOHUD="off")
    run_success(result, "default smoke mode")
    require(harness.child_env().get("RENPY_HOST_SMOKE_SECS") == "30", "default smoke deadline is not 30")

    result = harness.run("-s", "7", "--debug", RENPY_HOST_MANGOHUD="off")
    run_success(result, "explicit smoke/debug mode")
    require(harness.child_env().get("RENPY_HOST_SMOKE_SECS") == "7", "explicit smoke deadline changed")
    require(harness.cargo_argv()[2:] == ["build", "-p", "renpy-host"], "debug profile cargo args are wrong")


def test_wrapper_forwarding(harness: Harness) -> None:
    before_separator = ["unknown pre-option", "雪"]
    after_separator = ["--release", "--smoke=99", "--dash", "two words", "", "*", "line\nbreak", "黄梅戏"]
    expected = [str(harness.game), *before_separator, *after_separator]
    for wrapper in (False, True):
        result = harness.run(
            "--normal", *before_separator, "--", *after_separator,
            wrapper=wrapper, RENPY_HOST_MANGOHUD="off",
        )
        run_success(result, "wrapper forwarding" if wrapper else "canonical forwarding")
        require(harness.host_argv() == expected, "wrapper/launcher changed exact NUL-delimited argv/order")
        require(
            harness.cargo_argv()[2:] == ["build", "-p", "renpy-host", "--release"],
            "selectors after -- were parsed instead of forwarded",
        )


def test_build_only_isolation(harness: Harness) -> None:
    for wrapper in (False, True):
        absent_game = harness.root / ("must-not-be-created-wrapper" if wrapper else "must-not-be-created-canonical")
        absent_source = harness.root / ("missing-source-wrapper" if wrapper else "missing-source-canonical")
        sentinel = harness.root / "build-only-sentinel"
        sentinel.write_text("unchanged", encoding="utf-8")
        sentinel_before = tree_manifest(harness.root)
        updates = {name: "invalid-runtime-value" for name in MOVIE_ENV}
        updates.update(
            RENPY_HOST_GAME=str(absent_game), HUANGMEIC_GAME_SRC=str(absent_source),
            RENPY_HOST_MANGOHUD="invalid-runtime-value", RENPY_HOST_PHASE0_SIGNALS="invalid-runtime-value",
        )
        result = harness.run(
            "--build-only", "--release", "--relink", "--", "--host-argument",
            wrapper=wrapper, build_only_path=True, **updates,
        )
        run_success(result, "isolated wrapper build-only" if wrapper else "isolated canonical build-only")
        cargo = harness.cargo_argv()
        require(cargo[0] == str(ROOT / "host"), f"cargo cwd changed: {cargo[0]}")
        require(cargo[1] == str(harness.target), f"CARGO_TARGET_DIR changed: {cargo[1]}")
        require(cargo[2:] == ["build", "-p", "renpy-host", "--release"], f"cargo args changed: {cargo[2:]}")
        require(not harness.host_capture.exists(), "build-only launched the host")
        require(not harness.mangohud_capture.exists(), "build-only launched MangoHud")
        require(not harness.forbidden_capture.exists(), "build-only invoked runtime/overlay commands")
        require(not absent_game.exists() and not absent_source.exists(), "build-only touched runtime trees")
        # Capture files are the only expected harness mutations; the sentinel itself must be byte/metadata stable.
        current = tree_manifest(harness.root)
        before_record = next(item for item in sentinel_before if item[0] == b"build-only-sentinel")
        after_record = next(item for item in current if item[0] == b"build-only-sentinel")
        require(before_record == after_record, "build-only mutated unrelated filesystem sentinel")


def test_mangohud_policies(harness: Harness) -> None:
    result = harness.run("--normal", RENPY_HOST_MANGOHUD="auto")
    run_success(result, "MangoHud auto fallback")
    require(harness.host_capture.exists(), "auto fallback did not launch host")
    require(not harness.mangohud_capture.exists(), "auto fallback invoked absent MangoHud")

    result = harness.run("--normal", RENPY_HOST_MANGOHUD="required")
    run_failure(result, "MangoHud required/absent")
    require(b"required but not found" in result.stderr, "required/absent error is unclear")
    require(not harness.host_capture.exists(), "required/absent launched host")

    harness.install_mangohud()
    result = harness.run("--normal", RENPY_HOST_MANGOHUD="off")
    run_success(result, "MangoHud off")
    require(not harness.mangohud_capture.exists(), "off invoked MangoHud")

    for policy in ("auto", "required"):
        result = harness.run("--normal", RENPY_HOST_MANGOHUD=policy)
        run_success(result, f"MangoHud {policy}/available")
        mango = harness.mangohud_argv()
        require(mango[:2] == ["--dlsym", str(harness.target / "release/renpy-host")], f"{policy} MangoHud argv changed")

    result = harness.run("--normal", RENPY_HOST_MANGOHUD="sometimes")
    run_failure(result, "invalid MangoHud policy")
    require(not harness.host_capture.exists(), "invalid MangoHud policy launched host")


def movie_defaults(**updates: str) -> dict[str, str]:
    values = {
        "RENPY_HOST_MANGOHUD": "off",
        "RENPY_HOST_MOVIE_W": "1920",
        "RENPY_HOST_MOVIE_H": "1080",
        "RENPY_HOST_MOVIE_LAYOUT_W": "1920",
        "RENPY_HOST_MOVIE_LAYOUT_H": "1080",
        "RENPY_HOST_MOVIE_FPS": "30",
        "RENPY_HOST_MOVIE_PRESENT": "1b",
        "RENPY_HOST_MOVIE_MAX_FRAMES": "360",
        "RENPY_HOST_MOVIE_CHUNK_FRAMES": "20",
        "RENPY_HOST_MOVIE_KICKSTART_FRAMES": "8",
        "RENPY_HOST_MOVIE_MIN_PLAYABLE": "2",
        "RENPY_HOST_MOVIE_LAYOUT_CACHE": "0",
        "RENPY_HOST_MOVIE_RSS_MB": "4096",
    }
    values.update(updates)
    return values


def assert_invalid(harness: Harness, label: str, args: Iterable[str] = ("--normal",), **updates: str) -> None:
    result = harness.run(*args, **movie_defaults(**updates))
    run_failure(result, label)
    require(not harness.host_capture.exists(), f"{label} launched host")


def test_movie_and_smoke_validation(harness: Harness) -> None:
    integer_names = (
        "RENPY_HOST_MOVIE_W", "RENPY_HOST_MOVIE_H", "RENPY_HOST_MOVIE_LAYOUT_W",
        "RENPY_HOST_MOVIE_LAYOUT_H", "RENPY_HOST_MOVIE_MAX_FRAMES",
        "RENPY_HOST_MOVIE_CHUNK_FRAMES", "RENPY_HOST_MOVIE_KICKSTART_FRAMES",
        "RENPY_HOST_MOVIE_MIN_PLAYABLE", "RENPY_HOST_MOVIE_RSS_MB",
    )
    for name in integer_names:
        assert_invalid(harness, f"non-decimal integer {name}", **{name: "1.0"})
        assert_invalid(harness, f"signed integer {name}", **{name: "+1"})

    invalid_environment = (
        ("zero decode width", {"RENPY_HOST_MOVIE_W": "0"}),
        ("non-decimal decode height", {"RENPY_HOST_MOVIE_H": "1.5"}),
        ("negative layout width", {"RENPY_HOST_MOVIE_LAYOUT_W": "-1"}),
        ("non-decimal layout height", {"RENPY_HOST_MOVIE_LAYOUT_H": " "}),
        ("zero max frames", {"RENPY_HOST_MOVIE_MAX_FRAMES": "0"}),
        ("non-decimal chunk frames", {"RENPY_HOST_MOVIE_CHUNK_FRAMES": "ten"}),
        ("zero RSS", {"RENPY_HOST_MOVIE_RSS_MB": "0"}),
        ("zero FPS", {"RENPY_HOST_MOVIE_FPS": "0"}),
        ("negative FPS", {"RENPY_HOST_MOVIE_FPS": "-1"}),
        ("NaN FPS", {"RENPY_HOST_MOVIE_FPS": "NaN"}),
        ("infinite FPS", {"RENPY_HOST_MOVIE_FPS": "Infinity"}),
        ("invalid present", {"RENPY_HOST_MOVIE_PRESENT": "2"}),
        ("invalid layout-cache boolean", {"RENPY_HOST_MOVIE_LAYOUT_CACHE": "perhaps"}),
        ("invalid layout-cache numeric", {"RENPY_HOST_MOVIE_LAYOUT_CACHE": "2"}),
        ("minimum below one", {"RENPY_HOST_MOVIE_MIN_PLAYABLE": "0"}),
        ("min exceeds kickstart", {"RENPY_HOST_MOVIE_MIN_PLAYABLE": "31"}),
        ("kickstart exceeds chunk", {"RENPY_HOST_MOVIE_KICKSTART_FRAMES": "61"}),
        ("chunk exceeds max", {"RENPY_HOST_MOVIE_CHUNK_FRAMES": "361"}),
        ("bounded hostile width cannot wrap", {"RENPY_HOST_MOVIE_W": "9" * 128}),
        ("overlong integer rejected", {"RENPY_HOST_MOVIE_W": "9" * 129}),
    )
    for label, updates in invalid_environment:
        assert_invalid(harness, label, **updates)

    for args in (
        ("--smoke=0",),
        ("--smoke=-1",),
        ("--smoke", "-1"),
        ("--smoke=",),
        ("--smoke", "not-a-number"),
        ("--smoke", "1.5"),
        ("--smoke=1000000000000000000",),
    ):
        assert_invalid(harness, f"invalid smoke input {args!r}", args=args)

    result = harness.run("--smoke=12", **movie_defaults())
    run_success(result, "valid smoke input")
    require(harness.child_env().get("RENPY_HOST_SMOKE_SECS") == "12", "valid smoke deadline changed")

    result = harness.run("--normal", **movie_defaults())
    run_success(result, "valid 1920x1080x360 movie budget")


def test_rss_boundaries(harness: Harness) -> None:
    base = movie_defaults(
        RENPY_HOST_MOVIE_W="1",
        RENPY_HOST_MOVIE_H="1",
        RENPY_HOST_MOVIE_LAYOUT_W="1",
        RENPY_HOST_MOVIE_LAYOUT_H="1",
        RENPY_HOST_MOVIE_CHUNK_FRAMES="1",
        RENPY_HOST_MOVIE_KICKSTART_FRAMES="1",
        RENPY_HOST_MOVIE_MIN_PLAYABLE="1",
        RENPY_HOST_MOVIE_RSS_MB="1",
    )

    exact_1b = {**base, "RENPY_HOST_MOVIE_PRESENT": "1b", "RENPY_HOST_MOVIE_MAX_FRAMES": "262144"}
    run_success(harness.run("--normal", **exact_1b), "present 1b exact MiB boundary")
    assert_invalid(harness, "present 1b over MiB boundary", **{**exact_1b, "RENPY_HOST_MOVIE_MAX_FRAMES": "262145"})

    exact_1a = {**base, "RENPY_HOST_MOVIE_PRESENT": "1a", "RENPY_HOST_MOVIE_MAX_FRAMES": "262143"}
    run_success(harness.run("--normal", **exact_1a), "present 1a exact MiB boundary")
    assert_invalid(harness, "present 1a over MiB boundary", **{**exact_1a, "RENPY_HOST_MOVIE_MAX_FRAMES": "262144"})

    exact_layout_cache = {
        **base,
        "RENPY_HOST_MOVIE_LAYOUT_CACHE": "1",
        "RENPY_HOST_MOVIE_LAYOUT_W": "2",
        "RENPY_HOST_MOVIE_MAX_FRAMES": "131072",
    }
    run_success(harness.run("--normal", **exact_layout_cache), "layout-cache exact MiB boundary")
    assert_invalid(
        harness,
        "layout-cache over MiB boundary",
        **{**exact_layout_cache, "RENPY_HOST_MOVIE_MAX_FRAMES": "131073"},
    )


def phase_values(words: Iterable[str]) -> tuple[str, ...]:
    values: list[str] = []
    for word in words:
        for spelling in (word, word.upper(), word.capitalize()):
            if spelling not in values:
                values.append(spelling)
    return tuple(values)


def test_phase0_normalization(harness: Harness) -> None:
    false_values: tuple[str | None, ...] = (None, "", "0", *phase_values(("false", "no", "off")))
    true_values = ("1", *phase_values(("true", "yes", "on")))
    for mode in (("--normal",), ("--smoke=1",)):
        for value in false_values:
            result = harness.run(
                *mode,
                **movie_defaults(RENPY_HOST_PHASE0_SIGNALS=value),
            )
            run_success(result, f"Phase0 false-like {value!r} in {mode[0]}")
            require("RENPY_HOST_PHASE0_SIGNALS" not in harness.child_env(), f"Phase0 false-like leaked: {value!r}")
        for value in true_values:
            result = harness.run(
                *mode,
                **movie_defaults(RENPY_HOST_PHASE0_SIGNALS=value),
            )
            run_success(result, f"Phase0 truthy {value!r} in {mode[0]}")
            require(harness.child_env().get("RENPY_HOST_PHASE0_SIGNALS") == "1", f"Phase0 truthy not normalized: {value!r}")
        for value in ("maybe", "2", "n", "y"):
            result = harness.run(
                *mode,
                **movie_defaults(RENPY_HOST_PHASE0_SIGNALS=value),
            )
            run_failure(result, f"invalid Phase0 {value!r} in {mode[0]}")
            require(not harness.host_capture.exists(), f"invalid Phase0 launched host: {value!r}")


def run_harness_test(
    launcher: Path,
    wrapper: Path,
    test: Callable[[Harness], None],
) -> None:
    harness = Harness(launcher, wrapper)
    try:
        test(harness)
    finally:
        harness.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher", type=Path, default=DEFAULT_LAUNCHER)
    parser.add_argument("--wrapper", type=Path, default=DEFAULT_WRAPPER)
    args = parser.parse_args()
    launcher = args.launcher.resolve()
    wrapper = args.wrapper.resolve()

    tests: list[tuple[str, Callable[[], None]]] = [
        ("source purity and final guard", lambda: test_source_purity(launcher)),
        ("help and explicit modes", lambda: run_harness_test(launcher, wrapper, test_help_and_modes)),
        ("wrapper argument forwarding", lambda: run_harness_test(launcher, wrapper, test_wrapper_forwarding)),
        ("build-only isolation", lambda: run_harness_test(launcher, wrapper, test_build_only_isolation)),
        ("MangoHud policies", lambda: run_harness_test(launcher, wrapper, test_mangohud_policies)),
        ("movie, smoke, frame, and overflow validation", lambda: run_harness_test(launcher, wrapper, test_movie_and_smoke_validation)),
        ("binary-MiB RSS boundaries", lambda: run_harness_test(launcher, wrapper, test_rss_boundaries)),
        ("Phase0 normalization", lambda: run_harness_test(launcher, wrapper, test_phase0_normalization)),
    ]
    for name, test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(f"PASS {name}")
    print("OK hmc_launcher_contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
