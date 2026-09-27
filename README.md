# 玄月 Data Agent

这是一个跨平台桌面 Data Agent 项目。先在 Mac 上开发个人可用版本，再验证 Windows 和 Linux。首个业务场景是导入经营数据、发现异常、下钻查看并生成有来源的报告。数据默认留在本地；只有经过授权，才向云模型发送必要内容。

**当前按小增量开发，技术路线评审同步进行；已有本机界面预览，尚无可用的经营数据分析客户端。** 产品目标是让用户发起自主 Agent 任务，选择主智能体及其内核；LangGraph 和 AgentScope 都要能承担这个角色。Skill 供 Agent 按需阅读和判断。用户还应能编排规则更固定的工作流，在编排时指定主 Agent 及其内核；固定的是流程步骤，主 Agent 仍可按流程派发子 Agent。Agent 自主运行后调用工作流属于后期候选。可插拔接口和各项能力的实现方式仍需逐步验证。

## 项目目标：让仓库本身容易阅读

第一次打开仓库的人，应能在十分钟内找到产品目标、当前阶段、下一步工作和相关证据。设计文档先讲用户会做什么、系统怎样回应；字段、协议和实验细节放在对应的参考页。每项建议标明状态，旧提案保留历史记录，不让读者误认为已经定案。

先读这三篇，其余文件按遇到的问题查阅：

1. [产品目标与阶段](docs/product/01-product-brief.md)：要做什么，先做到哪一步。
2. [整体架构](docs/architecture/01-candidate-architecture.md)：不同 Agent 内核怎样接入同一个产品。
3. [研发流程](docs/engineering/01-development-process.md)：下一步验证什么，如何记录结果。

想查某个术语、技术细节或实验，请从[设计导航](docs/00-discovery-summary.md)进入。

想知道现在开发到哪一步，打开[开发时间线](docs/engineering/progress.html)。本机界面预览已能保存项目、会话和公开执行记录；当前切片让用户随文字附一张 PNG/JPEG 图片交给所选主内核。工具仍只有纯计算样例，经营数据文件分析和正式桌面安装包尚未开发。时间线把已写代码、实验和未开发候选分开列出；页面是单文件，本地可直接打开。

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
src/xuanyue/    当前产品 Python 包；llm/ 放模型接入，engines/ 放引擎接入
desktop/        React 界面与 Electron 开发窗口
tests/           产品代码的测试
scripts/         获取和核验上游源码等脚本
```

## 当前代码与实验

产品代码在 [`src/xuanyue/`](src/xuanyue/)；[第一段测试](tests/test_runtime.py)用脚本模型和合成只读工具跑通 AgentScope 文字根任务。[第二段验收](docs/engineering/02-live-model-smoke.md)用本机模型跑通真实文字与工具回合。[双主内核与模型配置](docs/engineering/03-dual-kernel-and-model.md)说明两种框架的接入范围。[本机界面预览](docs/engineering/04-local-session-ui.md)保存项目、会话、公开事件和图片附件。业务数据分析尚未开发。

| 文件 | 当前职责 |
| --- | --- |
| `types.py` | 任务、事件、文字和图片消息、模型请求等数据类型 |
| `interfaces.py` | 主内核、模型、工具三个功能接口 |
| `runtime.py` | 按任务指定的主内核精确分派 |
| `engines/registry.py` | 登记内置及已安装扩展引擎，按名称创建一个主引擎 |
| `engines/agentscope.py` | 转换 AgentScope 的模型、工具和公开事件 |
| `engines/langgraph.py` | 转换 LangGraph 的模型、工具和公开事件 |
| `config.py` | 从 TOML 选择供应商与模型，从环境或 `.env` 读取密钥 |
| `llm/router.py` | 将产品模型 ID 绑定到协议客户端和供应商实际模型名 |
| `llm/openai_compatible.py` | 将文字、图片和工具历史转换为 OpenAI 兼容 Chat Completions 请求 |
| `tools.py` | 本地工具登记、参数校验、逐次授权和执行 |
| `storage.py` | 将项目、会话、运行和公开事件存入本机 SQLite；图片写入本机附件目录 |
| `chat.py` | 读取已完成问答，按会话指定的内核与模型运行并保存事件 |
| `server.py` | 向本机界面提供项目、会话、图片附件和运行记录接口 |

`engines/` 与 `llm/` 按接入职责分目录。`AgentScopeKernel` 和 `LangGraphKernel` 都实现 [`AgentKernel`](src/xuanyue/interfaces.py)。`EngineRegistry` 按名称构造选定引擎；`Runtime` 按 `Task.kernel` 精确派发。新引擎可通过安装包入口点登记；[接入方法与验证范围](docs/engineering/03-dual-kernel-and-model.md#增加一个-agent-引擎)集中说明。

### 在本机界面查看会话与执行记录

构建 `desktop/` 后运行 `.venv/bin/xuanyue-app`，在 `http://127.0.0.1:8787/` 创建项目和会话、选择主内核与模型并连续提问。模型明确开启 `image_input` 时，每轮可附一张不超过 5 MiB 的 PNG/JPEG 图片。服务把附件留在本机，只将运行所需的图片发给所选模型。安装、配置、启动命令和范围见[本机界面预览](docs/engineering/04-local-session-ui.md)。

