#!/usr/bin/env python3
"""
Parent Process Runner infrastructure for Revult / renpy-host.

Encapsulates subprocess execution with explicit isolation using nonce-based temporary directories,
implements the authoritative 6-field JSON envelope specification, absorbs provisional metrics from
child processes (Single Writer Principle), handles signal/exit cleanup, and fails closed on errors.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

# Global registry of active temporary directories for cleanup upon exit / signals
_ACTIVE_TEMP_DIRS: set[str] = set()
_CLEANUP_REGISTERED = False


def _cleanup_temp_dirs() -> None:
    """Removes all registered temporary directories."""
    while _ACTIVE_TEMP_DIRS:
        temp_dir = _ACTIVE_TEMP_DIRS.pop()
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def _signal_handler(signum: int, frame: Any) -> None:
    """Handles termination signals by cleaning temp directories and exiting."""
    _cleanup_temp_dirs()
    sys.exit(128 + signum)


def _ensure_cleanup_handlers() -> None:
    """Ensures atexit and signal traps are installed once."""
    global _CLEANUP_REGISTERED
    if not _CLEANUP_REGISTERED:
        atexit.register(_cleanup_temp_dirs)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(sig, _signal_handler)
            except (ValueError, OSError):
                # Signals might not be settable in non-main threads or certain environments
                pass
        _CLEANUP_REGISTERED = True


def create_nonce_temp_dir(prefix: str = "revult_runner_", base_dir: str = "/tmp") -> Path:
    """
    Creates a unique nonce-based temporary directory.
    Example: /tmp/revult_runner_a1b2c3d4e5f60718
    """
    _ensure_cleanup_handlers()
    os.makedirs(base_dir, exist_ok=True)
    nonce = secrets.token_hex(8)
    dir_name = f"{prefix}{nonce}"
    temp_path = Path(base_dir) / dir_name
    temp_path.mkdir(parents=True, exist_ok=False)
    _ACTIVE_TEMP_DIRS.add(str(temp_path.resolve()))
    return temp_path


def remove_nonce_temp_dir(temp_path: Union[str, Path]) -> None:
    """Removes a specific nonce temporary directory and unregisters it."""
    resolved = str(Path(temp_path).resolve())
    if resolved in _ACTIVE_TEMP_DIRS:
        _ACTIVE_TEMP_DIRS.discard(resolved)
    try:
        if os.path.exists(resolved):
            shutil.rmtree(resolved, ignore_errors=True)
    except Exception:
        pass


def compute_sha256_file(file_path: Path) -> str:
    """Computes SHA-256 digest of a single file in chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_declared_inputs_digest(
    input_paths: Sequence[Union[str, Path]],
    fail_closed: bool = True,
    repo_root: Optional[Path] = None,
) -> str:
    """
    Computes a deterministic SHA-256 digest across all declared input files.
    If input_paths is empty, returns sha256(b"").
    If a file is missing and fail_closed=True, raises FileNotFoundError.
    """
    if not input_paths:
        return hashlib.sha256(b"").hexdigest()

    resolved_files: List[Path] = []
    for item in input_paths:
        p = Path(item)
        if not p.is_absolute() and repo_root is not None:
            p = repo_root / p
        
        if not p.exists():
            if fail_closed:
                raise FileNotFoundError(f"Declared input does not exist: {p}")
            continue

        if p.is_dir():
            # Recursively collect all files in directory deterministically
            for root, _, files in os.walk(p):
                for f in files:
                    resolved_files.append(Path(root) / f)
        elif p.is_file():
            resolved_files.append(p)
        else:
            if fail_closed:
                raise ValueError(f"Declared input is neither regular file nor directory: {p}")

    if not resolved_files:
        return hashlib.sha256(b"").hexdigest()

    # Sort files by canonical path for deterministic order
    resolved_files.sort(key=lambda x: str(x.resolve()))

    # Compute aggregate hash: hash of sorted (canonical_path + file_sha256)
    aggregate_hasher = hashlib.sha256()
    for f in resolved_files:
        file_hash = compute_sha256_file(f)
        rel_or_canon = str(f.resolve())
        entry = f"{rel_or_canon}:{file_hash}\n".encode("utf-8")
        aggregate_hasher.update(entry)

    return aggregate_hasher.hexdigest()


