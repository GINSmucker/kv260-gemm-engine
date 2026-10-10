# 当前有效状态

有效实验日期：2026-10-10。状态以匹配源码、运行包、平台及测量身份为依据。

## 配置与职责

默认开发配置为OPT38/v5，身份见[开发锁](locks/DEVELOPMENT_LOCK.json)；正式资格参考为[BASELINE_LOCK](locks/BASELINE_LOCK.json)。当前研究配置为OPT45＋X128＋Y768＋Layout4，固定qkv/fc1/fc2在PL、proj/head在CPU，每图36次PL。

A数据通路为单512MAC、125MHz、TM32/TN768；X使用32行×64个128bit字LUTRAM，Y累加器96KiB。B使用intra4/inter1、顺序执行、spinning0、图优化关闭；量化、反量化、Erf和布局复用ORT线程池。C owner持有CU与BO，B直接访问导入的X/Y，保留cache sync、Done和排空检查。

[研究身份锁](locks/RESEARCH_LOCK.json)绑定的AB_SHARED_OPT45_X128_Y768_LAYOUT4_FIXED_20261010有33个清单成员，manifest SHA为204aab4d8120445fb24e1dd10cad1c9f0dbfe5aefba0456fba9b15c40a6e7cf8。源码、运行文件及证据由[资产锁](locks/ASSETS.lock.json)中的opt45-source/runtime/evidence固定。

## 整图与资源测量

边界为初始化完成后的Runner.run，不包括会话创建、输入文件读取及结果SHA核对。每段60个正式样本，输入20张×3次、线程固定、逐层诊断关闭。

|集合|p50 ms|p95 ms|p99 ms|max ms|
|---|---:|---:|---:|---:|
|固定测量一|168.647137|171.387642|177.503626|178.259022|
|固定测量二|169.086792|174.866617|195.331145|198.734057|
|200次连续调用|168.725388|173.517146|178.354562|185.660837|

同B插件、固定路由和同窗较快OPT43参照的p50改善为4.831634%/4.649080%；两组20/20张图的配对中位数更快。第二组p99/max高于较快参照，慢样本全部保留，不能承诺尾部已解决。参照完整分布与560个无诊断样本位于opt45-evidence。

200次运行期间client fd8/线程7固定、RSS101192→101448KiB；owner fd11/线程1、RSS21124KiB固定。三次会话重建、提交后断开、QUERY完成/ABORT及新会话完整图通过。关闭后client fd4/线程4、owner fd10/线程1；内存保留原因待核实，200次不证明长期无泄漏。

## 硬件与数值范围

HLS运行model_gemm_opt45_y768_v1_20261010_183212_157的160有效＋8拒绝、10925312整数输出、320输入SHA及同地址复用PASS。完整AXI RTL为16事务、418064整数、高地址/4KiB边界/受控背压/排空PASS；关键loop II1，top不流水。

平台model_system_20261010_184640_939为125MHz，WNS0.641ns/WHS0.010ns；65024 LUT、52837 FF、56 RAMB36、517 DSP，LUT占器件55.52%。14寄存器、三路128bit与131项DDR配置通过配对核验。HLS资源及估计时钟见[综合摘要](../hls/reports/synthesis.md)。

实板20次shape探针、1365664整数一致；三张完整验证图共12257976整数及X/Xq/sx/Y/Z/logits逐位一致；正式整图logits逐位一致。完整FP32边界p50：head0.551121、proj1.053051、qkv1.618456、fc1 1.931909、fc2 1.835943ms。算子诊断边界与无诊断整图不能混用。

带宽研究记录34689次PL调用、3896项本机回传文件哈希核验。公开资产提供必要摘要、原始性能样本和身份，完整矩阵/trace仍在本机验证记录中。

## 有效边界与问题

正式独立C、PL500/full质量、独立长期稳定性及正式发布签署均NOT RUN。普通libm GELU融合和1/2线程配置有数值FAIL，不能作为等价实现。当前优先问题是慢样本、PS/缓存行为及有界运行之外的资源趋势；固定W BO、额外HP、Stream或新队列不属于实施设计。

原始实验的最终root/UI检查PASS：starter/99999999Hz、无硬件使用者/ZOCL/render、严重内核错误0、UI未暂停。boot6f08c7ac-6431-4fdc-af4d-12b02a8e494e仅标识该实验，不能当作实时占用确认。SSH间歇性timeout/reset根因未确认；永久AXI卡死及长期overlay切换未证明。

证据入口：[资产锁](locks/ASSETS.lock.json)、[源码身份](SOURCE_MAP.json)、[结果表](../results/summary.csv)和[验证状态](verification.md)。
