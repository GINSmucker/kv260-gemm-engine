# GEMM PS/PL 接口

本文描述 OPT40 开发分支的 PS/PL 接口，绑定 HLS 源码提交 `79ac98b250a0d90e94ec94c1cd388682cc3f4140`。它替代 OPT38 的 32 位数据口约定；C 在集成前须用新 IP 的驱动头和 Vivado/XRT 实际连接再次核对。

## 1. 数学与数据格式

```text
Y[M x N] = X[M x K] * W[K x N]
```

- X、W：有符号 INT8，row-major。
- Y：有符号 INT32，row-major。
- 地址：64 位字节地址。X/W/Y 位于 PL 可访问的外存缓冲区。
- X、W：128 位 AXI 字，每字 16 个 INT8；第一个元素放在低 8 位，后续元素依次放在更高位。
- Y：128 位 AXI 字，每字 4 个 INT32；第一个元素放在低 32 位。
- 连续内存中的 X/W/Y 仍分别是 row-major 的 INT8/INT8/INT32 数组；主机无需按矩阵 tile 重排。
- M、K、N 必须匹配 `hls/include/model_gemm_config.hpp` 中登记的尺寸。当前合法尺寸均满足 `K % 16 == 0`、`N % 4 == 0`。

| M | K | N |
|---:|---:|---:|
| 1 | 192 | 1000 |
| 197 | 192 | 192 |
| 197 | 192 | 576 |
| 197 | 192 | 768 |
| 197 | 768 | 192 |

## 2. HLS 顶层接口

顶层函数为 `model_gemm_top`，控制协议为 `ap_ctrl_hs`。三个 128 位 AXI4 Master 数据口分别为 `m_axi_gmem0`（X 读）、`m_axi_gmem1`（W 读）、`m_axi_gmem2`（Y 写）；地址和 M/K/N 经 `s_axi_control` 配置。

| 参数 | 含义 |
|---|---|
| `X` | `const ap_uint<128>*`，X 缓冲区地址 |
| `W` | `const ap_uint<128>*`，W 缓冲区地址 |
| `Y` | `ap_uint<128>*`，Y 缓冲区地址 |
| `M` | 输出行数 M |
| `K` | 归约维度 K |
| `N` | 输出列数 N |
| 返回值 | 0：成功；1：不支持的尺寸；2：对齐条件不满足 |

## 3. AXI-Lite 寄存器

以下偏移来自 OPT40 的 Vitis 2026.1 综合报告；集成时仍以新生成 IP 的驱动头文件和 Vivado Address Editor 为准。

| 寄存器 | 偏移 |
|---|---:|
| CTRL / GIE / IER / ISR | `0x00 / 0x04 / 0x08 / 0x0c` |
| `ap_return`（状态码） | `0x10` |
| X low / high | `0x18 / 0x1c` |
| W low / high | `0x24 / 0x28` |
| Y low / high | `0x30 / 0x34` |
| M / K / N | `0x3c / 0x44 / 0x4c` |

`ap_ctrl_hs` 中 start 为 bit 0，done 为 bit 1，idle/ready 为 bit 2/3。runtime 不应手工假设旧 IP 的寄存器布局；重新导出 IP 后必须核对。

## 4. XRT 约定

板端软件负责：

1. 按实际形状至少分配 X=`M*K` 字节、W=`K*N` 字节、Y=`M*N*4` 字节的 XRT BO，并确认适合 128 位数据口的地址对齐与 AXI 连接；
2. 按上述 lane 顺序填充 X/W，并同步到设备；
3. 设置三个 64 位地址及 M/K/N；
4. 启动 kernel 并等待安全完成；
5. 将 Y 同步回主机并与 Golden Reference 比较。

OPT40 顶层指针类型、AXI 数据宽度和输出写回方式均与 OPT38 不同。当前 `board/runtime/` 不能视作已经适配 OPT40；C 必须用本分支重新生成的 IP 核对 BO、参数和回读结果。HLS 综合或协同仿真通过不等于板端集成完成。
