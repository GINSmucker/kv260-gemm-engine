# Git 与交付流程

## 分支

- `main`：可共享、状态明确的稳定版本。
- `hls/<name>`：A 的计算核开发。
- `model/<name>`：B 的参考模型与数据开发。
- `board/<name>`：C 的平台与 XRT 开发。
- `chore/<name>`：目录、文档和工程维护。

开发完成后通过 Pull Request 合入 `main`。不要把每个时间节点做成长期分支；重要、可复现节点用 tag，例如 `v0.2.0`。

## 提交内容

- Git：源码、脚本、配置、小型测试数据、Markdown 摘要。
- Release：IP 压缩包、`.xclbin`、大体积报告或可交付二进制。
- 不提交：Vitis/Vivado cache、完整工作目录和可重建的中间文件。

## Pull Request 最低说明

1. 改了什么，以及对应成员职责。
2. 接口是否变化。
3. 已运行哪些验证；未运行的项目明确写 `NOT RUN`。
4. 关键资源、时序或板端性能是否变化。
5. 相关 Release、报告或测试数据的位置。
