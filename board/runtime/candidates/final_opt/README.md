# Sparse24 线程策略、诊断与完整请求候选

本目录生成独立身份的运行源码，不修改`snapshots/sparse24`、原模型清单或原验收记录。变体使用相同模型、权重、36次PL调用、50图上限、900秒窗口和数值编译选项。[验证状态](../../../../results/final_optimization/20261101/status.json)及[模型复算](../../../../results/final_optimization/20261101/model_review.json)分别绑定源码、二进制、输入和原始输出。

默认服务使用`model-only`配对：原owner、插件及JPEG预处理，PL会话4/1、顺序执行、图优化关闭，Run期间自旋、最后一个Run返回后停止自旋；动态分块关闭。CPU参考保持原配置。`runtime_options.py`保留显式的诊断候选参数，默认服务不开放参数选择接口。

OpenSSL INFO摘要及并行RGB摘要属于完整请求候选，默认不启用。两轮暖请求中位数均下降，但原版重复波动超过较小收益，且首请求第二轮退化，未达到超出已观察波动的采用条件。所有慢样本及两个计时客户端中断记录保留在独立结果材料中。诊断、完整模型性能、完整请求和实际服务回归不能混为同一组成绩。

| 变体 | 功能 | 采用条件 |
|---|---|---|
| `baseline` | 原始插件与 owner，作为同工具链对照 | 当前身份、窗口和板端对照通过 |
| `diagnostic` | 分段 Linear 墙钟、完整 Native 启动控制和轮询次数 | 仅诊断；不得作为未插桩速度成绩 |
| `openssl-info` | INFO 和正常关闭时，直接对已同步静态权重 BO 的相同 payload 范围计算 OpenSSL SHA256 | ARM、自测、BO 篡改失败路径、全输出及完整请求净收益通过 |

`openssl-info` 保留全部四个 BO 的同步、保护区检查、两份完整权重摘要检查、失败行为及 INFO 持久化。它只替换 `final_checks()` 的两次摘要实现及临时 payload 复制；初始化、模型、CPU 参考、寄存器读回和完成确认保持原实现。此项处于 Runner 计时之外。

## 生成与构建

在仓库根目录使用经过原验证器检查的完整还原包，为每个变体选择尚不存在的目录：

```powershell
python -B board/runtime/candidates/final_opt/prepare.py --bundle .test_build/submission-20261101/bundles/sparse24 --out .test_build/final-opt-stage/diagnostic --variant diagnostic
```

`--variant baseline` 和 `--variant openssl-info` 分别生成对照与摘要候选。`candidate_manifest.json` 记录原清单、生成器、模型和全部候选文件的 SHA；原始 `tools_manifest.json` 只作为 `baseline_tools_manifest.json` 保存，不假称覆盖变更后的源码。

在匹配的 AArch64 Linux / g++13 / XRT 环境，使用分离的新构建目录：

```text
python -B build.py --candidate /absolute/candidate --out /absolute/new-build --compiler g++-13
```

`build.py` 先核对全部候选文件，编译两个 CPU 自测、插件及 owner，运行 CPU 自测，检查 AArch64 ELF 和实际诊断导出并保存命令、编译器及二进制身份。它不运行 owner，也不提供硬件窗口授权。g++6 的本机头文件检查不能替代此构建。

## 诊断接入与计时边界

由具有当前 boot、固件、模型、锁、唯一 owner 和预算证明的协调器接入 `capture.py`。该模块不创建、停止或替换 owner。原协调器的逐图 CPU 参考、INFO、错误保留、已提交请求不重放及正常关闭责任保持有效。

1. 创建 ORT 会话前，调用 `enable_profile(options, prefix)`；单独运行不启用 ORT profiler 的诊断版，以评估 profiler 扰动。
2. 在原有 admission 后，用 `diagnostic_run(runner, image, run_id)` 包裹一次原始 `Runner.run`。输入必须保持原有 FP32 张量。禁止 `KV5_DUMP` 和 `KV5_QKV_DUMP`。
3. 原协调器执行该图的 INFO、保存完整 CPU/PL 输出；再调用 `complete_capture(capture, cpu, actual, native_receipt)` 核对全部 1000 个 FP32 原始位并补齐身份。
4. 正常结束会话后由 ORT 导出 profiler。用 `analyze.py --capture capture.json --native performance_native.json --ort ort.json --out new-analysis.json` 分析，不在图内输出或写盘。

每个 Linear 记录七个绝对 monotonic 纳秒时间：进入、量化前、量化后、输入同步后、IPC 返回后、矩阵往返后、反量化后。PS Linear 的同步／IPC 点与量化结束重合。互斥的 setup、量化、输入同步、IPC、输出同步／PS 整数矩阵计算及输出分配／反量化区间闭合到全部 Linear；其余墙钟明确记为 `outside_Linear_uncovered`，包含 attention、LayerNorm、GELU、PatchEmbed、ORT、BEGIN/END 和 Python 等尚未独立覆盖的工作。

