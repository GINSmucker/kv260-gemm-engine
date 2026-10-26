# 项目进度

更新时间：2026-10-09

## 已完成

- 第一周 Naive GEMM 和第二周 tiled dense GEMM 历史版本已由 Git tag 保存。
- 已接收团队交接版本中的 OPT38 HLS 源码。
- OPT38 使用 8×16 外积阵列，设计并行度为 128 MAC/cycle。
- 已把仓库整理为 `hls / model / board / docs / results` 五个主要目录。
- 已导入 B 的量化和 INT64 Golden Reference 脚本。
- 已导入 C 提供的平台创建脚本。
- 已在 Vitis 2026.1 下完成 OPT38 C Simulation：160 个合法用例通过，8 个非法尺寸正确拒绝，共比较 10,925,312 个输出。

## 尚未完成

- 重新综合并记录 DSP、BRAM、LUT、FF、时序和 latency。
- 完成 C/RTL Co-simulation。
- 取得并整理最新 XRT v5 runtime 源码。
- 生成新 IP / `.xclbin`，完成 KV260 板端正确性与性能测试。
- 补齐可复现的小型输入用例与真实测试结果。

所有验证状态见 `verification.md`，不要用“源码已复制”替代“验证已通过”。
