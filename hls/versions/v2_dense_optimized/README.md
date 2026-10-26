# OPT46：计算—写回重叠实验

## 实验目标

以 2026-10-15 本机验证通过的 OPT45 为基线，只改变内部结果交接和写回调度。
本版本是实验候选，不替代 OPT45，也不表示已经实现加速。

保持不变：`Y = XW`、INT8 输入、INT32 累加/输出、五种合法尺寸、
32×16 乘加微核、TM=32/TN=768、128 位 X/W/Y AXI、顶层参数与错误码、
8 ns 目标时钟、1 ns 时钟不确定度。W 仍然每次调用重新加载，X 不做双缓冲。

## 改了什么，为什么

- `src/model_gemm_core.hpp`：保留原 X/W 加载和乘加循环，将生产结果与 AXI 写回拆成
  `compute_and_pack`、`write_output_blocks`，在 `dense_layer` 内以 `DATAFLOW` 连接。
- 两个输出块各容纳 16×768 个 INT32，即每块 48 KiB、合计 96 KiB 逻辑容量；
  显式绑定 BRAM，不复制原有的 96 KiB、512 分区 LUTRAM 累加缓存。
  实际 BRAM/LUT/FF 增量由综合决定，不能仅按字节数推断。
- 计算端一次将 16 个结果打包为一个内部 512 位字，目标每周期交接一个字；
  写回端每周期取其中一个 128 位片段，目标仍为每周期一个 AXI 数据拍。
  **512 位只用于片内输出缓存，外部 AXI 总线仍是 128 位。**
- `hls::stream_of_blocks<...,2>` 的锁负责 FREE→写入→READY→读出→FREE 的交接。
  写锁离开作用域即可发布块，不必等整次矩阵计算结束。C++ 模型不能证明真实双缓冲
  背压行为；有限两块的交接和并发必须用 RTL 联合仿真验证。
- 写回仍然按每行连续地址进行，保持原来的逐行 Burst 机会；最后一个 M 尾块、
  head 的 N 尾块只写有效元素，消费者与生产者使用相同的块顺序和有效范围。
- `hls_config.cfg`：只增加数据流性能分析开关，其余运行参数沿用 OPT45。
- `include/model_gemm_config.hpp`：只更新版本注释，不修改常量或外部声明。
- 顶层 `src/model_gemm_top.cpp` 和原有两个测试文件逐字节沿用 OPT45。

这样可以让“下一块计算”和“上一块写回”有机会并行，但打包本身仍占用周期，
缓存满时计算端仍需等待，第一块填充与最后一块排空也不能消除。head 只有一行，
不应期待和大矩阵相同的收益。加速与否须以每种尺寸的实测 RTL 周期判断。

## 文件与 Vitis GUI 配置

工作区继续使用 `D:\GEMM`。目录内已准备本地组件描述 `vitis-comp.json`，
其格式沿用当前 OPT45，仅将名称和生成目录改为 OPT46：

- Component name：`model_gemm_opt46`
- Component location：`D:\GEMM`
- Configuration：`D:\GEMM\model_gemm_opt46\hls_config.cfg`
- 顶层函数：`model_gemm_top`
- Design：`src/model_gemm_top.cpp`、`src/model_gemm_core.hpp`、`include/model_gemm_config.hpp`
- Test Bench：`tb/testbench.cpp`、`tb/public_cases.hpp`

刷新 Vitis 组件列表；若未自动发现，使用导入/添加已有 Vitis 组件，选择
`D:\GEMM\model_gemm_opt46`。不要再新建同名目录覆盖源码。
该描述文件不等于已经在当前 GUI 中注册，仍须你确认列表显示与配置。
GUI 中核对实际显示的源码路径必须落在 OPT46，不能仍指向 OPT45；
Work Dir 应为 `model_gemm_opt46`（生成目录在组件内，不指向 OPT45）。
本实验使用 Vitis 2026.1 的 `hls_streamofblocks.h`；不承诺兼容 C 原版 2020.2.2。

依次运行 C Simulation、C Synthesis、C/RTL Cosimulation。
CSim 使用 `full`，Cosim 使用 `rtl`；原有测试内容不变。
本机原生 C++ 检查不等于 Vitis GUI CSim，更不等于 RTL 或板端验证。

交付前本地检查（2026-10-15，工具自带 MinGW G++ 9.5）：

| 检查 | 状态 |
|---|---|
| C++11 语法检查 | PASS |
| 原生 C++ `full`，160 有效 / 8 拒绝 / 10,925,312 个输出比较 | PASS |
| 原生 C++ `dev_reuse_extreme_v1`，换 X / 换 W / 极值连续调用 | PASS |
| Vitis GUI CSim / 综合 / RTL Cosim / 板端验证 | NOT_RUN |

原生检查使用原有完整 INT64 标准答案；没有用新打包函数生成标准答案。
本地原生编译器不同于 Vitis CSim 的 clang-16，因此仍需运行 GUI 流程。

## 验收与对照

1. CSim：160 个有效用例、8 个非法参数用例，全部输出匹配。
2. Cosim：8 个有效用例、8 个非法参数用例，无死锁或数据错乱。
3. 综合：乘加核心仍为 512 路目标，`outer_k` II=1，Y 写回 II=1；
   三路 AXI 仍为 128 位，Y 的连续写回 Burst 仍被推断。
4. Dataflow Viewer/分析报告：两个任务确实有重叠；检查输出块阻塞与通道实现，
   不把出现 `DATAFLOW` 字样本身当作加速证据。
5. 对比下表各次有效调用的 Latency，不用混入非法参数用例的平均值比较。
   关注新增 BRAM、LUT、FF 和后续布局布线时序；不以资源未超过 100% 代表易于实现。

以下是 **OPT45 / Vitis 2026.1 / 2026-10-15** 的同机基线，不是 C 的旧工具版数据：

| 尺寸 M×K×N | OPT45 RTL Latency（周期） | OPT46 |
|---|---:|---|
| 1×192×1000 | 25,336 | 待验证 |
| 197×192×192 | 45,804 | 待验证 |
| 197×192×576 | 106,292 | 待验证 |
| 197×192×768 | 134,768 | 待验证 |
| 197×768×192 | 108,192 | 待验证 |

OPT45 HLS 资源基线：DSP 512、LUT 82,792、FF 76,894、BRAM_18K 152。
这些是 HLS 估计，不是板端或布局布线结果。仿真耗时也不是核执行时间。

参考：[AMD Stream-of-Blocks 建模与交接](https://docs.amd.com/r/en-US/ug1399-vitis-hls/Stream-of-Blocks-Modeling-Style)、
[存储资源与绑定](https://docs.amd.com/r/en-US/ug1399-vitis-hls/Resource-Usage)。
