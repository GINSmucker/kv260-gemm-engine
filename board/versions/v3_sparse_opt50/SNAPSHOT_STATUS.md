# v3 / OPT50 源码与历史证据快照

归档日期：2026-10-26。本目录整理阶段只复制已有源码和小型证据，没有综合、布局布线、ARM 编译、固件加载或板端计算。仓库上传由上层归档流程完成，不属于本目录的设备操作。

## 本目录保存了什么

| 路径 | 内容与边界 |
|---|---|
| `runtime_snapshot/` | 已发布 `fair_compare_v1_fix1` 的 46 个清单载荷和原 `tools_manifest.json`，逐字节复制，原 pins 未重写。不是通用的自动上板工具。 |
| `runtime_tests/` | 自研合成/CPU 单测与一份同字节 worker 测试副本；没有 ARM ELF、模型或真实图片。 |
| `loadonly_snapshot/` | 当前曾使用的 load-only v2 发布源码及原清单。加载属于硬件修改，源码归档不是加载授权。 |
| `platform/` | 真正产生 fd96539d 系统的 simfix v4 脚本、配置、测试和自研 F8 观察器 RTL。没有 Vivado 工程缓存或导出 IP RTL。 |
| `platform/external_inputs/` | 板级数据、C 参考包和本地 PS VIP 生成物的来源/哈希索引；不是这些输入本体。 |
| `platform/verification_v2/` | 修复后的系统报告核查器及历史核查清单；不以旧核查器的后处理失败否定已生成的硬件产物。 |
| `deployment_sources/` | 当时的 DTS、shell 与 metadata-only xclbin 的 XML/JSON/INI 源文件；没有位流、DTBO 或 xclbin 二进制。 |
| `model_export/` | 完整模型适配包的导出器和离线检查源码；外部模型输入未包含。 |
| `protocol/` | 固定 `H576_SPARSE24_SPLIT128_V1` 格式和打包工具说明的原文快照。 |
| `evidence/` | 原系统/部署清单、实现报告和本次文件来源对齐记录。历史文件中的日期、路径和 NOT_RUN 不作追溯改写。 |
| `COPY_INDEX.json` | 每份机械复制文件的原路径、长度和 SHA256。 |

`runtime_snapshot/tools_manifest.json` 为 6668 B，SHA256 `76cd08c4f37ee26724cd781cf5a560a804e29d3fa7f59cb995e07561b100b47f`。编译源码清单为 4776 B，SHA256 `da6a2c0d2d8596ac3caa0fe88dadcc6f29c68349d569aca7e32d7d7f961462b4`。新增归档说明和测试不属于该冻结发布清单，不能据此替换历史 root 执行副本。

## 能力和实际构建来源

OPT50 是一个共享的 W8A8 MAC 电路，运行时切换 Dense / 2:4 Sparse；不是两个 IP，也不是 W4A8 混合精度。固定 H576 完整模型有 49 个 Linear：36 个在 PL，13 个在 PS。当前对照的两组使用同一份剪枝后的数学权重：all_dense 为 36D/0S，mixed 为 24D/12S。不能用未剪枝模型替代 all_dense 对照。

硬件正式来源是 `D:/GEMM/opt50_system_simfix_20261024_v4`，不是 `opt50_system_resetfix_20261024_v3`。后者和更早失败工程保留作历史，不能只按目录版本数字选择。

