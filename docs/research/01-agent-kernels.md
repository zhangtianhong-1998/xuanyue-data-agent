# Agent 内核路线研究

状态：候选方案，待评审。调研日期：2026-09-24（Asia/Shanghai）。

首版场景已明确为“上传经营数据，发现异常、下钻并生成报告”；数据保存在本地，经用户授权才能向云模型发送必要内容。本文研究内核，不决定完整产品技术栈。

## 1. 当前建议

**建议优先验证独立 Python runtime，以 LangGraph 承担可持久化的分析流程，数据操作使用有类型的工具合同。** 模型调用放在可替换适配器后面；Pydantic AI 是模型、工具与结构化结果层的备选，需与直接调用供应商 SDK 的路线做一次小型对照，不在首轮同时引入两套完整 Agent 循环。

这个判断来自产品所需的确定性计算、审批与恢复，及本次真实库实验。它不表示 LangGraph 已通过多模型质量、三平台分发或安全验收。

DSH 和 Hermes 适合作为产品及内核设计参考，也保留直接采用其内核的备选。当前不建议先 fork 一个完整通用助手，再把数据分析作为附加工具：那会同时引入通用终端、插件运行、会话兼容、供应商适配和桌面升级的维护负担。这是本项目的取舍建议，不是对上游质量的评价。

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

**建议。** 首轮由 LangGraph 管理唯一的任务状态机；如果选择 Pydantic AI，用在有限节点中的模型调用、结构化输出与工具校验。不要先叠加 LangGraph、Pydantic Graph 和另一套长期任务引擎。若后续发现 LangGraph 状态复杂度超过收益，再用同一组合同实验比较 Pydantic AI 的持久化方案。

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

**限制。** 这不是 Agent 思考或统计质量测试；两个分析节点只返回合成常量。不是完整崩溃恢复证明；只覆盖指定节点边界和故障点。未测 OS 级隔离、断电、磁盘损坏、Windows/Linux、迁移、取消传播、MCP、记忆准确性。尤其不能把应用自己实现的幂等键写成 LangGraph 自动保障所有远程动作 exactly-once。

## 8. 候选路线比较

以下是基于上述证据的项目判断，不是框架跑分。

| 路线 | 对本项目的收益 | 主要代价 | 本轮判断 |
| --- | --- | --- | --- |
| Python + LangGraph + 自有工具合同 | 数据计算与统计库可直接调用；流程、审批、证据结果可分离 | 桌面侧需 Python runtime 打包、升级与进程通信；模型适配仍要做 | 优先验证 |
| Python + Pydantic AI / 其持久化方案 | 有类型模型输出、依赖注入和 provider 适配集中 | 恢复、审批 UI 和外部作用还需端到端验证；版本变化需锁定 | 模型层候选 / runtime 对照 |
| TS + DSH 插件或 SDK | 通用 Agent、skills、会话、插件与 UI 参考较完整 | 接入分析库仍可能需要 Python；预览期 API 与子任务后端差异 | 第二路线，先小实验 |
| Python + Hermes 改造 | memory、skills、委派和 TTS 设计丰富 | 通用助手依赖与权限面较大，分析领域合同要补 | 参考设计；直接采用需额外评估 |
| TS 全栈 + 自建 loop / TS 编排库 | 桌面、UI、协议可共用类型与工具链 | 自建调度和持久化成本；复杂统计通常仍需 Python worker | 用户明确要求单语言时再对照 |
| Claude Agent SDK 为唯一内核 | 容易接入成熟 Claude Code 行为 | 二进制与服务条款边界、多供应商需求、运行时可控性 | 不作为唯一内核；保留可选后端 |

Python runtime 不必等于“用户安装 Python”。候选产品应打包固定 runtime，桌面程序管理它的生命周期。启动、退出、崩溃重启、版本迁移和 native 依赖必须在三平台分别验证；本轮尚未完成。

