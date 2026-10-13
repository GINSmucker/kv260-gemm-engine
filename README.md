# KV260 GEMM Engine

面向DeiT Tiny完整模型的KV260计算工程。A维护hls，B维护model，C维护board；共享接口、验证状态与使用方式位于docs。

## 当前运行配置

|配置|实现|用途与资格|
|---|---|---|
|默认开发|OPT38，128MAC，100MHz，载荷型wire5，49层PL|Git主入口及既有开发资产；身份见开发锁|
|下一步Dense FPGA性能优化基准|OPT45，512MAC，125MHz，TM32/TN768，X/W/Y128bit|从冻结研究源码开始；完整边界、平台及实板证据可用，正式独立C、PL500/full质量与独立长稳NOT RUN|
|资源取舍候选|A_OPT40_AXI128_M16N16，256MAC，10ns目标|固定到A分支68a9e643；用于LUT/DSP取舍研究，同条件实板性能比较NOT RUN|
|正式资格参考|OPT35a/V2A|身份与签署范围由正式父版锁固定|

OPT45是下一步Dense FPGA性能优化的主基准；A候选的用途是资源取舍。该选择不转移正式资格。A接手入口为[hls/README](hls/README.md)，其中列出源码、配置、测试、接口与证据的准确路径。Git内hls/src对应默认OPT38，OPT45由已锁定的Release源码资产提供。

GitHub已发布的PS比较配置采用QuantQKV和固定W BO。qkv/fc1/fc2固定PL，proj/head固定CPU；owner独占CU/BO，B通过匹配的共享接口访问X/Y。模型、量化顺序及logits要求保持逐位一致。

已发布硬件/PS实验日期为2026-10-10，算法契约复核日期为2026-10-11。已发布PS组合两组无诊断Runner.run的p50为161.476920/161.470765ms，p95为163.026194/163.562457ms，每组200正式样本全部保留。各机制收益只对应同窗参照，不跨窗口相加；长期尾部上限未证明。这些指标只对应资产锁绑定的发布配置，不代表未发布的本机PS或稀疏实验。当前状态见[progress](docs/progress.md)与[verification](docs/verification.md)。

## 接手与软件检查

先读[AGENTS](AGENTS.md)、[唯一公共接口](docs/interface.md)和[工作流](docs/workflow.md)。使用已有Python3.12/NumPy1.26.4/Pillow12.3.0/ORT1.22.1环境，从仓库根运行：

```powershell
python -B docs/tools/check.py
python -B model/golden.py --check model/cases/tensors.npz
python -B docs/tools/assets.py opt45-source --download --extract
python -B docs/tools/check.py --cpp --research-source build/assets/opt45-source/opt45-source --cxx <existing-cxx> --hls-include <existing-vitis-include>
```

普通C++检查不访问FPGA，HLS pragma被忽略。完整研究运行文件和原始性能样本分别通过opt45-runtime、opt45-evidence取得；[资产锁](docs/locks/ASSETS.lock.json)固定名称、长度、SHA256及源清单。默认不会下载全部资产，缓存位于忽略的build/assets。

- [A：HLS计算核](hls/README.md)
- [B：模型、量化与共享适配](model/README.md)
- [C：平台和运行时](board/README.md)
- [性能与资源](results/summary.csv)
- [源码及资产定位](docs/SOURCE_MAP.json)
- [分支用途](docs/branches.md)

共享板卡由维护者串行分配。取得文件不授权加载；每个新构建、boot和接口组合必须有自己的身份、占用检查、数值与恢复验证。

当前PS组合取得方式：先下载并解包opt45-runtime，再执行python -B docs/tools/assets.py ps-runtime --download --extract及ps-evidence；21成员复用、11成员增量，组装后核验32成员。完整机制样本与有界慢样本由ps-evidence提供。
