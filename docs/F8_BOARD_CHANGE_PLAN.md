# F8 临时上板与恢复方案

状态：2026-09-28 用户明确授权后，本方案已执行完成。首次 ABI 校验失败后修正位宽，第二次实板 **9 次 / 576 输出、客户端断开恢复、starter 回退均 PASS**；完整结果见[本轮报告](../results/c_integration/f8_runtime_20260928_RESULT.md)，正式封存验收仍 NOT RUN。
首次实际执行已完成加载与回退：`board_load_260053649355199` 在原生服务 ABI 校验处失败，未提交 GEMM，回退 PASS。随后只读发现 `FPD_SLCR.afi_fs=0xa00`，HPM0 实际 128 bit，与第一版平台的 32 bit 不匹配；现已完成匹配 128 bit 的重建。重试沿用本方案与本次授权，使用新清单记录修正产物。

重试构建 `f8_system_20260928_004840_982` 已 PASS：WNS=3.373 ns / WHS=0.010 ns、DRC 0 Error / 5 Warning、15,572 条可布线网全部完成、无未约束内部端点。XSA 顶层 bit/HWH 逐字节对应，Bootgen 字节序核验 PASS。新包 [f8_bundle_20260928_012117_473/bundle_manifest.json](../results/c_integration/f8_bundle_20260928_012117_473/bundle_manifest.json) 已部署，31 文件哈希全匹配；清单 SHA-256=`bafb429c5262cea3d6c53b8eac9f86e9e37d18540839cbb050a638bf4e06734d`，硬件 tag=`0x19ff2553 / 0xbd1377d1`。新增只读 HPM0/HP0 位宽预检、启动寄存器日志、独立运行日志名；回退文件、设备树和系统变更范围不变。
这是开发检查计划；正式封存验收仍独立进行。执行代码为 [run_f8_board_validation.py](../board/tools/run_f8_board_validation.py)。

最终运行 ID=`262414267686403`，原生服务 uid=1000 / PID=224571；服务安全退出、设备使用者为空后完成回退。板卡现为 starter、FPGA operating、ZOCL 未加载、无 render 节点、DFX active；临时应用已移除，31 文件哈希不变。[实际汇总](../results/c_integration/f8_bundle_20260928_012117_473/board_validation_summary.json)。

最终重试包的 Linux BIN SHA-256=`5f0c63330a21bda7f7e9fa64e53fb5e8312ada9d77e46cba95e4a72ae1c56f99`；ARM 程序 SHA-256=`b1c0a5b31ec02a5351d082ab7d9f6f885e8cb8b56f75e74befe01ee6603b7c20`。DTBO 与 metadata 沿用下方相同哈希；完整最终文件清单以上方新包为准。

## 目标与当前状态

- 板卡：KV260 / commercial K26；SSH `kv260`，用户 `ubuntu`。
- 内核 `6.8.0-1015-xilinx`，ZOCL / XRT 2.18；DFX manager `2024.2.0-0ubuntu1~xlnx1`。
- 当前是 `k26-starter-kits_image_1`，FPGA operating，ZOCL 未加载，无 render 节点，DFX manager active。
- 2026-09-28 主机时间标记的只读预检：HP0 RDCTRL/WRCTRL 均为 `0x3b0`，低 2 bit=0，128 bit 与硬件设计一致；PL0 为 `99,999,999 Hz`。
- 板端墙上时钟不正确，日志另保留 monotonic；阶段日期使用本机 Asia/Shanghai。

## 具体变更

1. 对已通过综合/实现/时序/DRC 的 F8 bundle 验证所有 SHA-256，以及 starter 回退文件；检查板卡没有其他 FPGA/DRM/MMIO 使用者。
2. 在 `/lib/firmware/xilinx/kv260-f8` 新建一个临时应用目录，仅包含 `kv260-f8.bit.bin`、`kv260-f8.dtbo`、`shell.json`；已存在则拒绝覆盖。
3. 使用现有 `dfx-mgr-client -remove 0` 卸载 starter；加载已安装的 `zocl` 模块，再以 `-load kv260-f8` 编程 PL 并应用临时设备树。
4. DTBO 使用 Linux FPGA region 的四个标准 PL reset 引用；添加 `xlnx,fclk` 的 PL0 100 MHz 消费者和固定名称 `zyxclmm_drm` 节点。
5. 使用 `ubuntu` uid 和仅对子进程生效的 render supplementary group 启动 XRT 服务和 checker；默认用户组、udev 和系统 Python 不改变。
6. 服务验证硬件 magic/ABI/构建 tag 和元数据 UUID，独占 CU，分配三个真实 BO，完成公开开发测试。

