# OPT50 最终成果与 H576 历史工作台

在仓库根目录运行`pwsh -File ./start-final.ps1`，访问 **http://127.0.0.1:4179/**，可查看当前Dense/Sparse24的500张精度、195张性能曲线、原始事件耗时分解和四张架构图，下载逐图数据及报告SVG。该服务仅读取存档证据，不发送板端命令。需要已有PowerShell 7、Node.js 24或更高版本，无需安装依赖。

`final-server.mjs`提供最终成果入口，`final-data.mjs`核对统计和逐图计时区间；历史工作台可通过`/legacy/#overview`访问。

## H576 历史工作台

在仓库根目录用PowerShell 7运行`pwsh -File ./start-workbench.ps1`，然后访问 **http://127.0.0.1:4178/#overview**。需要已有Node.js 24或更高版本，无需安装前端依赖。

[完整使用指南](../../docs/workbench.md)说明启动、换端口、停服、曲线、路由、下载和必要文件。默认不配置板端通道，可离线查看原H576 Dense存档；`DEMO`任务使用合成数据。

本目录保留C原网页代码及其直接依赖，没有迁入板端agent或硬件加载工具。其旧H576实时协议尚未适配当前OPT50 Sparse24。当前模型的500张验收结果见[最终数据](../../results/README.md)。
