# 当前验证范围

有效实验日期2026-10-10。每个PASS只适用于其源码、工具、平台和输入；资料的软件完整性检查与原始硬件测量分别登记。

|对象|状态|依据与范围|
|---|---|---|
|OPT45 C-sim|PASS|160有效+8拒绝/10925312整数/320输入SHA及同地址复用|
|OPT45 HLS/导出|PASS|2020.2.2，target8ns/uncertainty1ns；关键loop II1，单512MAC|
|OPT45 AXI RTL|PASS|16事务/418064整数，高地址、4KiB边界、背压、const与排空|
|OPT45配对平台|PASS|WNS0.641/WHS0.010ns、14寄存器、128bit三路与131项DDR、bit/bin配对|
|公开实板shape|PASS|20次/1365664整数，实际板频124999999Hz|
|完整FP32边界|PASS|3图/12257976整数；X/Xq/sx/Y/Z/logits逐位一致，20图×3正式|
|无诊断整图|PASS（研究测量）|两组各60正式样本、20/20配对中位数更快；所有慢样本保留|
|资源与断开|PASS（有界）|200连续、3次重建、提交后断开恢复；client RSS+256KiB，不证明长期无泄漏|
|独立root/UI|PASS（实验终态）|starter/99999999Hz、无使用者/ZOCL/render，严重内核错误0|
|正式独立C / PL500/full质量 / 独立長稳 / 正式发布|NOT RUN|研究交付不能替代签署|
|默认OPT38 2026.1 C-sim|PASS（队友报告）|main f5ead7a；该工具配置的综合/RTL/板端NOT RUN|
|新构建的HLS/RTL/ARM/平台/实板资格|NOT RUN|构建内容或身份改变后需要独立验证|
|源码资产中的CMake配方|NOT RUN|Linux/AArch64现有SDK要求；仅说明编译，不加载设备|

opt45-evidence包含六段完整无诊断正式样本360条、连续200条、三张图验证/重建/断开记录，以及生产、完整边界、RTL、HLS和配对平台摘要。矩阵和长trace由原本机清单管理，不随资料重复分发。原始local_gate只负责其HLS范围，系统判断以对应实板与整图报告为依据。

## 资料与软件入口

```powershell
python -B docs/tools/check.py --node
python -B docs/tools/assets.py opt45-source --download --extract
python -B docs/tools/check.py --cpp --research-source build/assets/opt45-source/opt45-source --cxx <existing-cxx> --hls-include <existing-vitis-include>
python -B model/golden.py --check model/cases/tensors.npz
```

资产归档与成员SHA校验、冻结33成员与55文件A交付核对是文件完整性检查。普通C++独立INT64 TB忽略HLS pragma，不能称RTL cosim；小head为1000输出，UI使用17 Node/6 Python软件用例。当前资料校验状态由PACKAGE_MANIFEST与相应命令输出固定。

资料软件核对PASS：281清单成员及当前链接/导入；默认两核和OPT45普通C++各8有效+8拒绝、418064完整INT64输出；17 Node/6 Python用例及1000输出公开head。OPT45源成员112、冻结运行成员33与A交付55逐SHA匹配。560条正式样本的28个分位数/最大值复算一致，251节点/49 QuantLinear图结构有效，运行权重等同冻结Wq拼接。该范围没有新增PL请求或硬件资格。

## 指标与限制

HLS estimate7ns、loop II1、top Interval和RTL总周期与板端完整FP32边界、Runner.run分别报告。HWH时钟值124998749Hz与实板124999999Hz是各自报告字段，名义频率125MHz。不得将不同层级数值相加或归因到未测阶段。

p99/max不是所有参照都更优；200次不足以证明长稳。普通libm GELU与1/2线程存在数值FAIL，已排除。独立AR到首R、末W到B为NOT REPORTED，OPT45独立X/Y阶段及诊断墙钟扰动NOT MEASURED。高地址RTL通过不等于新的实板高地址覆盖。

身份与证据：[资产锁](locks/ASSETS.lock.json)、[SOURCE_MAP](SOURCE_MAP.json)、[性能资源表](../results/summary.csv)、[当前状态](progress.md)。
