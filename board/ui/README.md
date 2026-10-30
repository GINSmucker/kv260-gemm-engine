# C 的 H576 可视化工作台

在仓库根目录用PowerShell 7运行`pwsh -File ./start-workbench.ps1`，然后访问 **http://127.0.0.1:4178/#overview**。需要已有Node.js 24或更高版本，无需安装前端依赖。

[完整使用指南](../../docs/workbench.md)说明启动、换端口、停服、曲线、路由、下载和必要文件。默认不配置板端通道，可离线查看原H576 Dense存档；`DEMO`任务使用合成数据。

本目录保留C原网页代码及其直接依赖，没有迁入板端agent或硬件加载工具。其旧H576实时协议尚未适配当前OPT50 Sparse24。当前模型的500张验收结果见[最终数据](../../results/README.md)。
