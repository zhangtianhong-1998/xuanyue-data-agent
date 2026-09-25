# Agent 内核路线研究

状态：历史调研证据；其中以 LangGraph 为唯一任务状态机的建议已失效。调研日期：2026-09-24（Asia/Shanghai）；需求修订：2026-09-25。

首版场景已明确为“上传经营数据，发现异常、下钻并生成报告”；数据保存在本地，经用户授权才能向云模型发送必要内容。本文研究内核，不决定完整产品技术栈。

## 1. 本文用途与现行评审

本页保留 2026-09-24 的 DSH、Hermes、Claude SDK 等来源证据。固定版本的三路线比较见[内核选型](../architecture/02-kernel-selection.md)和[对照实验](09-framework-comparison.md)；当前待评审的需求落实方案见[ADR-0004](../architecture/decisions/ADR-0004-dual-primary-kernel.md)。用户已明确 LangGraph、AgentScope 均须可成为主 Agent，本文的历史推荐不能据此替代双向验证。

## 2. 研究方法与证据边界

证据分三类：

- **官方声明**：文档、README、许可证。能说明项目公开承诺或许可范围，不能代替运行验证。
- **源码证据**：阅读锁定 commit 的接口和实现；未启动 DSH/Hermes、未执行它们的安装脚本。
- **本机执行**：只运行 `research/spikes/kernel-contract/` 自编合成实验，使用真实 LangGraph 库，无 LLM/API/业务数据。

已下载三个上游，保留各自 Git 历史，均为 shallow partial clone。`git fsck --connectivity-only` 返回 0，工作树干净；未声明子模块。锁定元数据和根许可证 SHA-256 见 [kernel-sources.json](../../research/upstreams/kernel-sources.json)。根许可证核对不等于所有依赖、字体、模型、商标均已完成审查。

