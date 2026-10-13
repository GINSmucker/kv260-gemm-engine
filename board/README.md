# C：平台、共享运行时与设备生命周期

默认运行源码位于runtime，默认平台位于vivado；当前研究实现的准确编译输入、HLS/IP与平台脚本通过opt45-source取得。driver_fixes提供固定候选补丁，ui是可选工作台，其设备能力需要真实就绪身份。

OPT45配对平台model_system_20261010_184640_939为125MHz，X/W/Y128bit，经SmartConnect共用128bit HP0，HPM0/HP0匹配；14寄存器和131项DDR参数已核验。UUID/tags、运行图、owner和插件均由完整研究包身份固定。

## 取得与核对

```powershell
python -B docs/tools/assets.py opt45-source --download --extract
python -B docs/tools/assets.py opt45-runtime --download --extract
python -B docs/tools/assets.py ps-runtime --download --extract
python -B docs/tools/assets.py ps-evidence --download --extract
python -B docs/tools/assets.py opt45-evidence --download --extract
```

当前组合运行包含配对bit.bin、DTBO、metadata、owner、B插件及32成员manifest；源码包含匹配平台脚本与55文件HLS/IP冻结交付。既有board-runtime资产提供固定驱动候选；不能从资产存在推断板卡当前已加载。

当前组合源码和实测参数入口为组装包source/build_runtime.py，Linux/AArch64依赖与SDK路径由调用方明确确认；在新构建副本编译，不覆盖冻结包。基础opt45-source的CMake配方只对应基础owner，配方验证NOT RUN。原源码身份和预编译资格是可核验范围，重建产物需重新验证。默认runtime/CMakeLists.txt默认只编译软件协议测试，BUILD_XRT_OWNER是显式开关。

## 运行契约

维护者确认独占、固定驱动/boot/包身份并加载匹配平台后，运行包的cli.py run接受bundle、plugin、socket、input和递增run-id；匹配图为graphs/model_w8a8_pl.onnx。Runner.run不包含文件读取/会话创建；该测量边界在[progress](../docs/progress.md)登记。

owner独占CU/BO；B初始化导入两个共享FD，RUN只传控制。owner检查Done与AXI排空，B完成FROM_DEVICE后反量化。START之后完成未知时保留owner/BO，不强杀、重放或自动回退CPU。断开恢复需QUERY确认完成、ABORT封闭及新会话完整验证。

STOP后确认CU/AXI静止、无设备使用者，再卸载临时模块并恢复starter/100MHz；boot/QSPI/复位不由仓库授权。当前研究覆盖200连续、3次重建和断开恢复，正式独立C及长稳NOT RUN。历史boot和回执不能替代实时占用检查。

唯一契约见[interface](../docs/interface.md)，源码/包绑定见[SOURCE_MAP](../docs/SOURCE_MAP.json)，资格见[verification](../docs/verification.md)。
