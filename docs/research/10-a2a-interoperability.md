# A2A 协议与跨内核协作证据

状态：研究证据，2026-09-25 核验。产品接口与取舍统一见[跨内核协作设计](../architecture/08-runtime-interoperability.md)，本页只记录协议、源码和验证边界。

## 1. 版本必须分开记录

| 对象 | 本次锁定 | 核验结果 |
|---|---|---|
| A2A 规范仓库 | `v1.0.1`，`3303592588e388e62e0f69f701af531d2f4e3991` | GitHub 最新 release 发布时间为 2026-05-28；官网和仓库首页的规范入口仍标 `1.0.0`，存在说明滞后 |
| 线上的协议版本 | `1.0` | 规范按 Major.Minor 协商；patch 不参与协议兼容协商，不能发送 `A2A-Version: 1.0.1` 来表达本次源码版本 |
| 官方 Python SDK | `a2a-sdk==1.1.5`，`9f0f00cb0417cb59958d3186d81651be7d9b9d59` | 2026-09-21 发布；支持 A2A 1.0，并提供 0.3 兼容模式。SDK 版本不等于协议版本 |
| AgentScope 源码 | `a38821287f35e9e45ed193d9d864cb46f263c946` | 已有 A2A 1.0 客户端适配器与服务端示例；不能根据旧 issue 判断其没有 A2A 支持 |

