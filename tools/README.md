# v3 开发机工具

- `check_version_layout.py`：只读核对本分支目录、README、活跃配置和源码SHA。
- `check_version_snapshot.py`：只读核对分支文件清单。
- `run_active_native.py`：只编译运行当前OPT50合成C++ testbench，必须提供全新输出目录。
  会写本机程序、临时文件和日志；不调用HLS/RTL/Vivado，不访问板卡。

板端软件的文件核验与CPU单测见 `board/README.md`。固定启动身份不因仓库目录调整而失效。
