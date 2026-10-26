# 软件参考模型

负责人：B

- `quantize.py`：模型输入与权重的量化辅助逻辑。
- `golden.py`：使用较宽整数累加生成 Golden Reference。
- `cases/manifest.json`：可共享测试用例的清单。

当前两个 Python 脚本来自团队交接版本。测试数据尚未随本次整理导入，因此 manifest 标记为 `NOT_AVAILABLE`。

新增用例时至少记录：用例名、M/K/N、数据类型、布局、随机种子或来源、输入文件哈希、期望输出文件哈希。大型 tensor 不直接提交 Git，可放 Release 并在 manifest 中写下载位置和 SHA256。
