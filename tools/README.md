# v2 开发机工具

- `check_version_layout.py`：只读核对统一目录、说明入口、配置路径和源码SHA。
- `run_active_native.py`：使用已有Windows Vitis附带clang编译、运行本分支合成testbench。
  必须提供新输出目录，会写本机程序和日志，不访问板卡，不运行HLS或RTL。

`docs/tools/check.py` 在本分支委托当前布局检查，不声称原整包未变化。
原检查器逐字节保留于 `docs/provenance/check_upstream.py`；原整包清单不改写。
本分支更新首页、文档和活跃路径，因此对原整包逐文件检查会报告差异，这不是H576内核计算失败。
