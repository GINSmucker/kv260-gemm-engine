# OPT50：H576 Dense / Sparse24 共享计算核

日期：2026-10-22。目标器件：KV260 / `xck26-sfvc784-2LV-c`。

工程版本为 OPT50；数据格式标识仍为 `H576_SPARSE24_SPLIT128_V1`，不因工程改名而变更。

这是基于 OPT46 的新 HLS 源码工程，不覆盖旧核。目标是**一个 IP、一套共享乘加阵列、调用时通过 mode 切换 Dense / 2:4 Sparse**，不是生成两个核再二选一。

当前状态：本机原生 C++ 功能测试通过；**尚未运行 Vitis CSim、综合、RTL Cosim 或板测**。不能据此宣称 II=1、512 DSP、Burst 成功或达到 125 MHz。最终共享电路和资源必须看综合后的 RTL、层级及资源报告。

## 文件用途

| 文件 | 作用 |
|---|---|
| `src/model_gemm_top.cpp` | 唯一顶层；AXI 接口、参数检查、权重加载及错误返回 |
| `src/model_gemm_core.hpp` | 同一乘加循环、输入选择、结果打包和计算/写回重叠 |
| `include/model_gemm_config.hpp` | 数据类型、硬件容量、支持尺寸与顶层声明 |
| `tb/testbench.cpp` | 独立 INT64 参考、模式切换、边界及真实层测试 |
| `tb/axi_cases/` | 从已验证 AXI 数据包复制的 12 层测试载荷；不含新剪枝 |
| `hls_config.cfg` | Vitis 2026.1 配置；目标 8 ns，uncertainty 1 ns |
| `docs/implementation.md` | 旧核分析、共享电路、访存设计及验收方法 |
| `docs/sparse24_axi_format_v0.1.md` | 原格式文档的逐字节快照，不修改其历史状态描述 |
| `reports/validation.md` | 本次实际测试结果与未验证项目 |
| `tools/run_native_tests.ps1` | 本机 C++ 编译/测试脚本；不运行 HLS、不生成 IP |

## 接口和尺寸

计算：`X_INT8[M,K] * W_INT8[K,N] -> Y_INT32[M,N]`，不做 bias、scale、量化或剪枝。

唯一顶层 `model_gemm_top(X, W_data, W_meta, Y, M, K, N, mode)`：

- mode=0：Dense，W_data 是紧密排列的完整权重；外部 W_meta 不读取。
- mode=1：Sparse，W_data 是两个保留值，W_meta 是两个位置共一个 nibble。
- X / W_data / W_meta / Y 均以 128 bit 字为传输单位。X 为 gmem0，两个 W 指针共用 gmem1，Y 为 gmem2。
- 所有矩阵保持 row-major，不新增逐行 padding。Sparse 严格采用 `H576_SPARSE24_SPLIT128_V1`；见格式快照。
- Host 提供 64 字节对齐、足长的独立 BO，64 bit 设备地址；Dense 的 W_meta 也提供至少 16 B 全零占位，不传空指针。

**首版硬件能力子集**：`1<=M<=197`，`1<=K,N<=576`，`K%16==0`，`N%16==0`，`K*N<=110592`。M 尾块支持；K/N 非整拍尺寸明确拒绝，不声称已支持所有格式合法尺寸。

真实 H576 PL 尺寸 `197x192x576` 与 `197x576x192` 均支持。qkv 保持 Dense；选定的 fc1/fc2 可 Sparse，其余 fc1/fc2 仍 Dense。proj/head 保持 PS 路由；不支持将旧 768 隐藏维或 N=1000 的 head 直接传入本核。

返回码：0 成功；1 非法/超限尺寸；2 非法 mode；3 非法位置码。前三项在计算启动前处理，metadata 错误也在 DATAFLOW 前检查，此实现拒绝时不写 Y。Host 仍必须提前检查 BO 长度、地址和同步；硬件参数没有长度字段，**不能检测短 BO 或错误物理地址**。

这是**新控制 ABI**：新增 W_meta、mode 和返回状态。不能直接复用旧 runtime 的寄存器偏移、旧平台 metadata 或 xclbin。

## 在 Vitis 图形界面运行

部署后的源码目录为 `D:\GEMM\model_gemm_opt50`。源码目录和 IDE 组件目录分开，避免“组件目录已经存在”导致无法创建。

1. 工作区选 `D:\GEMM`。Create HLS Component，组件名用 `model_gemm_opt50_hls`，位置 `D:\GEMM`。
2. Configuration File 选 Existing File，指定源码目录中的 `hls_config.cfg`。不要选 Generate from Vitis HLS Project。
3. 确认 Sources 为 top/core/config，Test Bench 为 testbench；顶层 `model_gemm_top`。若向导没有解析相对路径，手动添加源码目录内的对应文件；最终配置应使用这些文件的正确位置。
4. 确认器件、8 ns 时钟和 1 ns uncertainty。先 C Simulation，默认参数 `full`，应有 37 次调用。
5. 再做真实载荷 CSim：把 `csim.argv` 临时改为 `golden D:/GEMM/model_gemm_opt50/tb/axi_cases`，应有 48 次调用。完整路径避免 CSim 工作目录变化造成找不到数据。
6. C Synthesis 后检查共享阵列、`shared_mac_steps` 的实际 II、资源、时序和 Burst。通过后做 C/RTL Cosimulation，`cosim.argv=rtl`，13 次调用；这是 testbench 的测试集合名称，不是另一个顶层。
7. 先保留当前 13 调用 Cosim；需要全部真实权重 RTL 验证时，另将 `cosim.argv` 改成同样的 `golden ...`，预计更慢。板测前还要完成 AXI 事务监测和新 ABI/runtime 集成。

不要只看 GUI 的绿色完成标志；检查结果 PASS、输出数量和报告是否对应本次源码。不要在 Vitis 中误选 OPT45/46 的同名 top。

## 本机 C++ 复测（可选）

在本目录的 PowerShell 中运行：

```powershell
.\tools\run_native_tests.ps1 -VitisRoot 'D:\Vivado\2026.1\Vitis'
```

功能：使用工具链自带 clang 和 ap_int/stream_of_blocks 头文件，编译后运行 full、rtl、golden 三个测试集合。**会创建或更新本目录 `.build/` 内的程序、日志和结果**；仅临时修改当前进程 PATH 并恢复；不修改系统环境、旧源码、板卡、模型或 Git。它不是 Vitis HLS CSim，更不是 RTL Cosim；本机运行秒数不能用来报告 FPGA latency。

## 下一阶段的门槛

先做 HLS 验证和资源审查，再讨论进一步加速。不为追求 II=1 写假的 DEPENDENCE，不先扩大阵列，不同时叠加输入双缓冲等新变量。若 LUT、时序或 BRAM 不合适，保留这份功能基线，单独做受控版本比较。
