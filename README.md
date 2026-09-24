# 玄月 Data Agent

一个面向经营数据分析的跨平台桌面产品研究项目。当前处于需求与技术路线评审阶段，尚无可运行的完整客户端。

已确认：个人使用优先；本地文件导入；本地保存数据；必要内容经用户授权后调用云模型。后续覆盖多 Agent、MCP、Skills、Memory、模型能力适配、TTS、分析工作流和深度研究；本体/语义层只预留接入点。

## 从这里阅读

1. [第一轮规划与技术路线评审](docs/00-discovery-summary.md)
2. [产品需求](docs/product/01-product-brief.md) 与 [用户故事](docs/product/02-user-stories.md)
3. [候选架构](docs/architecture/01-candidate-architecture.md)
4. [实验结果与边界](docs/research/04-experiment-results.md)
5. [研发流程与目录约定](docs/engineering/01-development-process.md)

## 复现实验

所有数据均为合成数据；锁文件固定依赖，首次运行可能需要下载依赖。无需模型密钥。

```sh
uv run --project research/spikes/data-analysis-contract --frozen --python 3.12 research/spikes/data-analysis-contract/run.py
uv run --project research/spikes/kernel-contract --frozen --python 3.12 research/spikes/kernel-contract/run_experiment.py --output research/spikes/kernel-contract/results/local-rerun.json
uv run --project research/spikes/mcp-local-contract --frozen --python 3.12 research/spikes/mcp-local-contract/run.py
```

上述实验不需要下载参考项目源码。若需要复核调研中的源码，可选执行 `python3 scripts/upstreams.py --fetch`，下载清单中的固定版本并验证；已有源码时用 `python3 scripts/upstreams.py` 只做验证。源码清单见 [上游说明](research/upstreams/README.md)。

实验通过只证明文档声明的局部行为，不代表模型分析质量、产品性能或三系统已完成验收。

## 版本管理

公开内容包括设计文档、自编实验代码、锁文件、合成输入与结果。第三方源码、依赖环境、构建产物、密钥和真实业务数据不随主仓库上传；参考上游仅保留固定 commit、来源和获取脚本。

本项目尚未选定开源许可证；参考上游的许可见各自清单。
