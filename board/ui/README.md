# KV260 LAB 统一工作台

在仓库根目录运行`pwsh -File ./start-final.ps1`，访问 **http://127.0.0.1:4179/#overview**。原工作台命令`pwsh -File ./start-workbench.ps1`在4178端口提供同一界面。需要已有PowerShell 7和Node.js 24或更高版本，无需安装依赖。

左侧“成果总览”“性能与耗时”“系统架构”展示当前Dense／Sparse24的500张精度、195张曲线、原始回执分解和四张架构图；旧H576数据集中在“历史实测”。“样例演示”与真实推理分开，不以合成结果回退。默认浏览不提交硬件计算。

`server.mjs`提供统一HTTP入口，`final-api.mjs`提供最终数据、下载和架构文件，`final-data.mjs`核对统计。`sparse24-api.mjs`和`sparse24-client.mjs`代理现有板端HTTP服务并校验回执；`public/sparse24-live.js`管理连接、JPEG上传、原请求查询和真实结果。`public/app.js`管理导航，`final-lab.js`渲染当前成果。文件身份见[补充来源清单](../../supplemental_sources.json)，数据见[最终结果](../../results/README.md)。

[使用指南](../../docs/workbench.md)说明连接、JPEG上传、真实回执和板端运行边界。Sparse24接入使用独立协议，不把旧H576任务冒充当前结果；不加载固件或修改计算核与运行配置。下载的完整回执保留`device_closed`，不将常驻设备宣称为已关闭。
