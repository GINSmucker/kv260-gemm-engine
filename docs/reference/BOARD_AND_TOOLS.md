# KV260 板卡与工具

已验commercial K26器件为`xck26-sfvc784-2LV-c`，KV260 board part为`xilinx.com:kv260:part0:1.1`。本机Vivado/Vitis HLS/Bootgen实际2020.2.2；目标100MHz，HLS period10ns/uncertainty1ns。board XML本身不补充缺失器件族；新电脑先检查工具与K26数据是否存在。

板端已用Ubuntu24.04、kernel6.8.0-1015-xilinx、XRT/ZOCL2.18、g++13。固定驱动上游为[XRT 44be12a](https://github.com/Xilinx/XRT/tree/44be12a574e82a5eea2589f25be45ac88a5cc591)，叠加[引用补丁](../../board/driver_fixes/zocl_2_18_refcounts.patch)及[CU卸载补丁](../../board/driver_fixes/zocl_2_18_cu_teardown.patch)。候选模块SHA在BASELINE_LOCK，系统安装模块未替换。

官方board数据来自[XilinxBoardStore 2020.2.2固定提交](https://github.com/Xilinx/XilinxBoardStore/tree/4f0fb5bf8aa5afe17a666a2dd8d35cf3b347016e/boards/Xilinx)，K26平台参考为[kria-vitis-platforms固定提交](https://github.com/Xilinx/kria-vitis-platforms/tree/f46e63e9a0c10c9718208623d77132eb630ca5b4/platforms/vivado)。IP Release包含用于重建的KV260 1.1原始板文件、许可证和来源，不上传工具数据库或SDK安装包。

本机按原项目原生PowerShell7构建。示例均从仓库根目录执行，显式指定安装工具：

```powershell
./hls/tools/hls.ps1 -Candidate opt35a -Stage csim -HlsTool '<installed-vitis_hls.bat>'
./board/runtime/tools/platform.ps1 -Candidate opt35a
./board/runtime/tools/platform.ps1 -Candidate opt35a -Build -VivadoTool '<installed-vivado.bat>'
```

平台默认只准备新工作副本；`-Build`才调用Vivado。它修正工作副本三路HAS_BURST元数据，生成新fingerprint/tags；不能沿用历史A2或B插件身份。HLS导出/完整监测仍需自己的阶段与报告，不以本次普通C++测试替代。

SSH由各成员在本机配置。主机别名`kv260`、用户`ubuntu`、项目目录`/home/ubuntu/fpga_project`是团队接口；地址和密钥保持本地。

```text
Host kv260
    HostName <maintainer-provided-address>
    User ubuntu
    IdentityFile <local-private-key>
```

只读用户预检：`python -B board/runtime/tools/board_preflight.py --host kv260`。它读取boot/kernel/模块/render/owner进程，不访问overlay/configfs，不加载或卸载任何内容；不能替代root C0、公共锁或板卡使用许可。

运行产物通过`docs/tools/assets.py board-runtime --download --extract`取得。其中historical_ready、candidate及paired_manifest是历史身份，不是当前boot的可直接执行授权。维护者必须重新固定owner/config/boot/window/driver/tags/UUID，先软件核对，再按独占窗口执行和回退。

加载与运行链：维护者安排临时runtime权限和配对overlay→唯一owner持有BO/CU→B通过C调用→完成/排空/结果消费→SAFE_STOP→确认无设备用户、CU和AXI静止→回退starter。UNKNOWN状态保留owner/BO，不强杀、不重发或卸载。boot/QSPI/reboot/reset与系统安装另行授权。

回退后的starter、无ZOCL/render、XRT0是预期状态。每窗仍有4条已知属性告警；不能把它解释为全面泄漏修复或任意长期切换安全。

当前开发AB1使用原OPT38配对清单d43d1b8f…d07f9，与基础35a运行包的BIN/DTBO/metadata不同。本次新增opt38-platform按需资产并与v5源码硬编码UUID/tags匹配；旧驱动仍复用，不能把35a的metadata或bit混入38。新资产只是文件，实际板卡安排、运行库/模型/owner与boot、AXI静止/回退仍由维护者固定。
