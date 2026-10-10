# 验证清单

当前对象为 OPT40 源码基线 `79ac98b250a0d90e94ec94c1cd388682cc3f4140`，工具为 Vitis 2026.1、目标器件为 `xck26-sfvc784-2LV-c`、时钟目标为 10 ns。源码与独立 OPT40 工作区的已测文件逐字节一致；下表区分“该工作区已测”与“本仓库路径重新运行”。不继承 OPT38 或旧 bitstream 的通过状态。

| 项目 | 状态 | 说明 |
|---|---|---|
| OPT40 源码原样导入 | PASS | top、core、config、testbench、HLS 配置的 SHA-256 与已测工作区一致 |
| C Simulation（独立 OPT40 工作区） | PASS | 2026-10-10 22:18；5 个合法尺寸各 1 组确定性小幅值输入，1 个非法尺寸 |
| C Synthesis（独立 OPT40 工作区） | PASS | 2026-10-10 22:21；资源和 Burst 见 `hls/reports/synthesis.md` |
| C/RTL Co-simulation（独立 OPT40 工作区） | PASS | 2026-10-10 22:26；Verilog / XSIM，当前 testbench |
| 在本仓库路径重新运行 CSim / 综合 / Cosim | NOT RUN | 尚未验证复制后的 HLS 配置在新路径下可复现 |
| 全零、INT8 边界、随机分布及充分的尾块测试 | NOT RUN | 当前 OPT40 testbench 的覆盖范围小于旧 OPT38 testbench |
| 本分支生成并发布新 IP / xclbin | NOT RUN | 不复用 OPT38 产物；二进制应放 Release |
| OPT40 XRT runtime 适配与编译 | NOT RUN | 128 位数据口需与 C 的运行端核对 |
| KV260 正确性测试 | NOT RUN | 需与 Golden Reference 逐元素比较 |
| KV260 性能测试 | NOT RUN | 需注明尺寸、频率、源码提交及计时边界 |

## 每次候选版本的最低验收

1. C Simulation 覆盖全部登记尺寸，并至少包含零值、边界值和随机值；当前 OPT40 尚未满足后一项。
2. C Synthesis 保存关键资源、II 与 latency 数据；10 ns 目标还需后续实现时序确认。
3. C/RTL Co-simulation 输出与 C testbench 一致。
4. 板端输出逐元素对比 Golden Reference。
5. 性能记录绑定 Git commit、IP/xclbin 哈希、矩阵尺寸和工具版本。