本应用选择 **PL_FLAT**；XRT 元数据放在项目目录，由原生服务单独登记。
已核对 DFX manager 2024.2 源码：只有 XRT_FLAT 且应用目录有 xclbin 时才更新 `/etc/vart.conf`；本应用和 starter 回退目录均不符合该条件。
脚本只比较该配置的原内容/不存在状态，不写 `/etc`。不修改 `/boot`、QSPI 或启动固件，不重启。

## 开发检查与判定

- 零值、单位矩阵、INT8 极值、非对称符号矩阵各两次；真实 BO 回读逐元素对照独立整数 checker。
- 保存硬件 AXI 错误、未完成事务与数据拍统计，检查 X/W 未被写坏、三个 BO 尾部 guard 完整以及旧返回数组不被后续任务覆盖。
- 另提交一个真实任务后断开 Python socket；同一原生 PID 必须继续持有 BO。显式 RECOVER 在 Done/Continue、Idle、无未完成事务、bus quiet 后才允许新任务。
- 恢复后再做一次逐元素比较。合计 9 次比较 / 576 个输出，另 1 次断开任务。
- 本次实际分配并验证了三个高地址 DDR BO。永久 AXI 卡死、物理错误注入、硬件 reset/isolation 恢复、前部 guard、长期重复切换和性能 benchmark 不由这些开发用例自动覆盖。

## 回退与异常边界

正常结束时：checker 断开；对原生进程自身发送 SIGTERM；只有原生服务报告 `F8_XRT_SERVICE_SAFE_STOP` 并退出、无其他设备使用者，才卸载 F8。
随后卸载本次 ZOCL 模块，重新加载原 starter；逐项核对 overlay、FPGA operating、PL0 rate/enable count、模块/render 状态、DFX manager 与 XRT 枚举。
最后验证临时应用文件仍为本次哈希，逐文件删除并移除空目录。

如原生进程无法证明静止，脚本保留原生进程、BO、overlay 和临时应用，报告恢复未完成。
不强杀、不自动重新编程正在访问 DDR 的 master。后续恢复必须依据实际状态另行处理。
SSH 断开不会自动杀死原生所有者；加载脚本也会继续执行已经开始的正常恢复路径。

## 固定回退产物

目录 `/usr/lib/firmware/xilinx/k26-starter-kits`：

| 文件 | SHA-256 |
|---|---|
| k26_starter_kits.bit.bin | `f9dfe95d14881a2768b2c0e6a36fc363635e8ae7882ecad9725d270490395d3e` |
| k26_starter_kits.dtbo | `1e998ffd5d33a05da3746df3603e65603e1628006bc878209a836cc9bd3c4ae1` |
| shell.json | `2967ccbc1f75c8d4accd1b6fecc11b16a32a5d7992e56c6eebceb99a3410f777` |

## 首次失败尝试的配对产物（保留证据，重试用上方新包）

- 构建：`f8_system_20260928_000320_202`。
- 硬件源码指纹：`fda427e6ba30f04933e96f1edecf74c60c0c150c18b69fe4a44558acbce3aabc`。
- 构建 tag：`0xfda427e6 / 0xba30f049`。
- 配对清单：[f8_bundle_20260928_003336_214/bundle_manifest.json](../results/c_integration/f8_bundle_20260928_003336_214/bundle_manifest.json)。已部署到 `/home/ubuntu/fpga_project/deploy/f8_bundle_20260928_003336_214`，30 个文件 SHA-256 全部匹配。
- 布线后 WNS=5.306 ns、WHS=0.010 ns；零未约束端点、15,121 条可布线网全部完成、零路由错误。DRC 为 0 Error / 5 Warning：4 条原 IP DSP 未启用 MREG 的性能建议，1 条 SmartConnect 内部无负载 reset 网络提示；没有据此修改计算架构。
- Linux BIN：`4ce7557c7075e659370a6abf98c6fbcbd4c96ecc96c6024b13afcdd8421ea4b5`。
- DTBO：`5a85e77c14a8c99c5a0e3c020111db72ea41bc530a73619a4cc599da4e364228`。
- metadata xclbin：`c68d584e01e03fae0cc3b77ace626eab4435979bf908199181d0e89384294537`。
- 原生 ARM 程序：`0268b449f6b1c87c202127468774a2db06ea618a89bcf44787e50a25f74dc0cb`。
- 执行前授权依据：项目 `AGENTS.md` 要求临时设备树/PL 加载在具体变更、配对产物和回退方案就绪后取得维护者授权。

授权后执行（由本机 SSH 发起，在 KV260 sudo 会话中运行）：

```sh
python3 /home/ubuntu/fpga_project/deploy/f8_bundle_20260928_012117_473/board/tools/run_f8_board_validation.py /home/ubuntu/fpga_project/deploy/f8_bundle_20260928_012117_473 --authorized-temporary-load
```
