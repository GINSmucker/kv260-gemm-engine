# KV260 LAB：统一展示与使用

KV260 LAB在同一界面内提供OPT50最终成果、性能与耗时、系统架构、交互演示和H576历史实测。默认首页展示原始Dense与Sparse24的500张分类结果，性能统一采用同调度第306～500张的195张记录。成果页面只读取保存证据，不发送板端命令。

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
- **推理工作台、线性层实验、测评演示**：未配置匹配板端通道时使用明确标识的DEMO合成数据，实际PL调用为0。上传图片不会使固定样例成为真实分类结果。
- **运行记录、连接与诊断**：查看本机任务、导入报告和保存连接配置；保存配置不执行设备探测或硬件计算。

统计服务核对主结果、逐图CSV与耗时分解。最终证据缺失或不一致时，成果页面显示不可用，不使用历史数字或样例补位。测量边界见[测量口径](measurement.md)，当前数据见[最终结果](../results/README.md)。

## 文件与来源

`board/ui/server.mjs`提供统一HTTP服务，`final-api.mjs`提供最终数据、下载和架构文件，`final-data.mjs`核对统计。`public/app.js`管理导航与任务视图，`public/final-lab.js`和`final-lab.css`渲染当前成果。`final-server.mjs`提供4179启动入口。

当前网页源文件身份见[补充来源清单](../supplemental_sources.json)。H576历史数据来自`codex/v2-h576-dense`提交`71651c3`，保留原路径和内容；存档读取器校验其SHA256。其[来源索引](../results/model_planning/h576_closeout_20261016_01/delivery_index.json)、[正式性能](../results/model_planning/v5_h576_fair_20261016_02/analysis.json)、[质量记录](../results/model_planning/v5_h576_quality_20261016_01/quality_recount.json)和[有限连续调用](../results/C/h576/c_h576_endurance_20261016_02/C_ENDURANCE_RECEIPT.json)分别保留。

## 板端运行边界

`start-workbench.ps1`的`-BoardUrl`参数仅接受维护者提供的、匹配旧H576协议的本机SSH转发地址。它不能直接连接当前Sparse24 owner或替代其启动、固件和会话门禁。本机网页整合不修改计算核、固件和已验收运行配置。

板卡上独立部署的服务拥有自己的地址、程序和当前启动状态；本机归档页面不是它的实时回执。新的硬件运行按[快速开始](quickstart.md)与原运行包约束单独核验。

## 离线检查

```powershell
node --test --test-isolation=none board/ui/tests/workbench.test.mjs board/ui/tests/charts.test.mjs board/ui/tests/final.test.mjs
```

检查覆盖本机任务、统计、HTTP、数据下载和静态成果渲染，不访问板卡，也不构成新的实板验收。
