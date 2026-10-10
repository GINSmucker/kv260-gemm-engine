# 训练相关数据与开发测试

本项目使用ImageNet预训练DeiT-Tiny，现有“训练数据”用于量化校准和参数选择；未从零训练十类模型。原始数据是[fastai Imagenette 320](https://github.com/fastai/imagenette)，保持1000类输出和WNID标签索引。当前固定样本已独立打为约21.26MiB的按需Release，图片不进入Git，模型与旧运行资产复用。

## 固定划分

|划分|数量|来源与关系|用途|
|---|---:|---|---|
|calibration|256|train，各类25/26张|尺度、范围和量化统计|
|selection|100|train，每类10张；与calibration分离|参数/候选选择|
|regression|20|selection子集，每类2张，原顺序保持|已有20图功能、输入逐位复现|
|validation_fast|500|val，每类50张|冻结参数后的快速质量回归|
|validation_full|3925|完整val，包含fast500|全量扩展；本小包只保留其冻结名单|

包内独立JPEG共856张：256+100+500，regression20不重复存储。选中train内容与全部val按SHA分离；元数据seed20260930和既有名单保持，不重新采样。训练/选择与验证分开，不能用validation_fast调参后再声称独立质量。独立C验收私有种子/向量不属于这套公开图片资料，也未读取或上传。

[DATASET.lock](../locks/DATASET.lock.json)绑定原清单、标签、预处理、官方原包与B模型身份；[ASSETS.lock](../locks/ASSETS.lock.json)绑定新数据归档。包内原splits.json的raw_counts、created_utc、val_predictions是9月30日历史字段，不表示本包含全部13400图，也不改变后续真实实验Gate。

## 获取与校验

从仓库根目录/执行，已有项目Python3.12依赖和已登录gh：

```powershell
python -B docs/tools/assets.py dataset --download --extract
python -B docs/tools/dataset.py verify --root build/assets/dataset/dataset
```

解包要求新目录；已有旧数据包时保留，换新克隆或新目录。数据根为build/assets/dataset/dataset，train/val相对路径与冻结模型的splits.json一致。包含原JPEG、4份冻结元数据、20图张量引用、逐文件清单、来源/许可说明，不复制权重、大NPY矩阵或trace。

模型仍从原资产取得：

```powershell
python -B docs/tools/assets.py model --download --extract
python -B docs/tools/dataset.py check-regression --root build/assets/dataset/dataset --bundle build/assets/model/B_MODEL_BUNDLE_V1
```

check-regression在本机重新预处理20张JPEG，逐字节对照已发布的20个输入张量；不运行模型或PL。需要固定NumPy1.26.4/Pillow12.3.0。包中保留原tensor SHA与顺序，改动预处理应重新建立数值资格，不能静默更新旧参考。

## 预处理和标签

短边**248**，非256；Pillow BICUBIC；224中心裁剪，坐标round ties-even；转RGB、无自动EXIF旋转；uint8→FP32/FP32(255)，原mean/std，连续NCHW[1,3,224,224]。规范和实现分别在包内preprocess.json及[冻结源](../../model/b/preprocess.py)。

Imagenette十类对应的1000类索引为0、217、482、491、497、566、569、571、574、701。不要用ImageFolder默认0～9类别编码替代，也不截断1000 logits。目录WNID和冻结labels_imagenet1k.json为准。

## 其他开发者的CPU测试

先做20图公开小样本，目录使用新的结果路径：

```powershell
python -B -m model.b.cli evaluate --bundle build/assets/model/B_MODEL_BUNDLE_V1 --data-root build/assets/dataset/dataset --split validation_fast --backends windows_fp32 windows_w8a8_reference --limit 20 --threads 4 --output-dir results/my_data_smoke
```

Windows使用上述windows_后端；ARM对应arm_fp32/arm_w8a8_reference，只在实际ARM主机执行；普通fp32/w8a8不是有效枚举。去掉--limit执行固定500图。测试使用原CLI的逐图SHA、1000类Top1/Top5和同机配对评价；4线程保持。它是CPU模型评测，PL starts0，不能替代ARM、PL、公平性能或新模型验收。已有跨主机量化logits失败继续保留，不放宽容差。GUI演示图片和本数据的质量测评分别表述。

calibration/selection用于离线B流程；后续训练/微调另建模型/数值身份和新实验清单，不覆盖当前冻结包。A/C的整数层测试继续使用既有公开TB或B层case，而不是把JPEG直接传给GEMM。

## 需要全量数据时

原官方[Imagenette 320归档](https://s3.amazonaws.com/fast-ai-imageclas/imagenette2-320.tgz)341663724字节，SHA256：

```text
569b4497c98db6dd29f335d1f109cf315fe127053cedf69010d047f0188e158c
```

train9469/val3925。本小包提供所有实际校准/选择和快速验证数据；完整train及余下val从官方源按此摘要取得，避免重复上传约326MiB原包。显式下载到新文件、再安全解包：

```powershell
Invoke-WebRequest -Uri 'https://s3.amazonaws.com/fast-ai-imageclas/imagenette2-320.tgz' -OutFile 'build/assets/imagenette2-320.tgz'
python -B docs/tools/dataset.py unpack-full --archive build/assets/imagenette2-320.tgz --output build/assets/official-imagenette
python -B docs/tools/dataset.py verify --root build/assets/dataset/dataset --images-root build/assets/official-imagenette/imagenette2-320 --split validation_full
```

unpack-full拒绝错误SHA、链接/路径逃逸和已有目标。完整评测的--data-root改为build/assets/official-imagenette/imagenette2-320，--split validation_full，其他身份/名单保持。不要在856图包上运行full3925并称为全量测试。

## 来源与使用范围

随包保留[上游仓库](https://github.com/fastai/imagenette)的Apache-2.0许可、固定revision及原图权利说明；仓库许可不等于重新许可原照片。用于队友研究教育复测，使用前阅读来源与[原数据条款](https://image-net.org/download.php)，保留归属，不授予额外商业许可。

Imagenette来自ImageNet，可能与预训练数据相关；此处用于相同模型量化/替换的配对回归，不表述为标准ImageNet-1k或新域泛化成绩。本次数据打包与输入复现不产生新的训练、质量、性能或发布Gate。