def get_evidence_revision(repo_root: Optional[Path] = None) -> str:
    """
    Retrieves the git revision (SHA) or fallback environment variable REVULT_EVIDENCE_REVISION.
    """
    env_rev = os.environ.get("REVULT_EVIDENCE_REVISION")
    if env_rev:
        return env_rev.strip()

    cwd = str(repo_root) if repo_root else None
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            timeout=5,
        )
        sha = res.stdout.strip()
        if sha:
            return sha
    except Exception:
        pass

    return "unknown"


def build_envelope(
    command: Sequence[str],
    exit_code: int,
    provisional_metrics: Optional[Dict[str, Any]] = None,
    declared_inputs_digest: Optional[str] = None,
    evidence_revision: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Constructs the standard 6-field JSON envelope:
    {
      "timestamp_utc": "ISO-8601 string",
      "evidence_revision": "git-sha or revision string",
      "declared_inputs_digest": "sha256 digest of input files",
      "command": ["argv..."],
      "provisional_metrics": { ... },
      "exit_code": 0
    }
    """
    now_iso = timestamp_utc or datetime.now(timezone.utc).isoformat()
    rev = evidence_revision or get_evidence_revision()
    digest = declared_inputs_digest if declared_inputs_digest is not None else hashlib.sha256(b"").hexdigest()
    metrics = provisional_metrics if provisional_metrics is not None else {}

    return {
        "timestamp_utc": now_iso,
        "evidence_revision": rev,
        "declared_inputs_digest": digest,
        "command": list(command),
        "provisional_metrics": metrics,
        "exit_code": exit_code,
    }


def write_envelope(envelope: Dict[str, Any], output_path: Union[str, Path]) -> None:
    """
    Authoritatively writes the 6-field JSON envelope to disk.
    (Single Writer Principle).
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Write atomically via temp file in same directory
    temp_target = out_file.parent / f".{out_file.name}.tmp.{secrets.token_hex(4)}"
    try:
        with open(temp_target, "w", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2)
            f.write("\n")
        temp_target.replace(out_file)
    finally:
        if temp_target.exists():
            try:
                temp_target.unlink()
            except Exception:
                pass


def run_parent_process(
    command: Sequence[str],
    declared_inputs: Optional[Sequence[Union[str, Path]]] = None,
    envelope_output_path: Optional[Union[str, Path]] = None,
    provisional_metrics_file: Optional[Union[str, Path]] = None,
    cwd: Optional[Union[str, Path]] = None,
    env: Optional[Dict[str, str]] = None,
    temp_base_dir: str = "/tmp",
    repo_root: Optional[Path] = None,
    fail_closed: bool = True,
    stdout: Any = None,
    stderr: Any = None,
) -> Dict[str, Any]:
    """
    Executes a subprocess command wrapped in isolated nonce temp directory,
    absorbs provisional metrics, creates the 6-field JSON envelope, cleans up temp,
    and returns the envelope dict.
    """
    if not command:
        raise ValueError("Command cannot be empty")

    _ensure_cleanup_handlers()
    temp_dir = create_nonce_temp_dir(prefix="revult_runner_", base_dir=temp_base_dir)
    
    # Calculate declared inputs digest before running command (fail-closed if missing)
    try:
        inputs_digest = compute_declared_inputs_digest(
            declared_inputs or [],
            fail_closed=fail_closed,
            repo_root=repo_root,
        )
    except Exception as e:
        # Cleanup temp dir before failing
        remove_nonce_temp_dir(temp_dir)
        if fail_closed:
            raise
        inputs_digest = f"ERROR: {e}"

    # Prepare environment variables
    sub_env = os.environ.copy() if env is None else env.copy()
    sub_env["REVULT_RUNNER_TEMP_DIR"] = str(temp_dir.resolve())
    sub_env["TMPDIR"] = str(temp_dir.resolve())
    
    default_metrics_path = temp_dir / "provisional_metrics.json"
    actual_metrics_path = Path(provisional_metrics_file) if provisional_metrics_file else default_metrics_path
    sub_env["REVULT_PROVISIONAL_METRICS_PATH"] = str(actual_metrics_path.resolve())

    exit_code = 1
    provisional_metrics: Dict[str, Any] = {}

    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            env=sub_env,
            stdout=stdout,
            stderr=stderr,
        )
        exit_code = proc.returncode

        # Absorb provisional metrics if present
        if actual_metrics_path.exists():
            try:
                with open(actual_metrics_path, "r", encoding="utf-8") as mf:
                    provisional_metrics = json.load(mf)
            except Exception as e:
                provisional_metrics = {
                    "error": f"Failed to parse provisional metrics: {e}",
                    "raw_path": str(actual_metrics_path),
                }
                if exit_code == 0 and fail_closed:
                    exit_code = 1
        elif default_metrics_path.exists() and default_metrics_path != actual_metrics_path:
            try:
                with open(default_metrics_path, "r", encoding="utf-8") as mf:
                    provisional_metrics = json.load(mf)
            except Exception:
                pass

    except Exception as e:
        exit_code = 1
        provisional_metrics = {"runner_exception": str(e)}
    finally:
        # Build envelope before deleting temp directory
        envelope = build_envelope(
            command=command,
            exit_code=exit_code,
            provisional_metrics=provisional_metrics,
            declared_inputs_digest=inputs_digest,
            evidence_revision=get_evidence_revision(repo_root=repo_root),
        )

        if envelope_output_path:
            write_envelope(envelope, envelope_output_path)

        # Cleanup isolated nonce temp directory
        remove_nonce_temp_dir(temp_dir)

    return envelope


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parent Process Runner with isolation, envelope generation, and cleanup."
    )
    parser.add_argument(
        "--input",
        "-i",
        action="append",
        dest="inputs",
        default=[],
        help="Declared input file or directory to hash into declared_inputs_digest (repeatable).",
    )
    parser.add_argument(
        "--envelope-out",
        "-o",
        dest="envelope_out",
        default=None,
        help="Path where authoritative JSON envelope should be written.",
    )
    parser.add_argument(
        "--provisional-metrics",
        "-m",
        dest="provisional_metrics",
        default=None,
        help="Path to provisional metrics file written by child process (optional).",
    )
    parser.add_argument(
        "--temp-dir",
        dest="temp_dir",
        default="/tmp",
        help="Base directory for creating nonce temp directory.",
    )
    parser.add_argument(
        "--cwd",
        dest="cwd",
        default=None,
        help="Working directory for child command.",
    )
    parser.add_argument(
        "--no-fail-closed",
        dest="fail_closed",
        action="store_false",
        default=True,
        help="Do not fail immediately on missing inputs or parsing errors.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command and arguments to execute (after '--' or as remaining positional args).",
    )

    args = parser.parse_args(argv)

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    if not cmd:
        parser.error("No command specified to execute.")

    try:
        envelope = run_parent_process(
            command=cmd,
            declared_inputs=args.inputs,
            envelope_output_path=args.envelope_out,
            provisional_metrics_file=args.provisional_metrics,
            cwd=args.cwd,
            temp_base_dir=args.temp_dir,
            fail_closed=args.fail_closed,
        )
        ret = envelope["exit_code"]
        if ret < 0:
            return 128 + (-ret)
        return ret
    except Exception as e:
        print(f"ERROR [parent_runner]: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