真实模型的服务地址和上游模型名从被 Git 忽略的 `xuanyue.toml` 读取，可从 [`xuanyue.example.toml`](xuanyue.example.toml) 复制后填写；密钥只放环境变量或配置同目录的 `.env`。配置字段见[双主内核与模型配置](docs/engineering/03-dual-kernel-and-model.md#配置真实模型)。此前用于验收的终端 CLI 已移除；本机界面是当前入口。

### 模型接入边界

`ModelClient` 是产品的模型调用接口。`ModelRouter` 用任务指定的产品模型 ID 找到客户端和供应商实际模型名；同名的上游模型也可登记为不同的产品选项。当前只有 OpenAI 兼容的 Chat Completions 适配器；[OpenAI 工具调用说明](https://developers.openai.com/api/docs/guides/function-calling)给出了该协议的消息往返。

两种主内核都使用同一个 `Task`、`ModelClient` 和 `ToolService`。AgentScope 通过 `_AgentScopeModel(ChatModelBase)` 接入；LangGraph 通过 `_ProductChatModel(BaseChatModel)` 接入。模型请求都交给 `ModelRouter`，再到当前唯一的 `ChatCompletionsClient`。两套 Agent 循环各自由原框架执行；我们没有复用它们内置的供应商模型客户端。

[上一段 AgentScope 本机验收](docs/engineering/02-live-model-smoke.md)只证明 AgentScope 2.0.8 的文字与函数工具回合可运行。当前模型桥已接入可见文字流和用户图片输入，不返回实测用量，也不支持完整供应商参数。桥依赖该版本的 `_call_api` 扩展点，升级要重新验证。依据为 AgentScope 的[模型接口说明](https://doc.agentscope.io/tutorial/task_model.html)和固定版本[模型基类源码](https://github.com/agentscope-ai/agentscope/blob/v2.0.8/src/agentscope/model/_base.py)。

LangGraph 适配器使用 LangChain 1.4.0 的 [`create_agent`](https://docs.langchain.com/oss/python/langchain/agents) 构建 LangGraph Agent，并用 `BaseChatModel` 桥接产品的 `ModelClient`。当前已验证文字流、用户图片请求和函数工具的短回合；同步模型调用、持久检查点和历史节点恢复都未接入。原先研究的 `langgraph.prebuilt.create_react_agent` 已标记弃用，因此产品代码没有使用该入口。具体范围见[双主内核与模型配置](docs/engineering/03-dual-kernel-and-model.md)。

Claude 原生 Messages 也是后续候选，需要独立的供应商协议适配器。Claude 的 `system`、`tool_use`、`tool_result` 与 Chat Completions 的消息格式不同；其 OpenAI 兼容层[官方说明有字段和能力限制](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)，不能只换 `base_url` 就声称兼容。[Claude Messages API](https://platform.claude.com/docs/en/api/messages/create)是协议依据。涉及 thinking 的续跑材料如何保存，需要另行评审；现有产品消息只记录公开内容。协议资料查阅于 2026-09-27。

桌面界面目前只是本机开发预览，数据分析和三平台安装包还没有实现。当前接口只覆盖已接入的文字、单张用户图片和函数工具请求；恢复、取消或 A2A 仍待验证。

安装上述 Python 依赖后，运行 `.venv/bin/python -m unittest discover -s tests -v` 可验证产品代码。下一段行为等审阅当前草稿 PR 后再定。

[实验索引](research/spikes/README.md)列出可复现的检查、失败和限制。其中，[跨内核 A2A 委派实验](research/spikes/runtime-interoperability/README.md)用合成输入完成了 LangGraph 固定父流程调用 AgentScope 子任务的 10 项检查；[AgentScope 独立主任务实验](research/spikes/agentscope-primary/README.md)验证了根任务调用本地工具的最短路径。现在两种内核也已通过同一文字与函数工具任务的代码对照，但完整任务生命周期和反向委派仍未验证。局部通过不代表模型质量、产品性能、沙盒隔离或三平台交付已经验收。

普通发布包实验无需下载参考项目。可选运行 `python3 scripts/upstreams.py --fetch` 获取固定版本源码并核验；运行 `python3 scripts/upstreams.py` 只核验已有文件。AgentScope 固定 main 版本的 SOP 预览实验另有源码要求，不能与发布包实验混为一谈。

## Git 与公开范围

设计文档、自编实验、锁文件、合成输入和可公开结果由 Git 管理。第三方整仓、依赖环境、真实业务数据、密钥、运行数据库及用户附件不上传。来源和固定 commit 见[上游说明](research/upstreams/README.md)。

本项目尚未选定开源许可证；上游及依赖许可需分别核验。
