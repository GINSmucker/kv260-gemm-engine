# Current Stage

当前阶段：固定 F8 的 PS/XRT/DDR/PL 最小闭环开发验证完成。
当前子阶段：整理修正后的源码、精确配对产物、失败记录与成功证据，提交仓库供协作复核。

实验日期：2026-09-28，Asia/Shanghai。
本次仓库整理没有重新运行板卡、Vivado 或 ARM 编译。
下述板测 PASS 均指同一个已经完成的实际运行。

最近完成：

- 首次上板暴露 HPM0 位宽不匹配，修复平台并完整重建。
- 真正分配三个高地址 DDR BO，运行固定 8×8 INT8 GEMM。
- 9 次比较、576 个 INT32 输出一致。
- 提交后客户端 socket 断开、同一 native 所有者恢复后再次计算通过。
- 原生服务安全退出、F8 卸载、starter 恢复完成。

正在做：

- 按源码、配对包、原始结果和可复现命令整理仓库交付。
- 离线核对清单与 Git 交付字节；现有 F8 20 项与 C1 7 项软件测试已复跑通过。

下一步：

- 由独立验收人员使用冻结候选进行正式验收。
- 在另行确定范围后定义性能 benchmark。

当前不进入 D64、P2、INT4、稀疏或流水线优化。
文档更新与仓库提交不等同于新阶段授权。

# Current Implementation

计算定义：`Y = XW`。
`X[M,K]`、`W[K,N]`、`Y[M,N]`，当前 M=K=N=8。
X/W 为 signed INT8，Y 为 signed INT32，行主序。
每个输出从零累加；无 scale、zero-point、saturation 或 requantization。

计算 IP 来自原 `v0.1.0` Release。
原计算源文件、testbench 和 HLS 配置保持原样。
没有新增 PIPELINE、UNROLL、ARRAY_PARTITION、DATAFLOW 或 tiling。
局部修正的是隔离构建副本的三个 HAS_BURST 元数据值。

当前平台：

- 器件 `xck26-sfvc784-2LV-c`。
- 官方 KV260 / K26C board files 1.1。
- Vivado 2020.2.2，时钟约束 10 ns / 100 MHz。
- PS HPM0 128 bit 经 SmartConnect 访问两个 32-bit 控制从设备。
- GEMM control base=`0xA0000000`，status base=`0xA0010000`。
- 三个 32-bit GEMM 数据 master 经互连访问 PS HP0 128 bit DDR。

软件链：四方法后端 → chain 状态机 → Unix socket → ARM XRT 所有者 → BO/PL。
元数据声明两个 user-managed CU；硬件 GEMM 仍使用 ap_ctrl_chain。
host 显式 Start、Done、Continue；不能混用 xrt::run。
metadata xclbin 不携带 bitstream/overlay/PDI。

三个实际 BO 各分配 4096 B，有效数据为 64/64/256 B。
完整 64-bit 地址来自 XRT，输入与输出分别执行规定的缓存同步。
X/W 不可被写坏，Y 先填毒值，三个 BO 都检查尾部 guard。
prepare 保存权重快照，run 返回独立数组，旧结果不能被后续任务覆盖。

native 所有者独立持有 CU/BO；客户端断开不析构 DMA 资源。
FAULT 后需要明确 RECOVER，并证明 CU 静止与 AXI 排空。
永久 AXI 卡死时没有硬件 reset/isolation 恢复，资源保持隔离。
无 CPU fallback。

# Latest Results

成功构建：`f8_system_20260928_004840_982`。
成功配对包：`f8_bundle_20260928_012117_473`。
成功板测：`262414267686403`。
板卡墙上时钟不正确；日志使用 monotonic，实验日期使用主机时间。

实际结果：

- 零值、单位矩阵、INT8 极值、非对称符号矩阵各两次 PASS。
- 恢复后一次计算 PASS，合计 9 次、576 个逐元素比较。
- 另 1 个提交任务用于断开恢复；不计入 9 次输出比较。
- 原生 PID=224571，uid=1000，临时 render 组。
- X=`0x846f0c000`、W=`0x845735000`、Y=`0x846f11000`。
- 每任务 AXI errors=0、outstanding=0、CTRL=4、quiet=1。
- X AR/R=64/128，W AR/R=512/512，Y AW/W/B=4/64/4。
- 31 个配对文件运行前后 SHA-256 均匹配。

断开恢复覆盖提交后未消费结果的情形。
没有证明 socket 断开时 DMA 仍然活动；永久阻塞和强杀未测试。
最后 SAFE_STOP、native exit=0，设备使用者清空后回退成功。
回退状态为 starter、FPGA operating、ZOCL 未加载、无 render 节点、XRT 0 devices。

本机及软件证据：

- F8 host/runtime 20 项开发测试 PASS。
- C1 CPU 接口 7 项回归 PASS。
- 仓库整理复跑 C1 首次 6 PASS / 1 临时目录 PermissionError；解除沙箱限制后原测试 7/7 PASS，失败日志保留。
- AXI status RTL 114 项检查 PASS。
- WNS=3.373 ns，WHS=0.010 ns；TNS/THS=0。
- 15572 条可布线网全部完成，路由错误 0。
- DRC 0 Error / 5 Warning。
- 全平台 LUT=7510、FF=11496、BRAM tile=1、DSP=4。

