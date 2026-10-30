# 验证清单

当前分支用于重新整理项目。下表仅记录本目录中的当前版本，不继承旧目录或旧 bitstream 的通过状态。

| 项目 | 状态 | 说明 |
|---|---|---|
| OPT38 源码导入 | PASS | 源自 `team-handoff-20261009` |
| Vitis 2026.1 C Simulation | PASS | 160 个合法用例、8 个拒绝用例；10,925,312 个输出一致 |
| Vitis 2026.1 C Synthesis | NOT RUN | 资源与时序待记录 |
| C/RTL Co-simulation | NOT RUN | 等综合通过后执行 |
| IP / xclbin 生成 | NOT RUN | 不能复用旧候选物冒充当前版本 |
| XRT runtime 编译 | NOT RUN | 最新源码尚未导入 |
| KV260 正确性测试 | NOT RUN | 需与 Golden Reference 比较 |
| KV260 性能测试 | NOT RUN | 需注明尺寸、频率和计时边界 |

## 每次候选版本的最低验收

1. C Simulation 覆盖全部登记尺寸，并至少包含零值、边界值和随机值。
2. C Synthesis 达到目标时钟，并保存关键资源与 latency 数据。
3. C/RTL Co-simulation 输出与 C testbench 一致。
4. 板端输出逐元素对比 Golden Reference。
5. 性能记录绑定 Git commit、IP/xclbin 哈希、矩阵尺寸和工具版本。