- 运行：`build_20261024_132157_775_c2e60ae4`。
- 产物导出：同一系统根目录下 `finish_20261024_v1`。
- 系统核查：`verification_20261024_v1/verify_system_v2.py`。
- UUID：`fd96539d-4cbc-f732-7694-0f0236ca1cf3`；F8 tags：4254487453、1287452466。
- 器件：`xck26-sfvc784-2LV-c`；工具：Vivado/Vitis 2026.1。
- 目标 PL0 125 MHz，128-bit HPM0/HP0，GEMM/F8 地址 `0xA0000000/0xA0010000`。
- 原系统报告的 WNS +0.561 ns、WHS +0.010 ns 是该构建的历史实现证据，不是本次重新实现或当前板卡时钟实测。
- 原始导出 bit.bin SHA `033f8ed4752b2d70b7fc93ba3a5c4669371d73660bb84c134c88b67abadd6e2b`；Linux 固件是完整 32-bit 字节交换派生物，SHA `7de86da2363e92453f9de350dcf81a2b1d4d2e4fe4a64ad1e66db8da9563c662`。这两种文件不可混用。

复位极性修正是生产电路变更；PS VIP 本地仲裁修正只属于仿真环境，不进入综合/实现，也不是 AMD 官方补丁。原版 VIP 失败不被改写为通过。

HLS 原来源为 `D:/GEMM/model_gemm_opt50`。本次只读对齐结果在 `evidence/hls_source_alignment.json`：core/config 与真实 CSim、wrapc、wrapc_pc 头文件副本逐字节一致；top 与真实预处理文件末尾定义去注释/空白后相同；38 份导出 Verilog 与系统输入副本相同。导出 IP 本身没有保存原始 C++ 副本，因此这是一条文件来源链，不是新的编译或形式等价证明。更早 native/SOB 修复报告中的待验证状态只代表报告创建时刻。

## 可移植性限制：不能 clone 后直接运行板端窗口

冻结软件中保留了当时的安全约束：boot_id `f0f2eed8-5820-4df5-ba19-d05527720f97`、load plan `74abb1e54663`、对应 systemd unit、root 私有目录、合作锁、编译器 SHA 和 Python/模型固定路径。guardian PID/start_ticks 来自对应服务和 `/proc`，不是只凭 PID 数字放行。Native C++、worker、proof、resident 等多侧也绑定启动/接口身份。

以下路径仍是历史部署位置，并未因归档而改写：

- `/home/ubuntu/fpga_project/opt50_integration/fair_compare_fd96539d_v1_fix1`
- `/home/ubuntu/fpga_project/opt50_integration/full_model_fd96539d_v1_fix1/model`
- `/home/ubuntu/fpga_project/model_b/.venv/bin/python`
- `/var/lib/opt50-loadonly-fd96539d-v2/plan_74abb1e54663/plan.json`

新板卡、新启动、新目录或重新生成的位流必须另建并审查配置/计划；不能简单删除门禁、搜索替换 boot_id，或手工启动 owner 来绕过。每份计算计划仅一张零输入图、36 次 START，无预热/循环重试。真实图像精度、稳定性能统计和历史 141.95 ms 单样本各有独立范围。

`platform/scripts/run_vivado.ps1` 的默认 Vivado 安装路径是 D 盘，工作目录要求 ASCII。当前不提供满足全部外部输入的 `platform/project_manifest.json`，所以直接运行会拒绝；历史完整清单放在 `evidence/platform_project_manifest.json`。准备新独立快照时需要完整外部输入，并会产生新指纹/UUID；旧 runtime 不会自动接受新的位流。

## 必须另取回或本地生成的输入

