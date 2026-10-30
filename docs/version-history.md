# 版本能力与验证范围

当前交付为OPT50 Sparse24及原始Dense的真实图片对照。历史分支保留各自的源码和验证范围；只有当前[500张结果](../results/board500/metrics.json)使用本文主交付的两模型、输入及计时配置。

## 主要版本

| 版本 | 已有能力与证据 | 完整模型精度／耗时的范围 |
|---|---|---|
| [v1 初级Dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v1-dense-initial) | Tiled Dense核；历史C仿真、综合及RTL摘要；另有该分支本机功能检查 | 配对平台与完整模型板测NOT RUN，没有当前500张数据 |
| [v2 H576 Dense](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v2-h576-dense) | 512 MAC、125 MHz目标、128 bit；49 Linear中36PL／13PS；完整模型、工作台及公开回执 | 原H576十类既有3,925张及其500张子集；历史性能、浏览器交互与有限连续调用分别计范围 |
| [v3 OPT50 Sparse12](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/v3-opt50-sparse) | 同核运行Dense／2:4 Sparse；整图36PL／13PS；同剪枝权重的36D与24D＋12S对照 | 历史零输入各单次Runner.run为145.728／143.484 ms；不代替真实图片精度或稳态性能 |
| 当前OPT50 Sparse24 | 24个MLP Linear剪枝微调；每图12D＋24S；未剪枝Dense对照每图36D | 同500张精度和CPU／PL逐位一致；同调度195张四组CPU／PS+PL耗时，详见[测量口径](measurement.md) |

H576旧500张与本次500张来自不同资料，不能因数量相同而视为相同测试集。历史原始FP32、W8A8数值参考、单次零输入、预热中位数和当前逐图Runner.run也不能直接混合排名。

## 其他分支

| 分支 | 用途与边界 |
|---|---|
| [F8 XRT集成](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/f8-xrt-board-integration-20260928) | 旧固定8×8 GEMM开发交付；576个INT32结果一致及受控恢复记录。不是当前完整模型性能证据 |
| [OPT40](https://github.com/GINSmucker/kv260-gemm-engine/tree/hls/opt40-axi128-burst) | 256 MAC、100 MHz目标的HLS／RTL研究分支；完整模型板测NOT RUN |
| [runtime G0/G1](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/runtime-g0-g1-20261007) | 接收器、身份及记录协议研究；真实socket、owner和C ABI尚未完整集成，ARM／板测NOT RUN |
| [H576汇总](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/h576-summary-20261019)／[team-handoff](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/team-handoff-20261007) | 两分支文件内容一致，包含H576完整交付和工作台。展示最小依赖已收录当前main |
| [H576阅读入口](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/h576-reading-entry-20261019) | 旧main的说明与预览入口；原文保存在[历史目录](provenance/pre-final-main/README.md) |
| [最终成果提交分支](https://github.com/GINSmucker/kv260-gemm-engine/tree/codex/final-opt50-sparse24-20261030) | 已通过PR合入main，当前最终模型、统计和证据的归档来源 |

## 补充资料的来源

[supplemental_sources.json](../supplemental_sources.json)逐文件记录来源提交、原路径、长度和SHA256：

- 网站及H576架构资料来自v2提交`71651c35062871262ff67d6ee26527c86e0fce2d`，网站使用见[指南](workbench.md)。
- 两个OPT50 SystemVerilog测试台及六份历史实现报告来自v3提交`c5739fc7d9dd70130521ac9376e8c80ee726b0d2`。测试台身份与原平台配方匹配；报告身份与原实现核查清单匹配。
- [交互式架构图](diagrams/architecture.html)下载后在本机浏览器打开；GitHub预览不执行HTML。其[说明数据](diagrams/architecture.json)、[原验证记录](diagrams/verification.json)与[Archify许可证](diagrams/LICENSE.archify.txt)一并保留。图描述原H576 Dense；字体链接可能访问Google Fonts，不连接板卡。

复制原始文件只补齐资料和本地依赖，不构成本轮重新运行HLS、RTL、Vivado或板端推理。平台重建仍需匹配的外部IP、board preset、VIP和测试数据，不因补回两个测试台而宣称全新系统可直接构建。
