# GEMM PS/PL 接口

## 三阶段归档入口（2026-10-26）

原 OPT38 接口说明在下方保留。本分支的三个源码快照必须分别选用对应 ABI，不能混用旧 runtime：

| 快照 | 顶层参数 | 当前接口依据 |
| --- | --- | --- |
| v1 / 历史 tiled v0.2 | `gemm_top(X,W,Y,M,N,K)` | [原始源码与范围](../hls/versions/v1_dense_initial/SNAPSHOT_STATUS.md)；特别注意 M,N,K 顺序 |
| v2 / OPT46 | `model_gemm_top(X,W,Y,M,K,N)` | [配置和原始 top](../hls/versions/v2_dense_optimized/SNAPSHOT_STATUS.md)；与旧 H576 来源未完整配对 |
| v3 / OPT50 | `model_gemm_top(X,W_data,W_meta,Y,M,K,N,mode)` | [128bit split-format](../hls/versions/v3_sparse_opt50/docs/sparse24_axi_format_v0.1.md) 和对应平台 ABI |

v3 当前导出控制偏移（字节）：返回状态0x10；X=0x18、W_data=0x24、W_meta=0x30、Y=0x3c，
四个指针均64bit；M=0x48、K=0x50、N=0x58、mode=0x60。控制/中断寄存器仍为0x00/0x04/0x08/0x0c。
mode0按Dense表示读取同一组数学权重，mode1按2:4表示读取；旧六参数metadata不能用于此核。
必须以本次生成的驱动头、平台metadata和运行时验证一致为准，不能只照表写入任意驻留电路。
完整路由和身份见[版本索引](versions/README.md)。本次仅增加文档入口，不修改默认OPT38或任何板卡配置。

## 原 OPT38 候选接口（历史默认树）

本文描述当前 OPT38 HLS 候选的最小公共接口。若源码、Vivado 地址分配或 runtime 与本文不一致，应先停止集成并更新接口版本。

## 1. 数学与数据格式

```text
Y[M x N] = X[M x K] * W[K x N]
```

- X、W：有符号 INT8，row-major。
- Y：有符号 INT32，row-major。
- 地址：64 位字节地址。
- 当前 HLS 输入在 AXI 上按 32 位字打包，每个字包含 4 个 INT8 元素。
- M、K、N 必须匹配 `hls/include/model_gemm_config.hpp` 中登记的尺寸。

## 2. HLS 顶层接口

顶层函数为 `model_gemm_top`，控制协议为 `ap_ctrl_hs`。

| 参数 | 含义 |
|---|---|
| `x` | X 缓冲区地址 |
| `w` | W 缓冲区地址 |
| `y` | Y 缓冲区地址 |
| `m` | 输出行数 M |
| `k` | 归约维度 K |
| `n` | 输出列数 N |

## 3. AXI-Lite 寄存器

以下偏移来自当前交接版本，最终以新生成 IP 的驱动头文件和 Vivado Address Editor 为准。

| 寄存器 | 偏移 |
|---|---:|
| CTRL / GIE / IER / ISR | `0x00 / 0x04 / 0x08 / 0x0c` |
| ERROR | `0x10` |
| X low / high | `0x18 / 0x1c` |
| W low / high | `0x24 / 0x28` |
| Y low / high | `0x30 / 0x34` |
| M / K / N | `0x3c / 0x44 / 0x4c` |

`ap_ctrl_hs` 中 start 为 bit 0，done 为 bit 1，idle/ready 为 bit 2/3。runtime 不应手工假设旧 IP 的寄存器布局；重新导出 IP 后必须核对。

## 4. XRT 约定

板端软件负责：

1. 为 X、W、Y 分配 XRT BO；
2. 填充 X/W，并同步到设备；
3. 设置三个 64 位地址及 M/K/N；
4. 启动 kernel 并等待安全完成；
5. 将 Y 同步回主机并与 Golden Reference 比较。

当前仓库尚未取得最新 v5 runtime 源码，因此 `board/runtime/` 仅为占位，不能视作已完成的 ABI 实现。
