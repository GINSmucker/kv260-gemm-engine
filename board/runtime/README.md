# 冻结运行包索引

| 快照 | 用途 | 外部载荷 |
|---|---|---|
| `sparse24` | B24常驻CPU4/PL4，受检r1调度 | `sparse24-payload.tar.gz` |
| `dense36` | 原Dense模型的独立容量／编译源码 | `dense36-payload.tar.gz` |
| `dense500_fix3` | 最终成功的Dense500串行外层、正确CLI与有界报告读取 | 无 |
| `sparse500_runner` | Sparse累计500张的受检有限runner及恢复资料 | 无 |
| `loadonly_v3` | 临时加载／持锁guardian源包 | 平台资产及原环境依赖 |
| `layer02` | 同剪枝权重Dense/Sparse两次START层验证 | `layer02-payload.tar.gz` |

这些目录中的文件按原字节保存。包含全部外部载荷的清单并不意味着这些二进制进入Git；根目录`release_sources.json`明确区分stored/external。使用`tools/restore_bundle.py`还原至新的完整目录，不能直接从不完整的快照执行原验证器。

快照中的Python、C++和头文件共同固定了原执行身份。其README中的启动ID、绝对路径、旧计划和历史闭合材料属于原现场约束；公开归档不是通用新板卡一键部署工具。

最终Dense入口使用fix3外层，其调用受检Dense36检查函数，并保留原归档SHA、摘要、模型、数据集和资源关闭校验；没有修改原Dense编译代码。后续重构应产生新身份并另行验证。
