# 团队协作规则

## 目录责任

- A 维护 `hls/`：计算核、HLS testbench、配置和综合摘要。
- B 维护 `model/`：量化、Golden Reference、测试用例及误差定义。
- C 维护 `board/`：Vivado 平台、XRT runtime 和板端测试。
- `docs/`、`results/` 由三人共同维护。

## 基本约定

1. `docs/interface.md` 是软硬件接口的共同依据；接口变更必须同步修改它。
2. 功能开发使用独立分支，通过 Pull Request 合并到 `main`。
3. 不提交 Vitis/Vivado 工作目录、缓存和大体积生成文件。
4. 可复现脚本和小型报告进入 Git；`.xclbin`、IP 压缩包等放 GitHub Release。
5. 验证结果只能写 `PASS`、`FAIL` 或 `NOT RUN`，不得把旧版本结果当成当前版本结果。
6. 每个性能数据必须注明源码提交、工具版本、时钟、矩阵尺寸和测量位置。
