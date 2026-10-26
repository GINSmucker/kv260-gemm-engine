# OPT50 共享 Dense / Sparse24 计算核设计说明

日期：2026-10-22。本文描述本次源码设计意图，硬件结果以本次 Vitis 综合和 RTL 验证为准。

## 1. 从旧核继承什么

只读分析来源是 `D:\GEMM\model_gemm_opt46` 的 top/core/config/testbench/config/report。其关键逻辑：

1. X、W、Y 使用 128 bit AXI；整层 W 装入片上 16 个 byte bank 后复用。
2. M 分块 32 行、N 微块 16 列；K 方向流水，32x16 个乘加位置展开，目标每周期 512 次实际 MAC。
3. X 的 32 行分 bank，同一行数据广播给 N 方向的乘法位置。
4. 使用 stream_of_blocks 让计算与 Y 写回成为独立 DATAFLOW 任务。
5. Y 以完整行连续写回，不把每个 N 微块分别写 DDR。

旧核不是简单“循环加 UNROLL”：持续供数、存储端口、结果排布决定阵列能否工作。本版保留以上思想，但不照抄旧 768 隐藏维白名单。旧 OPT46 报告顶层约 DSP=520、LUT=83648、FF=78038、BRAM18=210，估计周期 8.237 ns；这不是新核资源，也不是旧核已经满足 8 ns 的证据。

## 2. 为什么不是两套 Dense/Sparse 引擎

源码中只有一个 `model_gemm_top`，只有一处 `selected_x * weights[nn]` 乘法表达式，经 32 行、16 列展开。mode 是每次启动锁存的运行时值，不是模板参数，不调用 `dense_core()` 与 `sparse_core()` 两个计算函数，也不使用 FUNCTION_INSTANTIATE 进行模式专门化。

```text
                       Dense：step 对应的完整 W 值、step%4
W byte banks + mode ──选择──────────────────────────┐
                       Sparse：保留值、每列 position│
                                                   v
X 片上每行一个 128bit字 -> 当前四元组 -> 4选1 -> 同一 32x16 MAC -> INT32
                                                     |
                                           打包/缓冲 -> Y Burst writer
```

对一个 K 四元组，Dense 做 4 个 step；Sparse 只做 slot0、slot1 两个 step：

```text
Dense:  acc[m,n] += X[m,4g+s]       * W[4g+s,n]       (s=0..3)
Sparse: acc[m,n] += X[m,4g+p_s(g,n)]* values[g,n,s]   (s=0..1)
```

每列的位置独立，不能把一个 p 广播给全部 N。X 每行先读同一个四元组，再由每列自己的位置码做 4:1 字节选择，避免变成每列都向同一个 RAM 随机读。所有乘加位置仍共用同一套选择后的数据入口。

