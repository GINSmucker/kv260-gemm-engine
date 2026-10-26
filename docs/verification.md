# v3 验证范围

活动源码身份见 [VERSION.json](../VERSION.json)，源码来源为归档 commit `00efbf17ec540610f3b1a3f33d05fdcb1ed508ae`。本次整理改变活动目录和说明入口，未改 OPT50 top/core/config/testbench 或原47文件 PS 包内容；活动 HLS 配置采用 portable full/rtl 版本，未在新位置运行 HLS。

| 检查 | 结果 | 范围 |
|---|---|---|
| 活动HLS五文件身份 | PASS | top/core/config/testbench/config文件SHA和长度 |
| 原运行包47文件 | PASS | 与归档逐字节相同，46载荷清单重新校验 |
| 运行时合成Python单测 | PASS | 101项，98通过、3个外部/原生POSIX fixture缺失跳过 |
| 活跃HLS目录原生C++ | PASS | 当前top/testbench编译执行rtl合成用例集；不是RTL，见results/active_native_20261026.json |
| 模型合成Python单测 | PASS | 98项，TEMP/TMP指向checkout专用目录；不下载、训练或访问设备 |
| 新HLS配置CSim/RTL | NOT RUN | native用例名rtl不构成RTL仿真 |
| 新布局Vivado/ARM编译/实板 | NOT RUN | 没有加载、START或重新测量当前设备 |
| Sparse重复性能/真实图片板端精度 | NOT RUN | 单次零输入对照不能替代这些验收 |

本机检查的完整记录与首次sandbox临时目录失败原因见 [v3_active_layout_checks_20261026.json](../results/v3_active_layout_checks_20261026.json)。先前归档目录的原生C++结果只属于其原路径；若使用统一 `tools/run_active_native.py`，它会在全新输出目录编译本机程序，不是HLS、RTL或FPGA计时。最新回执从 [结果入口](../results/README.md) 查阅。

历史 [同权重公平样本对](../results/v3_fair_pair_20261025.json) 的数值、身份与正常关闭结果为PASS。它来自fd96539d平台、同一剪枝模型和二进制：all_dense36D 对 mixed24D12S，各单次零输入。145.727537ms与143.483705ms是包含PS/PL协作的Runner.run；排除初始化、加载、图片预处理、序列化和计时后INFO全量核验。主机完成观察窗口包含轮询，不能称为纯PL周期，重叠区间不相加。

历史时序/报告证据在 [board/evidence](../board/evidence/system_verified_fd96539d.json)，硬件来源对应见 [hls_source_alignment.json](../board/evidence/hls_source_alignment.json)。本次文件对齐不扩大旧报告的输入覆盖或当前设备状态保证。Dense H576原有真实图片/稳态验证与Sparse候选保持独立身份。