## 9. 本项目应自行掌握的核心合同

### 9.1 执行状态与证据

建议定义 `Run`、`Step`、`ToolCall`、`Artifact`、`DatasetSnapshot`、`Approval`、`Usage`。每项工具调用绑定任务 ID、父任务 ID、输入哈希、数据快照、工具版本和幂等键。公开事件只记录计划、参数、返回、状态、来源与异常，不保存或展示模型隐藏推理。

推荐任务状态：`queued → running → waiting_for_user / paused → completed / failed / cancelled`。失败与拒绝审批分开；超预算、用户取消、工具异常、模型拒绝也分别保留。恢复先检查代码、工具 schema、输入快照与审批范围是否仍匹配；不匹配则显式要求重新确认或重跑相关步骤。

图表、下钻查询和报告引用同一个 evidence artifact。模型只能基于工具已计算的结果写解释；报告保留过滤条件、指标定义、比较区间、样本量和缺失情况。统计分解输出标记为“贡献”，因果结论需额外设计和证据。

### 9.2 多 Agent 协作

首轮建议“主分析 Agent + 按需专家任务”，先限于两个并发子任务、一级委派；这些数值是待评审起点，可配置。角色可包括数据检查、维度分析、报告复核，但角色名称不代替明确输入输出。

| 合同 | 建议行为 | 验收场景 |
| --- | --- | --- |
| 任务预算 | 顶层统一记账，限制 tokens、模型请求、工具次数、并发、运行时间与金额；启动子任务先预留额度 | 并发子任务不能分别拿到完整父预算 |
| 权限继承 | 子任务权限是父权限的子集，工具运行前由宿主再次检查 | 子任务不能通过新 MCP、skill 或本地文件扩大范围 |
| 取消 | 父取消向子任务、模型流、工具进程传播，超时后终止工作进程 | 取消与新子任务创建发生竞争也不会遗留后台任务 |
| 状态恢复 | SQLite checkpoint 加任务事件；有作用工具做幂等、可查询状态或补偿 | 导出到一半、模型超时、进程退出后可解释地继续 |
| 输出汇合 | 返回结构化结论、证据引用和缺口；冲突保留为冲突 | 两个子 Agent 指标口径不同时禁止直接拼接结论 |

### 9.3 MCP 与后续语义服务

截至访问日，MCP `latest` 重定向至 `2026-07-28`；协议概述包含按请求协商等变化。不能在设计中把旧握手模型写死为唯一规范。先以目标 SDK 实际支持的版本建立本地测试矩阵，记录协议版本、transport、扩展与服务端能力。[M1]

产品侧管理服务安装/配置、凭证引用、工具发现、授权、取消、超时、断连与结果大小限制。MCP tools/resources/prompts 的内容带来源，服务的只读声明不自动等于可信权限证明。stdio MCP 本身是本机进程，也必须受到可执行文件、环境变量和工作目录约束。

预留 `SemanticContextProvider`：返回指标与实体定义、适用范围、版本、约束、来源引用及未解析状态；本轮不实现语义层。将来接入本体服务时，仍需绑定到当前数据快照，不能以本体定义代替数据质量验证。

### 9.4 Skills

兼容 Agent Skills 的目录与 `SKILL.md` 格式；先加载摘要，命中后加载正文和必要资源。记录版本/hash、来源和执行依赖。规范中的 `allowed-tools` 仍为实验字段，不能依赖它作为系统安全边界；真正授权在宿主工具层执行。[S1]

首版先提供经过测试的内置分析 skills，例如“销售变动贡献拆解”“缺失与重复检查”“报告证据复核”。自动产生的 skill 先进入候选区，用户审阅或回归验证后启用。导入技能包不触发任意安装脚本。

### 9.5 Memory

建议分四类持久化对象：任务工作状态、用户偏好、已确认的业务口径、方法/skills。数据快照与分析证据作为独立 artifact 保存，长期记忆引用它们而非复制大量原始行。