`ALLOCATION function instances=compute_and_pack limit=1` 限制该 helper 的实例数，但**不能代替综合后乘法器共享证明**。应在层级/运算器报告及 RTL 中确认 mode 控制的是 mux，而不是出现两个 mode 专用计算阵列；DSP 实际映射也不保证正好 512 个。[AMD ALLOCATION](https://docs.amd.com/r/en-US/ug1399-vitis-hls/Allocation)、[FUNCTION_INSTANTIATE](https://docs.amd.com/r/en-US/ug1399-vitis-hls/Function_instantiate)。

## 3. 严格遵守数据格式

格式 ID：`H576_SPARSE24_SPLIT128_V1`。`docs/sparse24_axi_format_v0.1.md` 是原约定的未改动快照；原文开头“尚未实现”等是创建时状态，不代表本工程当前测试状态。

- 每条记录 `t=g*N+n`；values 的字节偏移为 `2*t+s`。
- `code=p0|(p1<<2)`；偶数 t 在低 nibble；合法码为 4/8/C/9/D/E。
- values 是 INT8 补码；保留值为零仍保留其位置，不能由非零值推 mask。
- 128 bit 的低位对应低地址；Y 按 little-endian INT32 紧密排列。
- Sparse 不扩成完整 W，也不把 NPZ 文件或 UINT8 positions 原样上传 DDR。
- Dense 外部 W_meta 不读；计算内部读取已初始化的本地 metadata[0]，不是访问这个外部占位 BO。

硬件子集要求 K、N 均为 16 的倍数，并明确检查容量。这样不需要逐行 padding；每行 X/Y、values/meta 总长度都是整数拍。M=197 的最后 5 行由循环上界处理。K4/N2 的文档示例只做独立格式测试，DUT 对该尺寸返回不支持。

此限制是本版能力边界，不改变格式本身允许更一般尺寸的定义。未来若支持非整拍 N，必须重新实现跨行字节打包/尾拍；不能给每行补 16 字节再仍声称使用原格式。

## 4. 片上供数与资源控制

W_data 使用 16 个固定 byte bank，bank=byte_index%16。Dense 一步取 16 个列权重。Sparse 一步需要 16 列各自的 slot 值，使用每 bank 两个连续地址读取覆盖 32 个字节，再选择 slot，不复制整份 W RAM。metadata 另用一份 128 bit BRAM 缓存。

X 使用 32 个行 bank，各行一步读一个 128 bit 字。先选其中的 32 bit 四元组，再按列选 INT8。内层 MAC 累加器改为当前 32x16 的寄存器数组，而非为全部 N 微块建立可变地址的累加 RAM。

结果缓冲采用 16 行、最多 36 个 N 组的 512 bit block：

- stream_of_blocks 深度 2；producer/consumer 的锁也计入深度。
- 低 16 行写到一个被持有的 block；高 16 行暂存一份 pending_upper。
- 全部 N 微块完成后释放低半块；再单独取得写锁，复制高半块。任何时刻最多持有一个写锁。
- M 尾块有效行不超过 16 时，只发布/消费一个 block，否则两个。

这样避免 1152 深度的 32 行 block 跨越 1024 深度的明显 RAM 纵横比分界。两块 SOB 加一份暂存的逻辑容量合计 108 KiB；上半块复制最多 576 个周期。这是**显式面积/重叠折中**，不是声称 BRAM 必定少于旧核；物理 BRAM 取决于映射、端口和 RAM 几何尺寸。[AMD stream_of_blocks](https://docs.amd.com/r/en-US/ug1399-vitis-hls/Specifying-Arrays-as-Stream-of-Blocks)。

本版每次调用重新加载 W，尚不支持 runtime 跨层常驻权重。Sparse 少传有效载荷，但不会让按 Dense 上限配置的 W RAM 自动缩成一半。

## 5. 访存与 Burst 推断

围绕 AMD 的 Burst 前提组织源码：native unsigned 计数、连续单调地址、长度在发请求前确定、不在 DDR 操作周围加逐元素条件。相关依据：[AMD Burst 前提与限制](https://docs.amd.com/r/en-US/ug1399-vitis-hls/Preconditions-and-Limitations-of-Burst-Transfer)。

| 路径 | 组织方式 | 应检查的循环 |
|---|---|---|
| W_data | 按模式先确定拍数，独立连续加载 | `load_data_words` |
| W_meta | 仅 Sparse；与同 bundle 的 W_data 分阶段加载 | `load_metadata_words` |
| X | 每行先确定 base/row_words，再完整行连续读 | `load_x_words` |
| Y | 完整有效行连续写，无逐 lane DDR 条件 | `write_y_words` |

同一个 gmem1 上 W_data 与 W_meta 不交替逐组读。metadata 合法性检查在片上进行，并在 DATAFLOW 启动前完成；不因检查而打断 DDR 读循环，也不允许 producer 提前退出后留下 writer 等待。

AXI 最大 read burst=16、Y 最大 write burst=64 只是配置上限，不是成功推断长度。row=576 时 Y 一行 144 拍，不是要求一条超长 burst；工具还会按最大长度和 4 KiB 边界等拆分。Host 的 64 B 对齐也不是保证全部访问不跨 4 KiB。

真实层权重流量：Dense 110592 B；Sparse values 55296 B + metadata 13824 B = 69120 B，减少 37.5%。这不是整模型或总DDR流量减少比例。两个真实尺寸的理想 MAC step 数都从 48384 降至 24192（按 7 个 M tile 计），但还有加载、metadata检查、清零、打包、写回、同步及软件开销，不能保证延迟减半。

## 6. 错误、边界与不会检测的事情

- mode、容量、尺寸检查在外部读写前；非法 metadata 在加载后、计算前拒绝。
- 不支持的 K/N 不向上补齐；有效 M 行在循环外确定，无效行的 X 片上清零且不写 Y。
- INT8 乘积为 INT16 后符号扩展到 INT32；最大 K576 的最坏幅值界为 9437184，远小于 INT32 上限。
- TB 以每个 m_axi 的最大 depth 分配完整 C 缓冲区，再加前后 guard，避免 cosim wrapc 根据 depth 转储超过短分配。
- 返回错误、guard 和 C++ 比较不能证明没有非法 AXI 读。短 BO、错物理地址、不同 BO 重叠、cache sync 等由 Host 在启动前检查。
- 不使用 `DEPENDENCE inter false` 掩盖真实累加反馈；若 II 未达到 1，应分析真实 scheduling 报告。

## 7. 硬件验收顺序

1. Vitis CSim full、golden 通过；保存对应源码哈希。
2. 综合确认一个顶层和共享计算任务，查看实际运算器/资源；不能以“一份 IP zip”推断只有一套阵列。
3. 查看 shared_mac_steps 的实际 II、关键路径、BRAM/LUT/FF/DSP，确认不是只有 pragma 请求了 II=1。
4. Burst 报告对应上表四个循环；查看实际端口位宽、交易长度，保留本次报告。
5. RTL Cosim 覆盖运行时切换、真实形状、尾块和拒绝路径，确认 DATAFLOW 无死锁。
6. 加事务监测：Dense W_meta 地址范围零读，所有 AR/AW/W 均在各真实分配段内，4 KiB 拆分、Y 写入覆盖与边界正确。当前 TB guard 不能代替这一步。
7. 新寄存器定义、平台 metadata/xclbin 和 runtime 适配完成后才上板。完成检查、cache sync、异常退出不能删除。

本轮不改变模型剪枝方案、保留值、量化参数或 PS/PL 路由，不做额外输入双缓冲实验。