Native 另记完整执行入口、START 前、START 返回、完成证明、执行返回和 pause 次数。`full_start_control` 从执行入口至 START 返回，覆盖参数写入／读回及 observer clear；它和完成等待、完成后控制均嵌套在对应 IPC 区间中。pause 次数不是睡眠时长，完成等待不是纯 PL 周期。

ORT结果保留节点记录与分线程算子墙钟，剔除同线程嵌套的MatMulInteger／Erf子项。`combine_profile`通过49个Linear包络约束两种时钟的偏移边界，在主执行线程构造互斥区间并闭合到该图Runner；其他线程时间不相加。带profiler图单独报告，不能将其分项等比例移植到低扰动图。原设置、低扰动诊断及ORT profiler三组记录分别保存。

## 验证与采用

```powershell
python -B -m unittest discover -s tests -p test_final_optimization.py -v
python -B board/runtime/candidates/final_opt/check_host.py --bundle .test_build/submission-20261101/bundles/sparse24 --compiler C:/Xilinx/Vitis_HLS/2020.2/tps/win64/msys64/mingw64/bin/g++.exe --crypto-library 'C:/Program Files/Git/ucrt64/bin/libcrypto-3-x64.dll' --out .test_build/final-opt-host-check
```

本机 C++ 检查覆盖 SHA 已知答案、块边界、真实权重 payload 与保护区边界、单字节篡改、SHA 实现失败传播、记录复用／越界／缺失拒绝。它不验证真实 BO、ARM ABI、完整模型或性能。

`tools/latency_diagnostics/measure_requests.py` 使用原始 JPEG、同一代理入口和 100 ms 查询策略，保留首个请求、所有后续请求及慢样本，不重试 POST。完整请求、板端 request_work、预处理、CPU 参考和 Runner 各自统计。首个请求是否包含初始化由 session/run ID 判断，不自动称为启动后首张。

性能采用门槛在候选实测之前固定：同输入／模型／环境的有界配对对照中，Runner 中位收益须同时超过 2 ms 和 3%；完整请求中位收益须同时超过 50 ms 和 3%。至少两个有界对照轮次，保留全部样本，收益须超过对照重复测得的波动，且不得出现数值差异、必要检查缺失或解释不了的尾部退化。门槛不足以单独证明因果；环境、boot 或版本变化必须单列，无法取得有效对照时保留原默认配置。

本目录不改变 proj 路由或硬件。没有实际热点归因及包含新增调用开销的整网对照时，不生成 48 次调用版本，也不将硬件估算计入性能表。安装与回退必须使用已验证的完整配对包，在确认 CU/AXI 安静且原 owner 正常关闭后由现场流程执行，不能混配插件、owner 或模型。

## 执行和复算入口

`coordinate.py`作为root复制的`tools/run_model.py`运行，沿用原Native门禁的父进程argv、cgroup、当前boot、两个锁和限额；错误保留现场，不发送未知owner信号，不重放图。`launch_window.py`只派发新计划，标签不可复用。`quality_campaign.py`覆盖固定500张实际JPEG／输入与全部输出；`three_arm_campaign.py`在固定195张上比较原设置、运行后停止自旋和动态分块。每个owner按协议正常关闭后才派发下一窗口。

`build_service_sha.py`从实际服务源码独立构建摘要候选，保留服务专用门禁。模型窗口owner与服务owner的入口和SHA不同，不能相互安装。`install_trial.py model-only`使用完整原配对备份生成Python线程策略和计时；`optimized`是摘要／预处理试装，`original`用于回退。切换须正常停服务、检查实际设备用户及关闭证明，并经新的正常boot进入；`adopt.py`只在已通过的配置上更新采用回执，不改运行文件。

服务全局保留最多500个请求目录，不能靠删除历史编号或提高上限扩大验收。`tools/measure_service_quality.py`对固定第306–500张原图各提交一次；`check_service_quality.py`与`tools/analyze_service_quality.py`核对实际安装的owner、原始JPEG／NPY、195,000个FP32输出和真实关闭事件。此195张服务回归是部署验证，固定500张模型质量仍使用独立模型窗口证据，二进制身份分别列示。

客户端仅对已提交编号做有界GET查询；结果暂不可读或服务仍在完成持久化时，只读等待，不重试POST。`compact_closed_copies.py`只接受已独立核验归档、旧boot正常关闭、逐文件SHA及保留重建来源的计划，移除其可重建模型／输入拷贝；不删除原始模型、输入来源、结果、计划、事件、二进制或请求历史。
