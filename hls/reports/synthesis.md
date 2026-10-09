# OPT38 综合摘要

## 候选信息

- 顶层：`model_gemm_top`
- 目标：KV260 / `xck26-sfvc784-2LV-c`
- 时钟：10 ns
- 结构：8×16 外积，128 MAC/cycle
- 当前配置工具：Vitis 2026.1

## 本仓库重新综合结果

| 指标 | 结果 |
|---|---|
| C Simulation | PASS（160 个合法用例、8 个拒绝用例） |
| C Synthesis | NOT RUN |
| C/RTL Co-simulation | NOT RUN |
| Estimated clock | NOT RUN |
| Latency | NOT RUN |
| DSP | NOT RUN |
| BRAM | NOT RUN |
| LUT | NOT RUN |
| FF | NOT RUN |

OPT38 的“128 MAC/cycle”描述的是源码微结构，不等于已经确认占用 128 个 DSP，也不等于整个矩阵每周期完成 128 个最终输出。真实资源映射和吞吐必须以本目录重新生成的综合报告为准。

综合后应在本文件记录：Git commit、Vitis 版本、目标 part、时钟、每个登记尺寸的 latency，以及 DSP/BRAM/LUT/FF。原始大报告不要直接提交；如需交付，放入 GitHub Release。
