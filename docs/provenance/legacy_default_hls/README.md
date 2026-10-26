# HLS 计算核

负责人：A

## 当前候选

`OPT38 full-K v1`，来源为团队交接版本 `team-handoff-20261009`。

- 顶层：`model_gemm_top`
- 数据：X/W 为 INT8，Y/累加为 INT32
- tile：`TM=8`、`TN=64`、`TK=32`
- 微结构：每周期形成 8×16 外积，设计并行度 128 MAC/cycle
- X/W AXI 指针：32 位打包，每个字包含 4 个 INT8
- 控制：AXI4-Lite + `ap_ctrl_hs`

当前只接受以下 5 个尺寸：

| family | M | K | N |
|---|---:|---:|---:|
| head | 1 | 192 | 1000 |
| proj | 197 | 192 | 192 |
| qkv | 197 | 192 | 576 |
| fc1 | 197 | 192 | 768 |
| fc2 | 197 | 768 | 192 |

它不是通用任意 M/N/K 的 dense 引擎。修改尺寸表、打包方式或顶层接口时，必须同步修改 testbench 与 `docs/interface.md`。

## 文件

```text
src/model_gemm_top.cpp       HLS 顶层和 AXI pragma
src/model_gemm_core.hpp      计算核心
include/model_gemm_config.hpp 类型、容量和合法尺寸
tb/testbench.cpp             C/RTL 共用测试平台
tb/public_cases.hpp          公开测试向量定义
hls_config.cfg               Vitis 2026.1 工程配置
reports/synthesis.md         综合数据摘要
```

## 当前验证状态

源码最初面向 Vitis HLS 2020.2.2。本目录的迁移配置已在 Vitis 2026.1 下通过 C Simulation：160 个合法用例通过，8 个非法尺寸被正确拒绝，共比较 10,925,312 个输出。C Synthesis 和 C/RTL Co-simulation 仍需重新运行，当前状态以 `docs/verification.md` 为准。

生成的工作目录放在 `hls/work/`，不要提交到 Git。导出的 IP/`.xo` 应作为 GitHub Release 附件保存。
