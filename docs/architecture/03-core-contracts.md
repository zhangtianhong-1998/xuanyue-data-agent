# 核心对象与多模态消息

[返回设计入口](../00-discovery-summary.md) · 上一步：[内核选型](02-kernel-selection.md) · 下一步：[多内核与 A2A](08-runtime-interoperability.md)

日期：2026-09-25；状态：建议方案，待评审。**本页是对象名称、身份及数据归属的唯一详细定义。** 下表是设计契约，尚未生成正式 SDK 或数据库迁移。

## 1. 统一到什么程度

建议由产品定义稳定对象，用适配器映射 AgentScope、LangGraph、模型供应商和 MCP 的格式。框架内部的状态仍可保持原生形式；UI、存储、权限和插件不能直接依赖框架内部类。统一语义和身份，不要求把图片、表格、音频转换成同一种文本。

| 对象 | 最小字段 / 关系 | 负责什么 |
| --- | --- | --- |
| Project / Session | project_id；session_id → project_id | 项目隔离；连续对话容器，一个会话可有多次运行 |
| WorkflowDefinition / NodeDefinition | workflow_id、version、schema_version、nodes、edges；node_id、kind、输入输出类型、重试和预算策略 | 可编辑流程的版本；运行开始后固定版本 |
| Run | run_id、session_id、workflow_version（可选）、primary_binding_id、workflow_binding_id（可选）、input_refs、profile_snapshot_id、status | 一次顶层执行；明确主 Agent 与流程调度的绑定，不能仅用会话 ID 或框架私有 ID 代替 |
| Branch | branch_id、root_run_id、parent_branch_id、fork_checkpoint_ref、changed_inputs、head_ref | 多次尝试的来源关系；分叉生成新 run，原记录保持可读 |
| NodeAttempt | attempt_id、run_id、branch_id、node_id、attempt_no、parent_attempt_id、status | 节点的一次尝试；并发、重试和子任务都能定位 |
| AgentDefinition / AgentTask | agent_id、version、model_policy、tool_allowlist、execution_profile_ref；task_id、parent_task_id、role、input_refs、budget_ref、runtime_binding_id | Agent 的职责和任务；role 区分 primary / delegated，根任务没有 parent_task_id；受管理子任务权限只可缩小，外部只交出获准内容，结果独立复核 |
| RuntimeBinding | binding_id、runtime_kind/version、adapter_id/version、role、execution_location、capability_digest、native_state_schema | role 为 primary_agent / delegated_agent / workflow；LangGraph、AgentScope 都须能绑定 primary_agent，流程调度实现另定 |
| ExecutionProfile / CapabilityReport | profile_id/version、required_operations、input/output_schema、visibility、recovery_granularity、policy_requirements；declared/verified/unknown、tested_at | 运行所需能力与后端实测能力分开；选择规则见[多内核](08-runtime-interoperability.md) |
| DelegationRecord | delegation_id、parent_attempt_id、child_run_id、binding_id、request_hash、principal/project/session/branch、endpoint_ref、remote_task/context/message_id、status、budget_reservation、revision | 本地运行与远端任务持久关联；结果未知时用于核对，不能自行证明远端去重 |
| TaskObservation | delegation_id、remote_state、local_state、observed_at、source_event_ref、coverage、usage_status | 同时保存远端事实与产品解释；reply 结束不等于 task 完成 |
| HandoffSnapshot | snapshot_id、source_run/checkpoint_ref、schema_version、allowed_message_refs、completed_outputs、pending_goals、profile_snapshot_ref、tool_versions、losses | 在声明的业务边界移交可移植内容；不是框架私有检查点的翻译 |
| Message | message_id、session_id、run_id、sender、role、blocks、reply_to、created_at、visibility、schema_version | 可跨供应商保存的消息；sender 可区分用户、Agent、工具 |
| ArtifactRef | artifact_id、version、sha256、media_type、size、storage_ref、origin、access_scope | 不可变文件或产物引用；storage_ref 不是对模型开放的任意本地路径 |
| ToolCall / ToolResult | call_id、tool_id、tool_version、arguments_ref、attempt_id；call_id、status、output_refs、error | 参数 schema 校验、调用与返回一一对应；模型不能伪造成功结果 |
| ModelInvocation / RouteDecision | invocation_id、model_id、capability_snapshot、input_refs、usage；route_id、allowed_choices、choice、policy_version、fallback_reason | 本次调用和路由可审计；声明能力与实际探测结果分开 |
| RunEvent | event_id、seq、run_id、branch_id、node_id、attempt_id、parent_event_id、delegation_id、source_event_ref、coverage、type、time、payload_ref | 公开轨迹的持久事件；不用消息列表充当运行日志 |
| CheckpointRef | runtime_binding_id、backend、namespace、checkpoint_id、workflow_version、state_schema_version、artifact_refs | 引用可恢复的状态；不等于沙盒进程快照或业务数据库备份 |
| Grant / Budget | grant_id、scope、action、destination、expires_at、revision；budget_id、parent_id、reserved、spent、limits | 当前有效授权及共享预算；权限不由模型自行恢复 |
| EffectIntent / EffectReceipt | operation_id、arguments_hash、target、grant_ref、status；operation_id、remote_id、result_ref | 发布、文件写入等副作用的请求和回执；未知结果先核对 |
| ExecutionManifest | attempt_id、code_hash、input_refs、runtime_digest、policy_version、grant_refs、limits | 本次沙盒执行的固定清单；具体策略见[沙盒](05-sandbox.md) |
| MemoryObservation / ProfileFact / ProfileSnapshot | observation_id、subject、scope、source_ref；fact_id、key、value、state、revision、supersedes、valid_time；snapshot_id、fact_refs、revision | 观察、当前偏好与某次运行读取的画像版本；更新规则见[画像](06-profile-memory.md) |

