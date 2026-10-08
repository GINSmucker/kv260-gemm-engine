# 完整层 FPGA GEMM

计算为`Y[M,N]=X[M,K]×W[K,N]`。M是输入行数，K是完整归约长度，N是输出列数。X/W外部是紧凑row-major signed INT8字节，Y是INT32。一次ap_ctrl_hs start完成一整个Linear；M/N调度、完整K和尾块在PL完成。

|family|M|K|N|DeiT每图次数|
|---|---:|---:|---:|---:|
|qkv|197|192|576|12|
|proj|197|192|192|12|
|fc1|197|192|768|12|
|fc2|197|768|192|12|
|head|1|192|1000|1|

共49次完整层、1045949952 MAC。只接受这五shape；错误shape在数据访问前拒绝。host不能把它拆成每tile start或CPU补K。

OPT35a采用TM8/TN64/TK32和8×8=64MAC微核。W每start整层加载并按bank供数；X在K tile加载，跨N tile会重读；acc分64bank、八组清零；输出有效行段连续burst。源码入口：[top](../hardware/model_gemm/candidates/opt35a_clear_v1_full/src/model_gemm_top.cpp)、[core](../hardware/model_gemm/candidates/opt35a_clear_v1_full/src/model_gemm_core.hpp)、[公开TB](../hardware/model_gemm/candidates/opt35a_clear_v1_full/tb/testbench.cpp)。

OPT38采用128MAC、W16bank、完整K的X行条带驻留，在N循环之外只加载一次当前条带，acc用LUTRAM四周期清零。紧凑head N1000的奇数K行需旋转两组8列半组；无效lane内部读地址钳位并置零。TK32只剩兼容常量，计算为完整K归约。源码：[OPT38 core](../hardware/model_gemm/candidates/opt38_fullk_v1_full/src/model_gemm_core.hpp)。

两者都每次start重装当前W，没有跨层或跨图PL权重缓存。C任务级W快照只可能省PS文件工作，不能称省掉PL当前每start的DDR读取。

接口三路master数据32bit、地址64bit，packed32每拍承载四个INT8；MMIO始终为字节地址。Y每拍INT32，不能把输入字数当字节数。逻辑最大容量X151296字节、W192000字节、Y151296个INT32；host另有guard/alignment策略。

默认契约与寄存器见[capabilities](../hardware/model_gemm/contracts/capabilities.json)及[register map](../hardware/model_gemm/contracts/register_map.md)。OPT38的重建使用IP资产中自己的契约与manifest；其最终监测工具在[monitor v2](../hardware/model_gemm/candidates/opt38_fullk_v1_full_monitor_v2/verify_axi_monitor.py)。

现有OPT35a平台WNS1.836ns/WHS0.010ns，OPT38平台WNS2.473ns/WHS0.010ns。HLS时钟估计9ns、循环II、top Interval、RTL延迟、host wait与实板kernel cycles分别表达；当前模型实板cycles不可用。OPT38旧整网比较仍无效，不能从128MAC、RTL代理202.499640ms或旧wait197.777ms宣称模型加速。

10月9日已有新的同软件AB1验证：同v5的35a→38→35a二十图core887.999602→438.384590→888.105542ms，比较有效，下降50.632344%，当前开发硬件采用38；原旧V2A比较保持其INCONCLUSIVE。这不是新的HLS/器件实现，复用原38配对包与独立A2；正式PL/CPU加速、质量/稳定性/发布仍未签。见[开发锁](../research/DEVELOPMENT_LOCK.json)。

P1 Naive冻结和历史F8不在本子项目中重新实现或改写；原作者v0.2通用尺寸接口也不自动兼容此五shape ABI。
