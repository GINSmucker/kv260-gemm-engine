# ABI4兼容资产

f05-source/f05-runtime为ABI4/wire4固定研究资产，由资产锁标明文件身份。其完整图BEGIN/COMMIT、持久事务与恢复规则只适用于该匹配实现，不能用于共享Map型wire5或默认载荷型wire5。

当前开发与研究入口见[progress](../progress.md)、[interface](../interface.md)。ABI4不作为默认或OPT45运行配置；需要审阅兼容实现时显式取得对应固定资产及内部契约，不改写已有原始测试字段或签署范围。