来源：[规范 release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)、[规范 §3.6](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/specification.md#36-versioning)、[SDK release](https://github.com/a2aproject/a2a-python/releases/tag/v1.1.5)、[SDK 兼容表](https://github.com/a2aproject/a2a-python/blob/9f0f00cb0417cb59958d3186d81651be7d9b9d59/README.md#L39-L53)。下载清单见 [a2a-sources.json](../../research/upstreams/a2a-sources.json)，外部源码不纳入本项目 Git。

1.0 有实质 API 变化：Python 类型改为 Protobuf，`Part` 直接设置 `text/raw/url/data`；状态采用 `TASK_STATE_*`；JSON-RPC 方法是 `SendMessage`、`SendStreamingMessage`、`GetTask`、`SubscribeToTask` 等。0.3 的 `message/send`、`tasks/resubscribe` 和旧 Pydantic 示例不能直接拼入 1.0 实现。[SDK 迁移指南](https://github.com/a2aproject/a2a-python/blob/9f0f00cb0417cb59958d3186d81651be7d9b9d59/docs/migrations/v1_0/README.md)

## 2. 协议能传递什么

以下是规范事实，不表示某个框架或远端服务已完整实现。

| 对象/操作 | 规范事实 | 对本项目的直接影响 |
|---|---|---|
| `Message.message_id` | 由消息创建方生成；消息角色只有 client/user 与 server/agent | 保留本地消息 ID 与外部 ID 的对应关系，不能用角色推断调用者权限 |
| `Task.id` | 新任务 ID 由服务端生成；客户端带 `task_id` 时必须指向已存在任务 | 本地 Run ID 不能直接冒充远端新 Task ID |
| `context_id` | 关联若干任务和消息；服务端可接受客户端提供值，也可拒绝；可过期清理 | 相同 context 不证明内部记忆完整存在，更不证明跨内核检查点兼容 |
| 多轮继续 | 提供 Task 与 context 时二者必须匹配；只给 Task 时服务端推导 context | 映射记录要包含对端与权限范围，不能仅按一段 task 字符串查询 |
| `reference_task_ids` | 表达相关任务引用 | 可用于新尝试的上下文；没有承诺复制历史节点或执行状态 |
| `Part` | `text/raw/url/data` 四种内容，含 MIME 与可选文件名 | 可传文本、媒体、文件与结构化数据，但不会自动完成模型模态适配 |
| `Artifact` | 任务产物，ID 在任务内唯一；流事件用 `append`、`last_chunk` 表达分片 | 产物 ID 不是内容哈希；完整性校验、授权下载与本地归档需另做 |

来源：[规范 §3.4 身份语义](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/specification.md#34-multi-turn-interactions)、[固定版 proto 对象](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/specification/a2a.proto#L167-L322)。设计影响由本项目据此推导。

### 状态与恢复的限制

`SUBMITTED/WORKING` 表示已接收和执行中；`INPUT_REQUIRED/AUTH_REQUIRED` 是中断等待；`COMPLETED/FAILED/CANCELED/REJECTED` 是终态。终态任务不能再收继续执行的消息，需要创建新任务。规范还保留 `UNSPECIFIED`，不能把未知状态视为完成。[状态定义](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/specification/a2a.proto#L186-L219)

这套状态描述对外任务，不定义 LangGraph 节点、AgentScope 内部循环、父子任务树、工作流版本、检查点迁移或回溯执行。`COMPLETED` 也不代表分析结论通过了本项目的业务验收。协议刻意允许远端保持内部工具、记忆和执行状态不透明。[官方核心概念](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/topics/key-concepts.md)

## 3. 流、重连、取消与重复执行

| 情况 | 已确认事实 | 尚需应用承担的部分 |
|---|---|---|
| 流式响应 | AgentCard 声明 streaming；任务流先返回 Task，再返回状态/产物事件；活跃流按生成顺序接收事件 | 保存本地事件序号、父子关联、节点输入输出与重试记录 |
| 重新订阅 | 1.0 `SubscribeToTask` 首个事件必须为当前 Task；终态任务不接受订阅 | 当前快照不等于断线期间每条事件的补发；核心事件对象没有持久事件游标或内部节点记录 |
| SDK 默认重新订阅 | 返回 Task 后尝试接入仍存在的任务队列 | 不应据此承诺进程重启后的历史事件恢复；本次检查的默认 handler 要求队列仍活跃 |
| 取消 | Cancel 尝试取消，可能返回不可取消；任务生命周期不依附于单条流连接 | 关闭 SSE 不等于取消；已发生的外部副作用不因取消自动撤销；子任务递归取消需要产品策略 |
| 重发消息 | 规范仅说 Send Message **可以**幂等，可用 message ID 去重 | 不能假设超时重试会自动避免重复收费、写入或发起子任务 |
| 持久化 | SDK 提供 TaskStore 接口；示例常用 InMemoryTaskStore，进程结束会丢失 | 持久 TaskStore 也不自动持久化内部 Agent 状态、队列或效果账本 |

来源：[订阅 §3.1.6](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/specification.md#316-subscribe-to-task)、[流顺序 §3.5.2](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/specification.md#352-streaming-event-delivery)、[幂等 §3.3.1](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/specification.md#331-idempotency)、[SDK default handler](https://github.com/a2aproject/a2a-python/blob/9f0f00cb0417cb59958d3186d81651be7d9b9d59/src/a2a/server/request_handlers/default_request_handler.py#L598-L635)、[内存 TaskStore](https://github.com/a2aproject/a2a-python/blob/9f0f00cb0417cb59958d3186d81651be7d9b9d59/src/a2a/server/tasks/inmemory_task_store.py#L20-L26)。

SDK 源码审查还发现：默认 handler 的 `_setup_message_execution` 会创建执行任务，本次审查没有发现它以 message ID 建立持久去重账本。这里只评价该默认实现，不宣称所有 SDK 扩展或服务器都不去重。[固定源码](https://github.com/a2aproject/a2a-python/blob/9f0f00cb0417cb59958d3186d81651be7d9b9d59/src/a2a/server/request_handlers/default_request_handler.py#L268-L337)

## 4. AgentCard、授权与接口兼容

AgentCard 声明服务接口、协议版本、输入输出 MIME、AgentSkill、认证方案，以及 streaming、push notification、扩展等能力。它没有通用的“可恢复内部节点”“支持任意内核迁移”字段。Card 支持 JWS 签名；验证签名只能确认相应密钥签署了声明，仍需判断服务与密钥是否可信，能力是否经过验证。[AgentCard 与能力字段](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/specification/a2a.proto#L334-L453)、[签名验证](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/specification.md#843-signature-verification)

认证采用绑定层的请求头或元数据；服务端必须按调用者权限检查任务和资源访问。父 Agent 的访问凭证、用户授权和子 Agent 对第三方服务的凭证是不同对象。A2A 定义 `AUTH_REQUIRED` 及向上请求授权的过程，但未统一规定令牌交换、最小权限转授权、费用预算或本地沙盒。[规范 §7](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/specification.md#7-authentication-and-authorization)、[访问范围 §13.1](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/specification.md#131-data-access-and-authorization-scoping)

**设计含义：**AgentCard 和产物 URL 都是外部输入。读取声明不能自动执行其中的指令、授予能力或下载任意地址。接口版本、扩展、媒体类型要协商；不了解必需扩展时应拒绝调用。认证信息应通过安全通道处理，避免放进会进入轨迹和模型上下文的普通消息。授权变化不能以“用户感知不到切换”为由跳过。

## 5. AgentScope 现有适配器的实际范围

锁定源码中的 `A2AAgent` 是有状态的**客户端**适配器，保存远端 context 和等待继续的 Task ID；服务端示例另外用官方 SDK 的 `AgentExecutor` 包装 AgentScope Agent。两者不能混称为自动服务端发布能力。[客户端源码](https://github.com/agentscope-ai/agentscope/blob/a38821287f35e9e45ed193d9d864cb46f263c946/src/agentscope/agent/_a2a_agent.py)、[服务端示例](https://github.com/agentscope-ai/agentscope/blob/a38821287f35e9e45ed193d9d864cb46f263c946/examples/a2a/server.py)

本次源码核对出的限制：

- text/raw/URL Parts 可转为 AgentScope 内容块；结构化 data Part、thinking/tool/hint 块与 push notification 不在该客户端适配器支持范围内。
- `INPUT_REQUIRED/AUTH_REQUIRED` 会结束当前本地 reply，并映射为 `ReplyFinishedReason.COMPLETED`。这表示本轮回复结束，不能据此把远端任务标成完成。
- 发现远端任务仍运行时拒绝再发消息；发现任务不存在时可开始新任务。产品若需要严格避免重执行，必须拦截后者并展示已失去恢复条件。
- 示例服务端只把文本 delta 发为产物，并使用内存任务存储；它没有证明持久恢复、完整内部轨迹、权限继承或通用多模态桥接。

来源：[AgentScope A2A 说明](https://github.com/agentscope-ai/agentscope/blob/a38821287f35e9e45ed193d9d864cb46f263c946/examples/a2a/README.md)。这是固定主分支源码审查；发布包与混合内核实验的实际行为以实验锁文件和结果为准。

## 6. 与 MCP、SKILL.md 的关系

A2A 的 Task、消息和产物适合独立 Agent 之间委派工作；MCP 继续用于工具、资源等接入。把一个 Agent 包装为 MCP 工具可以实现调用，但若要跟踪长任务、中断状态与产物，还要额外表达这些语义。[官方 A2A/MCP 对照](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/topics/a2a-and-mcp.md)

A2A `AgentSkill` 是 Card 中的能力描述，字段包括名称、说明、标签、例子和输入输出类型；规范没有定义下载并执行 `SKILL.md` 的流程。两种 skill 应在界面与对象中分开命名，避免因对方宣称某项能力就导入本地执行材料。[AgentSkill 字段](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/specification/a2a.proto#L434-L453)

## 7. 本页证明到哪里

本页完成官方版本核验、固定源码审查和版本下载记录。它支持“通过协议包装不同内核”的技术可行性判断；不证明任意内核可以无损转换，也不证明任意第三方 A2A 服务兼容全部能力。真实 LangGraph 父 Agent 与 AgentScope 子 Agent 的运行结果、失败记录和限制另见[混合内核实验](../../research/spikes/runtime-interoperability/README.md)，不以源码存在替代实测。

HTTPS 认证、恶意服务、重连丢包、远端重启、任务取消与副作用竞争、跨租户访问和第三方模型质量仍需要后续专项验证。研发验收入口见[跨内核协作设计](../architecture/08-runtime-interoperability.md)。
