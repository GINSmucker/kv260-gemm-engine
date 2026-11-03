# KV260平台与ARM交付

`platform/`保存OPT50平台脚本、配置和自研observer相关来源；`deployment_sources/`保存DTS、shell及metadata源描述。

`runtime/snapshots/`保存实际受检源码；模型／case二进制由外部资产补齐。原清单不因移入Git而改写，详见 [运行包索引](runtime/README.md)。

[ui/](ui/README.md)提供KV260 LAB统一展示入口，包含当前Dense/Sparse24成果、耗时分解、系统架构及H576历史数据，并通过独立HTTP适配层连接已有Sparse24服务进行JPEG推理；[使用指南](../docs/workbench.md)说明连接、上传、结果校验和演示边界。该接入不修改板端程序、计算核或固件。

[service/](service/README.md)保存板端实际部署的监督进程、模型运行支持、非特权HTTP服务、静态页面及systemd单元；[板端使用指南](../docs/boot-demo.md)说明电脑网口、现场推理和维护操作。

`platform/tests/tb_opt50_board_logic.sv`与`platform/input/observer/tb_f8_status.sv`保存两份平台测试台；[实现报告](../results/README.md#原opt50硬件实现报告)提供配对时序和资源证据。

参考环境：Ubuntu24.04.2/aarch64，kernel6.8.0-1015-xilinx，匹配XRT2.18/zocl、g++13及XRT/UUID开发头库；ORT1.22.1、NumPy1.26.4。编译器及运行依赖受原收据约束。

单个Native owner掌握CU和BO；client只同步所需输入／输出并发送有序请求。静态权重只在owner初始化同步，图后INFO执行完整保护区与静态权重检查。每次图36次PL调用；批次最多50图，结束后正常关闭并提交结果。

还原／编译和真实硬件运行分开。新启动须用新的guardian/layer计划，旧boot和token不得复用；不自动重试、卸载、重置或为通过而删除门禁。现有完整板测证据在Release中，无需为阅读报告重新上板。
