# 完整层计算与数据通路

矩阵为X[M,K]×W[K,N]=Y[M,N]，INT8输入、INT32完整K归约，五类白名单和尾块约束见[接口](../interface.md)。默认OPT38的源码位于hls，研究OPT45的准确源位于opt45-source资产。

OPT45使用32×16外积、512MAC，TM32/TN768；W完整加载，X为32行×64个128bit字LUTRAM，累加器96KiB。每M块初始化无效行、装载有效X后完成归约和Y写回；没有DATAFLOW重叠、跨start权重缓存、双缓冲或队列。三路128bit共用HP0，Y直接从累加器打包。

性能采用完整FP32边界与无诊断整图分别验证。loop II、top Interval、RTL周期、HLS估计与板测不互换。设计参数/资源见[综合摘要](../../hls/reports/synthesis.md)，当前有效结果见[progress](../progress.md)，原始报告在opt45-evidence。
