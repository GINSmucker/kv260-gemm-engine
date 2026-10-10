# B：模型、量化与Golden

quantize.py和golden.py为共享数学入口；b/保留已发布完整DeiT工具，v5/是从已发布源包导入的最小运行时适配，research/保存明确未采用研究源。原数值代码/冻结权重不因目录整理重新训练。

- DeiT-Tiny，12block/197token/embed192/3heads/224输入/1000类，49个完整Linear。
- 动态行scale与按输出通道W scale；[-127,127]/zp0/RNE；FP32先乘sx、再sw、再bias，不融合改舍入。
- 完整INT64独立乘法检查，再安全转换INT32；不是tile实现或HLS估计。

## 小case

cases/tensors.npz是公开冻结B案例l48_real：完整head M1/K192/N1000，约194KiB。含Xq/Wq、sx/sw/bias、完整INT32和FP32预期；cases/manifest.json保留来源、逐数组SHA和shape，不来自C留出。

```powershell
python -B model/golden.py --check model/cases/tensors.npz
```

完整152层case、权重、20张量与模型图通过旧model资产按需取得，不在Git复制。校准256/选择100/回归20（selection子集）/快测500的856原JPEG在数据Release中，见[DATASETS](../docs/reference/DATASETS.md)。

## CPU与v5

模型包取得后，从根目录执行：
```powershell
python -B -m model.b.cli infer --bundle build/assets/model/B_MODEL_BUNDLE_V1 --tensor build/assets/model/B_MODEL_BUNDLE_V1/regression/00_input.npy --backend windows_fp32 --output results/runs/my_cpu.json
```

ARM用arm_fp32/arm_w8a8_reference，必须在ARM执行。保持4线程和依赖；跨主机量化logits FAIL、未签质量/性能限制继续保留。

model/v5/runtime.py与cli.py不直接取得板卡，连接维护者已验证的v5 owner；b_plugin.cpp/client.cpp等头用CMake实际配方独立编译。v5不具旧V2A/F05持久恢复语义；接口和配对见[唯一接口](../docs/interface.md)、[v5交接](../docs/reference/V5_AB_HANDOFF.md)及[开发锁](../docs/locks/DEVELOPMENT_LOCK.json)。