| 对象 | 正式来源与锁定版本 | 本次范围 |
| --- | --- | --- |
| DeepSeek Harness | [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)，`46a7f68b0922371ce7144b668b90e377d8e799f4`，根 manifest `0.1.7-rc.1` | clone；读源码、README、根 LICENSE；未构建 |
| DSH Desktop | [dataelement/dsh-desktop](https://github.com/dataelement/dsh-desktop)，`69705b23117801389aafb1eab35055dd20744312`，根 manifest `0.1.1` | clone；桌面宿主边界；manifest 版本不等于已发布安装包版本 |
| Hermes Agent | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)，`c9dca726514b709cf6e677d236a79fc8d0627f37`，Python manifest `0.21.4` | clone；委派、memory、MCP、TTS 源码；未构建 |
| Claude Agent SDK Python | [anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python)，`dce7cdac8276c004e08f4d94acffbf85dfbbd116` | 远端 HEAD 与固定 commit LICENSE；官方 SDK 文档；未 clone/运行 |
| Claude Code 公共仓库 | [anthropics/claude-code](https://github.com/anthropics/claude-code)，`56f36532530f88b572854538d685fcf781141e8c` | 固定 commit LICENSE；不读取泄漏源码 |
| LangGraph | [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)，LICENSE 核对 HEAD `bdb85b5aa87a21de68371d2e534b81aeed398f57`；实验包 `1.2.12` | 官方文档与根 LICENSE；PyPI 锁定版本本机实验。HEAD 不代表实验包 commit |
| Pydantic AI | [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai)，LICENSE 核对 HEAD `150ccddb9420f2ee87dd54aa3a87d4c1706b6390` | 官方文档与固定 commit LICENSE；未安装/运行，不把滚动文档功能视为旧版本已具备 |

## 3. DSH：先区分桌面宿主与 Agent 内核

**事实。** `dataelement/dsh-desktop` 是独立社区桌面应用；正式 Agent 内核上游是 `deepseek-ai/deepseek-harness`。两者根 LICENSE 均为 MIT，但各自依赖和商标约束独立。桌面仓库当前 README 明确 Linux、Windows ARM64 尚不支持，所以不能以“跨平台”标题推导三个目标平台已经可交付。[D1][D2]

**源码证据。** DeepSeek Harness 用 TypeScript 定义统一的子 Agent 能力声明、请求、输出和停止原因；不支持的调用能力应在开始前拒绝。委派深度从持久化 session header 读取，并与运行时深度取较大值，避免恢复任务后绕过深度限制。MCP 连接管理有重连上限；skills 通过提供方注册表区分摘要和完整内容。[D3][D4][D5][D6]

**已知边界。** 子 Agent 文档明确描述进程内所有权、缺少持久化父级邮箱，以及已接受但未写入日志的消息在崩溃时可能丢失。这说明“能恢复会话”与“所有协作消息可靠投递”是两项要求，不能合并验收。[D7]

**可借鉴。** 能力不满足时显式报错；模型路由、工具权限与子 Agent 生命周期分开；持久化深度；可枚举子任务；桌面宿主启动/停止独立 runtime。Electron renderer 的 `contextIsolation`、`nodeIntegration: false`、`sandbox` 是可见实现，但这不证明 Agent 运行的系统命令受到同等沙箱限制。[D8]

**建议。** 如果后续确认希望快速获得通用文件、代码和插件操作，可增做 DSH SDK/插件方式的最小数据分析扩展。实验应验证“只允许注册数据工具”的配置是否在所有子 Agent 后端仍成立，不能只测试父 Agent 的工具列表。

## 4. Hermes：参考长期使用体验，谨慎承接默认执行面

**事实。** Hermes Agent 正式上游为 NousResearch，根 LICENSE 为 MIT。本次 Python manifest 为 `0.21.4`。委派配置默认深度为 1；并发、一次性任务的子任务总量、子任务超时都有独立配置。[H1][H2]

**源码证据。** 子 Agent 危险操作默认拒绝；代码也允许配置自动批准。注册晚到的子 Agent 时会补传父任务已发出的停止信号，处理“父任务已停止，子任务刚启动”的竞争窗口。memory 有提供方生命周期接口，内置文件 memory 写入会加锁、重新读取，并检查外部修改。[H2][H3][H4][H5]

**源码证据。** MCP 传输实现含 stdio、HTTP/SSE、OAuth 等适配；TTS 也拆成多服务和本地实现。源文件列出协议探测版本，不能从“支持 MCP”直接推出与本项目未来服务的协议版本兼容。[H6][H7]

**可借鉴。** 日常偏好、会话回溯、任务经验转 skills 的产品机制；子任务进度、取消传播和 TTS 配置结构。

**建议。** 分析结果先保存为有来源的 evidence artifact，再决定是否提升为记忆。只把用户确认的指标口径、偏好和已验证方法写入长期记忆。一个“这次促销可能导致下降”的模型判断，不应自动变成下一次分析的业务事实。Hermes 的通用能力不能代替我们对数据快照、指标版本和分析证据的管理。

## 5. Claude Code / Claude Agent SDK：可集成，不能当作完整开放内核

**事实。** Python SDK 仓库根许可证是 MIT；Claude Code 公共仓库许可证保留全部权利，并指向商业服务条款。官方 SDK 说明明确把 SDK 描述为运行 Claude Code binary 的库。SDK 包的开放许可不覆盖其调用的全部组件。[C1][C2][C3]

**官方声明。** SDK 提供工具、hooks、子 Agent、MCP、权限、session 恢复、skills 等功能；第三方产品不得在未经批准时提供 claude.ai 登录或借用其额度，文档引导使用 API key。实际使用受适用条款约束。[C3]

**建议。** 可把它作为可选的专用执行后端，或用于开发流程；本项目若要求多供应商、本地模型和自定义治理，不宜把它设为唯一不可替换的内核。产品的外发授权、证据仓库和任务状态应由自己的 runtime 管理。这里仅记录上游许可文本和产品约束，不做所有分发方式均可商用的结论。

## 6. LangGraph 与 Pydantic AI：分别看流程控制与类型合同

**官方声明。** LangGraph 关注有状态的长期执行、持久化、流式输出与人工介入；可独立于 LangChain 使用。checkpointer 保存单任务状态，store 管理跨任务信息。恢复 `interrupt()` 时节点从头执行，因此节点前半段的外部作用必须可重放。[L1][L2][L3]

**本机执行。** 实验采用 `langgraph==1.2.12`、`langgraph-checkpoint-sqlite==3.1.1` 和 Python 3.12.13，8 项检查通过，详见下一节。没有使用 LangSmith 云服务，也没有产生模型网络请求。

**官方声明。** Pydantic AI 提供有类型的 agent/工具/输出、多供应商适配、MCP 和多 Agent 模式；当前滚动文档还包含 subagents、skills、memory、持久化执行等模块。委派文档要求传递或汇总 usage，说明了整棵任务树取消的方式。其根 LICENSE 是 MIT。[P1][P2][P3]

**当时建议，现已失效。** 2026-09-24 曾提出由 LangGraph 管理唯一的任务状态机、将其他框架放在其节点内。这与后来明确的“双内核都可担任主 Agent”需求不符，不再作为实施依据。LangGraph 的持久化实验证据仍可复用；AgentScope 担任主 Agent、委派 LangGraph 子 Agent，以及用户工作流由谁调度，需要按[ADR-0004](../architecture/decisions/ADR-0004-dual-primary-kernel.md)另行验证。

## 7. 已完成的无模型实验

路径：[research/spikes/kernel-contract](../../research/spikes/kernel-contract/README.md)。脚本、依赖锁和合成结果均进入主仓库；依赖环境和临时运行数据库不进入。

| 检查 | 结果 | 能说明什么 |
| --- | --- | --- |
| 两个分析分支并行并汇合 | 通过 | barrier 相遇、线程不同、时间区间重叠；每分支结果可见 |
| 写报告前暂停 | 通过 | 保存审批上下文与待运行节点 |
| 退出后在新 Python 进程恢复 | 通过 | 当前包版本的 SQLite checkpoint 可跨进程使用 |
| 完成的分支不重复产出 | 通过 | 此图与此恢复点的结果没有被重复追加 |
| 拒绝审批结束 | 通过 | 拒绝分支无报告标记 |
| 在报告外部作用提交后突然退出 | 通过 | 故障注入退出码为 73，新进程重跑报告节点并完成 |
| 重放时报告效果只保留一次 | 通过 | 应用的唯一键有效；节点尝试两次、效果一份 |
| 拒绝任务没有外部效果 | 通过 | 独立模拟作用账本无该任务报告 |

**限制。** 这不是 Agent 思考或统计质量测试；两个分析节点只返回合成常量。不是完整崩溃恢复证明；只覆盖指定节点边界和故障点。未测 OS 级隔离、断电、磁盘损坏、Windows/Linux、迁移、取消传播、MCP、记忆准确性，也未验证 AgentScope 作为主 Agent。尤其不能把应用自己实现的幂等键写成 LangGraph 自动保障所有远程动作 exactly-once。

## 8. 详细设计与后续验证

[核心对象](../architecture/03-core-contracts.md)、[工作流/轨迹](../architecture/04-workflow-and-trace.md)、[画像](../architecture/06-profile-memory.md)仍作为设计草案阅读；双主 Agent 要求与下一阶段门见[ADR-0004](../architecture/decisions/ADR-0004-dual-primary-kernel.md)。本页的上游观察是研究证据，不对尚在重审的架构作批准。

## 11. 证据索引

以下链接访问日期均为 2026-09-24。源码链接锁定 commit，路径行号对应本机读取位置；文档链接为上游滚动文档。

- **D1**：[DSH Desktop README.md:102–133](https://github.com/dataelement/dsh-desktop/blob/69705b23117801389aafb1eab35055dd20744312/README.md#L102-L133)：平台与独立社区声明；[LICENSE](https://github.com/dataelement/dsh-desktop/blob/69705b23117801389aafb1eab35055dd20744312/LICENSE)。
- **D2**：[DeepSeek Harness package.json:1–10](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/package.json#L1-L10)；[LICENSE](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/LICENSE)。
- **D3**：[packages/subagent/subagent/src/types.ts:119–163](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/subagent/subagent/src/types.ts#L119-L163)：能力声明与取消信号。
- **D4**：[packages/subagent/subagent/src/depth.ts:18–35](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/subagent/subagent/src/depth.ts#L18-L35)：持久化深度。
- **D5**：[packages/mcp/mcp-client/src/connection.ts:1–45](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/mcp/mcp-client/src/connection.ts#L1-L45)：连接管理与重试上限。
- **D6**：[packages/skill/skill/src/index.ts:1–10](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/skill/skill/src/index.ts#L1-L10) 及 [56–91](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/skill/skill/src/index.ts#L56-L91)：skill 提供方、摘要与正文。
- **D7**：[packages/subagent/subagent/README.md:180–195](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/subagent/subagent/README.md#L180-L195)：上游明确披露的恢复和投递限制。
- **D8**：[DSH Desktop src/main/index.ts:523–531](https://github.com/dataelement/dsh-desktop/blob/69705b23117801389aafb1eab35055dd20744312/src/main/index.ts#L523-L531)；[src/main/runtime/harness-runtime.ts:521–568](https://github.com/dataelement/dsh-desktop/blob/69705b23117801389aafb1eab35055dd20744312/src/main/runtime/harness-runtime.ts#L521-L568)：renderer 与 runtime 进程边界。
- **H1**：[Hermes LICENSE](https://github.com/NousResearch/hermes-agent/blob/c9dca726514b709cf6e677d236a79fc8d0627f37/LICENSE)；[pyproject.toml:1–15](https://github.com/NousResearch/hermes-agent/blob/c9dca726514b709cf6e677d236a79fc8d0627f37/pyproject.toml#L1-L15)。
- **H2**：[tools/delegate_tool_config.py:17–59](https://github.com/NousResearch/hermes-agent/blob/c9dca726514b709cf6e677d236a79fc8d0627f37/tools/delegate_tool_config.py#L17-L59) 及 [85–148](https://github.com/NousResearch/hermes-agent/blob/c9dca726514b709cf6e677d236a79fc8d0627f37/tools/delegate_tool_config.py#L85-L148)：预算、并发和默认审批行为。
- **H3**：[tools/delegate_tool_child_run.py:63–99](https://github.com/NousResearch/hermes-agent/blob/c9dca726514b709cf6e677d236a79fc8d0627f37/tools/delegate_tool_child_run.py#L63-L99)：晚到子任务的取消传播。
- **H4**：[agent/memory_provider.py:84–122](https://github.com/NousResearch/hermes-agent/blob/c9dca726514b709cf6e677d236a79fc8d0627f37/agent/memory_provider.py#L84-L122)：记忆提供方合同。
- **H5**：[tools/memory_tool_store.py:235–258](https://github.com/NousResearch/hermes-agent/blob/c9dca726514b709cf6e677d236a79fc8d0627f37/tools/memory_tool_store.py#L235-L258)：内置文件记忆写入检查。
- **H6**：[tools/mcp_tool_transport.py:1–26](https://github.com/NousResearch/hermes-agent/blob/c9dca726514b709cf6e677d236a79fc8d0627f37/tools/mcp_tool_transport.py#L1-L26)：传输模块与探测请求。
- **H7**：[tools/tts_tool_providers.py:1–7](https://github.com/NousResearch/hermes-agent/blob/c9dca726514b709cf6e677d236a79fc8d0627f37/tools/tts_tool_providers.py#L1-L7)：TTS 后端分离。
- **C1**：[Claude Agent SDK Python LICENSE](https://github.com/anthropics/claude-agent-sdk-python/blob/dce7cdac8276c004e08f4d94acffbf85dfbbd116/LICENSE)。
- **C2**：[Claude Code LICENSE.md](https://github.com/anthropics/claude-code/blob/56f36532530f88b572854538d685fcf781141e8c/LICENSE.md)。
- **C3**：[Claude Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)：SDK/binary 边界、功能、身份验证和适用条款。
- **L1**：[LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)；[MIT LICENSE](https://github.com/langchain-ai/langgraph/blob/bdb85b5aa87a21de68371d2e534b81aeed398f57/LICENSE)。
- **L2**：[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。
- **L3**：[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：恢复会重跑节点开头的代码。
- **P1**：[Pydantic AI overview](https://pydantic.dev/docs/ai/overview/)。
- **P2**：[Pydantic AI multi-agent applications](https://pydantic.dev/docs/ai/guides/multi-agent-applications/)。
- **P3**：[Pydantic AI MIT LICENSE](https://github.com/pydantic/pydantic-ai/blob/150ccddb9420f2ee87dd54aa3a87d4c1706b6390/LICENSE)。
- **M1**：[MCP specification 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28)。
- **S1**：[Agent Skills specification](https://agentskills.io/specification)。
