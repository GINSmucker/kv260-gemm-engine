# AXI128 打包与 Golden 工具使用说明

工具：`pack_sparse24_axi.py`。格式依据：同目录的 `sparse24_axi_format_v0.1.md`。

只依赖 Python 标准库和 NumPy，使用现有 `.venv`，无需重新安装环境。
这一步只验证数据格式和整数运算，不运行图像分类、不训练、不启动 HLS，也不访问板卡。
本工具补齐格式文档创建时尚未实现的 Python 部分；不改写已绑定哈希的格式文档及旧模型历史记录，HLS/RTL/板测仍为 `NOT_RUN`。

## 1. 三条常用命令

以下命令在 `h576_sparse24` 目录的 PowerShell 中执行。`-X utf8` 统一文字编码，`-B` 禁止写 `.pyc` 缓存。

### 内存自测：不生成数据文件

```powershell
.\.venv\Scripts\python.exe -X utf8 -B .\pack_sparse24_axi.py selftest
```

核对文档的固定字节示例、六种位置码，以及 30 组跨拍/尾拍尺寸下 Dense 与 Sparse 的计算结果。
更完整的异常输入、Host 描述参数和保护测试：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest -v test_pack_sparse24_axi
```

单元测试只使用合成数据和临时测试目录，不改真实模型。

### 导出：会新增文件、占用磁盘

```powershell
.\.venv\Scripts\python.exe -X utf8 -B .\pack_sparse24_axi.py export `
  --candidate .\candidates\h576_sparse24_mlp12_c062_v1 `
  --output-dir .\candidates\h576_sparse24_mlp12_c062_axi128_v1
```

`--candidate` 是只读输入，脚本会核对其固定身份和全部文件哈希。
`--output-dir` 必须是 `candidates/` 下的全新目录，不能与来源嵌套，存在时直接拒绝，不能覆盖。
重复导出请使用新的输出目录名，例如末尾改为 `_v2`，不要删除或覆盖旧模型来重跑。

成功时最后生成 `manifest.json`；中途失败保留新目录及 `failure.json` 供排查，不将其当成成功测试包。

### 磁盘复核：只读，不更新回执

```powershell
.\.venv\Scripts\python.exe -X utf8 -B .\pack_sparse24_axi.py verify `
  --package .\candidates\h576_sparse24_mlp12_c062_axi128_v1
```

重新核对文件字节数/哈希、逐层尺寸与来源、解码后的权重、二进制和 NPY golden，以及两种模式的输出。
会再次读取原 c062 NPZ 作为独立依据；来源搬家后，增加 `--source-candidate 新路径`。来源缺失时失败，不静默跳过来源对照。

## 2. 输出目录有什么

```text
h576_sparse24_mlp12_c062_axi128_v1/
├── README.md
├── manifest.json
├── verification.json
├── examples/tiny/
│   ├── weights/                  # 小例子的 Dense/Sparse 权重段
│   └── case/                     # X.bin、Y_golden.bin、Y_golden.npy
└── layers/
    ├── layer_26/
    │   ├── weights/
    │   │   ├── W_dense_data.bin
    │   │   ├── W_dense_meta.bin
    │   │   ├── W_sparse_data.bin
    │   │   └── W_sparse_meta.bin
    │   ├── public/               # 原公开 Xq 与新算的 golden
    │   └── edge/                 # 六行边界 Xq 与新算的 golden
    └── ...其余 11 层
```

- `.bin` 没有文件头，可供后续 C++ testbench 读取。整个缓冲区末尾补零到 16 字节，行间不补齐。
- `X.bin` 是 INT8；`Y_golden.bin` 是 little-endian INT32，末尾含 AXI 整拍 padding。
- `Y_golden.npy` 仅供 NumPy 使用，包含形状和类型文件头，不含 AXI 尾部 padding；不能把 NPY 文件直接送入核。
- `W_dense_data.bin` 是**同一份剪枝 Wq** 的完整 Dense 排列，不是原未剪枝模型的权重。
- `W_dense_meta.bin` 为 16 字节全零占位段；Dense 运算不读此段。
- `W_sparse_data.bin` 保存 values；`W_sparse_meta.bin` 保存位置码，不是完整 mask。
- 路径、M/K/N、mode、各段有效长度/分配长度、SHA256 均在 manifest 中；其中路径相对测试包根目录。

每层只存一份权重，`public/edge` 共用；每组输入均用 mode 0、1 两条路径核对。
文件没有绑定设备物理地址，不代表已经分配 XRT BO；上板时仍须满足 64 字节起始对齐和配套新 ABI。

## 3. Golden 怎样得到

```python
from pack_sparse24_axi import golden_dense_int32

# Xq、Wq 直接来自原 NPZ；Wq 是完整的剪枝权重矩阵。
Y_int32 = golden_dense_int32(Xq, Wq)
```

内部是 `Xq.astype(INT64) @ Wq.astype(INT64)`，检查无 INT32 溢出后才转换。
它不调用 metadata 解码、不使用 positions，不从压缩数据重建 Wq 后再冒充原始标准答案。

另一路 `emulate_axi(...)` 从 `.bin` 对应字节解码；Sparse 通过位置选择 X 并执行两槽乘加，
逐元素与上述 golden 比较。它是 Python 功能仿真，不是周期精确仿真，也不是硬件性能测量。

12 层各有公开输入和边界输入，共 24 组、935424 个源 golden INT32 元素；另含文档小例子 `[18,-3]`。
这些公开 Xq 来自原 Dense 模型中间快照，不是剪枝模型新传播的激活，也不能替代分类精度验收。

## 4. 后续 HLS 怎么使用

1. 从 manifest 的 `cases` 读取某个测试的 M/K/N；从 `modes` 选择 Dense=0 或 Sparse=1。
2. 按相应 `buffers` 路径读取 X、W_data、W_meta，注意不能把相同模式名误当模型精度基线。
3. 核计算后，将前 `M*N` 个 INT32 与 `Y_golden.bin` 或 NPY 逐元素比较，并检查 Y 尾拍为零。
4. 再覆盖不支持尺寸、非法 mode、短缓冲区和越界访问等异常测试。

Host 可调用 `validate_call(...)` 检查地址/容量及传入的尺寸白名单；此检查器保守拒绝缓冲区重叠。
不传白名单时仅校验软件格式，不能据此认定某个真实 IP 支持任意尺寸。

**本阶段通过不等于 CSim、Cosim、综合或板测通过。原冻结模型与运行包均保持不变。**
