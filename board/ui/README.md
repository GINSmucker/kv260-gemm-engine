# KV260 LAB 统一工作台

在仓库根目录运行`pwsh -File ./start-final.ps1`，访问 **http://127.0.0.1:4179/#overview**。原工作台命令`pwsh -File ./start-workbench.ps1`在4178端口提供同一界面。需要已有PowerShell 7和Node.js 24或更高版本，无需安装依赖。

左侧“成果总览”“性能与耗时”“系统架构”展示当前Dense／Sparse24的500张精度、195张曲线、原始回执分解和四张架构图；旧H576数据集中在“历史实测”。交互任务保留原有运行记录和明确标识的DEMO模式。默认浏览不连接板卡或发送硬件命令。

`server.mjs`提供统一HTTP入口，`final-api.mjs`提供最终数据、下载和架构文件，`final-data.mjs`核对统计。`public/app.js`管理导航与任务视图，`final-lab.js`渲染当前成果。文件身份见[补充来源清单](../../supplemental_sources.json)，数据见[最终结果](../../results/README.md)。

[使用指南](../../docs/workbench.md)说明启动、页面、数据来源和板端运行边界。本机工作台的旧H576实时协议未适配当前Sparse24运行包；网页展示整合不改变板端计算核、固件或运行配置。
