# 验证汇总

|范围|状态|依据与限制|
|---|---|---|
|队友main Vitis2026.1 C-sim|PASS（队友报告）|main f5ead7a/PR4，160合法+8拒绝/10925312输出；本轮未重跑|
|主线2026.1 C synthesis / RTL cosim / 板端|NOT RUN|不继承历史2020.2.2实验|
|原OPT38完整2020.2.2 HLS/RTL/独立A2|PASS（历史身份）|原public证据/交付，128MAC/100MHz；迁移不成为新Gate|
|原AB1 20图同v5比较|PASS（开发可比性）|438.385ms、较35a减少50.632%；非正式PL/CPU/质量/长稳发布|
|AB6后续同v5两窗|PASS（开发可比性）|431.021/430.068ms；第二CPU参考漂移超限，整体INCONCLUSIVE|
|数据包856JPEG/20预处理身份|PASS（原数据交付）|dataset_release固定SHA；不等同准确率|
|本轮目录身份/链接/Python导入|PASS|279文件第一次完整复验；收尾最终清单与冷检出另见本轮小回执|
|本轮普通C++公开TB|PASS|OPT35a/OPT38各8有效+8拒绝、各418064完整INT64输出；忽略HLS pragma|
|本轮既有UI测试|PASS|17 Node +6 Python；无板卡请求|
|本轮wire5/49层凭据及小head|PASS|C++17协议/拒绝检查；NPZ完整1000 INT64输出；0PL|
|本轮同机CPU输入00|PASS|max_abs FP32=0 / W8A8=0，0PL；仅同机功能回归|
|本轮856JPEG/20预处理|PASS|865payload校验、20输入逐字节等同冻结张量；不代表准确率|
|本轮v5 ARM配方|PREPARED_NOT_COMPILED|检查源/命令，不运行编译器/设备；CMake/XRT/平台/HLS均NOT RUN|
|本轮ARM build / PL / HLS / RTL|NOT RUN|只迁移资料与软件路径|

Gate、测量状态和语义范围分开。旧回执原字段、旧失败、UNKNOWN与未报告cycles保留；本次目录提交不代签C、不读取私有留出。公共原始小回执在results/evidence；旧路径的定位见MIGRATION_MAP。

[本轮小回执](../results/evidence/publication/LAYOUT_VERIFICATION_20261010.json)记录软件范围与未运行项。首轮根路径导入失败、Windows帮助入口SIGHUP失败在本机记录保留，修正后复验；不篡改旧实验结论。

## 软件检查入口
python -B docs/tools/check.py --cpp --node
python -B model/golden.py --check model/cases/tensors.npz
C运行时的test_runtime.cpp检查wire5及49层凭据，0PL；CMake XRT build限Linux/AArch64已有SDK。软件编译不是C synthesis或RTL。
