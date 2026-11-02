# 精度与性能口径

## 精度

相同500张图片，250个真实标签类，完整1000类logits排名。原始Dense W8A8和剪枝微调Sparse24分别以自己的ORT CPU参考校验，不能要求两个不同模型的logits相同。Top5按分数降序，平分时较小输出索引优先。

Dense正确353/452张，Sparse24正确357/461张（Top1/Top5）。Top1有32张由错变对、28张由对变错，净增加4张。两模型各比较500,000个FP32输出，CPU/PL逐位不匹配均为0。

## 性能

主性能表固定第306..500张，共195张。该范围的Sparse软件包SHA为`38c130e8632b4433f4217d840d9d9578699fb654de6095df17003d3f7fa0217d`，与Dense保持相同的PS调度／融合实现和ORT4/1配置；Dense模型身份和容量变化由独立编译产生。

每张只保留一次记录，分批常驻CPU会话和Native owner。195张的会话边界均为5、50、50、50、40张。不能把这一组称为交错重复、稳态benchmark或长期分位数保证。

CPU数值参考307ms量级不代表优化过的CPU INT8 GEMM上限。两个模型分别报告对应CPU加速比，不将Dense CPU与Sparse PL的不同权重结果冒充同模型硬件加速。

`Runner.run`包含PS算子、量化／反量化、图BEGIN/END、36次PL调用与必要client缓存同步；排除session初始化、图片预处理、完整后验校验和CPU参考运行。这里只报告完整模型延迟，不由此推导纯PL周期或能耗。

均值使用全部195个有效记录，中位数用标准样本中位数；P95为排序后p×(n−1)位置的线性插值。主表平均延迟Dense143.493978ms，Sparse136.588410ms，下降4.812445%，速度比1.050558。

最初500张Sparse记录包含多个软件／生命周期版本，原摘要完整保留但不将其总体均值作为同版本主比较。对应原始范围、输入SHA和四组CPU/PSPL时间见 [逐图表](../results/board500/paired195_cpu_pl.csv)。

## 原始事件回执的耗时分解

同一195张图片的390份Native回执与worker记录可划分为以下互不重叠的区间。各分段均值之和等于主表的`Runner.run`均值，单位为ms。

| 区间 | Dense | Sparse24 |
|---|---:|---:|
| START写入返回至完成证明 | 26.611137 | 20.503519 |
| Native启动准备 | 0.121786 | 0.127620 |
| 完成证明后的层验证 | 0.202747 | 0.197912 |
| BEGIN至END内的其余时间 | 115.746944 | 114.958256 |
| Runner计时与BEGIN/END边界之差 | 0.811365 | 0.801103 |
| Runner.run总计 | 143.493978 | 136.588410 |

START至完成证明包含FPGA执行、软件轮询、MMIO和完成／AXI排空检查，不能解释为纯PL周期。Native启动准备从`DMA_ACTIVE_BEFORE_START`开始，到`START_WRITE_RETURNED`结束；层验证从`DMA_COMPLETION_PROVEN`到`LAYER_COMPLETION_VERIFIED`。其余图内时间包含PS算子、量化／反量化、IPC和缓存同步，现有回执不能将这些成本单独拆开。

计时外，CPU参考均值分别为308.540631和303.003168ms；INFO内的图后缓冲检查均值分别为291.916908和250.122911ms。缓冲检查不等于完整INFO耗时。输入准备、结果保存和批末关闭也在Runner计时之外；上述数据不能相加当作完整端到端延迟。

[分解数据](../results/board500/timing_breakdown.json)保存逐图区间、逐层均值和原始回执路径／SHA256，固定原证据归档SHA。复算命令：

```text
python -B tools/analyze_timing.py --archive /path/to/paired500-evidence.tar.gz
```

该操作只读取已保存回执，不访问板卡，也不产生新的性能实测。
