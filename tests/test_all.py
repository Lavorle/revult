import unittest
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "tests"))

import test_parent_runner
import test_golden_mae


class TestParentRunner(unittest.TestCase):
    def test_standard_6_field_envelope_structure(self):
        test_parent_runner.test_standard_6_field_envelope_structure()

    def test_provisional_metrics_absorbed(self):
        test_parent_runner.test_provisional_metrics_absorbed()

    def test_fail_closed_on_child_non_zero_exit(self):
        test_parent_runner.test_fail_closed_on_child_non_zero_exit()

    def test_fail_closed_on_missing_declared_input(self):
        test_parent_runner.test_fail_closed_on_missing_declared_input()

    def test_temp_dir_cleanup_on_success_and_failure(self):
        test_parent_runner.test_temp_dir_cleanup_on_success_and_failure()

    def test_cli_execution(self):
        test_parent_runner.test_cli_execution()


class TestGoldenMae(unittest.TestCase):
    def test_missing_baseline_fails(self):
        test_golden_mae.test_missing_baseline_fails()

    def test_dimension_mismatch_fails(self):
        test_golden_mae.test_dimension_mismatch_fails()

    def test_mae_under_threshold_passes(self):
        test_golden_mae.test_mae_under_threshold_passes()

    def test_mae_over_threshold_fails(self):
        test_golden_mae.test_mae_over_threshold_fails()

    def test_compare_or_bootstrap_pure_function(self):
        test_golden_mae.test_compare_or_bootstrap_pure_function()


if __name__ == "__main__":
    unittest.main()
