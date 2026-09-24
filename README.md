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
python3 scripts/upstreams.py
```

源码清单见 [上游说明](research/upstreams/README.md)。实验通过只证明文档声明的局部行为，不代表模型分析质量、产品性能或三系统已完成验收。

## 版本管理

设计文档、自编实验代码、锁文件、合成输入与结果由主仓库管理。外部上游分别保留 Git，主仓库记录固定 commit 与获取脚本。未上传远程仓库，未选择本产品的开源许可证；上游许可见各自清单。
