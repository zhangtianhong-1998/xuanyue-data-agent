# ADR-0004：LangGraph 与 AgentScope 均可担任主 Agent（待评审）

- 日期：2026-09-25。
- 状态：**Proposed，未批准实施**。用户已明确提出双主 Agent 能力；下面的技术安排仍需评审。
- 取代：[ADR-0002](ADR-0002-kernel-selection.md)、[ADR-0003](ADR-0003-runtime-interoperability.md) 中固定 LangGraph 主流程、AgentScope 仅为子执行器的提案。两条旧 ADR 均未获批准。

## 需求与待决问题

同一产品中，LangGraph 或 AgentScope 都应能成为一次任务的**主 Agent**：接收目标、维护自己的执行状态、委派和观察子 Agent、汇总结果，并通过统一界面呈现。用户可为新任务选择主 Agent；混合父子至少要分别检查 LangGraph 主→AgentScope 子、AgentScope 主→LangGraph 子。父子职责与运行绑定需要明确，不能把某框架永久固定为父级。

主 Agent 的职责与**用户可编辑工作流的调度责任**是两项不同问题。采用产品自有调度、让两个框架各自适配 WorkflowRuntime，或采用其他分工，目前都只是候选。不能因为 AgentScope 的历史节点分叉尚未验证，就直接排除它担任主 Agent；也不能把能担任主 Agent 等同于两个框架已能无损执行同一完整工作流。

产品持有会话、规范消息、任务/产物身份、授权、预算和公开事件的设计方向可以继续评审。A2A 是跨进程委派的候选传输，不自动提供内部全轨迹、状态迁移或可靠去重。任务运行后固定所选绑定；原生检查点跨内核可移植性仍需单独验证。具体契约以重审后的[多内核设计](../08-runtime-interoperability.md)为准。

## 本轮阶段门

1. 先核对需求及文档：分别写清主 Agent、工作流调度器、子 Agent、传输层的责任；列出两种主 Agent 的等价操作与可见差异。本阶段不启动完整产品实现。
2. 下一轮针对未验证的 AgentScope 主→LangGraph 子，以及 AgentScope 单独担任主 Agent 的任务生命周期、补充输入、恢复和取消做合成实验；与已有 LangGraph 主→AgentScope 子的实验按同一契约比较。失败和不支持项保留。
3. 根据双向证据评审工作流调度责任与 MVP 交付范围，再决定实现阶段。需要节点历史分叉或可编辑工作流时，应另列验收，不把任务级主 Agent 切换冒充完整工作流互换。

当前证据：[框架对照](../../research/09-framework-comparison.md)和[单向 A2A 委派实验](../../../research/spikes/runtime-interoperability/README.md)。两者尚未完成上述双主 Agent 验收；本 ADR 是待评审的需求落实方案，不是已批准选型。
