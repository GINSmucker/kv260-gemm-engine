# KV260 平台与 XRT

负责人：C

## 当前内容

- `vivado/create_system.tcl`：从团队交接版本导入的平台创建脚本。
- `vivado/constraints.xdc`：约束占位文件。
- `runtime/`：最新 XRT v5 runtime 的目标位置，目前只有占位文件。

## 当前限制

交接包的最新 Git 源码中未包含完整 v5 runtime 实现，因此这里没有用旧 V2A/V2B 代码冒充新版本。取得权威源码后，应将公开头文件、实现、测试和构建配置放入 `runtime/`，并核对 `docs/interface.md`。

`create_system.tcl` 也需要在新目录、新 IP 和当前 Vivado 版本下重新验证；存在文件不代表平台已经可以生成或上板。
