# v1 工具入口

`check_version_layout.py` 是当前分支的只读结构/源码身份检查入口，不编译、不访问板卡。

`run_version_native.py` 和 `check_version_snapshot.py` 为旧三阶段归档工具，按 `hls/versions/` 中的目录操作，不是当前活跃核选择器；不要把它们运行成功当作 v1 平台或 HLS/RTL 验收。

`run_active_native.py` 为当前分支原生功能入口：使用已有 Vitis 的头文件和本机编译器，仅编译 `hls/src/gemm.cpp` 与 `hls/tb/testbench.cpp`，运行无实参。输出必须放在全新的 Git 忽略目录，保留编译/运行日志和源码 SHA。它会写文件、运行本机可执行程序，但不运行 XRT/ARM owner 或访问 FPGA。
