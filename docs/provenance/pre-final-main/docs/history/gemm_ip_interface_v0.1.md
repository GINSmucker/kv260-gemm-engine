# GEMM IP 接口说明 v0.1

## 1. 功能定义

顶层函数：`gemm_top`

计算：

\[
Y = XW
\]

其中：

- `X`：8×8，INT8，row-major
- `W`：8×8，INT8，row-major，不预转置
- `Y`：8×8，INT32，row-major
- 乘法结果：INT16
- 累加与输出：INT32

元素地址关系：

```text
X[i][k] = X[i * 8 + k]
W[k][j] = W[k * 8 + j]
Y[i][j] = Y[i * 8 + j]
```

计算公式：

\[
Y[i][j] = \sum_{k=0}^{7} X[i][k] \times W[k][j]
\]

## 2. 缓冲区要求

| 缓冲区 | 元素类型 | 元素数 | 容量 |
|---|---:|---:|---:|
| X | INT8 | 64 | 64 B |
| W | INT8 | 64 | 64 B |
| Y | INT32 | 64 | 256 B |

X、W、Y 均位于 PL 可访问的外部存储器中。软件端负责分配缓冲区、填写 X/W、传入物理地址并读取 Y；若系统不具备硬件缓存一致性，启动前需刷新 X/W，完成后需失效 Y 的 CPU Cache。

## 3. 硬件接口

| 接口 | 协议 | 方向 | 数据宽度 | 用途 |
|---|---|---|---:|---|
| `m_axi_gmem0` | AXI4 Master | Read | 8 bit | 读取 X |
| `m_axi_gmem1` | AXI4 Master | Read | 8 bit | 读取 W |
| `m_axi_gmem2` | AXI4 Master | Write | 32 bit | 写回 Y |
| `s_axi_control` | AXI4-Lite Slave | R/W | 32 bit | 控制及缓冲区地址 |
| `ap_clk` | Clock | Input | — | 目标 100 MHz |
| `ap_rst_n` | Reset | Input | — | 同步、低有效 |
| `interrupt` | Interrupt | Output | 1 bit | 完成中断 |

顶层控制协议为 `ap_ctrl_chain`。推荐使用 HLS 导出的驱动和寄存器定义，不在软件中重复维护一套手写地址表。

## 4. AXI-Lite 寄存器

| 偏移 | 名称 | 说明 |
|---:|---|---|
| `0x00` | CTRL | 启动、完成、空闲、就绪、继续和自动重启 |
| `0x04` | GIER | 全局中断使能 |
| `0x08` | IP_IER | IP 中断使能 |
| `0x0C` | IP_ISR | IP 中断状态 |
| `0x10` | X low | X 地址低 32 位 |
| `0x14` | X high | X 地址高 32 位 |
| `0x1C` | W low | W 地址低 32 位 |
| `0x20` | W high | W 地址高 32 位 |
| `0x28` | Y low | Y 地址低 32 位 |
| `0x2C` | Y high | Y 地址高 32 位 |

CTRL 关键位：

```text
bit 0  AP_START
bit 1  AP_DONE
bit 2  AP_IDLE
bit 3  AP_READY
bit 4  AP_CONTINUE
bit 7  AUTO_RESTART
bit 9  INTERRUPT
```

## 5. 软件调用流程

```text
分配 X/W/Y 缓冲区
→ 按 row-major 写入 X 和 W
→ 处理输入 Cache 一致性
→ 设置 X/W/Y 的 64 位物理地址
→ 启动 IP
→ 轮询完成位或等待中断
→ 处理输出 Cache 一致性
→ 读取并验证 INT32 输出 Y
```

## 6. v0.1 限制

- 矩阵尺寸固定为 8×8，不支持运行时 M/N/K。
- 不支持 Bias、激活、重定标、截断或 INT8 输出。
- 不支持尾块、2:4 稀疏和 W4A8。
- 当前直接访问外部存储器，尚未加入片上缓存、Tiling 或双缓冲。

交付 IP：`gemm_top.zip`
