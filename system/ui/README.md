# KV260 工作台接手

UI为零npm依赖Node>=24应用；从`system/ui`执行`npm test`和`npm start`。板端代理使用Python3.12及冻结模型依赖，不负责驱动、overlay或复位。

真实连接使用本机SSH loopback转发，先确认端口与现有任务；不要结束其他成员的服务。

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8741:127.0.0.1:8740 kv260
$env:KV260_UI_BOARD_URL='http://127.0.0.1:8741'
node server.mjs
```

页面4173，板端代理8740；地址未配置时只提供显式DEMO，真实请求失败不会自动切DEMO/CPU。BOARD结果绑定实际图片/张量SHA、1000logits、Top5、主机、boot、模型与真实PL starts。停止等安全软件边界，不强杀owner。

`board_agent.py`保留历史v1/CPU功能，`board_agent_v2a.py`为版本化接合入口，均显式要求bundle/runs；V2A PL另要求ready/bridge/config/plugin。当前组合完整UI实板Gate NOT RUN，历史v1 S4的通过不能沿用。用`python board_agent_v2a.py --help`读取参数，不运行历史硬编码启动脚本。

部署文件和调用顺序见[模型说明](../docs/MODEL_AND_RUNTIME.md)与[接口](../docs/INTERFACES.md)。URI和capability版本变化由C/UI联合更新，未完成真实接合时保持拒绝。
# G1离线诊断collector（2026-10-08）

`timing-v3-g1.mjs`只验证`b_layer_timing_v3_g1`与`b_layer_trace_v3_g1`的49层请求/计时身份，要求CPU_REFERENCE、0真实PL starts、精确分区闭合，并输出`production_capability=false`。不放行生产agent或旧ready；当前组合完整UI Gate仍未通过。

从system执行`node tools/collect_v3_timing.mjs <timing.jsonl> <trace.jsonl> <fresh-output.json>`，原始文件SHA随输出保留。`tools/check_v3_collector.mjs <actual-B-output-dir>`在真实候选记录上验证8项错误拒绝。完整接口、B终末导出计入session.run及未运行项见[本次G0/G1记录](../docs/G0_G1_LOCAL_20261008.md)。
