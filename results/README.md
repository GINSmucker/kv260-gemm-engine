# 性能、资源与验证证据

本分支当前为v2 H576；[原公开范围](../docs/verification.md)与[本分支身份](../VERSION.json)分别记录。
`active_native_20261026.json` 是新活跃目录下的本机C++检查，不是新增HLS/RTL或板端结果。
原整包校验对已更新的README报告差异；旧清单和旧检查器保留，不重签其身份。

summary.csv包含OPT45固定整图、连续调用、HLS与完整平台指标，每行注明范围、单位、时钟和证据位置。HLS估计与routed资源、完整边界和Runner.run分别解读，不相加分位数或跨身份推断收益。

opt45-evidence提供完整560条无诊断正式样本、三图验证及资源/断开记录，以及原始HLS/RTL/平台/数值摘要。研究测试日期2026-10-10；原始报告的签署和字段保持不变。

Git中的evidence按其原验证身份使用。大量矩阵/trace、冻结构建和SDK留在本机或按需Release；资料整合不产生新硬件资格。当前状态见[verification](../docs/verification.md)、[progress](../docs/progress.md)。
