# F8 XRT 运行链

本轮范围：固定 `(M,K,N)=(8,8,8)`，signed INT8 × INT8 → INT32，行主序。
原作者发布 RTL、P1 kernel 和 `P1_NAIVE_CORRECT_V1` 保持原样；没有 D64 或 P2 优化。
本页记录开发验证，正式封存验收另行执行。

## 实现与调用

| 层 | 实现 | 责任 |
|---|---|---|
| C 四方法入口 | `board/f8_backend.py` | capabilities / prepare / run / release，固定 8×8，权重快照，输出独立拷贝 |
| chain 状态机 | `board/f8_host.py` | 请求验证、完整 64-bit 地址、Start / Done / Continue、有界等待、FAULT |
| Python adapter | `board/f8_xrt.py` | 验证 bundle 哈希、服务进程身份、硬件 tag 与 xclbin UUID；有界 Unix socket RPC |
| 原生所有者 | `board/native/f8_xrt_service.cpp` | 独占 `xrt::ip`、实际 `xrt::bo` 分配/地址/map/sync、缓存和 guard、异常资源保留 |
| 新配对平台 | `hardware/kv260_f8/create_system.tcl` | PS HPM0 128 bit → AXI-Lite 32 bit；三个 GEMM master → SmartConnect → PS HP0 128 bit DDR；100 MHz |
| 状态观测 | `hardware/kv260_f8/f8_status.v` | 错误响应、未完成事务、读写计数、硬件指纹；不修改 GEMM 数据通路 |

XRT 元数据把两个 CU 声明为 **user-managed / AP_CTRL_NONE**，禁止另用 `xrt::run` 调度。
GEMM 的实际 RTL 仍是 **ap_ctrl_chain**，由本 host 独占操作。
`kv260_f8_metadata.xclbin` 只登记 IP/内存/连接信息，没有 BITSTREAM、OVERLAY 或 PDI。
Linux FPGA image 和 DTBO 由独立加载流程处理，不能让 XRT 再次编程 PL。

显式调用方式（在板端配对 bundle 目录，原生服务已就绪时）：

```python
from board.backend import TaskSpec, select_backend
backend = select_backend("fpga_f8", manifest_path="bundle_manifest.json", socket_path="f8.sock")
handle = backend.prepare(TaskSpec(8, 8, 8), W_int8)
result = backend.run(handle, X_int8)
backend.release(handle)
backend.close()
```

`fpga_f8` 缺少 bundle 或服务 socket 会明确拒绝，不降级到 CPU。
`release` 释放 prepare 权重 handle，重复调用安全；`close` 断开客户端。BO 继续由原生服务持有，加载器确认硬件静止后停止该服务才回收 BO。
原 `cpu_reference` 入口保留；其结果不能作为 PL 证据。
CLI 支持 `--backend fpga_f8 --f8-manifest ... --f8-socket ...`；case 必须满足 F8 尺寸和原 C1 文件规范。

## 内存与控制边界

- 三个实际 BO 各 4096 B；有效 X/W/Y 分别为 64/64/256 B，使用 allocation 起点、offset=0、64 B 对齐。
- 地址来自 `xrt::bo::address()`；运行时拒绝重叠，且限制在板上实际低 2 GB / 高 2 GB DDR 范围。
- ZOCL 对本平台的 PS RAM topology 使用 CMA 分配；bank 0 是分配索引，实际物理地址仍单独核验。HP0 的完整解码窗口大于板上实际 DDR 容量，不能据此接受额外地址。
- 输入 TO_DEVICE；Y 有效区预填毒值、尾部 guard 并 TO_DEVICE，然后才 Start。Done 后 FROM_DEVICE，检查输入未被写坏和尾部 guard，再返回 Y。
- BufferInfo 的读写属性描述本接口角色；它不代表 IOMMU 强制隔离。当前只有尾部 guard，没有 allocation 前部 guard。
- 输出交给校验器后仍是独立数组；后续运行不能覆盖旧结果。
- 正常收尾必须 Continue 并看到 Done 清零、Idle=1、Start=0；完成前不允许再次写地址或启动。

## FAULT 与恢复

Python 超时/退出后，独立原生进程继续持有设备、CU 和 BO；关闭客户端 socket 不等于释放 DMA 内存。
整个 RPC 使用总时限，连接超时后丢弃该连接，防止下一请求误消费旧响应。
`F8Session.recover()` 只在 FAULT 调用；真实 adapter 向同一原生所有者请求 RECOVER。

恢复证明必须同时包含：auto_restart=0、三个未完成事务计数为 0、AXI 总线安静，以及 CU 已停止。
若 Done 仍在，先 Continue 再确认 idle；然后清状态统计并允许新任务。
该路径覆盖已完成并排空的超时任务；永久 AXI 卡死时保持隔离状态，**未实现硬件 reset/isolation 恢复**。
不能对尚未证明静止的所有者使用 SIGKILL、卸载 overlay 或重新加载 bitstream。
进程被外部强杀、内核崩溃、断电的资源保证不在本实现范围内。

## 状态寄存器

GEMM base=`0xA0000000`，保留发布寄存器表；status base=`0xA0010000`。

