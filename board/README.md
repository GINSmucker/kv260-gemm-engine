# C：KV260平台、运行时和板端验证

vivado/保留实际PS/AXI/DDR平台创建脚本/DT及observer；runtime/已取得AB1 v5真实源码，model_gemm_runtime.cpp/hpp对应原kernel.cpp/h，owner.cpp/protocol.h原字节导入。kernel.h仅提供旧include兼容名；legacy/model_backend保存冻结V2A与未采用V2B源。driver_fixes/为修复候选；ui/为已有可选工作台。

[docs/interface](../docs/interface.md)是唯一公共接口；运行平台和metadata须与docs/locks/DEVELOPMENT_LOCK.json绑定的OPT38配对一致。历史35a包不能混入38，新构建不继承旧A2。

## 软件与构建

不访问设备的协议检查由runtime/test_runtime.cpp和CMake protocol_test目标提供；Python总体检查位于docs/tools/check.py。

```powershell
python -B board/runtime/tools/build_runtime.py --variant v5-ab1 --uuid-include <existing-sdk>
./board/runtime/tools/platform.ps1 -Candidate opt38
```

默认只预览/准备新build副本，--execute限已有SDK的AArch64 Linux；-Build才运行Vivado2020.2.2。实际XRT owner或B插件由runtime/CMakeLists.txt显式BUILD_XRT_OWNER开关编译，Windows不编译/运行Linux owner，目录软件迁移不等于ARM资格。

## 产物与调用

```powershell
python -B docs/tools/assets.py v5-runtime --download --extract
python -B docs/tools/assets.py opt38-platform --download --extract
```

资产进入build/assets/且校验原SHA；旧Release/tag字节保持。真实owner使用维护者提供的metadata/固定W/私有root，临时驱动、独占、boot、tag、地址与AXI静止按窗口方案固定。无直接加载/驱动/boot操作在本轮验证中执行。

原C1软件、异常恢复、ARM/XRT、完整49层以及独立A/C历史证据分开计范围，见[verification](../docs/verification.md)与results/evidence。UNKNOWN保留owner/BO；先确认真实完成再停止，不能强杀、重放或据退出码推断回退成功。
