import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Add gates to sys.path
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "host" / "python" / "gates"))

import golden_mae as gm


def test_missing_baseline_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        actual_path = tmp_path / "actual.rgba"
        gm.write_raw_rgba(actual_path, 2, 2, b"\x00\x00\x00\xff" * 4)

        missing_baseline = tmp_path / "non_existent_baseline.rgba"

        # CLI execution
        cmd = [
            sys.executable,
            str(repo_root / "host" / "python" / "gates" / "golden_mae.py"),
            "--actual",
            str(actual_path),
            "--baseline",
            str(missing_baseline),
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode != 0, f"Expected non-zero exit code, got {proc.returncode}"
        assert not missing_baseline.exists(), "Missing baseline should NEVER be created implicitly"

        data = json.loads(proc.stdout)
        assert data["status"] == "FAIL"
        assert data["passed"] is False
        assert "Baseline file does not exist" in data["error"]


def test_dimension_mismatch_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        actual_path = tmp_path / "actual.rgba"
        baseline_path = tmp_path / "baseline.rgba"

        gm.write_raw_rgba(actual_path, 2, 2, b"\x00\x00\x00\xff" * 4)
        gm.write_raw_rgba(baseline_path, 3, 2, b"\x00\x00\x00\xff" * 6)

        cmd = [
            sys.executable,
            str(repo_root / "host" / "python" / "gates" / "golden_mae.py"),
            "--actual",
            str(actual_path),
            "--baseline",
            str(baseline_path),
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode != 0, f"Expected non-zero exit code, got {proc.returncode}"

        data = json.loads(proc.stdout)
        assert data["status"] == "FAIL"
        assert data["dimension_match"] is False
        assert "Dimension mismatch" in data["error"]


def test_mae_under_threshold_passes():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        actual_path = tmp_path / "actual.rgba"
        baseline_path = tmp_path / "baseline.rgba"
        out_json = tmp_path / "metrics.json"

        # Difference of 1 on one channel out of 16 values
        # MAE = (1 / 16) / 255 = 0.000245... <= 2/255 (0.007843)
        actual_bytes = bytearray(b"\x00\x00\x00\xff" * 4)
        actual_bytes[0] = 1
        gm.write_raw_rgba(actual_path, 2, 2, actual_bytes)
        gm.write_raw_rgba(baseline_path, 2, 2, b"\x00\x00\x00\xff" * 4)

        cmd = [
            sys.executable,
            str(repo_root / "host" / "python" / "gates" / "golden_mae.py"),
            "--actual",
            str(actual_path),
            "--baseline",
            str(baseline_path),
            "--output",
            str(out_json),
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0, f"Expected exit code 0, got {proc.returncode}. stderr: {proc.stderr}"

        data = json.loads(proc.stdout)
        assert data["status"] == "PASS"
        assert data["passed"] is True
        assert data["dimension_match"] is True
        assert data["mismatch_count"] == 1
        assert data["max_delta"] == 1
        assert out_json.is_file()

        file_data = json.loads(out_json.read_text(encoding="utf-8"))
        assert file_data["status"] == "PASS"


def test_mae_over_threshold_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        actual_path = tmp_path / "actual.rgba"
        baseline_path = tmp_path / "baseline.rgba"

        # Large difference
        actual_bytes = b"\xff\xff\xff\xff" * 4
        baseline_bytes = b"\x00\x00\x00\xff" * 4
        gm.write_raw_rgba(actual_path, 2, 2, actual_bytes)
        gm.write_raw_rgba(baseline_path, 2, 2, baseline_bytes)

        cmd = [
            sys.executable,
            str(repo_root / "host" / "python" / "gates" / "golden_mae.py"),
            "--actual",
            str(actual_path),
            "--baseline",
            str(baseline_path),
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode != 0, f"Expected non-zero exit code, got {proc.returncode}"

        data = json.loads(proc.stdout)
        assert data["status"] == "FAIL"
        assert data["passed"] is False
        assert data["dimension_match"] is True
        assert data["mae"] > gm.MAE_MEAN_LIMIT


def test_compare_or_bootstrap_pure_function():
    # Verify compare_or_bootstrap function fails closed on missing baseline
    ok, msg = gm.compare_or_bootstrap("non_existent_gate_name_xyz", 2, 2, b"\x00" * 16)
    assert ok is False
    assert "FAIL-CLOSED" in msg

    # Verify no baseline was written
    target_dir = gm.golden_dir("non_existent_gate_name_xyz")
    baseline_file = target_dir / "baseline.rgba"
    assert not baseline_file.exists(), "compare_or_bootstrap must not create baseline file"


if __name__ == "__main__":
    test_missing_baseline_fails()
    test_dimension_mismatch_fails()
    test_mae_under_threshold_passes()
    test_mae_over_threshold_fails()
    test_compare_or_bootstrap_pure_function()
    print("All golden_mae unit tests passed successfully!")
