# host/scripts/debug — 调试探针归档（非发版门）

本目录只放一次性调试探针：`run_hmc_feel_*`、`run_hmc_menu_video_product.sh`、
`single_tree_probe.py`（validate-first 已完成使命，留档备查）。

发版门一律留在 `host/scripts/` 上层：
`phase9_gates.sh`、`run_golden_tests.sh`、`phase1_gates.sh`、
`benchmark_bc160.sh`、`build_appimage.sh`、`build_sdist_manifest.sh`、
`run_the_question.sh`、`run_hmc_menu_video_soak_probe.sh`、
`run_huangmeic_playtest.sh`、`phase5/phase7/phase8_gates.sh`（AGENTS.md §6 按需）。
CI（`.github/workflows/host.yml`）只认上层路径，搬文件需同步改 CI。
