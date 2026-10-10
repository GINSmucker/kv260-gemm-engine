# HLS 计算核

负责人：A

## 当前候选

`OPT40` 开发候选，源码基线 `79ac98b250a0d90e94ec94c1cd388682cc3f4140`。从已完成 CSim、综合、Cosim 的独立 OPT40 工作区原样导入；尚未在本仓库路径下重新运行。

- 顶层：`model_gemm_top`
- 数据：X/W 为 INT8，Y/累加为 INT32
- tile：`TM=16`、`TN=16`、`TK=32`
- 微结构：每个 K 迭代形成 16×16 外积，设计并行度 256 MAC/cycle；综合报告中计算循环 `II=1`
- X/W/Y AXI 指针：128 位打包；每拍分别承载 16 个 INT8 / 4 个 INT32
- 控制：AXI4-Lite + `ap_ctrl_hs`

当前只接受以下 5 个尺寸：

| family | M | K | N |
|---|---:|---:|---:|
| head | 1 | 192 | 1000 |
| proj | 197 | 192 | 192 |
| qkv | 197 | 192 | 576 |
| fc1 | 197 | 192 | 768 |
| fc2 | 197 | 768 | 192 |

它不是通用任意 M/N/K 的 dense 引擎。修改尺寸表、打包方式或顶层接口时，必须同步修改 testbench 与 `docs/interface.md`。旧 OPT38 的 32 位指针接口不能直接当作 OPT40 的 IP 接口使用。

## 文件

```text
src/model_gemm_top.cpp       HLS 顶层和 AXI pragma
src/model_gemm_core.hpp      计算核心
include/model_gemm_config.hpp 类型、容量和合法尺寸
tb/testbench.cpp             C/RTL 共用测试平台
hls_config.cfg               Vitis 2026.1 工程配置
reports/synthesis.md         综合数据摘要
```

## 当前验证状态

独立 OPT40 工作区已在 Vitis 2026.1 下通过 C Simulation、C Synthesis 和 C/RTL Co-simulation；原样导入的文件 SHA-256 已核对。当前 testbench 仅覆盖 5 个合法尺寸各一组确定性小幅值输入，以及 1 个非法尺寸；尚未补齐全零、INT8 边界值和随机分布测试。仓库路径下重新运行、导出新 IP 和 KV260 板端测试为 `NOT RUN`。准确状态见 `docs/verification.md`。

在 Vitis GUI 中选择本目录的 `hls_config.cfg`，核对 top 为 `model_gemm_top`、目标器件和 10 ns 时钟，并依次运行 CSim、综合和 Cosim。生成的工作目录应放在仓库外，或使用已忽略的 `hls/work/`；不要提交到 Git。导出的 IP/`.xo` 应作为 GitHub Release 附件保存。
