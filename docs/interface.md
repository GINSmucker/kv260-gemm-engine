# v1 GEMM 接口

唯一活跃声明是 `hls/src/gemm.h`，实现是 `hls/src/gemm.cpp`。来源为历史 Dense v0.2，不是 OPT38 的固定五尺寸接口。

## 数学和布局

`Y[M,N] = X[M,K] × W[K,N]`。X/W 为 signed INT8；完整 K 归约与 Y 为 signed INT32。矩阵为紧凑 row-major，W 不预转置。核不读取旧 Y，不含 bias、量化 scale、激活或累加旧输出。

运行时 M、N、K 各在 1～1024，尾块通过局部零填充处理。端口声明深度各为 1024×1024 个相应类型的元素，不是字节或128-bit字数量。

## 顶层与控制

```cpp
void gemm_top(const gemm_input_t* X,
              const gemm_weight_t* W,
              gemm_output_t* Y,
              int M, int N, int K);
```

**实参顺序 M、N、K**，不能改成后续版本的 M、K、N。X/W/Y 分别通过 gmem0/gmem1/gmem2 的 `m_axi` 访问，参数和 return 使用 `s_axilite` control。

非法维度在任何矩阵访存前直接返回，函数类型是 `void`；没有数值错误码。调用端必须先验证尺寸，不能假设可读 `ap_return/ERROR=0x10`，也不能把旧 Y 当作本次有效输出。

## 尚未确认的导出 ABI

本次没有该版本新的 IP 驱动头、component XML 或导出 RTL，因此不登记寄存器偏移、地址位宽、有效 AXI 位宽或板端控制协议细节为已验证事实。历史摘要声称 X/W 8-bit、Y 32-bit；当前源使用元素指针，但这些都不能代替实际导出接口复核。

不得沿用 OPT38 的 `0x3c/0x44/0x4c` M/K/N 表，也不得套用 OPT50 的四指针加 mode 协议。独立导出并核验后才可编写匹配驱动；当前 PS/PL 集成为 **NOT RUN**。
