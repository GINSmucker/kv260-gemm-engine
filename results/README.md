# 最终结果与证据

主结果为 [metrics.json](board500/metrics.json)。[paired195_cpu_pl.csv](board500/paired195_cpu_pl.csv)给出同195张的两模型CPU和PS/PL四组时间；[per_image_comparison.csv](board500/per_image_comparison.csv)给出完整500张预测、真实标签、输入SHA和原始记录时间。

[原始板端摘要](board500/board_summary.json)按原字节保存，不改写其中多软件版本500张总体统计；主性能只采用195张同调度范围。[独立复核](board500/local_independent_review.json)使用Python标准库重新读取两模型共2000个保存tensor，比较各自CPU/PL原始位、重排全部1000类并核对统计。

完整21,246,214字节证据归档SHA为`3dd66ac7e784153ce6d479be2017f54d678870cbc02d4f02dbdc681d366f591b`，作为外部资产保存。原始归档中的摘要SHA与Git中`board_summary.json`一致；Git不是完整原始归档目录，原SHA清单仅作为来源记录。

[B历史1800张CPU1报告](board500/B_cpu1_historical1800.json)与当前500张分列。`platform_history/`保留原系统来源／实现资料，不将整理目录当作新Vivado或新上板PASS。

后续Git来源提交和本轮离线检查记录分别列明。硬件运行时通过原部署包SHA固定，源码Git归档发生在板测之后。
