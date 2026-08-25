"""
Unit tests for host/scripts/runner/parent_runner.py.
Verifies envelope generation (6 standard fields), nonce temp directory isolation,
provisional metrics absorption, fail-closed handling on non-zero exit and missing inputs,
and temp directory cleanup.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Add runner directory to sys.path
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "host" / "scripts" / "runner"))

import parent_runner as pr


def test_standard_6_field_envelope_structure():
    """Test that envelope contains exactly the 6 required standard fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        env_out = tmp_path / "envelope.json"
        test_file = tmp_path / "input.txt"
        test_file.write_text("hello world\n", encoding="utf-8")

        envelope = pr.run_parent_process(
            command=["echo", "success"],
            declared_inputs=[test_file],
            envelope_output_path=env_out,
        )

        assert envelope["exit_code"] == 0
        assert "timestamp_utc" in envelope
        assert "evidence_revision" in envelope
        assert "declared_inputs_digest" in envelope
        assert "command" in envelope
        assert "provisional_metrics" in envelope
        assert "exit_code" in envelope
        assert envelope["command"] == ["echo", "success"]

        # Check written file
        assert env_out.exists()
        with open(env_out, "r", encoding="utf-8") as f:
            disk_env = json.load(f)
        assert disk_env == envelope


def test_provisional_metrics_absorbed():
    """Test that child process provisional metrics are absorbed into envelope."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        env_out = tmp_path / "envelope.json"
        metrics_file = tmp_path / "custom_metrics.json"

        # Child command writes provisional metrics to $REVULT_PROVISIONAL_METRICS_PATH
        child_py = """
import json, os, sys
metrics_path = os.environ.get("REVULT_PROVISIONAL_METRICS_PATH")
with open(metrics_path, "w") as f:
    json.dump({"gate": "G01_solid_image", "mae": 0.0012, "status": "PASS"}, f)
sys.exit(0)
"""
        envelope = pr.run_parent_process(
            command=[sys.executable, "-c", child_py],
            envelope_output_path=env_out,
            provisional_metrics_file=metrics_file,
        )

        assert envelope["exit_code"] == 0
        metrics = envelope["provisional_metrics"]
        assert metrics.get("gate") == "G01_solid_image"
        assert metrics.get("mae") == 0.0012
        assert metrics.get("status") == "PASS"


def test_fail_closed_on_child_non_zero_exit():
    """Test that runner properly returns and records child non-zero exit codes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        env_out = tmp_path / "envelope.json"

        envelope = pr.run_parent_process(
            command=[sys.executable, "-c", "import sys; sys.exit(42)"],
            envelope_output_path=env_out,
        )

        assert envelope["exit_code"] == 42
        assert env_out.exists()
        with open(env_out, "r", encoding="utf-8") as f:
            disk_env = json.load(f)
        assert disk_env["exit_code"] == 42


def test_fail_closed_on_missing_declared_input():
    """Test that missing declared inputs raise FileNotFoundError in fail-closed mode."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        missing_file = tmp_path / "non_existent_input.bin"

        try:
            pr.run_parent_process(
                command=["echo", "noop"],
                declared_inputs=[missing_file],
                fail_closed=True,
            )
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError as e:
            assert "Declared input does not exist" in str(e)


def test_temp_dir_cleanup_on_success_and_failure():
    """Test that nonce temp directory is completely removed after run."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_base = Path(tmpdir)

        # Check directories before
        initial_dirs = set(os.listdir(tmp_base))

        # Run command that records the temp dir path
        record_file = tmp_base / "recorded_temp_dir.txt"
        child_py = f"""
import os
td = os.environ.get("REVULT_RUNNER_TEMP_DIR")
with open(r"{record_file}", "w") as f:
    f.write(td)
"""
        envelope = pr.run_parent_process(
            command=[sys.executable, "-c", child_py],
            temp_base_dir=str(tmp_base),
        )

        assert envelope["exit_code"] == 0
        recorded_temp_dir = record_file.read_text().strip()
        assert recorded_temp_dir.startswith(str(tmp_base))
        assert not os.path.exists(recorded_temp_dir), f"Temp dir {recorded_temp_dir} should have been cleaned up"

        # Check failure case as well
        child_fail_py = f"""
import os, sys
td = os.environ.get("REVULT_RUNNER_TEMP_DIR")
with open(r"{record_file}", "w") as f:
    f.write(td)
sys.exit(7)
"""
        envelope_fail = pr.run_parent_process(
            command=[sys.executable, "-c", child_fail_py],
            temp_base_dir=str(tmp_base),
        )
        assert envelope_fail["exit_code"] == 7
        recorded_fail_temp_dir = record_file.read_text().strip()
        assert not os.path.exists(recorded_fail_temp_dir), f"Temp dir {recorded_fail_temp_dir} should have been cleaned up on failure"


def test_cli_execution():
    """Test executing parent_runner.py via CLI."""
    runner_script = repo_root / "host" / "scripts" / "runner" / "parent_runner.py"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        env_out = tmp_path / "cli_envelope.json"
        sample_input = tmp_path / "sample.txt"
        sample_input.write_text("sample data\n", encoding="utf-8")

        res = subprocess.run(
            [
                sys.executable,
                str(runner_script),
                "-i",
                str(sample_input),
                "-o",
                str(env_out),
                "--",
                "echo",
                "cli_test",
            ],
            capture_output=True,
            text=True,
        )

        assert res.returncode == 0
        assert env_out.exists()
        with open(env_out, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["exit_code"] == 0
        assert data["command"] == ["echo", "cli_test"]
        assert len(data["declared_inputs_digest"]) == 64


if __name__ == "__main__":
    test_standard_6_field_envelope_structure()
    test_provisional_metrics_absorbed()
    test_fail_closed_on_child_non_zero_exit()
    test_fail_closed_on_missing_declared_input()
    test_temp_dir_cleanup_on_success_and_failure()
    test_cli_execution()
    print("All parent_runner unit tests passed successfully!")