远端标识按服务身份、项目、会话与分支隔离；`contextId` 只是远端关联标识，不作为权限凭据。主绑定、流程绑定与子任务绑定在运行开始后固定；设置页更改常用内核不会追溯改写旧运行。标识采用不含业务含义的不透明 ID；时间保存 UTC 与原时区信息；schema 升级显式迁移。错误统一包含 code、message、retryable、origin 和公开诊断引用，不把 traceback 或凭据直接送入 UI。

## 2. 消息的 ContentBlock

`blocks` 是带 `type` 的有序联合类型。块的顺序保留用户表达顺序，未识别类型必须报错或保留为不可执行附件，不能悄悄删掉。

| type | 有效内容 | 进入模型时的规则 |
| --- | --- | --- |
| text | text、语言（可选） | 长文本可引用受控分片；裁剪须记录范围 |
| image | ArtifactRef、尺寸、alt（可选） | 原图与 OCR 是不同来源；不支持图片的模型不能直接收到空文本替代 |
| audio | ArtifactRef、时长、时间片；转写引用（可选） | 保留原音频与转写关联；是否送原音频取决于能力和授权 |
| video | ArtifactRef、时长、采样帧/音轨引用 | 记录抽帧时间与遗漏范围；不能宣称抽帧分析看完全部视频 |
| document | ArtifactRef、页码/区域、解析产物引用 | 原文、解析文本、截图分别引用，并记录解析版本 |
| table | ArtifactRef、schema_ref、行数、允许的投影视图 | 表格数据不内嵌进全部消息；查询或有界预览需保留筛选条件 |
| structured | schema_id、schema_version、JSON 或 ArtifactRef | schema 必须来自登记表；不执行其中夹带的代码 |
| tool_call / tool_result | ToolCall / ToolResult 引用 | 对应唯一 call_id；错误、取消、部分结果不能映射成成功 |

示例（合成标识，省略可选字段）：

```json
{"message_id":"m1","role":"user","blocks":[
  {"type":"text","text":"解释这张图，并核对表中的金额"},
  {"type":"image","artifact_ref":"a-chart-v1"},
  {"type":"table","artifact_ref":"a-table-v1","schema_ref":"s-orders-v1"}
]}
```

