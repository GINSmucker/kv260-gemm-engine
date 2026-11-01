# KV260 LAB：统一展示与使用

KV260 LAB在同一界面内提供OPT50最终成果、性能与耗时、系统架构、Sparse24真实推理、交互演示和H576历史实测。默认首页展示原始Dense与Sparse24的500张分类结果，性能统一采用同调度第306～500张的195张记录。成果页面只读取保存证据；真实推理须连接现有板端服务并显式提交JPEG。

## 启动与关闭

使用已有PowerShell 7和Node.js 24或更高版本，在仓库根目录执行：

```powershell
pwsh -File ./start-final.ps1
```

打开 **http://127.0.0.1:4179/#overview**。也可使用`pwsh -File ./start-workbench.ps1`，默认地址为 **http://127.0.0.1:4178/#overview**。两个命令运行同一套KV260 LAB界面，默认端口不同；无需安装npm依赖或下载模型／固件。

保持终端运行，按Ctrl+C关闭。端口被占用时可指定`-Port 4180`。根地址、`/legacy/#overview`和`/final.html#overview`均打开统一工作台。

## 页面与数据

- **成果总览**：当前Dense／Sparse24的精度、整网耗时、模型路由、CPU／PL逐位一致性和证据范围。
- **性能与耗时**：全部195张Dense／Sparse24双曲线，可切换ARM＋FPGA或CPU参考。鼠标移动或键盘调整图片滑块查看对应样本；页面还提供390份原始事件回执的计时分解、计时外验收成本与数据下载。
- **系统架构**：系统整体、硬件模块、软件流程和共享计算阵列四张图，支持交互源码卡片与报告SVG下载。硬件图根据设计资料整理，不是新生成的Vivado截图。
- **历史实测**：H576 Dense的3925张质量结果、5000次有限连续调用、两轮正式性能和三点交互曲线；这些数据保持原身份，不作为当前Sparse24结果。
- **推理工作台**：上传JPEG，向已经连接的Sparse24服务提交真实推理，显示Top5、板端计时、CPU／PL比较和Native调用／完成字段；连接失败或回执不完整时不显示合成分类。
- **板端记录**：读取已有服务最近100条保存索引，查看／下载完整回执，区分当前启动和历史启动。
- **样例演示、线性层实验、测评演示**：保留明确标识的DEMO合成数据，实际PL调用为0。上传图片不会使固定样例成为真实分类结果。
- **本机记录**：查看本机样例任务和导入报告，与现场板端记录分开。
- **连接与诊断**：填写Sparse24 HTTP地址并点击“连接板卡”，读取模型身份、boot ID、自检和忙闲状态。连接和刷新只查询，不提交推理或加载固件。

统计服务核对主结果、逐图CSV与耗时分解。最终证据缺失或不一致时，成果页面显示不可用，不使用历史数字或样例补位。测量边界见[测量口径](measurement.md)，当前数据见[最终结果](../results/README.md)。

## 连接板卡并上传推理

1. 确保电脑与板卡同一子网、IP不同。例如电脑`192.168.137.1`，板卡`192.168.137.2`。已有Sparse24服务应在板卡运行；本机网页不安装或重启它。
2. 打开“连接与诊断”，填写`http://192.168.137.2:8080`并点击“连接板卡”。客户端核对Sparse24模型身份、规范boot ID，以及READY状态下36次调用、0差异的启动自检。
3. 打开“推理工作台”，选择或拖入非空、最大8MiB的JPEG，再点击“开始板端推理”。板端沿用既有RGB、短边248、中心裁剪224和归一化；本机不改图像字节或运行参数。
4. 完成后查看真实Top5、Runner.run、CPU参考时间及回执摘要。可下载JSON；刷新后可在“板端记录”中查看保存结果。

每次推理使用一个32位十六进制请求编号，JPEG在本机计算SHA256并与板端回执绑定。编号在提交前保存到当前标签的sessionStorage；断线或刷新只查询原编号，不自动重发POST。错误模型、输入SHA、boot ID、调用路由或完成字段均拒绝作为成功结果。未知／保留状态不提供网页强停、复位、卸载或自动重跑。

CPU／PL逐位比较由板端报告，客户端未取得全部1000个logits，只核对输出摘要相等、Top5合法性和完成字段。现场图无人工标签，不计算准确率；Native调用计数不等于独立测得的物理START次数。Runner.run不包括预处理、CPU参考和INFO，也不是纯PL时间。设备是否关闭以回执`device_closed`为准。

## 文件与来源

`board/ui/server.mjs`提供统一HTTP服务，`final-api.mjs`提供最终数据、下载和架构文件，`final-data.mjs`核对统计。`sparse24-api.mjs`与`sparse24-client.mjs`处理本机到板端的代理、去重与回执校验；`public/sparse24-live.js`管理实测界面。`public/app.js`管理导航，`public/final-lab.js`和`final-lab.css`渲染归档成果。`final-server.mjs`提供4179启动入口。

当前网页源文件身份见[补充来源清单](../supplemental_sources.json)。H576历史数据来自`codex/v2-h576-dense`提交`71651c3`，保留原路径和内容；存档读取器校验其SHA256。其[来源索引](../results/model_planning/h576_closeout_20261016_01/delivery_index.json)、[正式性能](../results/model_planning/v5_h576_fair_20261016_02/analysis.json)、[质量记录](../results/model_planning/v5_h576_quality_20261016_01/quality_recount.json)和[有限连续调用](../results/C/h576/c_h576_endurance_20261016_02/C_ENDURANCE_RECEIPT.json)分别保留。

## 板端运行边界

Sparse24连接使用独立的`/api/sparse24/`接口，不需要在网页输入SSH密码。`start-workbench.ps1`的`-BoardUrl`参数仍只接受匹配旧H576协议的维护者转发地址，不用于当前Sparse24接入。网页不替代原服务的启动、固件、会话和完成门禁，不修改板端程序、计算核或已验收运行配置。

板卡服务保持自己的地址、程序和当前启动状态。本机归档数据、样例任务及现场回执分别展示；原运行包的完整重建按[快速开始](quickstart.md)单独核验。

## 本机Sparse24接口

| 接口 | 用途 |
|---|---|
| `POST /api/sparse24/connect` | JSON中的`endpoint`指定板端HTTP地址，仅查询状态并核对身份 |
| `GET /api/sparse24/status` | 查询当前配置地址的板端状态 |
| `POST /api/sparse24/infer/{id}` | `image/jpeg`原始字节，编号只尝试提交一次 |
| `GET /api/sparse24/result/{id}` | 读取并校验原编号回执；`download=1`下载JSON |
| `GET /api/sparse24/history` | 板端最近100条保存索引，不代表重新执行 |

本机服务仅监听loopback，拒绝跨源请求；板端地址限HTTP私网IPv4或本机地址，不接受账号、路径或重定向。提交前再查询READY和忙闲状态，绑定本次boot与图片摘要；不提供重放、模型切换或固件管理接口。

## 离线检查

```powershell
node --test --test-isolation=none board/ui/tests/workbench.test.mjs board/ui/tests/charts.test.mjs board/ui/tests/final.test.mjs board/ui/tests/sparse24.test.mjs
```

检查覆盖本机任务、统计、HTTP、数据下载和静态成果渲染，不访问板卡，也不构成新的实板验收。
