# v2：真实 H576 Dense 源码快照

归档更新：2026-10-26。此前此位置的 v2 选用 OPT46；本次按实际 H576 来源替换活动快照。
旧 OPT46 源码与报告保留在此前归档提交历史，本机副本另存于工作区
`.test_build/retained_opt46_20261026`，不再作为本分支的 v2。

## 来源与身份

- 来源分支：`codex/h576-summary-20261019`。
- 精确提交：`f506f14e44190d035842be6e4bbc0ff431592c5d`。
- 原始目录：`hls/h576/`；原来源映射为 `docs/locks/H576_SOURCE_MAP.json`。
- 五份核心/TB 和四份 monitor 全部与来源映射中的 `published_sha256`、`sha256` 相同，`exact_copy=true`。
- 最新 `codex/team-handoff-20261007` 中这九份 H576 文件与上述提交逐字节相同。
- `docs/locks/H576_PACKAGE.lock.json` 与本机 `h576-runtime-public-ui.tar.gz` 内的原 manifest
  逐字节相同，SHA256 为 `c18de455de083363d0b73a7b526f5262a5cb2371c8e91b5900a4447328694a76`。

| 文件 | 原始 SHA256 |
| --- | --- |
| `src/model_gemm_top.cpp` | `4f855e84019f1fbb4f6663d6f3eb46cf8a8c2c0d49f2346090fee42ade731d16` |
| `src/model_gemm_core.hpp` | `6f01b6f35beb63cedb4bf3aa39a1f2f39547811a8bf449e0ac6fc466a5e80719` |
| `include/model_gemm_config.hpp` | `7e10fbcfab10af95209461b728cf5b2b447ca267ecdee0e6550db812c7f206c4` |
| `tb/testbench.cpp` | `854880fc41e8162120075689daf631852d0000fc7c626339b51535a6b9d398d0` |
| `tb/public_cases.hpp` | `300a5c4d52f7c737b891a4338af554096268279c0f505c7a65a0ac9c9644af06` |

## 能力与机制

- `Y = XW`；X/W signed INT8、乘积 INT16、累加与 Y INT32，紧凑行主序。
- 唯一顶层为 `model_gemm_top(X,W,Y,M,K,N)`，三个 128-bit AXI master，64-bit 字节地址，`ap_ctrl_hs`。
- TM32/TN768，32×16 外积阵列，设计并行度 512 MAC，计算循环显式请求 II=1。
- 六种 M×K×N：`1×192×1000`、`197×192×192`、`197×192×576`、`197×192×768`、
  `197×768×192`、`197×576×192`。H576 fc1 与 qkv 同为 `197×192×576`。
- W 在每次调用时加载到片上 bank；X 按 M tile 加载；完整 K 归约后以连续 128-bit 字写回 Y。
- 它沿用 OPT45 Dense 计算路径，没有 OPT46 的 stream-of-blocks/DATAFLOW 输出双缓冲；
  不支持 INT4、2:4 压缩数据或 Dense/Sparse mode。

## 配置与证据边界

原 `hls/h576/` 没有 HLS 配置文件。来源仓库的 `hls/hls_config.cfg` 是另一个旧默认入口，
指向旧源码并使用 10 ns，不能视为 H576 的配对配置。本目录 `hls_config.cfg` 为本次迁移新建，
与独立 v2 分支适配配置逐字节相同；相对路径引用本目录五份源码/TB，目标 8 ns、不确定度 1 ns。
8 ns 依据已发布 `results/model_integration/model_system_20261016_130752_213/platform_verification.json`
的 `clock_constraint_ns`；这不等于恢复了当时全部 HLS 构建参数。

| 项目 | 状态 | 范围 |
| --- | --- | --- |
| 原九份文件与 H576 来源映射逐 SHA 核对 | PASS | 仅文件身份，不是新计算验证 |
| 新相对路径配置的 Vitis HLS/RTL 重建 | NOT RUN | 未运行综合、协同仿真或 IP 导出 |
| 本次归档平台实现、固件加载或板端计算 | NOT RUN | 未运行 Vivado 或访问板卡 |
| 当前H576活跃路径原生C++检查 | PASS | 新v2分支12个rtl合成事务及异常尺寸检查通过；不是RTL仿真，见results/versions/native_functional_20261026.json |

历史运行包绑定 platform fingerprint `d9b17a2f770261c5228f6c62b4a5c7d5ee9a9f5badb27136263ff015dc29ab96`，
observer tags `3652287023/1996644805`，512 MAC/125 MHz/128 bit。已发布平台核查报告记录
WNS +0.63 ns、WHS +0.01 ns、14 个寄存器、三路 128-bit 接口；这些属于原平台的历史证据，
不代表本次重新实现。本次归档没有重新证明 HLS 源码到位流的完整构建链。

已有 H576 完整模型、图片与有限连续运行结果仍按独立 v2 分支的
`docs/verification.md`、`docs/locks/H576_PACKAGE.lock.json` 及后续 C 回执解释；
原 lock 中早期 `NOT_RUN` 字段不作追溯改写，新目录也不继承为本次通过。
原 H576 约 142.7 ms 稳态记录可以关联其固定 Dense H576 软件/平台，不能冒称 OPT46 数据，
也不能直接作为 OPT50 同条件稀疏收益对照。
