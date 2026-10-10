# 公共证据与可验证身份

当前OPT45的HLS/IP由opt45-source固定，PS组合的源和运行由ps-runtime增量及opt45-runtime依赖组装，PS数据由ps-evidence固定。组合manifest32成员、A交付55成员及资产归档/成员SHA均可独立核验；SOURCE_MAP标明原项目来源与可取得的asset/member。

ps-evidence包含三个机制各1200条正式样本、两套200次有界调用、数值/重建/断开和默认API记录。连续计时含结果核对，不作为生产比较；所有慢样本保留。完整矩阵、trace及3896项本机证据由原清单管理，不随Git分发。

正式参考[BASELINE_LOCK](../locks/BASELINE_LOCK.json)、[A2](../../results/evidence/parent/A2_USABLE.json)、[C签署](../../results/evidence/parent/C_TECHNICAL_ATTESTATION.json)保持各自身份与适用范围；冻结B模型有989payload。私有C材料仅由C维护者管理，不包含在协作资产内。

原始报告与签署不改字节，local_gate的HLS范围不覆盖独立实板签署。当前状态统一见[verification](../verification.md)，身份见[SOURCE_MAP](../SOURCE_MAP.json)和[ASSETS](../locks/ASSETS.lock.json)。
