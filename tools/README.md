# 检查与还原工具

| 工具 | 行为 |
|---|---|
| `check_release.py` | 只读核对冻结源码、目录和声明结果；另检查补充文件、平台本地测试台、网站导入与数据、历史报告身份及架构图配对；不读取外部模型或访问设备 |
| `check_docs.py` | 只读检查当前文档的本地链接；不修改原历史快照文档 |
| `restore_bundle.py` | 校验Git文本和外部资产，在独立新目录还原完整受检包 |
| `verify_pair_archive.py` | 核对固定归档SHA、已保存CPU/PL tensor、Top5和性能统计；写入独立本机复核JSON |
| `run_native_hls.py` | 使用已有Windows Vitis工具执行native功能用例，生成新日志／程序；不进行HLS/RTL/板测 |
| `run_runtime_selftests.py` | 编译还原包的两项C++模拟后端／权重检查；不访问XRT或设备 |
| `analyze_timing.py` | 从固定归档的390份Native回执和195对worker记录复算整网计时区间；不测量新的纯PL周期 |
| `rehearse_submission.py` | 在独立新目录核对Release资产、还原三个运行包、复核保存logits与分解数据并执行源码／网页检查；可指定已有C++17编译器执行无设备自测 |

所有硬件加载和START均由原冻结包另行处理。这些检查通过不意味着设备当前驻留正确或允许直接运行原owner。使用步骤见 [快速开始](../docs/quickstart.md)。
