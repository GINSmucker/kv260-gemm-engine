# B · H576 十二层 Sparse 模型工具

活动工具来自归档 `model/versions/v3_sparse12/`，完整保留本地 Python import 依赖。当前候选固定十二层 26、27、30、31、34、35、38、39、42、43、46、47；其他 PL 层按 Dense，proj/head 保留 PS。

| 工具 | 用途 |
|---|---|
| [sparse24.py](sparse24.py) | 逻辑 values/positions、重建与整数参考 |
| [pack_sparse24_axi.py](pack_sparse24_axi.py) | `H576_SPARSE24_SPLIT128_V1` 的128bit物理打包 |
| [export_mlp12.py](export_mlp12.py) | 冻结 c062 既有掩码，不执行训练 |
| [eval_models.py](eval_models.py) | 相同预处理、Top1/Top5、新增错例 CSV |
| [download_imagenet100.py](download_imagenet100.py) | 用户指定公开数据集的开发样本取得工具 |
| [full_model_adapter/](full_model_adapter/prepare_model.py) | 固定完整模型导出、CPU reference 和离线检查 |

运行下载、导出或训练属于写文件/网络等操作，本次没有执行。模型、候选 NPZ、搜索证据、图片和标签不在 Git；原脚本仍保留它们的来源路径与 SHA 假设。完整模型适配器的 `WORKSPACE` 相对定位不因移动而变为独立可用，先核对 [外部输入](../docs/external_assets.md)，不能跳过哈希或生成占位权重。

原 requirements 是 Windows CPython3.12 CPU 环境锁，不能直接作为 Ubuntu aarch64 安装清单。无设备单测也可能创建自有临时数据；它们不进行训练或实板推理。`model/.test_tmp/` 是测试专用空容器，不是测试图片集。

已有依赖的Windows环境可从仓库根目录运行以下合成测试；它临时修改本PowerShell的TEMP/TMP并在结束后恢复，测试会创建/清理自有文件，不访问板卡：

```powershell
$modelSavedTemp = $env:TEMP
$modelSavedTmp = $env:TMP
try {
    $env:TEMP = Join-Path $PWD 'model/.test_tmp'
    $env:TMP = $env:TEMP
    python -X utf8 -B -m unittest discover -s model -p 'test_*.py' -v
} finally {
    $env:TEMP = $modelSavedTemp
    $env:TMP = $modelSavedTmp
}
```

| 当前完整模型身份 | SHA256 |
|---|---|
| 逻辑 weights.bin | `88a553bfd6bf0df05017b0497582fe09ac03cc6fdeb8e84e6bcf23ac022320f6` |
| W8A8 reference.onnx | `50f7e260b975d6038bff029c792d90bd26a011fa2171e44ca7991dd5c765bc49` |
| custom graph | `8a462d73b5e8bb6544f65c6187dc621b560ae1329520f5556430ff42c97f0dfa` |
| AXI cases manifest | `6d34397bae245400f19f674e98eb5726896aaa2a3e2545adf656254527585fcf` |

格式说明见 [model/docs](docs/sparse24_axi_format_v0.1.md)，实际核的尺寸子集与控制 ABI 见 [共同接口](../docs/interface.md)。同权重 Dense/Sparse 数值一致不能替代剪枝与未剪枝模型的真实图片精度比较。
