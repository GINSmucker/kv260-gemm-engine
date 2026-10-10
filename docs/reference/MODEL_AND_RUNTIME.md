# 模型与调度职责

DeiT Tiny采用224输入、12 blocks、49个Linear和1000输出。研究图251节点，qkv/fc1/fc2固定PL、proj/head固定CPU，每图36次PL。PatchEmbed、attention及其他FP32算子由PS/ORT执行。

冻结B模型989payload与Wq/sw/bias由正式模型清单固定。RNE、逐行X scale、逐通道W scale、[-127,127]/zp0与浮点顺序不变；Layout4只搬运数据，Erf和量化复用ORT四线程。当前实现详见[model](../../model/README.md)。

研究B导入X/Y BO，CPU结果采用普通151296B工作区；owner独占控制和DMA生命周期。RUN不带矩阵载荷，必要cache sync保留，配对owner/plugin/graph与metadata不能混用。使用方式见[board](../../board/README.md)，契约见[interface](../interface.md)，资格见[verification](../verification.md)。
