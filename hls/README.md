# OPT50 活动计算核

活动顶层`model_gemm_top`、共享Dense/2:4核心、容量配置和testbench来自已保存v3 OPT50源码，字节身份由根目录`release_sources.json`固定。接口见 [共同接口](../docs/interface.md)。

`hls_config.cfg`使用相对路径，目标K26、125MHz，CSim用`full`、Cosim用`rtl`用例集。此Git整理没有执行新的HLS综合、RTL协同仿真或Vivado实现。

已有Windows Vitis2026.1可用以下工具在新输出目录进行native C++功能检查：

```text
python -B tools/run_native_hls.py --vitis-root D:/Vivado/2026.1/Vitis --out .test_build/native_hls
```

先建立`.test_build`父目录。`rtl`在native工具中只是用例集名，不是RTL仿真。测试会编译和运行本机程序、写日志，不访问硬件；需要现有vendor头与工具链，不自动安装工具。
