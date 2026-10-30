# OPT50 PS / PL 共同接口

唯一硬件顶层为 `model_gemm_top(X,W_data,W_meta,Y,M,K,N,mode)`。计算 `Y_int32[M,N]=X_int8[M,K]×W_int8[K,N]`，每次从零累加。量化、bias、scale、剪枝／训练与后处理由 PS 软件完成。

| mode | W_data | W_meta |
|---:|---|---|
| 0 | 完整Dense INT8 W[K,N] | 不读取 |
| 1 | K方向每4个权重保留2个值 | 两个位置的压缩编码 |

物理格式为 [SPLIT128 v1](../model/docs/sparse24_axi_format_v0.1.md)，INT8有符号、小端128位字。X/W/Y紧密row-major，无逐行tile padding。硬件尺寸要求 M=1..197，K/N为16的正倍数且不超过576，K×N≤110592；Sparse K没有尾组。

| 寄存器／参数 | 偏移 |
|---|---:|
| CTRL / GIE / IER / ISR | 0x00 / 0x04 / 0x08 / 0x0c |
| 返回值 | 0x10 |
| X low/high | 0x18 / 0x1c |
| W_data low/high | 0x24 / 0x28 |
| W_meta low/high | 0x30 / 0x34 |
| Y low/high | 0x3c / 0x40 |
| M / K / N / mode | 0x48 / 0x50 / 0x58 / 0x60 |

四个指针为64位字节地址。W_data/W_meta共用gmem1；只有一个共享MAC IP。返回0成功、1形状错误、2模式错误、3metadata错误。控制协议ap_ctrl_hs：START bit0、DONE bit1、IDLE bit2、READY bit3。

受检fd96539d平台的GEMM/observer基址为0xA0000000/0xA0010000，目标125MHz。实际设备访问仍须核对导出头、驻留固件、时钟、复位和当前启动身份；本文不是寄存器操作授权。

Sparse24的PL线性层索引为2、3、6、7、10、11、14、15、18、19、22、23、26、27、30、31、34、35、38、39、42、43、46、47；其余12个PL QKV层为Dense，proj/head共13层保留PS。相同核的原始Dense模型对照执行36D/0S。

PS使用静态权重初始化、共享缓冲、client X/Y同步及严格有序START/完成/drain协议。全缓冲区、保护区和静态权重检查在计时后INFO完成。图内区间可能重叠，不能相加。
