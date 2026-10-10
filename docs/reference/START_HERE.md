# 零上下文接手

首读[README](../../README.md)、[AGENTS](../../AGENTS.md)、[接口](../interface.md)、[当前状态](../progress.md)和[验证](../verification.md)，确认默认开发、研究交付与正式资格的不同范围。

A入口[hls](../../hls/README.md)，B入口[model](../../model/README.md)，C入口[board](../../board/README.md)。实际源与二进制身份由[SOURCE_MAP](../SOURCE_MAP.json)及资产锁固定；未下载资产的本地路径不能冒充已有程序。

使用选定Python3.12和固定NumPy1.26.4/ORT1.22.1/Pillow12.3.0，不自动改Conda base或系统配置。从仓库根运行：

```powershell
python -B docs/tools/check.py
python -B model/golden.py --check model/cases/tensors.npz
python -B docs/tools/assets.py opt45-source --download --extract
python -B docs/tools/check.py --cpp --research-source build/assets/opt45-source/opt45-source --cxx <existing-cxx> --hls-include <existing-vitis-include>
```

研究运行文件用opt45-runtime，完整560条性能样本及验证用opt45-evidence；模型和图片分别用model/dataset资产。下载需要私有仓库权限及已认证gh；解包拒绝覆盖已有目标。普通软件检查不打开设备。

给新Codex的任务可写为：请先按上述入口核对当前有效实现、源/包身份、我的A/B/C职责和未通过范围，再在独立codex分支完成一个明确改动及相应验证。不得继承其他会话的板卡授权、修改固定标签或代签C。
