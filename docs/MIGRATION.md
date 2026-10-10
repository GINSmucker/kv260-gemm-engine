# 已上传资料的目录迁移

2026-10-10将0b2ee9a协作资料与队友已合并main f5ead7a（PR #4）的A/B/C骨架整合。237个原system成员逐字节归位，重复OPT38/平台源合并；当前Git不保留system/。本机原system缓存暂留在忽略目录，不复制为云端源码，不因清理触及受保护产物。

|原位置|当前位置|
|---|---|
|system/hardware/model_gemm/candidates/opt38_fullk_v1_full|hls/src、hls/tb；唯一配置在hls/src|
|system/hardware/model_gemm的configs/scripts/contracts/其他候选|hls/config、hls/scripts、hls/contracts、hls/candidates|
|system/hardware/model_gemm/platform|board/vivado|
|system/board/model_backend|board/runtime/legacy/model_backend|
|已发布v5-source及运行资产公开头|board/runtime与model/v5的实际源|
|system/model|model/b与requirements|
|system/vendor/research模型源|model/vendor、model/research|
|system/ui|board/ui|
|system/docs|docs/reference；当前共享入口为interface/progress/verification/workflow|
|system/evidence和发布校验|results/evidence|
|system资产/身份锁|docs/locks|
|system/tools|docs/tools、board/runtime/tools、hls/tools|

[逐文件映射](MIGRATION_MAP.json)记录原位置、现位置、原SHA和路径适配后的SHA；[SOURCE_MAP](SOURCE_MAP.json)保持原来源/归档成员/源码身份。标为PATH_ADAPTED的模块只改定位/调用路径，仍需软件复验，不冒充原字节未变。数值、wire5、原平台内容与签署不改。

## 新入口

从仓库根运行：
- python -B docs/tools/check.py [--cpp --node --model-bundle ...]
- python -B docs/tools/assets.py <asset> --download --extract
- python -B docs/tools/dataset.py verify --root build/assets/dataset/dataset
- python -B board/runtime/tools/build_runtime.py --variant v5-ab1 --uuid-include <sdk>
- hls/tools/hls.ps1 / board/runtime/tools/platform.ps1

资产进入build/assets/；数据、模型、运行包/IP九个旧资产名/URL/压缩内容/摘要保持。已发布tag仍展示原system结构，按当时指令复现；当前分支用本表。不能将旧Release的下载说明当当前路径。main新版源码骨架与原2020.2.2历史报告是不同工具实验，不把移植当Vitis2026.1重新综合/实板PASS。

## 保留与范围

完整公开证据原字节放results/evidence，签署中的旧本机results路径/NOT RUN都是历史字段；不要改原回执。历史接口和原README在docs/history；接口唯一依据为docs/interface.md。旧tag v0.1/v0.2、三handoff及数据release不移动，P1冻结不改变。

model/cases/tensors.npz只含冻结B公开完整head（M1/K192/N1000），不访问C私有种子/向量。约194KiB，独立INT64复核1000输出；权重/完整图/20图大张量/856JPEG仍按原资产下载。目录结果表列身份、时钟、scope与证据，不将HLS估计等同板测或p50相加。

本次整合在协作分支，PR经队友审阅再进main。原F8和早期G0/G1分支继续其历史用途，新开发从当前根结构接手。
