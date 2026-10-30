# 本轮发布检查

这些记录只证明各自列出的离线范围。原始板端验收结果见 [500张证据](../board500/metrics.json)。

| 检查 | 结果 | 范围 |
|---|---|---|
| [Git归档](git_archive.json) | PASS | 从源码提交重新导出的文件身份、文档与4项回归检查 |
| [Sparse24 CPU自测](sparse24.json) | PASS | 原始源码、49层权重打包和常驻协议的本机模拟后端 |
| [Dense36 CPU自测](dense36.json) | PASS | 原始源码、49层权重打包和常驻协议的本机模拟后端 |
| [OPT50功能测试](native_hls.json) | PASS | 本机C++执行13个调用，比较304,768个INT32输出 |
| 新Git目录的ARM重编译／上板 | NOT RUN | 整理后的目录没有重新执行硬件推理 |
| 本轮HLS综合／RTL仿真／Vivado实现 | NOT RUN | 本机功能测试不替代这些步骤 |

源码Git提交在原始板测之后创建；板测时的运行身份仍以冻结包、模型和固件SHA为准。检查工具、复现前提和命令见 [快速开始](../../docs/quickstart.md)。
