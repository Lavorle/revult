#!/usr/bin/env python3
"""Standalone regression gate for HuangmeiC host-overlay synchronization."""  # noqa: EXE001

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---
from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore


TEMP_OVERLAY_RE = re.compile(r"^\..+\.tmp\.[A-Za-z0-9]{8}$", re.DOTALL)


class GateFailure(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


def residue(game_dir: Path) -> list[str]:
    return sorted(
        entry.name
        for entry in game_dir.iterdir()
        if TEMP_OVERLAY_RE.fullmatch(entry.name)
    )


def run_sync(
    script: Path,
    overlay_src: Path,
    game_dir: Path,
    *,
    path_prefix: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        HMC_SYNC_SCRIPT=str(script),
        HMC_SYNC_SOURCE=str(overlay_src),
        HMC_SYNC_DEST=str(game_dir),
    )
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            'source "$HMC_SYNC_SCRIPT"; _sync_host_overlay "$HMC_SYNC_SOURCE" "$HMC_SYNC_DEST"',
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def assert_success(result: subprocess.CompletedProcess[str]) -> None:
    require(
        result.returncode == 0,
        f"sync failed rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}",
    )


def assert_failure(result: subprocess.CompletedProcess[str], marker: str) -> None:
    require(result.returncode != 0, "unsafe/faulted sync unexpectedly succeeded")
    require(marker in result.stderr, f"missing failure marker {marker!r}: {result.stderr!r}")


def write_file(path: Path, data: bytes, mode: int = 0o640) -> None:
    path.write_bytes(data)
    path.chmod(mode)


def make_failure_shim(shim_dir: Path, command: str, marker: str) -> None:
    shim = shim_dir / command
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' {marker!r} >&2\n"
        "exit 73\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)


def test_source_is_inert(script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    require("_sync_host_overlay()" in text, "missing _sync_host_overlay shell function")
    require("BASH_SOURCE[0]" in text, "script has no source guard")
    probe = r'''
set -u
set -- "one" "two words"
trap ':' USR1
before_pwd=$PWD
before_env=$(env -0 | sha256sum)
before_set=$(set +o)
before_shopt=$(shopt -p)
before_umask=$(umask)
before_traps=$(trap -p)
before_args=$(printf '%q ' "$@")
source "$HMC_SYNC_SCRIPT"
[[ $PWD == "$before_pwd" ]]
[[ $(env -0 | sha256sum) == "$before_env" ]]
[[ $(set +o) == "$before_set" ]]
[[ $(shopt -p) == "$before_shopt" ]]
[[ $(umask) == "$before_umask" ]]
[[ $(trap -p) == "$before_traps" ]]
[[ $(printf '%q ' "$@") == "$before_args" ]]
declare -F _sync_host_overlay >/dev/null
'''
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", probe],
        env={**os.environ, "HMC_SYNC_SCRIPT": str(script)},
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    require(result.returncode == 0, f"sourcing changed caller state: {result.stderr}")


def synced_content_fixture(script: Path, root: Path) -> tuple[Path, Path, dict[str, os.stat_result]]:
    src, dst = root / "overlay", root / "game"
    src.mkdir(parents=True)
    dst.mkdir()
    write_file(src / "identical.rpy", b"same\n", 0o600)
    write_file(dst / "identical.rpy", b"same\n", 0o644)
    os.utime(src / "identical.rpy", ns=(1_700_000_000_000_000_000,) * 2)
    os.utime(dst / "identical.rpy", ns=(1_600_000_000_000_000_000,) * 2)
    identical_before = (dst / "identical.rpy").stat()
    write_file(src / "changed file.rpy", b"new bytes\n", 0o751)
    write_file(dst / "changed file.rpy", b"old bytes\n", 0o600)
    changed_before = (dst / "changed file.rpy").stat()
    write_file(src / "missing.rpy", b"created\n", 0o604)
    write_file(src / ".hidden.rpy", b"dotfile\n", 0o640)
    write_file(src / "line\nbreak.rpy", b"nul-safe name\n", 0o641)
    write_file(src / "script_version.txt", b"must not copy\n")
    write_file(dst / "script_version.txt", b"operator owned\n", 0o600)
    script_version_before = (dst / "script_version.txt").stat()
    write_file(dst / "unrelated.rpy", b"leave me\n", 0o612)
    unrelated_before = (dst / "unrelated.rpy").stat()
    (src / "source-link.rpy").symlink_to("changed file.rpy")

    assert_success(run_sync(script, src, dst))
    return src, dst, {
        "identical": identical_before,
        "changed": changed_before,
        "script_version": script_version_before,
        "unrelated": unrelated_before,
    }


def test_identical_noop(script: Path, root: Path) -> None:
    _, dst, before = synced_content_fixture(script, root)
    identical_before = before["identical"]

    identical_after = (dst / "identical.rpy").stat()
    require(identical_after.st_ino == identical_before.st_ino, "identical inode churned")
    require(identical_after.st_mtime_ns == identical_before.st_mtime_ns, "identical mtime churned")
    require(stat.S_IMODE(identical_after.st_mode) == 0o644, "identical mode churned")


def test_changed_atomic_replace(script: Path, root: Path) -> None:
    _, dst, before = synced_content_fixture(script, root)
    require((dst / "changed file.rpy").read_bytes() == b"new bytes\n", "changed file stale")
    require((dst / "changed file.rpy").stat().st_ino != before["changed"].st_ino, "changed file was not atomically replaced")
    require(stat.S_IMODE((dst / "changed file.rpy").stat().st_mode) == 0o751, "changed mode wrong")


def test_absent_create(script: Path, root: Path) -> None:
    _, dst, _ = synced_content_fixture(script, root)
    require((dst / "missing.rpy").read_bytes() == b"created\n", "missing file absent")
    require(stat.S_IMODE((dst / "missing.rpy").stat().st_mode) == 0o604, "new mode wrong")


def test_special_names(script: Path, root: Path) -> None:
    _, dst, _ = synced_content_fixture(script, root)
    require((dst / "changed file.rpy").read_bytes() == b"new bytes\n", "space filename absent")
    require((dst / ".hidden.rpy").read_bytes() == b"dotfile\n", "dotfile absent")
    require((dst / "line\nbreak.rpy").read_bytes() == b"nul-safe name\n", "newline filename absent")


def test_script_version_skipped(script: Path, root: Path) -> None:
    _, dst, before = synced_content_fixture(script, root)
    require((dst / "script_version.txt").read_bytes() == b"operator owned\n", "script_version changed")
    script_version_after = (dst / "script_version.txt").stat()
    require(script_version_after.st_ino == before["script_version"].st_ino, "script_version inode churned")
    require(script_version_after.st_mtime_ns == before["script_version"].st_mtime_ns, "script_version mtime churned")


def test_source_symlink_ignored(script: Path, root: Path) -> None:
    _, dst, _ = synced_content_fixture(script, root)
    require(not (dst / "source-link.rpy").exists(), "source symlink followed")


def test_unowned_preserved(script: Path, root: Path) -> None:
    _, dst, before = synced_content_fixture(script, root)
    require((dst / "unrelated.rpy").read_bytes() == b"leave me\n", "unrelated changed")
    unrelated_after = (dst / "unrelated.rpy").stat()
    require(unrelated_after.st_ino == before["unrelated"].st_ino, "unrelated inode churned")
    require(unrelated_after.st_mtime_ns == before["unrelated"].st_mtime_ns, "unrelated mtime churned")
    require(not residue(dst), f"temporary residue: {residue(dst)}")


def test_unsafe_destinations(script: Path, root: Path, kinds: tuple[str, ...]) -> None:
    for kind in kinds:
        case = root / kind
        src, dst = case / "overlay", case / "game"
        src.mkdir(parents=True)
        dst.mkdir()
        write_file(src / "unsafe.rpy", b"replacement\n")
        target = case / "target.txt"
        if kind == "symlink":
            write_file(target, b"protected\n")
            (dst / "unsafe.rpy").symlink_to(target)
        elif kind == "directory":
            (dst / "unsafe.rpy").mkdir()
        else:
            os.mkfifo(dst / "unsafe.rpy", 0o600)

        assert_failure(run_sync(script, src, dst), "unsafe overlay destination")
        if kind == "symlink":
            require((dst / "unsafe.rpy").is_symlink(), "destination symlink replaced")
            require(target.read_bytes() == b"protected\n", "symlink target changed")
        elif kind == "directory":
            require((dst / "unsafe.rpy").is_dir(), "destination directory replaced")
        else:
            require(stat.S_ISFIFO((dst / "unsafe.rpy").lstat().st_mode), "FIFO replaced")
        require(not residue(dst), f"temporary residue after {kind} rejection")


def test_command_failure(script: Path, root: Path, command: str, marker: str) -> None:
    src, dst, shims = root / "overlay", root / "game", root / "shims"
    src.mkdir(parents=True)
    dst.mkdir()
    shims.mkdir()
    write_file(src / "fault.rpy", b"new\n", 0o755)
    write_file(dst / "fault.rpy", b"old\n", 0o600)
    before = (dst / "fault.rpy").stat()
    make_failure_shim(shims, command, marker)

    assert_failure(run_sync(script, src, dst, path_prefix=shims), marker)
    require((dst / "fault.rpy").read_bytes() == b"old\n", f"{command} lost old file")
    after = (dst / "fault.rpy").stat()
    require(after.st_ino == before.st_ino, f"{command} replaced old inode")
    require(after.st_mtime_ns == before.st_mtime_ns, f"{command} changed old mtime")
    require(not residue(dst), f"temporary residue after {command} failure")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path, required=True)
    args = parser.parse_args()
    script = args.script.resolve()
    with tempfile.TemporaryDirectory(prefix="hmc-overlay-sync-") as tmp:
        root = Path(tmp)
        tests: list[tuple[str, Callable[[], None]]] = [
            ("source inert", lambda: test_source_is_inert(script)),
            ("identical no-op and mtime", lambda: test_identical_noop(script, root / "identical")),
            ("changed atomic replace", lambda: test_changed_atomic_replace(script, root / "changed")),
            ("absent create", lambda: test_absent_create(script, root / "absent")),
            ("spaces/newline/dot names", lambda: test_special_names(script, root / "names")),
            ("script_version skipped", lambda: test_script_version_skipped(script, root / "script-version")),
            ("source symlink ignored", lambda: test_source_symlink_ignored(script, root / "source-symlink")),
            ("destination symlink", lambda: test_unsafe_destinations(script, root / "dest-symlink", ("symlink",))),
            ("destination directory and FIFO", lambda: test_unsafe_destinations(script, root / "dest-nonfiles", ("directory", "fifo"))),
            ("unowned preserved", lambda: test_unowned_preserved(script, root / "unowned")),
            ("injected cp cleanup", lambda: test_command_failure(script, root / "cp", "cp", "INJECTED_CP_FAILURE")),
            ("injected mv cleanup", lambda: test_command_failure(script, root / "mv", "mv", "INJECTED_MV_FAILURE")),
        ]
        for name, test in tests:
            try:
                test()
            except Exception as exc:
                print(f"FAIL {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1
            print(f"PASS {name}")
    print("OK hmc_overlay_sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