每项长期记忆至少有 `scope / source / created_at / updated_at / status / expires_at / supersedes`；状态区分候选、确认、失效和冲突。用户可查看、修订、删除及关闭记忆。检索返回记忆内容同时返回来源和适用范围；删除行为需覆盖索引及可恢复缓存。不要在首版引入自动永久记住所有对话的默认行为。

### 9.6 模型能力自适应

建立按 `provider + model + endpoint + version` 标识的能力记录，包括文本、图像输入、结构化输出、工具调用、上下文、流式/取消和费用。声明与实测分开，记录探测日期。未知能力应显示未知，不能靠模型名称猜测。

CSV/表格分析优先在本地解析、计算，再向模型发送获准的 schema、聚合值和证据摘录。只有扫描件、图片或视觉排版任务需要视觉输入时，才选择可接受该内容的多模态模型。文本模型可走 OCR/结构化提取后分析，明确标出视觉信息可能丢失；若无法满足任务则显示限制。多模态模型能看图片，不等于其表格算术准确。

切换模型还要满足用户的地区、供应商、数据外发和预算限制。授权绑定目标服务与内容范围，自动 fallback 不能悄悄扩大外发范围。云模型和外部 TTS 都属于外发。

### 9.7 TTS

TTS 独立于分析模型，定义 `list_voices / synthesize / stream / cancel` 适配器。配置 service URL、凭证引用、语言、voice、格式、语速与费用限制；初版可先配置一个服务并验证试听和取消。播报使用用户可见结论文本，不发送整个会话或底层数据。停播不应取消已经完成的分析，TTS 故障也不应使报告丢失。

### 9.8 Workflow

先实现少量可编辑分析模板与步骤面板：导入 → 质量检查 → 指标口径确认 → 趋势/异常 → 并行维度贡献 → 复核 → 报告。允许用户暂停、调整比较口径、跳过非必要分支和重跑依赖步骤。

每个步骤绑定有类型的输入、输出、来源、重试与审批策略。图形编辑器只是这一合同的编辑界面；“能拖出节点”不代表任务可恢复或分析正确。任意 Python/SQL 工作流编辑留到工具隔离与依赖管理验证以后。

## 10. 下一轮应做的实验与决策

建议本轮确认“独立 Python runtime + 图编排”的验证方向，不承诺最终框架。随后进行以下有退出条件的实验：

1. **模型与工具合同**：选一个文本模型、一个视觉模型、一个 OpenAI-compatible 服务，比较直接 SDK 与 Pydantic AI；测 schema 违例、工具失败、路由拒绝与费用记录。模型名和供应商在配置时确定。
2. **桌面分发**：最小桌面壳启动固定 Python runtime，完成一次文件导入和取消；三平台安装包逐个验证。没有 Windows/Linux 机器或 CI 的检查保留为未验证。
3. **Agent 树控制**：合成慢工具、不断生成子任务的模型替身、模拟超时，验证共享预算、权限不扩大、取消传播及孤儿进程清理。
4. **MCP 与 skills**：本地合成 MCP 服务同时提供正常、超时、超大输出与未授权工具；验证协议协商和取消。skill 越权请求应被工具层拒绝。补充进展：本轮后续 [SP-08](../../research/spikes/mcp-local-contract/README.md) 已验证 Python SDK 2.2.0 的本地通信、结构化返回、错误、协作式取消及实验宿主白名单；超大输出、远程认证、旧版互操作、Skill 执行权限仍待做。
5. **记忆与分析正确性**：用含冲突口径、已失效规则的数据样例，验证记忆的来源与失效；另建分析答案集，单独评估查询、图表和报告内容。

这些实验通过后再批准 ADR，进入首个可用版本。通用 shell、自动安装插件、外部网页深度研究和自由画布 workflow 可以分阶段加入，不应阻止首版经营分析流程交付。

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
