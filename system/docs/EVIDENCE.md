# 公共证据与身份索引

本目录只收小型公开回执和结论，不收原始矩阵、trace、私有C留出或全部工程。签署文件保持原字节；其中原本的本机results路径和当时NOT RUN是历史快照，不修改为当前状态。

|证据|用途|
|---|---|
|[父candidate](../evidence/parent/candidate.json)、[ready](../evidence/parent/ready.json)|固定实际采用身份；ready不授权新boot/window|
|[编译清单](../evidence/parent/compiled_sources.json)、[B标签修正](../evidence/parent/model_plugin_tag_only.diff)|来源是实际编译快照及修正版，通用源码旧tag不覆盖它|
|[S1](../evidence/parent/S1.json)、[S2](../evidence/parent/S2.json)、[完整图](../evidence/parent/image_verification.json)|实际范围及明确证据复用，不代替新组合验证|
|[父短测](../evidence/parent/short_screen.json)、[CF1比较](../evidence/cf1/comparison.json)|采用父版和无效研究比较分别保留|
|[平台](../evidence/parent/platform_verification.json)、[配对清单](../evidence/parent/paired_manifest.json)|已测平台身份与时序，不给重建平台继承A2|
|[OPT35a A2](../evidence/parent/A2_USABLE.json)、[C技术](../evidence/parent/C_TECHNICAL_ATTESTATION.json)、[OPT38 A2](../evidence/opt38/A2_USABLE.json)|原独立联合签署；私有留出仅有公开汇总|
|[B冻结清单](../evidence/model/B_bundle_manifest.json)、[冻结回执](../evidence/model/B_freeze_receipt.json)|989payload和质量历史范围；包内旧Gate不反写|
|[CHK05](../evidence/latest/native_accounting.json)、[RW06](../evidence/latest/RW06_decision.json)、[C审阅](../evidence/latest/C_runtime_audit.json)|最新分账、负面决定和静态源码推导|

[SOURCE_MAP](../SOURCE_MAP.json)记录原始项目相对路径、archive成员、源码SHA和交付位置。公共回执引用但未打包的原始材料标为LOCAL_ONLY；私有材料为PRIVATE_C_ONLY，仅C维护者审计。未包含原始证据不代表重新独立复验，也不使已有签署范围扩大。

本次交付检查记录在`DELIVERY_VERIFICATION.json`。它只证明公开文件身份、便携入口及本机软件接手，不产生新实板、HLS/RTL、性能、质量或正式发布Gate。

10月8日的新电脑接手/G0/G1精简回执在[evidence/runtime_v3/local_20261008.json](../evidence/runtime_v3/local_20261008.json)，源码和界面边界见[本次记录](G0_G1_LOCAL_20261008.md)。原日志在本机ignored build中；回执列出相对路径和SHA，Git不含原始矩阵/trace/二进制。本机核心、同机B等价与新电脑相对旧参考的FAIL分别报告；未产生新的A/C签署或实际PL启动。

V2A `software_core.h`是可选历史数学测试适配器，编译宏保护阻止它进入正常owner。它不是父版实际ARM编译输入，也未获得当前平台的新软件集成Gate；保留它是为了显式测试include依赖。默认构建不定义该测试宏。
