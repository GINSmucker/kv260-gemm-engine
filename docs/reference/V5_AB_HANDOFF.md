# v5运行配置与调用

默认载荷型v5为49层PL；研究共享v5为Map扩展及固定36层PL。二者使用32B little-endian头，完整兼容性由匹配源码、owner、B插件、graph、metadata和平台身份决定。

OPT45研究源码由opt45-source提供，运行包由opt45-runtime提供。Runner显式接收bundle/plugin/socket/graph，初始化模型，使用递增run-id同步执行；没有CPU故障fallback或RUN自动重传。共享X/Y、CPU普通工作区、FD生命周期与恢复规则见[interface](../interface.md)。

实际调用条件见[board](../../board/README.md)，数值与线程见[model](../../model/README.md)。完整性能样本和原始验证由opt45-evidence提供，状态见[verification](../verification.md)。
