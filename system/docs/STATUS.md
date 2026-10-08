# 当前开发状态 2026年10月8日

客户端日期为Asia/Shanghai。实验日期来自原报告；板端旧日历和本次上传时间不替代实验日期。当前正式开发父版为`system_a3_20261005_02`，OPT35a/V2A/修正B标签插件。详见[BASELINE_LOCK](../BASELINE_LOCK.json)。

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

最新F05 D5同boot父→候选→父完整结果中位6445.621097→1465.884971→6663.105521ms，候选session中位1400.893781ms；完整结果包含BEGIN、49层、COMMIT和必要结果/trace/profile导出。父最大漂移3.374142%、CPU最大0.498919%，三图均快于两侧父版。588starts/49031904输出摘要锚定、1481原始文件核验PASS；这是三图开发比较，不是正式S5/p95或采用。

F05软件总量扣wait/fsync的下界798.998ms>600，C ABI扣native/fsync下界422.725ms>100，B回执/trace下界35.973ms>30，三预算严格FAIL。fsync精确持续时间NOT REPORTED，采用BEGIN/COMMIT括号形成保守边界。G5仍关闭，OPT38新组合NOT RUN。下一目标是REQUEST非native306.895ms的细分及验证/ACK/B剩余工作，见[F05交接](F05_HANDOFF.md)。

F05 D3正常XRT/NEON和完整软件核对1604文件PASS，五图20429960输出、Xq/sx/Y/logits逐位一致；D4 52starts/4426408完整INT64输出、实际调用者死亡封存/COMMIT/安全回退PASS。D4和D5使用相同owner/bridge/plugin，身份在[F05_LOCK](../research/F05_LOCK.json)。硬件、原guard/const/AXI和冻结B数值不改。

CPU模型Windows/ARM的500和full3925质量已通过；跨主机量化logits仍有历史FAIL。完整PL质量、模型稳定性、当前V2A/V2B的完整UI与两次公平S5/正式发布仍未通过。历史v1 S3/S4、F8稳定性不自动放行当前组合。

最近实板状态引用10月8日F05最终独立root：boot`6f08c7ac-6431-4fdc-af4d-12b02a8e494e`，starter、无ZOCL/render/设备用户、公共锁空闲，CPU UI空闲恢复。四PL窗共640starts，OF0/严重错误0，各4条属性告警；B/A/P1/F8/C工具和原34审阅源保护PASS。上传会话不操作板卡，未重新连接核验。

当前模型BO高地址NOT_EXERCISED，kernel cycles不可用；永久AXI卡死硬件恢复、掉电、并发热拔插及长期切换未证明。原系统驱动保留；临时ZOCL v2须核对SHA和kernel后由维护者安排。

结果入口：[10月8日原始小回执汇总](../evidence/latest/runtime_progress_20261008.json)、[F05_LOCK](../research/F05_LOCK.json)、[历史CHK05](../evidence/latest/native_accounting.json)、[父版短测](../evidence/parent/short_screen.json)、[CF1比较](../evidence/cf1/comparison.json)、[OPT38 A2](../evidence/opt38/A2_USABLE.json)。证据范围说明见[EVIDENCE](EVIDENCE.md)。
