# A：完整层GEMM计算核

## 性能基准与资源候选

下一步Dense FPGA性能优化基准为OPT45_M32N16_Y768_125MHz，固定源码身份见[研究锁](../docs/locks/RESEARCH_LOCK.json)的performance_baseline。A负责人从该源码开始；默认src/tb仍属于OPT38，不能把它当作OPT45。已发布PS参照配置为固定亲和性、QuantQKV和固定W BO。

|架构项|OPT45性能基准|A_OPT40_AXI128_M16N16资源候选|
|---|---|---|
|并行计算|32×16，512MAC，完整K连续循环|16×16，256MAC，K按32组织|
|M197行块数|7|13|
|Y缓存/连续供数|96KiB，TN768；qkv/fc1整行144/192个128bit字|1KiB，TN16；每行每块4个128bit字|
|X/W/Y数据口|三路128bit，紧凑矩阵|三路128bit，紧凑矩阵|
|时钟证据|125MHz平台及实板；WNS0.641ns|10ns目标，实现时序NOT RUN|
|HLS LUT/DSP|81469/515，2020.2.2|26529/260，2026.1摘要|
|采用依据|完整AXI RTL、配对平台、实板及整图证据|LUT/DSP取舍；同条件实板比较NOT RUN|

A候选固定在[hls/opt40-axi128-burst@68a9e643](https://github.com/GINSmucker/kv260-gemm-engine/tree/68a9e643a9e998c1bd075434b31202f1545e00aa/hls)。其HLS/XSIM结果由该分支摘要报告，原始报告未随分支提供；BRAM摘要174/288，不能概括为所有资源都更省。256/512MAC、HLS上界或混合shape的平均周期均不直接证明整图加速比。

OPT45的采用是性能研究基准选择，正式独立C、PL500/full质量、独立长期稳定性和正式发布签署仍为NOT RUN。两种实现的本机契约复核见[验证](../docs/verification.md)，数值复核不能代替AXI或板测。

默认开发源码位于src与tb，配置唯一源为src/model_gemm_config.hpp；include提供兼容转发。默认OPT38采用128MAC、100MHz、32bit输入数据master。

当前研究源码由opt45-source资产提供：hardware/model_gemm/candidates/opt45_y768_v1。OPT45为512MAC、TM32/TN768、完整K，X/W/Y三路128bit；X为32行×64个128bit字LUTRAM，acc32×16×48个INT32、96KiB。W每start完整装载，X按M块初始化/加载，阶段无DATAFLOW重叠。Y直接按有效行/N范围打包，无额外复制。

五shape和14寄存器共同遵守[唯一接口](../docs/interface.md)。整型结果INT32、地址64bit字节，不支持任意shape或FPGA scale/bias。

## OPT45源码与证据定位

从仓库根按需取得[team-opt45-20261010](https://github.com/GINSmucker/kv260-gemm-engine/releases/tag/team-opt45-20261010)的opt45-source和opt45-evidence。已有资产由[ASSETS锁](../docs/locks/ASSETS.lock.json)核对大小、归档SHA和成员SHA；不重复提交源码压缩包、IP、bitstream、模型或SDK。

解包源码根为`build/assets/opt45-source/opt45-source`；下表源码路径相对此根。证据路径相对`build/assets/opt45-evidence/opt45-evidence`。

|职责|准确入口|
|---|---|
|顶层与AXI pragma|hardware/model_gemm/candidates/opt45_y768_v1/src/model_gemm_top.cpp|
|缓存、调度、完整K与Y写回|hardware/model_gemm/candidates/opt45_y768_v1/src/model_gemm_core.hpp|
|类型、五shape及容量|hardware/model_gemm/candidates/opt45_y768_v1/include/model_gemm_config.hpp|
|独立INT64参考与公开测试|hardware/model_gemm/candidates/opt45_y768_v1/tb/testbench.cpp、tb/public_cases.hpp|
|8ns/1ns目标、器件和测试profile|hardware/model_gemm/config/opt45_y768_v1.json|
|HLS执行|hardware/model_gemm/candidates/opt45_y768_v1/run_hls.ps1、run_hls.tcl|
|AXI观测与完整重放|同候选目录的axi_monitor.vh、inject_axi_monitor.py、verify_axi_monitor.py；hardware/model_gemm/scripts/run_monitored_replay.ps1|
|IP和14寄存器机器契约|results/A/A1/model_gemm_opt45_y768_v1_20261010_183212_157/delivery/manifest.json、contracts/register_map.json、contracts/model_gemm_registers.hpp|
|HLS/RTL结果|hls/run_manifest.json、hls/hls_export_inspection.json、hls/axi_monitor_result.json|
|布线、平台与真实接口|platform/platform_verification.json、measurements/B3_platform_analysis.json|
|逐尺寸边界、RTL周期与整图|measurements/B3_boundary_analysis.json、B3_RTL_analysis.json、production_analysis.json|

当前已发布PS参照通过opt45-runtime与ps-runtime增量组装，实测数据由ps-evidence提供，准确入口在[零上下文接手](../docs/reference/START_HERE.md)。硬件优化保持相同输入、软件参照、路由和测量边界；未发布的本机PS或稀疏实验不属于此冻结参照。

## 软件检查

```powershell
python -B docs/tools/assets.py opt45-source --download --extract
python -B docs/tools/assets.py opt45-evidence --download --extract
python -B docs/tools/check.py --cpp --research-source build/assets/opt45-source/opt45-source --cxx <existing-cxx> --hls-include <existing-vitis-include>
```

OPT45的ap_uint128要求显式提供含ap_int.h的Vitis include目录，不分发SDK。普通C++公开TB采用完整INT64参考，8有效+8拒绝、418064输出；不验证RTL背压。默认两个核可用同命令不传research-source检查。

## HLS与平台

源码资产根为build/assets/opt45-source/opt45-source。run_hls.ps1使用已有Windows Vitis_HLS2020.2.2、target8ns/uncertainty1ns；HLS工程各命令保持同一ASCII盘符。2026.1默认配置位于hls_config.cfg，资格由自己的工具报告确定。

保留完整目录结构创建独立工作副本，避免在冻结解包资产上改源码。从仓库根运行：

```powershell
$opt45Work = Join-Path (Get-Location) 'build/work/opt45-baseline'
if (Test-Path -LiteralPath $opt45Work) { throw '请选择尚不存在的工作副本目录' }
$null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $opt45Work)
Copy-Item -LiteralPath 'build/assets/opt45-source/opt45-source' -Destination $opt45Work -Recurse -ErrorAction Stop
```

从该工作副本根运行：

```powershell
./hardware/model_gemm/candidates/opt45_y768_v1/run_hls.ps1 -Stage csim
```

已有55文件HLS/IP交付可按manifest核对；平台脚本必须显式指定OPT45 DeliveryRoot、ManifestName及ExpectedManifestSHA256=68e5aebce7b2ccce672b6a8944e2c0392ad0d750786faef70d92a0a1a54ca217。新平台fingerprint/metadata/owner需要新身份验证，构建不自动继承A2。

解包资产是只读参照；新实验使用新的工作副本和运行目录，记录源/配置/工具/时钟身份。原样复跑也只产生本机自己的结果。修改后的候选需重新检查公开数值、非法shape、尾块、输入const/守卫、同地址复用及AXI完成；性能采用需有对应平台和同条件实板/整图数据。共享板卡占用与加载由维护者安排。

资源和验证分别见[综合摘要](reports/synthesis.md)、[verification](../docs/verification.md)。scripts/generate_contract.py只提供显式build输出的INT8声明工具，不写当前packed配置。
