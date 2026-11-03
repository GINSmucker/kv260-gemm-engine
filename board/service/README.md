# Sparse24板端服务源码

`deployment/`保存当前KV260上实际运行的应用源码与配套systemd单元，文件身份见[source_manifest.json](deployment/source_manifest.json)。`backend.py`、`cpu_optimizations.py`和`install_python_update.py`是等价CPU处理的维护入口；日常使用以下完整部署目录。

| 位置 | 职责 |
|---|---|
| deployment/private/backend.py | 硬件监督、当前启动自检、模型会话与持久化回执 |
| deployment/private/common.py、management.py | 请求编号、持久化、Unix socket帧和owner管理协议 |
| deployment/private/loader.py、loader_source/ | 已安装固件、驱动及当前启动身份检查 |
| deployment/private/model_runtime.py、source/ | PS模型调用、Native owner/client及整数计算支持源码 |
| deployment/private/activate.py、units/ | 服务启用入口与实际systemd配置 |
| deployment/web/web.py、common.py、static/ | 非特权HTTP服务及板端网页、统计和配图 |

部署路径为`/opt/opt50-demo-v1`，状态与回执位于`/var/lib/opt50-demo-v1`，当前状态文件为`/run/opt50-demo/status.json`。硬件服务沿用`/home/ubuntu/fpga_project/model_b/.venv/bin/python`；HTTP服务使用`/usr/bin/python3`，监听8080端口。参考环境为Ubuntu24.04.2/aarch64、XRT2.18、g++13、ORT1.22.1、NumPy1.26.4及Pillow。

在已有展示板卡上，上电后由`opt50-demo-hardware.service`与`opt50-demo-web.service`启动应用。Windows电脑访问`http://192.168.137.2:8080/#overview`；连接、JPEG提交、结果下载及关机见[板端使用指南](../../docs/boot-demo.md)。本机Node工作台另见[工作台说明](../../docs/workbench.md)。

维护人员可使用以下只读命令检查当前状态：

```sh
cat /run/opt50-demo/status.json
systemctl show opt50-demo-hardware.service opt50-demo-web.service -p ActiveState -p SubState -p MainPID
sudo journalctl -b -u opt50-demo-hardware.service -n 30 --no-pager -o cat
```

等待`READY`且未忙碌后再单次提交图片。请求编号、模型、输入SHA和当前boot绑定；断线时查询原编号，未知状态不自动重试或复位。服务入口保留现有的身份、独占和预算检查。

在仓库根目录运行`python -B -m unittest discover -s tests -v`，可检查部署文件身份、安装清单中的源码、Python依赖、网页资源和请求帧。第三方头文件声明见[board/licenses](../licenses/README.md)。
