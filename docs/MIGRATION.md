# 文件定位与资产使用

当前Git按职责组织：hls为计算核，model为模型与适配，board为平台/运行时/UI，docs为共享说明，results为公开证据与摘要。源码与运行身份见[SOURCE_MAP](SOURCE_MAP.json)，兼容路径映射由[MIGRATION_MAP](MIGRATION_MAP.json)提供。

默认开发源码直接位于Git。OPT45硬件源位于opt45-source；当前B/C源和完整研究运行文件由ps-runtime增量与opt45-runtime依赖组装，完整当前性能样本与验证摘要位于ps-evidence。资产名称、URL、SHA与源manifest见[锁](locks/ASSETS.lock.json)。

缓存根为build/assets，各asset解包拒绝覆盖已有目录；固定tag中的文件位置由对应tag定义。既有版本链接与机器映射用于定位相应身份，不能将其中回执当作实时就绪配置。

```powershell
python -B docs/tools/assets.py opt45-source --download --extract
python -B docs/tools/assets.py opt45-runtime --download --extract
python -B docs/tools/assets.py ps-runtime --download --extract
python -B docs/tools/assets.py ps-evidence --download --extract
python -B docs/tools/assets.py opt45-evidence --download --extract
```

默认OPT38与研究OPT45各自采用完整匹配的owner、插件、graph、metadata和平台。共享接口与运行约束见[interface](interface.md)；清单文件不授予板卡加载权限。
