# 来源、身份与证据

本次以main提交`becf9337915ecd855d91a8e7450c26945889a449`为基础。旧main的35个源码／文档／图片文件按字节保存在 [历史目录](provenance/pre-final-main/README.md)，原始清单见`source_manifest.json`。

HLS活动源码来自`codex/v3-opt50-sparse`的OPT50。逐文件SHA、冻结运行包和模型来源列在 [release_sources.json](../release_sources.json)。当前Git归档提交在板测之后创建；板测时源码由原始包SHA固定，不能把后来Git提交写成当时已经存在的运行提交。

| 对象 | SHA256 |
|---|---|
| Sparse24 W8A8模型 | `ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43` |
| 原Dense W8A8模型 | `f2f203362a337a357028b8b99c853cd39bdc812cf1da41775c9e233907d5f415` |
| Sparse当前调度源码清单 | `38c130e8632b4433f4217d840d9d9578699fb654de6095df17003d3f7fa0217d` |
| Dense源码清单 | `e60866fca203ee366c9c9717cdbc55256cdde6ced7c25f007faf923854ad7b46` |
| Dense外层fix3清单 | `7db67b174b3dd429fbd8d156ae08edca2689557277e371339321adb57011301d` |
| 成对500张完整证据 | `3dd66ac7e784153ce6d479be2017f54d678870cbc02d4f02dbdc681d366f591b` |
| 原Sparse500证据（嵌入上项） | `f14b1a7e3ec26fd7205e4b32ca57b0ae5ab55972cbcb353fbcb406003bc182fb` |
| Linux可加载bit.bin | `7de86da2363e92453f9de350dcf81a2b1d4d2e4fe4a64ad1e66db8da9563c662` |
| metadata xclbin | `715688e666adf813d0bd087c2dd385f571927d22ec9fae9967db20de3d2b8551` |

硬件UUID为`fd96539d-4cbc-f732-7694-0f0236ca1cf3`。外部平台包同时保留原始与bootgen转换后的二进制，不能因文件长度相同而混用。

原始完整证据包含plan、编译收据、原生调用／关闭记录、逐图CPU/PL输出和Sparse原证据。Git保存小型摘要／CSV；原始tensor和完整证明在独立Release资产中。结果聚合的工具代码也保留，第三方可不执行推理而重新计算精度与时间分布。

## Dense入口修正

首次Dense500尝试在参数解析阶段停止；fix1修正检查入口参数；fix2修正旧Sparse报告引用路径；fix3修正原接口最大64MiB与调用者512MiB请求的冲突，并同步修正最终报告读取。三个失败均在新计算计划创建前停止；没有把它们计为图片验收。

最终成功的外层fix3完整保留于`board/runtime/snapshots/dense500_fix3`。原Dense数学／Native源码及已编译收据没有因这些修正而改变。没有覆盖历史包或修改其SHA来冒充通过。