Artifact 仓库管理字节、哈希和权限；消息、检查点和事件只持有引用，避免三份 base64 副本。供应商可有 namespaced metadata，但它不能改变身份、权限和执行语义。用于继续供应商会话的不透明 token 单独受控保存；隐藏推理不进入公开消息或轨迹。

## 3. 投影到基础模型

`ModelAdapter` 接受 Message 与能力要求，返回 ProviderRequest 和转换记录。按以下顺序处理：

1. 用登记信息筛选模型；小型探测核对工具调用、结构化返回和媒体支持。`unknown` 不等于支持。
2. 检查本次输入类型、上下文大小、目的地授权与预算。选择文字模型、视觉模型或专用 OCR/转写节点。
3. 转换时显式记录保留、抽样、截取和派生内容。原图改成 OCR 需要工作流允许，并向用户说明覆盖范围。
4. 校验输出类型与 ToolCall；失败按有限次重试/升级/停下的策略处理。升级模型要重新匹配出网授权。

模型的 reasoning 能力、视觉能力、工具调用可靠性是不同维度；不能用“参数大/价格高”代替探测。TTS 消费用户确认的文本产物，生成 audio Artifact；TTS 的目的地授权独立于聊天模型。

## 4. 内核保留哪些扩展点

| 端口 | 操作边界 | K1 范围 |
| --- | --- | --- |
| PrimaryAgentRuntime | 具备 AgentTaskRuntime 的任务生命周期，加上根目标、公开计划、委派/汇合与结果交付 | LangGraph、AgentScope 都须以主绑定通过相同的产品验收；接口与工作流调度分开 |
| WorkflowRuntime | validate/compile/start、inspect/events、request_cancel；按选定路线提供 checkpoint/resume/history/fork | 产品自有调度层或双框架适配待比较；不能固定隐藏的 LangGraph 父任务 |
| AgentTaskRuntime | capabilities、submit、inspect/events、provide_input、request_cancel；可选 checkpoint/fork/handoff | 两种内核均可承担受管理子任务；主任务还须满足 PrimaryAgentRuntime |
| AgentTransport / DelegationService | 本地 IPC / A2A 协议、身份映射、提交账本、恢复核对 | 父子调用统一入口；能力与状态规则见[多内核与 A2A](08-runtime-interoperability.md) |
| ModelAdapter / CapabilityRegistry | 规范化请求与结果、能力探测、用量记录 | 至少一个真实服务；两种模型配置的路由验证 |
| ToolRegistry / MCPAdapter | 发现、schema、调用、取消、结果关联 | 本地 MCP；协议/SDK 与运行进程版本独立锁定 |
| SkillRegistry | 来源、版本、资源解析、工具需求 | 只加载声明；脚本走 SandboxProvider，正文不授予权限 |
| MemoryStore | observe、propose、apply、snapshot、retrieve、forget | 项目级偏好，规则由产品掌握 |
| SandboxProvider | 探测、准备、执行、取消、收集、销毁 | 一个真正隔离的本地后端；详见[沙盒](05-sandbox.md) |
| DomainToolProvider | 登记领域 schema、工具、结果和可视化建议 | 合成工具先验内核；Data 后续接入 |
| SemanticProvider / TTSProvider | 语义查询；播报产物 | 预留接口，语义层服务不实现；TTS 实际接入后续验收 |

Data 扩展继续使用 DatasetSnapshot、MetricSpec、QuerySpec、Evidence、ChartSpec/DrillState，分别绑定文件版本、口径、查询、证据和交互状态；详细草案在[Data 领域协议](07-data-contracts.md)。它们以领域 schema 注册，不进入通用调度器。算法规则只在[数据分析方法](02-analysis-methods.md)及原有查询实验维护。

## 5. 仍要验证

本轮框架实验仅验证文字+图片引用的序列化，没有验证视觉理解。正式契约需要在 K1 增加 schema 迁移、媒体大小/失效引用、供应商有损转换、工具关联错误和删除产物后的历史展示测试。先确定身份与语义，再生成 Python/TypeScript 类型；不在本轮提前建立完整应用。
