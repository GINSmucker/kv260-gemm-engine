# OPT50 PS/PL 共同接口

活动接口来源：[HLS top](../hls/src/model_gemm_top.cpp)、[容量配置](../hls/include/model_gemm_config.hpp)、[metadata XML](../board/deployment_sources/metadata_sources/embedded_metadata.xml)。本页替代活动路径中的旧 OPT38 说明；旧文档在 [provenance](provenance/pre_v3_default/docs/interface.md) 原样保留。

## 数值与模式

`Y_int32[M,N] = X_int8[M,K] × W_int8[K,N]`，每次从零累加。量化、反量化、bias、scale、剪枝和模型后处理在 PS；不能把 Dense/Sparse 选择当作在线训练。

| mode | W_data | W_meta |
|---:|---|---|
| 0 | 完整 Dense `W[K,N]`，INT8 row-major | 不读取 |
| 1 | 2:4 `values[K/4,N,2]`，INT8 | 每 K 方向四元组的两个位置压缩码 |

X/W/Y 均紧密 row-major，无逐行/tile padding。物理格式为 [H576_SPARSE24_SPLIT128_V1](../model/docs/sparse24_axi_format_v0.1.md)，独立缓冲按128bit字组织，小端 lane0 位于最低字节；INT8 需按有符号值扩展。格式支持范围不等于当前硬件能力。

当前核要求 M∈[1,197]；K/N 为16的正倍数、各不超过576；K×N≤110592。M 尾块支持，Sparse K 不支持尾组，合法调用不存在 INT32 溢出。未知 mode、无效形状或 metadata 必须拒绝。

## 顶层与寄存器

唯一顶层 `model_gemm_top(X,W_data,W_meta,Y,M,K,N,mode)`，返回32bit状态：0成功、1形状无效、2模式无效、3metadata无效。四个指针均64bit字节地址；X/W_data/W_meta/Y 的 AXI 数据位宽128bit，W_data/W_meta 共用 gmem1；不生成两个独立 MAC IP。

| 寄存器/参数 | 字节偏移 |
|---|---:|
| CTRL / GIE / IER / ISR | 0x00 / 0x04 / 0x08 / 0x0c |
| 返回状态 | 0x10 |
| X low/high | 0x18 / 0x1c |
| W_data low/high | 0x24 / 0x28 |
| W_meta low/high | 0x30 / 0x34 |
| Y low/high | 0x3c / 0x40 |
| M / K / N / mode | 0x48 / 0x50 / 0x58 / 0x60 |

控制协议 `ap_ctrl_hs`：START bit0，DONE bit1，IDLE bit2，READY bit3。旧六参数 Dense metadata/运行时不能用于本核。必须验证实际导出头、平台地址与驻留身份后才可访问寄存器，不能仅据此表操作任意设备。

## 平台与执行

已保存 fd96539d 平台使用 GEMM/F8 基址 `0xA0000000/0xA0010000`、目标125MHz、128bit HPM0/HP0 和同域复位。文件来源见 [board/evidence](../board/evidence/system_verified_fd96539d.json)；这不是对当前设备的新观察。

完整模型固定49个Linear：qkv/fc1/fc2共36层PL，proj/head共13层PS。十二个选中fc层可按Sparse运行。公平对照两组使用相同剪枝数学权重、同一owner/plugin：36D/0S 对 24D/12S。

PS负责静态权重初始化、必要client X/Y缓存同步、严格有序IPC/START、完成/idle/drain确认和正常关闭。图内保持低开销调度，完整BO/保护区/静态权重核查在计时后INFO。各时间区间可能重叠，不相加。软件窗口保留固定boot/guardian/pins门禁；[C入口](../board/README.md)说明当前不可移植部分。