100 MHz 是时序约束；Linux framework 报告 PL0=99,999,999 Hz。
它不是外部仪器测频，也不是 Fmax。
board_call_us=17012.470～17858.899，包含 Python/RPC/同步。
kernel latency、吞吐率、加速比：NOT RUN。
本次新的 HLS top Interval、loop II、HLS 时钟估计：NOT REPORTED。

首包本机构建通过，但实板原生 ABI 检查失败且未提交 GEMM。
原因是第一版 HPM0=32 bit，而板端当前启动配置是 128 bit。
首次失败和最终成功分别保留，不互相覆盖结果。

独立封存验收：NOT RUN。
内核 overlay 引用计数/泄漏提示保留；长期反复切换：NOT RUN。

# Concepts Learner Should Understand Now

1. **平台配对**：bitstream、PS 启动配置、DTBO、metadata、寄存器 ABI 和软件必须一致。
   能生成 bitstream 并通过时序，不代表运行中 PS 接口位宽已经匹配。

2. **设备内存所有权**：CPU 数组、虚拟地址、实际 BO 设备地址分别承担不同角色。
   BO 释放之前必须证明 DMA 停止；socket 断开不构成该证明。

3. **chain 握手**：Done 后还要 Continue，确认 Idle 与无未完成事务才能安全复用。
   user-managed 元数据意味着由 host 驱动寄存器，并未把硬件协议改为无控制。

4. **证据层级**：软件模型、RTL 仿真、本机构建、实际 BO/PL 输出是不同层级。
   9 次实板结果不能替代独立封存验收、物理故障注入或长期稳定性结论。

5. **性能口径**：调用时间包含传输与 RPC，全平台资源包含互连和观测逻辑。
   不能把调用耗时称作 kernel latency，也不能把时序裕量直接当作加速比。

# Questions Raised By Current Results

- 为什么 HPM0 位宽不符时，PL 能加载却读不对 magic/ABI？
- 高 DDR 地址的高 32 bit 非零，验证了地址链条中的哪些步骤？
- 输入同步、输出 poison、guard 与旧结果拷贝分别发现什么类型的错误？
- Done、Idle、outstanding=0 与 quiet=1 为什么需要同时考虑？
- 已完成任务恢复通过后，永久 AXI 阻塞仍缺少什么硬件机制？
- 为什么 starter 回退成功，仍不能认定长期 overlay 切换稳定？
- 哪些独立时间戳/计数器才足以把 kernel 与主机调用开销分开？

# Next Learning Boundary

先能从原始 JSONL 复核 9 次比较、BO 地址、同一 native PID 与回退链。
再核对构建清单、源码 SHA-256 与实际读回的两段 tag。
用首次失败与最终通过的差异理解 PS/PL 配对。

下一验收工作需要独立候选冻结和验收执行。
不得把本开发会话的公开 smoke 用例重新命名为盲测。
仓库不包含隐藏验收种子或封存测试文件。

未来优化候选尚未实施：减少重复 DDR 读取、局部存储复用、流水线和并行度。
开始优化前要先确定新实验范围及性能测量方法。
当前保持固定 F8 和原始计算 RTL。

# Relevant Files

- [交付及修改报告](F8_DELIVERY_20260928.md)：改了什么、原因、实板结果与限制。
- [复现步骤](F8_REPRODUCE.md)：离线检查、软件测试、精确包、重建与封装。
- [运行接口](F8_RUNTIME.md)：四方法、chain、资源生命周期与 FAULT。
- [加载与回退方案](F8_BOARD_CHANGE_PLAN.md)：具体设备变更与恢复条件。
- [实际结果](../results/c_integration/f8_runtime_20260928_RESULT.md)：全部阶段证据入口。
- [机器汇总](../results/c_integration/f8_bundle_20260928_012117_473/board_validation_summary.json)：本次实板数字及 raw log 哈希。
- [完整包清单](../results/c_integration/f8_bundle_20260928_012117_473/bundle_manifest.json)：封装时冻结的 31 文件。
- [平台与观测 RTL](../hardware/kv260_f8/README.md)：硬件文件入口。
- [native 所有者](../board/native/f8_xrt_service.cpp)：实际 XRT CU/BO、寄存器与缓存操作。
- [Python adapter](../board/f8_xrt.py)：传输、身份与恢复证明。
- [主机状态机](../board/f8_host.py)：请求和 chain 控制。
- [四方法后端](../board/f8_backend.py)：handle、权重与输出约束。
- [开发测试](../tests/)：20 项 F8 与 7 项 C1 软件检查。
- [公开板测 checker](../board/f8_smoke.py)：实际输出比较和断开恢复。
- [仓库完整性检查](../scripts/verify_f8_delivery.py)：离线复核入口。
- [原发布 IP](../results/external_ip_review_20260927/release/SOURCE.md)：ZIP、RTL 来源与哈希。
- [官方板卡数据](../board/hw/board_files/SOURCE.md)：K26/KV260 1.1 来源与许可证。
