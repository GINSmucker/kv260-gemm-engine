# v3 验证与结果

[v3_fair_pair_20261025.json](v3_fair_pair_20261025.json) 是用户提供板卡 `compare.py` stdout 的原始解析回执，身份与附件SHA在 [evidence_provenance.json](evidence_provenance.json)。它不是本次分支整理重新读取板卡root记录或重跑得到的结果。

| 历史同权重单次样本 | all_dense | mixed |
|---|---:|---:|
| 完整模型Runner.run | 145.727537ms | 143.483705ms |
| 12目标层主机完成观察合计 | 8.669497ms | 5.549665ms |
| 36层同类窗口合计 | 26.680320ms | 23.528775ms |

来源commit见 [VERSION.json](../VERSION.json) 与 [board来源](../board/README.md)。硬件为fd96539d系统、Vivado/Vitis2026.1、125MHz目标；两组同一剪枝模型/零输入/owner/plugin/ORT四线程配置，36次PL调用，对应197×192×576与197×576×192等模型维度。计时位置为Runner.run，排除setup、图片预处理、加载、序列化和计时后INFO验算；子区间相互重叠，不能相加，也不是纯PL周期。

历史状态：数值/身份/正常关闭检查 `PASS`，完整模型单次减少约1.54%，目标层观察窗口减少约35.99%。只测每组一次，不宣称稳定均值、P95或长期吞吐。Sparse候选真实图片板端准确率、重复性能统计为 `NOT RUN`。

旧归档路径的其他检查保留在 archive 分支；本分支的活动检查记录单列，不能继承旧路径的资格。

新活动路径检查见 [v3_active_layout_checks_20261026.json](v3_active_layout_checks_20261026.json)：5项HLS源/配置身份、47文件运行包字节对齐、46载荷清单通过；运行时合成单测98通过/3跳过，模型合成单测98通过。模型测试首次使用系统sandbox临时目录发生清理权限失败；保留失败记录，指定本checkout专用TEMP/TMP后通过，未改源代码或验证门禁。
