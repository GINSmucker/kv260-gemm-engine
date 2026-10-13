# 当前验证范围

已发布硬件/PS实验日期2026-10-10，算法契约复核日期2026-10-11。每个PASS只适用于其源码、工具、平台和输入；资料的软件完整性检查与原始硬件测量分别登记。OPT45为下一步Dense FPGA性能基准，A_OPT40_AXI128_M16N16为资源取舍候选；该决定不生成新的硬件或正式资格。

|对象|状态|依据与范围|
|---|---|---|
|OPT45 C-sim|PASS|160有效+8拒绝/10925312整数/320输入SHA及同地址复用|
|OPT45 HLS/导出|PASS|2020.2.2，target8ns/uncertainty1ns；关键loop II1，单512MAC|
|OPT45 AXI RTL|PASS|16事务/418064整数，高地址、4KiB边界、背压、const与排空|
|OPT45配对平台|PASS|WNS0.641/WHS0.010ns、14寄存器、128bit三路与131项DDR、bit/bin配对|
|公开实板shape|PASS|20次/1365664整数，实际板频124999999Hz|
|完整FP32边界|PASS|3图/12257976整数；X/Xq/sx/Y/Z/logits逐位一致，20图×3正式|
|组合无诊断整图|PASS（研究测量）|两轮候选各200正式、20/20输入配对更快，保留全部样本|
|组合资源与断开|PASS（有界）|200连续、3次重建、提交后断开；client/owner采样RSS固定，不证明长期无泄漏|
|独立root/UI|PASS（实验终态）|starter/99999999Hz、无使用者/ZOCL/render，严重内核错误0|
|正式独立C / PL500/full质量 / 独立長稳 / 正式发布|NOT RUN|研究交付不能替代签署|
|默认OPT38 2026.1 C-sim|PASS（队友报告）|main f5ead7a；该工具配置的综合/RTL/板端NOT RUN|
|新构建的HLS/RTL/ARM/平台/实板资格|NOT RUN|构建内容或身份改变后需要独立验证|
|源码资产中的CMake配方|NOT RUN|Linux/AArch64现有SDK要求；仅说明编译，不加载设备|

ps-evidence包含各机制两轮的完整正式样本、数值/重建/断开与默认API记录；opt45-evidence提供同一硬件的HLS/RTL/配对平台身份。矩阵和长trace由原本机清单管理，不随资料重复分发。原始local_gate只负责其HLS范围，系统判断以对应实板与整图报告为依据。

## 已发布PS参照

32成员manifest为7e8396d798c0dcac3c95ef4ad202f5fccfefc47837918ebd68df954a545a382e。固定亲和性、QuantQKV和W BO各两轮、每段20预热+200正式，3600正式样本保留。QuantQKV/固定W各720 Q/K/V张量、27233280 FP32值、147层/12257976整数及所有正式logits逐位一致；默认Runner额外3图和实际mask通过。

组合32成员复用21个硬件基础包成员及11个增量。200连续/3重建/非有限输入拒绝后复用/提交后断开恢复PASS；连续计时含结果检查，219.305993ms慢样本保留，不是生产比较。400个无诊断正式候选无超过180ms，只描述当前样本。正式独立C、PL500/full质量、独立长稳仍NOT RUN。

## A资源候选与本机契约

A固定源码为[hls/opt40-axi128-burst@68a9e643](https://github.com/GINSmucker/kv260-gemm-engine/tree/68a9e643a9e998c1bd075434b31202f1545e00aa/hls)：其摘要记录Vitis2026.1、10ns目标下五shape各一组小幅输入及一个非法shape的CSim/综合/Cosim PASS；原始报告未上传，本分支IP发布、平台、运行端适配和板测NOT RUN。

[本机契约证据](../results/architecture_contract_20261011.json)保留原始日期、固定Git/OPT45源SHA、独立INT64测试源码及两份输出日志。两者各13有效/6拒绝、1023248整数输出一致；覆盖五shape的INT8边界、全范围公开随机值、qkv零值及同地址输入/权重复用，输入const/输出守卫PASS。

该检查使用MinGW8.1.0和既有Vitis ap_int.h，HLS pragma被忽略。它不验证AXI事务、时序、DDR安全或性能；本机HLS/RTL/实板比较NOT RUN。两份日志字节一致反映相同测试结果，不代表二进制或硬件身份相同。

已有OPT45源码资产和C++编译器时，从根复现这项普通软件契约：

```powershell
git fetch origin hls/opt40-axi128-burst
python -B docs/tools/check.py --architecture-contract --research-source build/assets/opt45-source/opt45-source --cxx <existing-cxx> --hls-include <existing-vitis-include>
```

## 资料与软件入口

```powershell
python -B docs/tools/check.py --node
python -B docs/tools/assets.py opt45-source --download --extract
python -B docs/tools/check.py --cpp --research-source build/assets/opt45-source/opt45-source --cxx <existing-cxx> --hls-include <existing-vitis-include>
python -B model/golden.py --check model/cases/tensors.npz
```

资产归档与成员SHA校验、组合32成员与55文件A交付核对是文件完整性检查。普通C++独立INT64 TB忽略HLS pragma，不能称RTL cosim；小head为1000输出，UI使用17 Node/6 Python软件用例。当前资料校验状态由PACKAGE_MANIFEST与相应命令输出固定。

资料由PACKAGE_MANIFEST成员清单及链接/导入检查核对；默认两核与OPT45普通C++各8有效+8拒绝、418064完整INT64输出，17 Node/6 Python及1000输出公开head具备软件结果。已发布PS组合32成员、239节点图和完整机制样本按源manifest与原始结果核验；这不产生新的HLS/ARM/PL资格。

## 指标与限制

HLS estimate7ns、loop II1、top Interval和RTL总周期与板端完整FP32边界、Runner.run分别报告。HWH时钟值124998749Hz与实板124999999Hz是各自报告字段，名义频率125MHz。不得将不同层级数值相加或归因到未测阶段。

p99/max不是所有参照都更优；200次不足以证明长稳。普通libm GELU与1/2线程存在数值FAIL，已排除。独立AR到首R、末W到B为NOT REPORTED，OPT45独立X/Y阶段及诊断墙钟扰动NOT MEASURED。高地址RTL通过不等于新的实板高地址覆盖。

身份与证据：[资产锁](locks/ASSETS.lock.json)、[SOURCE_MAP](SOURCE_MAP.json)、[性能资源表](../results/summary.csv)、[当前状态](progress.md)。
