# KV260与构建环境

目标commercial K26为xck26-sfvc784-2LV-c，board part为xilinx.com:kv260:part0:1.1。研究OPT45使用Vivado/Vitis HLS/Bootgen2020.2.2、名义125MHz、HLS target8ns/uncertainty1ns；默认OPT38配置100MHz。新电脑须确认工具与器件数据真实存在。

已测ARM环境为Ubuntu24.04/kernel6.8.0-1015-xilinx、XRT/ZOCL2.18与g++13。驱动候选的身份由正式锁和配对运行证据固定；[引用补丁](../../board/driver_fixes/zocl_2_18_refcounts.patch)及[卸载补丁](../../board/driver_fixes/zocl_2_18_cu_teardown.patch)不表示系统安装模块已替换。

Windows使用PowerShell7；源码资产携带匹配board文件、observer、HLS/IP及平台脚本。ARM C++使用已有XRT、uuid、OpenSSL和ORT SDK；不复制系统库或自动安装工具。CMake研究配方验证NOT RUN。

SSH由维护者提供地址及本地密钥：host kv260、user ubuntu、项目/home/ubuntu/fpga_project。只读入口board/runtime/tools/board_preflight.py不加载驱动/overlay，不能替代root核验或使用许可。

设备运行遵守[board](../../board/README.md)与[接口](../interface.md)。实际独占、boot、driver、tags、UUID、CU/AXI静止和回退必须在当前窗口核实；boot/QSPI/复位及系统安装独立授权。
