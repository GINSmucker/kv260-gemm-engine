# 最终结果与证据

主结果为 [metrics.json](board500/metrics.json)。[paired195_cpu_pl.csv](board500/paired195_cpu_pl.csv)给出同195张的两模型CPU和PS/PL四组时间；[per_image_comparison.csv](board500/per_image_comparison.csv)给出完整500张预测、真实标签、输入SHA和原始记录时间。

[timing_breakdown.json](board500/timing_breakdown.json)由同195张的390份Native回执与worker记录复算，给出互不重叠的Runner区间、逐层均值及回执SHA。START至完成证明包含轮询和完成检查，不是纯PL周期；详细边界见[测量口径](../docs/measurement.md)。

[原始板端摘要](board500/board_summary.json)按原字节保存，不改写其中多软件版本500张总体统计；主性能只采用195张同调度范围。[独立复核](board500/local_independent_review.json)使用Python标准库重新读取两模型共2000个保存tensor，比较各自CPU/PL原始位、重排全部1000类并核对统计。

完整21,246,214字节证据归档SHA为`3dd66ac7e784153ce6d479be2017f54d678870cbc02d4f02dbdc681d366f591b`，作为外部资产保存。原始归档中的摘要SHA与Git中`board_summary.json`一致；Git不是完整原始归档目录，原SHA清单仅作为来源记录。

[B历史1800张CPU1报告](board500/B_cpu1_historical1800.json)与当前500张分列。`platform_history/`保留原系统来源／实现资料，不将整理目录当作新Vivado或新上板PASS。

后续Git来源提交和本轮离线检查记录分别列明。硬件运行时通过原部署包SHA固定，源码Git归档发生在板测之后。

## 原OPT50硬件实现报告

以下报告来自已验收fd96539d平台的原Vivado 2026.1构建`build_20261024_132157_775_c2e60ae4`。六份文件逐字节匹配现有[原核查清单](../board/platform/verification_v2/verification_manifest.json)，出处见[补充来源清单](../supplemental_sources.json)。

| 原始报告 | 用途 |
|---|---|
| [timing_summary.rpt](platform_history/implementation_reports/timing_summary.rpt) | 实现后setup／hold时序，原记录WNS +0.561 ns、WHS +0.010 ns |
| [utilization.rpt](platform_history/implementation_reports/utilization.rpt) | LUT、FF、BRAM及DSP分层资源 |
| [clocks.rpt](platform_history/implementation_reports/clocks.rpt) | 实现时钟与约束 |
| [check_timing.rpt](platform_history/implementation_reports/check_timing.rpt) | 时序约束完整性检查 |
| [drc.rpt](platform_history/implementation_reports/drc.rpt) | 设计规则检查及警告 |
| [route_status.rpt](platform_history/implementation_reports/route_status.rpt) | 布线状态 |

这些是原系统实现证据，不是本轮重新实现，也不测量图片推理耗时。当前500张分类和195张CPU／PS+PL计时仍以本页主结果为准。
