# 项目进度

更新时间：2026-10-10（OPT40 开发分支）

## 已完成

- 第一周 Naive GEMM 和第二周 tiled dense GEMM 历史版本已由 Git tag 保存。
- 已接收团队交接版本中的 OPT38 HLS 源码。
- OPT38 使用 8×16 外积阵列，设计并行度为 128 MAC/cycle。
- 已把仓库整理为 `hls / model / board / docs / results` 五个主要目录。
- 已导入 B 的量化和 INT64 Golden Reference 脚本。
- 已导入 C 提供的平台创建脚本。
- 已在 Vitis 2026.1 下完成 OPT38 C Simulation：160 个合法用例通过，8 个非法尺寸正确拒绝，共比较 10,925,312 个输出。
- 已从最新远端 `main` 建立独立的 `hls/opt40-axi128-burst` 分支，保留原有 OPT38 历史。
- 已导入 OPT40：16×16 输出 tile、256 MAC/cycle 设计并行度、X/W/Y 128 位 AXI、Y 写回 Burst。
- 与本分支源码逐字节一致的独立 OPT40 工作区已通过 Vitis 2026.1 CSim、综合和 Cosim；资源和延迟见 `hls/reports/synthesis.md`。

## 尚未完成

- 从本仓库路径重新跑 CSim、综合、Cosim，确认迁移后的配置可复现。
- 扩展 OPT40 testbench：全零、INT8 边界值、随机分布和更多尾块/非法参数用例。
- 取得并整理最新 XRT runtime；按 OPT40 的 128 位接口重新核对板端代码。
- 生成新 IP / `.xclbin`，完成 KV260 板端正确性、时序和逐尺寸性能测试。
- 将每组性能数据绑定源码提交、工具版本、输入形状和计时边界。

所有验证状态见 `verification.md`，不要用“源码已复制”替代“验证已通过”。
