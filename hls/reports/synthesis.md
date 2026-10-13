# HLS与平台综合摘要

## OPT45研究配置

目标xck26-sfvc784-2LV-c，Vitis HLS2020.2.2，target8ns/uncertainty1ns。运行model_gemm_opt45_y768_v1_20261010_183212_157，单512MAC、TM32/TN768、X/W/Y128bit。

|指标|HLS结果|
|---|---:|
|C-sim|160有效+8拒绝/10925312整数 PASS|
|完整AXI RTL|16事务/418064整数 PASS|
|估计时钟|7ns|
|BRAM_18K|124|
|DSP|515|
|FF|60461|
|LUT|81469|
|URAM|0|
|关键MAC/X/W/初始化/Y loop II|1|

top不流水；top Interval/latency由原hls_export_inspection.json逐字段提供，不写成loop II。受控RTL qkv/fc1/fc2周期113431/146363/107668，不是实板完整边界时间。

平台model_system_20261010_184640_939：125MHz、WNS0.641ns/WHS0.010ns；65024 LUT、52837 FF、56 RAMB36、517 DSP。HLS与routed资源是不同报告范围。

原始报告及55文件IP交付由opt45-source/opt45-evidence固定，源manifest68e5aebce7b2ccce672b6a8944e2c0392ad0d750786faef70d92a0a1a54ca217。

## 默认OPT38配置

Git的hls_config.cfg工具配置为Vitis2026.1、10ns。该配置C-sim160有效+8拒绝为main f5ead7a的队友报告；2026.1 C synthesis、RTL cosim、资源和板端NOT RUN。

默认2020.2.2身份的[HLS原报告](../../results/evidence/opt38/hls_export_inspection.json)为10ns约束/1ns uncertainty、9ns估计，BRAM_18K110/DSP131/FF16113/LUT14837；该结果只对应其冻结身份。新构建不能继承已测数据或签署。
