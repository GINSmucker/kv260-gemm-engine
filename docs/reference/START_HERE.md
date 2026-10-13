# 零上下文接手

首读[README](../../README.md)、[AGENTS](../../AGENTS.md)、[接口](../interface.md)、[当前状态](../progress.md)和[验证](../verification.md)，确认默认开发、研究交付与正式资格的不同范围。

完整协作入口是codex/team-handoff-20261007。main/hls/src是默认OPT38；下一步Dense FPGA性能优化必须选择[研究锁](../locks/RESEARCH_LOCK.json)绑定的OPT45。A_OPT40_AXI128_M16N16仅为资源取舍候选，其固定分支与验证范围见[A接手](../../hls/README.md)。

```powershell
git clone --depth 1 --single-branch --branch codex/team-handoff-20261007 https://github.com/GINSmucker/kv260-gemm-engine.git
cd kv260-gemm-engine
```

A入口[hls](../../hls/README.md)，B入口[model](../../model/README.md)，C入口[board](../../board/README.md)。实际源与二进制身份由[SOURCE_MAP](../SOURCE_MAP.json)及资产锁固定；未下载资产的本地路径不能冒充已有程序。

使用选定Python3.12和固定NumPy1.26.4/ORT1.22.1/Pillow12.3.0，不自动改Conda base或系统配置。从仓库根运行：

```powershell
python -B docs/tools/check.py
python -B model/golden.py --check model/cases/tensors.npz
python -B docs/tools/assets.py opt45-source --download --extract
python -B docs/tools/assets.py opt45-evidence --download --extract
python -B docs/tools/check.py --cpp --research-source build/assets/opt45-source/opt45-source --cxx <existing-cxx> --hls-include <existing-vitis-include>
```

研究运行文件用opt45-runtime，完整560条性能样本及验证用opt45-evidence；模型和图片分别用model/dataset资产。下载需要私有仓库权限及已认证gh；解包拒绝覆盖已有目标。普通软件检查不打开设备。

给A负责人新Codex的任务可写为：请先读AGENTS.md、hls/README.md、docs/interface.md、docs/progress.md和docs/verification.md，核验RESEARCH_LOCK.performance_baseline与ASSETS锁，按需取得OPT45源码和证据。以OPT45的512MAC/TM32/TN768/125MHz为Dense性能参照，保持五shape、完整K、紧凑INT8/INT32、字节地址与逐位数值契约；A_OPT40_AXI128_M16N16仅作资源取舍候选。先核对现有SDK与软件检查，在独立codex分支提交明确候选、可复现配置和实际验证，使用新的构建身份。不得继承其他会话的板卡授权、修改固定标签或代签C。

当前PS组合取得方式：先下载并解包opt45-runtime，再执行python -B docs/tools/assets.py ps-runtime --download --extract及ps-evidence；21成员复用、11成员增量，组装后核验32成员。完整机制样本与有界慢样本由ps-evidence提供。

仅接手A的Dense算法时取得opt45-source约5.55MB、opt45-evidence约119KB即可；模型、图片、配对运行文件按B/C联调需要取得。已验证缓存直接使用，重复--extract会拒绝覆盖。SDK、生成工程、矩阵和长trace不入Git。
