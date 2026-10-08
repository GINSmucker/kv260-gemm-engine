# 当前开发状态 2026年10月9日

客户端日期为Asia/Shanghai。实验日期来自原报告；板端旧日历和本次上传时间不替代实验日期。当前正式开发父版为`system_a3_20261005_02`，OPT35a/V2A/修正B标签插件。详见[BASELINE_LOCK](../BASELINE_LOCK.json)。

当前开发硬件已采用**AB1 OPT38 + 最小v5**，独立[开发锁](../research/DEVELOPMENT_LOCK.json)与正式父版分开。20图同软件比较VALID，core中位887.999602→438.384590→888.105542ms，较较快35a父版下降50.632344%；父最大漂移3.791476%、CPU3.610684%，logits逐位一致。三窗3823真实starts，候选五shape/8285456全INT64及实际客户端退出/QUERY/拒绝重放、root静止/starter/UI回退PASS。它是开发采用，不签正式PL/CPU加速比或发布。

最小v5的热SHA/ACK/持久账本/大JSON已移除，51RPC/图、32B wire5；独立诊断软件/C/B成本241.449/31.580/0ms达到旧目标。v5对F05的两次20图比较仍INCONCLUSIVE_DRIFT，旧父COMMIT长尾不能据此放宽；正式G5/父版未移动。见[V5/AB接续](V5_AB_HANDOFF.md)。

AB2修正版常量折叠与会话scratch、AB3量化/shape精简已通过软件检查；AB3现在173文件已回收，不再写“回收中”。但首个组合PL诊断`v5_ab23_screen_20261009_01`第0层Y逐位比较FAIL，41文件已收集、回退PASS，AB2/3不进入默认开发路径。根因未在本次上传中确认，不能归咎为已证明的折叠或硬件故障。AB5新probe未有本机完整回执，AB4/AB6未放行。以下表和段落保留历史范围。

|对象|已证范围|当前限制|
|---|---|---|
|OPT35a引擎|独立1357层/94880648完整INT64输出零差异、精确流量、真实A/C A2、回退PASS|不是模型性能或发布PASS|
|父版生产路径|152例S2、断开恢复；完整单图49start/4085992输出、logits0/fallback0；有效短筛选|短测中位6570.318084ms，对FP32前/后314.414075/314.884949ms；正式S5未通过|
|OPT38|128MAC；完整本机/平台/独立A2/生产单图通过|旧V2A比较父漂移6.079240%，INCONCLUSIVE，未采用|
|V2B CF1|实际ARM构建、软件协议、三启动恢复、完整单图通过|唯一性能比较父中位漂移18.861686%、输入07漂移31.602520%；未采用|
|B3 Neon|逐bitCPU、正常ARM、恢复、完整PL单图通过|只有组合资格，未签整网收益或采用|
|RW06页容器|34项ARM文件机制/59文件PASS，0 PL|成本父均值漂移6.508776%、部分组超过43%；生产关闭，不扩样|
|CHK05 native分账|52start/4238288完整INT64输出零差异；构建、恢复、49层图与回退PASS|诊断专用，净观察开销未量化，无公平性能或生产采用|
|V3重构|G1/G2软件与正常ARM、G3真实PL/恢复通过，G4完成|父逐图漂移9.186/17.365/7.026%，INCONCLUSIVE；600/100/30ms成本全部FAIL|
|F05 ABI4/wire4|D0～D5实施与实验完成，D1～D4功能/恢复PASS；D5有效开发比较|完整结果中位1465.884971ms，减少77.635%；软件/C/B预算全部FAIL，G5关闭，未替换父版|
|最小v5|R1/R2/R3通过；20图生产core888.361ms，诊断软件目标达到|对F05比较漂移无效，未签公平加速百分比|
|AB1 OPT38+v5|同软件20图比较有效，core438.385ms，开发硬件采用38|正式G5/PL对CPU/质量/长稳/UI/发布仍未通过|
|AB2/AB3|常量图修正版、本机/ARM软件检查通过，scratch容量收敛|首个组合PL第0层Y比较FAIL；未采用，保留失败和恢复|

历史F05 D5同boot父→候选→父完整结果中位6445.621097→1465.884971→6663.105521ms，候选session中位1400.893781ms；完整结果包含BEGIN、49层、COMMIT和必要结果/trace/profile导出。父最大漂移3.374142%、CPU最大0.498919%，三图均快于两侧父版。588starts/49031904输出摘要锚定、1481原始文件核验PASS；这是三图开发比较，不是正式S5/p95或采用。

F05软件总量扣wait/fsync的下界798.998ms>600，C ABI扣native/fsync下界422.725ms>100，B回执/trace下界35.973ms>30，三预算严格FAIL。fsync精确持续时间NOT REPORTED，采用BEGIN/COMMIT括号形成保守边界。G5仍关闭，OPT38新组合NOT RUN。下一目标是REQUEST非native306.895ms的细分及验证/ACK/B剩余工作，见[F05交接](F05_HANDOFF.md)。

F05 D3正常XRT/NEON和完整软件核对1604文件PASS，五图20429960输出、Xq/sx/Y/logits逐位一致；D4 52starts/4426408完整INT64输出、实际调用者死亡封存/COMMIT/安全回退PASS。D4和D5使用相同owner/bridge/plugin，身份在[F05_LOCK](../research/F05_LOCK.json)。硬件、原guard/const/AXI和冻结B数值不改。

CPU模型Windows/ARM的500和full3925质量已通过；跨主机量化logits仍有历史FAIL。完整PL质量、模型稳定性、当前V2A/V2B的完整UI与两次公平S5/正式发布仍未通过。历史v1 S3/S4、F8稳定性不自动放行当前组合。

最近已回收的AB1三窗和AB23首FAIL均记录安全回退；boot仍6f08c7ac…494e，AB23 rollback_errors为空/recovery=PASS。其他会话后续可能仍测试，以上不作本次实时独占或空闲确认。上传会话未访问板卡。历史F05四窗OF0/严重0/属性4及原冻结保护保持其当时范围。

当前模型BO高地址NOT_EXERCISED，kernel cycles不可用；永久AXI卡死硬件恢复、掉电、并发热拔插及长期切换未证明。原系统驱动保留；临时ZOCL v2须核对SHA和kernel后由维护者安排。

结果入口：[10月8日原始小回执汇总](../evidence/latest/runtime_progress_20261008.json)、[F05_LOCK](../research/F05_LOCK.json)、[历史CHK05](../evidence/latest/native_accounting.json)、[父版短测](../evidence/parent/short_screen.json)、[CF1比较](../evidence/cf1/comparison.json)、[OPT38 A2](../evidence/opt38/A2_USABLE.json)。证据范围说明见[EVIDENCE](EVIDENCE.md)。

最新小回执和失败：[10月9日进度汇总](../evidence/latest/runtime_progress_20261009.json)、[开发锁](../research/DEVELOPMENT_LOCK.json)。本次只固化已回收结果，不把运行中或后续窗口记为PASS。