| 输入 | 固定身份/用途 |
|---|---|
| HLS 导出 IP | 原 component XML SHA `e36268ecfe2f248aaba838c0862c24ba256e5ab9e4924b6d8a0ecf5f005d3fd0`；系统副本仅校正三个 HAS_BURST 声明，之后 SHA `51caa21c42a8b6e33c538f2a74d30a53d65c466e30e55b4ec1a39912cb4023e1`。RTL 不变。应以可校验 Release/重建产物提供，不在此复制派生 RTL。 |
| KV260/K26 board preset | `platform/external_inputs/board_files_SOURCE.md` 和 SHA 清单记录官方来源与 commit；取回时保留原许可证。当前没有整个板级包。 |
| C 参考资源包 | 原 `source_reference` 位于 `D:/GEMM/kv260_gemm_team_handoff_20261007/build/assets/opt45-source/opt45-source`；112 项资源索引 SHA `0cc0bfc66ed63697743a61449357c95825c4aff727e340f3f1cc9c8f06408a88`。prepare_system 会严格检查，不静默省略。 |
| AMD PS VIP | 使用已安装 Vivado 的匹配源，通过自研 `scripts/model_patch.py` 本地生成仿真副本。这里不分发完整 vendor 仲裁/参数源码或其源码差异全文。原源 SHA 与生成规则在脚本/patch_manifest 中。 |
| 12 层 AXI 测试包 | manifest SHA `6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf`，129 个载荷；真实权重/输入/golden 未包含。协议和生成器来源需与模型分支配套。 |
| 完整模型适配包 | 58 文件模型目录；manifest SHA `ae50446008cc2c335c391777a172b220bdc39618f039f0627e4f750ddd124ffb`，逻辑权重 SHA `88a553bfd6bf0df05017b0497582fe09ac03cc6fdeb8e84e6bcf23ac022320f6`，参考图 SHA `50f7e260b975d6038bff029c792d90bd26a011fa2171e44ca7991dd5c765bc49`。`model_catalog.hpp` 也属于这个外部目录，ARM 编译需要它。 |
| 模型导出源 | 原 C 公开 runtime 包、基线 ONNX、c062/MLP12 候选 NPZ、AXI 包；各自 pins 在 `model_export/prepare_model.py`，不可用新的微调模型冒充原同权重版本。 |
| ARM/Linux 开发环境 | Ubuntu 24.04.2/aarch64、内核 6.8.0-1015-xilinx、XRT 2.18、已有匹配 zocl、g++13/UUID/XRT 头库；模型运行固定 NumPy 1.26.4、ORT 1.22.1。Windows 模型导出依赖 NumPy/ONNX，不能直接把 Windows wheel 锁文件当 ARM 安装清单。 |

API 头文件（例如 ORT、picosha2）是原 PS 源码快照的一部分，保留其原版权声明及 headers 来源索引；没有打包运行库 DLL/SO。大模型、图片、ELF/SO/KO、IP ZIP、BIT/XSA、工程缓存应放在有权限的外部存储/Release，并在仓库留长度、SHA、来源和生成步骤。

## 可在开发机运行的有限检查

在有 NumPy 的 Python 环境执行 `python -X utf8 -B run_cpu_tests.py`，仅运行归档附带的合成 Python/文件权限模拟测试。它不打开设备、调用 Vivado 或运行 ARM owner，只在测试自己的临时目录中写入并清理文件；缺少外部历史 fixtures 的测试会明确 SKIP。测试通过不等于本次板端通过。

`runtime_snapshot/verify_tools.py` 仅检查原 46 文件清单；不会把新增归档说明当运行载荷。完整模型导出测试、HLS golden/RTL、平台完整流水线和 ARM 编译都需要上表外部输入，不应对缺失输入伪造 PASS。旧 source_manifest/pins 和历史结果均保持原字节，不为使 clone 后“全绿”而放宽。

2026-10-26 在本次 Windows 新目录执行结果：`run_cpu_tests.py` 共 101 项，98 项通过、3 项明确跳过；`runtime_snapshot/verify_tools.py` 的 46 文件校验通过。跳过项分别依赖未归档的完整模型 fixture、历史发布源 fixture 和原生 POSIX root 环境。没有执行这些缺失输入或原生板端测试。

需要缩小到对照/数值证据相关合成测试时，可在本目录的 PowerShell 执行：

```powershell
$env:PYTHONPATH = "$PWD/runtime_snapshot;$PWD/runtime_tests"
python -X utf8 -B -m unittest -v test_compare test_perf_proof test_model_inputs test_perf_worker
```

该命令仅为当前 PowerShell 进程设置模块搜索路径；测试在自有临时目录写入并清理合成数据，不启动设备或 owner。