| status offset | 含义 |
|---|---|
| 0x00 / 0x04 | magic `0x46383031` / ABI `0x00010000` |
| 0x08 / 0x0C | 构建 tag0 / tag1，由硬件源码指纹生成 |
| 0x10 | 错误锁存：X RRESP、W RRESP、Y BRESP、计数异常、非法 clear |
| 0x14 / 0x18 / 0x1C | X / W / Y 未完成事务 |
| 0x20 / 0x24 | X AR 请求 / R 数据拍 |
| 0x28 / 0x2C | W AR 请求 / R 数据拍 |
| 0x30 / 0x34 / 0x38 | Y AW 请求 / W 数据拍 / B 响应 |
| 0x3C | 即时 bus quiet；必须与 CU idle 和独占控制结合使用 |
| 0x40 | 写 `0x434C5245` 清统计，仅总线安静时允许 |

原发布 component.xml 的三个 HAS_BURST=0 与实际 RTL 冲突；只在新构建副本改为 1。
添加标量观测线会覆盖 Vivado interface 成员，必须同时保留 GEMM、SmartConnect 和观测端三个连接。
新脚本检查每条观测线恰有三个端点/一个输出驱动，综合遇到悬空引脚直接失败。

PS 与 PL 的接口位宽也必须匹配运行中的启动配置。首次上板的 HPM0=32 bit 与板端 `FPD_SLCR.afi_fs=0xa00`（HPM0=128 bit）不符，状态 ABI 校验失败且在提交前回退。平台已改为 HPM0=128 bit，加载前以只读映射核验 HPM0/HP0 均为 128 bit；没有写 PS 配置或启动固件。字段依据见 [AMD UG1087 afi_fs](https://docs.amd.com/r/en-US/ug1087-zynq-ultrascale-registers/afi_fs-FPD_SLCR-Register)。

## 构建、封装与证据

仓库自带精确配对包、原始 IP、板卡数据与软件测试；完整步骤见 [F8_REPRODUCE.md](F8_REPRODUCE.md)，本次变更与验收边界见 [交付报告](F8_DELIVERY_20260928.md)。

本机：`scripts/run_f8_system.ps1 -Build`；通过后使用 `scripts/package_f8_runtime.ps1 -SystemRunName <实际成功运行名>`。
封装执行 Bootgen，并逐字节验证 Linux BIN 等于 Vivado BIN 的完整 32-bit word 内反序；输出配对 bit/bin/hwh/xsa、DTBO、metadata、ARM binary、Python 源码及 SHA-256 清单。
原生服务已用板端 `g++-13`、XRT 2.18 和项目内 UUID 头文件编译，直接链接 `libxrt_coreutil` 与 `libuuid.so.1`。
包内的 `board_execution` 与 capability `validation` 为封装时快照；后续实板结果另写带运行 ID 的 `board_load_*.jsonl`、`native_owner_*.log` 和 `board_smoke_*.jsonl`，不回写已核验文件。相同配对包可再次执行，每次分别保存日志；正式验收状态仍单独记录。
入口和证据：

- [最终 XRT 原生源码/程序哈希与 metadata 检查](../results/c_integration/f8_runtime_research/native_compile_startup_diagnostic_hashes.txt)
- [metadata 生成及纯文件解析](../results/c_integration/f8_runtime_research/metadata_attempt1.txt)
- [20 项 host/runtime 开发测试](../results/c_integration/f8_runtime_research/host_runtime_tests_final.txt)
- [C1 7 项回归](../results/c_integration/f8_runtime_research/c1_regression.txt)
- [板端只读 HP0/clock 核验](../results/c_integration/f8_runtime_research/privileged_preflight.json)
- [DTBO 离线合并](../results/c_integration/f8_runtime_research/dtbo_offline_validation.txt)

板端开发检查入口 `board/f8_smoke.py`，使用公开零值、单位矩阵、极值和符号用例；9 次逐元素对比、1 次客户端断开后的显式恢复。
黄金结果只在 checker 内计算；后端输出必须来自真实 BO。函数级时间包含 Python/RPC/同步，不报告为 kernel latency 或性能加速比。

最终成功构建 `f8_system_20260928_004840_982`：WNS=3.373 ns，WHS=0.010 ns，零未约束端点、15,572 条可布线网全部完成；DRC 0 Error / 5 Warning（DSP MREG 建议 4 条、SmartConnect 无负载 reset 网络提示 1 条）。
配对交付 [bundle_manifest.json](../results/c_integration/f8_bundle_20260928_012117_473/bundle_manifest.json) 已部署板端；Bootgen 全量字节序核验、XSA 顶层 bit/HWH 对应关系和 31 文件运行前后哈希均 PASS。
实板运行 `262414267686403`：**9 次 / 576 输出 PASS，提交后客户端断开恢复 PASS，starter 回退 PASS**。三个 BO 实际为 `0x846f0c000 / 0x845735000 / 0x846f11000`，高 32 bit 非零；同一 native PID=224571 恢复后继续计算。每任务 X R/W R/Y W 为 128/512/64 个 32-bit 数据拍，AXI 错误与未完成事务均为 0；PL0 framework rate=99,999,999 Hz。
实际范围与原始证据见[完整结果](../results/c_integration/f8_runtime_20260928_RESULT.md)和[机器可读汇总](../results/c_integration/f8_bundle_20260928_012117_473/board_validation_summary.json)。独立封存验收、永久 AXI 卡死恢复和性能 benchmark 仍 NOT RUN；内核 overlay 引用计数告警保留，长期重复切换未验证。
临时上板入口为 [run_f8_board_validation.py](../board/tools/run_f8_board_validation.py)，具体步骤及固定哈希见 [加载与回退方案](F8_BOARD_CHANGE_PLAN.md)。
