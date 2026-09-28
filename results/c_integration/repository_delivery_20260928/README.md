# 2026-09-28 仓库交付复核

本目录记录把已经完成的 F8 工作整理到协作仓库后的本机复核。
此次没有重新上板，也没有重跑 Vivado、RTL 仿真或 ARM 编译。

| 检查 | 实际结果 | 输出 |
|---|---|---|
| 精确交付完整性 | PASS；31 个包文件、17 个当前源码、4 个硬件输入、31 个原 IP 解压文件、15 个 board files、3 个 raw board logs | [integrity.txt](integrity.txt) |
| F8 软件开发测试 | 20/20 PASS | [f8_tests.txt](f8_tests.txt) |
| C1 首次沙箱内回归 | 6 PASS / 1 ERROR；TemporaryDirectory 内写文件 PermissionError，清理亦受限 | [c1_tests_sandbox_failure.txt](c1_tests_sandbox_failure.txt) |
| C1 原代码解除沙箱限制重跑 | 7/7 PASS | [c1_tests.txt](c1_tests.txt) |
| 暂存 Git 对象核对 | 52 个受实测 SHA-256 约束的对象一致；文档目标全部存在于 Git 中 | [git_object_integrity.json](git_object_integrity.json) |

测试解释器为已有 `E:\anaconda\python.exe`，Python 3.12.7 / NumPy 1.26.4，未安装或修改依赖环境。
离线清单检查使用标准库 Python 3.12；两个既有 PowerShell 构建/封装脚本静态解析通过。
软件测试的模型/CPU PASS 不替代已归档的实板运行 `262414267686403`，正式验收仍 NOT RUN。
原运行源码和配对包保持相同字节，仓库新增的是交付说明、离线检查入口和 Git 文件规则。
