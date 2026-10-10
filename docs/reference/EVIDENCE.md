# 公共证据与身份索引

本目录只收小型公开回执和结论，不收原始矩阵、trace、私有C留出或全部工程。签署文件保持原字节；其中原本的本机results路径和当时NOT RUN是历史快照，不修改为当前状态。

|证据|用途|
|---|---|
|[父candidate](../../results/evidence/parent/candidate.json)、[ready](../../results/evidence/parent/ready.json)|固定实际采用身份；ready不授权新boot/window|
|[编译清单](../../results/evidence/parent/compiled_sources.json)、[B标签修正](../../results/evidence/parent/model_plugin_tag_only.diff)|来源是实际编译快照及修正版，通用源码旧tag不覆盖它|
|[S1](../../results/evidence/parent/S1.json)、[S2](../../results/evidence/parent/S2.json)、[完整图](../../results/evidence/parent/image_verification.json)|实际范围及明确证据复用，不代替新组合验证|
|[父短测](../../results/evidence/parent/short_screen.json)、[CF1比较](../../results/evidence/cf1/comparison.json)|采用父版和无效研究比较分别保留|
|[平台](../../results/evidence/parent/platform_verification.json)、[配对清单](../../results/evidence/parent/paired_manifest.json)|已测平台身份与时序，不给重建平台继承A2|
|[OPT35a A2](../../results/evidence/parent/A2_USABLE.json)、[C技术](../../results/evidence/parent/C_TECHNICAL_ATTESTATION.json)、[OPT38 A2](../../results/evidence/opt38/A2_USABLE.json)|原独立联合签署；私有留出仅有公开汇总|
|[B冻结清单](../../results/evidence/model/B_bundle_manifest.json)、[冻结回执](../../results/evidence/model/B_freeze_receipt.json)|989payload和质量历史范围；包内旧Gate不反写|
|[CHK05](../../results/evidence/latest/native_accounting.json)、[RW06](../../results/evidence/latest/RW06_decision.json)、[C审阅](../../results/evidence/latest/C_runtime_audit.json)|最新分账、负面决定和静态源码推导|

[SOURCE_MAP](../SOURCE_MAP.json)记录原始项目相对路径、archive成员、源码SHA和交付位置。公共回执引用但未打包的原始材料标为LOCAL_ONLY；私有材料为PRIVATE_C_ONLY，仅C维护者审计。未包含原始证据不代表重新独立复验，也不使已有签署范围扩大。

本次交付检查记录在`DELIVERY_VERIFICATION.json`。它只证明公开文件身份、便携入口及本机软件接手，不产生新实板、HLS/RTL、性能、质量或正式发布Gate。

V2A `software_core.h`是可选历史数学测试适配器，编译宏保护阻止它进入正常owner。它不是父版实际ARM编译输入，也未获得当前平台的新软件集成Gate；保留它是为了显式测试include依赖。默认构建不定义该测试宏。

10月8日新增[公共进度汇总](../../results/evidence/latest/runtime_progress_20261008.json)与[F05_LOCK](../locks/F05_LOCK.json)。汇总逐份记录原始路径/完整SHA与JSON值，不改旧V3/CHK05/CF1回执。F05最终S0沿用了旧模板的candidate_status字段，它不代表最新实现Gate；实际阶段以D3/D4/D5和STATUS为准。大原始证据仍LOCAL_ONLY，私有C材料不读取或上传。

10月9日[最新小回执](../../results/evidence/latest/runtime_progress_20261009.json)覆盖v5 R4、AB1、AB2/3软件、首组合已完成生产与profile失败、线程1/2/4、GELU拒绝及两次AB6。SHA绑定完整原文件；profile/probe标明selected_fields_summary，省去大logits。外层窗口完成不覆盖数值Gate；第二AB6同v5有效与整体CPU漂移无效同时保留。正式父版锁/历史签署原字节保持。

v5生产与诊断互相独立；删除热SHA/持久日志是已授权的新协议，不是把旧F05合同测试改成PASS。旧硬件等待计时仍非纯cycles。当前新Source包对应AB1原B代码，AB2/3候选delta不参与默认构建。若其他会话有后续新窗，需其新的原始回执另同步，不借首窗失败或既有PASS覆盖。
