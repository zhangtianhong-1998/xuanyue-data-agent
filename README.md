# 玄月 Data Agent

这是一个跨平台桌面 Data Agent 项目。先在 Mac 上开发个人可用版本，再验证 Windows 和 Linux。首个业务场景是导入经营数据、发现异常、下钻查看并生成有来源的报告。数据默认留在本地；只有经过授权，才向云模型发送必要内容。

**当前按小增量开发，技术路线评审同步进行；尚无可用客户端。** 产品目标是让用户发起自主 Agent 任务，选择主智能体及其内核；LangGraph 和 AgentScope 都要能承担这个角色。Skill 供 Agent 按需阅读和判断。用户还应能编排规则更固定的工作流，在编排时指定主 Agent 及其内核；固定的是流程步骤，主 Agent 仍可按流程派发子 Agent。Agent 自主运行后调用工作流属于后期候选。可插拔接口和各项能力的实现方式仍需逐步验证。

## 项目目标：让仓库本身容易阅读

第一次打开仓库的人，应能在十分钟内找到产品目标、当前阶段、下一步工作和相关证据。设计文档先讲用户会做什么、系统怎样回应；字段、协议和实验细节放在对应的参考页。每项建议标明状态，旧提案保留历史记录，不让读者误认为已经定案。

先读这三篇，其余文件按遇到的问题查阅：

1. [产品目标与阶段](docs/product/01-product-brief.md)：要做什么，先做到哪一步。
2. [整体架构](docs/architecture/01-candidate-architecture.md)：不同 Agent 内核怎样接入同一个产品。
3. [研发流程](docs/engineering/01-development-process.md)：下一步验证什么，如何记录结果。

想查某个术语、技术细节或实验，请从[设计导航](docs/00-discovery-summary.md)进入。

想知道现在开发到哪一步，打开[开发时间线](docs/engineering/progress.html)。目前只有 AgentScope 主任务和真实模型接入这两段待审的底层代码；时间线里另有实验、仓库整理和未开发候选，不代表这些功能已交付。页面是单文件，本地可直接用浏览器打开，从 GitHub 查看时先下载文件。

## 目录地图

```text
docs/
  product/       产品目标、阶段和用户故事
  architecture/  架构设计与待评审决策
  research/      上游资料、比较和证据解释
  engineering/   研发步骤与文档维护规则
research/
  spikes/        本项目编写的可复现实验
  upstreams/     单独下载的上游源码，不提交整仓
src/xuanyue/    当前产品代码，只有一个 Python 包
tests/           产品代码的测试
scripts/         获取和核验上游源码等脚本
```

## 当前代码与实验

产品代码在 [`src/xuanyue/`](src/xuanyue/)；[第一段测试](tests/test_runtime.py)用脚本模型和合成只读工具跑通 AgentScope 文字根任务。[第二段验收](docs/engineering/02-live-model-smoke.md)用本机火山引擎配置跑通真实模型与合成工具回合。当前仍没有用户界面和业务数据分析。根目录的 `pyproject.toml` 是唯一打包配置。

| 文件 | 当前职责 |
| --- | --- |
| `types.py` | 任务、事件、消息、模型请求等纯数据类型 |
| `interfaces.py` | 主内核、模型、工具三个功能接口 |
| `runtime.py` | 按任务指定的主内核精确分派 |
| `llm.py` | 按模型 ID 精确分派；将文字与工具历史转换为 OpenAI 兼容 Chat Completions 请求 |
| `tools.py` | 本地工具登记、参数校验、逐次授权和执行 |
| `agentscope.py` | 实现主内核接口，转换 AgentScope 的模型、工具和事件 |

`Runtime` 只调用 `AgentKernel`；当前由 `AgentScopeKernel` 实现。它获取模型和工具能力时只调用 `ModelClient`、`ToolService`。以后接入 LangGraph，应实现同一个 `AgentKernel` 接口，而不是让它依附在 AgentScope 之下。

LangGraph 产品适配器、桌面客户端和数据分析还没有实现；真实模型接入目前只验证一个本机合成任务，支持范围限于文字与函数工具，产品事件中的模型用量未知。`interfaces.py` 目前只约定已验证的文字任务运行，不提前声称支持恢复、取消或 A2A。

在仓库根目录运行 `uv venv .venv --python 3.11` 和 `uv pip install --python .venv/bin/python -e '.[agentscope,live-model]'`，再执行 `.venv/bin/python -m unittest discover -s tests -v`。真实模型的脱敏验收命令及结果见[验收记录](docs/engineering/02-live-model-smoke.md)。下一段行为等审阅当前草稿 PR 后再定。

[实验索引](research/spikes/README.md)列出可复现的检查、失败和限制。其中，[跨内核 A2A 委派实验](research/spikes/runtime-interoperability/README.md)用合成输入完成了 LangGraph 固定父流程调用 AgentScope 子任务的 10 项检查；[AgentScope 独立主任务实验](research/spikes/agentscope-primary/README.md)验证了根任务调用本地工具的最短路径。完整任务生命周期、反向委派和两种内核的同任务对照仍未验证。实验通过只说明已测行为，不代表模型质量、产品性能、沙盒隔离或三平台交付已经验收。

普通发布包实验无需下载参考项目。可选运行 `python3 scripts/upstreams.py --fetch` 获取固定版本源码并核验；运行 `python3 scripts/upstreams.py` 只核验已有文件。AgentScope 固定 main 版本的 SOP 预览实验另有源码要求，不能与发布包实验混为一谈。

## Git 与公开范围

设计文档、自编实验、锁文件、合成输入和可公开结果由 Git 管理。第三方整仓、依赖环境、真实业务数据、密钥、运行数据库及用户附件不上传。来源和固定 commit 见[上游说明](research/upstreams/README.md)。

本项目尚未选定开源许可证；上游及依赖许可需分别核验。
