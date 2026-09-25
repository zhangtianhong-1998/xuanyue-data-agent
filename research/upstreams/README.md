# 上游源码

源码按固定 commit 研究，当前有 DeepSeek Harness、DSH Desktop、Hermes Agent、Graphic Walker、Perspective、AgentScope、LangGraph 七个独立 Git checkout。它们不是已批准的产品依赖，未执行上游安装/构建脚本。

主仓库追踪 `kernel-sources.json`、`bi-sources.json`、`framework-sources.json` 及验证结果；第三方目录由各自 Git 管理并从主仓库排除。这样设计和自编实验可轻量评审，上游身份仍能复现。

从仓库根运行：

```sh
python3 scripts/upstreams.py
python3 scripts/upstreams.py --fetch
```

第一条只验证现有 checkout；第二条只下载缺失目录并 checkout 固定 commit。已有目录不重置、不覆盖；commit/origin/工作树不匹配会报失败。`--output research/upstreams/verification.json` 可保存结果。

`git fsck --connectivity-only` 通过表示 Git 对象连接检查通过，不表示上游代码安全、能运行或所有依赖许可已确认。浅克隆不包含完整历史；源码定位以清单 commit 为准。
