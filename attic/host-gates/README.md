# attic/host-gates — 已封存的 host 侧探针（2026-09-03）

8 个 standalone gate 脚本，曾位于 `host/python/gates/`，现封存于此。
封存原因：它们需要 host 进程内的 `renpy_host` FFI 才能运行，
无 CI 接线、无脚本引用（仅 `.agents/` 过程笔记提到），不符合
`RELEASE_GATES.md` 的“禁止新增 `host/python/gates/*`”冻结条款。

恢复命令（从仓库根）：

```bash
mv attic/host-gates/*.py host/python/gates/
```

注意：恢复后仍需按 `RELEASE_GATES.md` 修订规则先修订该文件，
否则恢复即违规。

| 文件 | 覆盖的信号 | pytest 侧是否已有对应覆盖 |
|---|---|---|
| adversarial_frame_stress_gate.py | 极限长宽比 + subsurface 句柄 churn | 部分（test_adversarial_m1_frame_stress.py） |
| adversarial_matrixcolor_pma.py | 10 种矩阵 CPU 参考 oracle | 部分（test_adversarial_matrixcolor_pma.py） |
| challenger_m2_dissolve_boundary.py | DISSOLVE 边界稳定性 | 部分（test_challenger_m2_empirical.py） |
| challenger_m2_rule_stress.py | 30 种 rule 全遍历 | 无直接对应 |
| challenger_m3_clip_stress.py | 裁剪四向不渗色 | 部分（test_challenger_m3_empirical.py） |
| challenger_m3_clock_bug_probe.py | clock set_pos 手动复现（对应 bug 已修） | 部分（test_challenger_m3_video_clock.py） |
| challenger_m3_video_clock_stress.py | pause/seek/audio 绑定风暴 | 部分（test_challenger_m3_video_clock.py） |
| hmc_m1_multipiece_stress.py | 50+ 九宫格 + 驱逐恢复 | 部分（test_adversarial_m1_frame_stress.py） |
