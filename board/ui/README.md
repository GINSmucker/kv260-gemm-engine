# KV260 工作台接手

UI为零npm依赖Node>=24应用；从`board/ui`执行`npm test`和`npm start`。板端代理使用Python3.12及冻结模型依赖，不负责驱动、overlay或复位。

真实连接使用本机SSH loopback转发，先确认端口与现有任务；不要结束其他成员的服务。

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8741:127.0.0.1:8740 kv260
$env:KV260_UI_BOARD_URL='http://127.0.0.1:8741'
node server.mjs
```

页面4173，板端代理8740；地址未配置时只提供显式DEMO，真实请求失败不会自动切DEMO/CPU。BOARD结果绑定实际图片/张量SHA、1000logits、Top5、主机、boot、模型与真实PL starts。停止等安全软件边界，不强杀owner。

`board_agent.py`提供CPU/v1入口，`board_agent_v2a.py`提供ABI2接合入口，均要求bundle/runs；V2A PL另要求匹配ready/bridge/config/plugin。OPT45共享36层的完整UI接合验证NOT RUN，不能把ABI2适配器作为共享Map接口。用`python board_agent_v2a.py --help`读取参数，启动必须显式指定当前配置。

部署文件和调用顺序见[模型说明](../../docs/reference/MODEL_AND_RUNTIME.md)与[接口](../../docs/interface.md)。URI和capability版本变化由C/UI联合更新，未完成真实接合时保持拒绝。
