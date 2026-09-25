# ADR-0003：LangGraph 主流程下的多内核委派（历史提案）

- 日期：2026-09-25。
- 状态：**Superseded proposal，未曾批准**。用户补充要求 LangGraph、AgentScope 均可担任主 Agent，原提案只让 AgentScope 担任子 Agent，因此失效；当前待评审方案见 [ADR-0004](ADR-0004-dual-primary-kernel.md)。
- 当时对应：US-043–050；拟修订 ADR-0002 中关于单内核接入的限制。

当时建议将旧 RuntimeAdapter 拆为 WorkflowRuntime、AgentTaskRuntime 与 AgentTransport。LangGraph 先作为默认编排器；子 Agent 可绑定 LangGraph、AgentScope 等实现，跨进程通过 A2A 委派。产品持有规范对象、能力档位、身份与委派账本。接口拆分仍可作为候选，但这一安排没有证明 AgentScope 能作为主 Agent 委派 LangGraph 子 Agent，不能作为现行路线。

原提案中正常操作使用统一界面、任务开始后固定绑定、原生 checkpoint 不承诺跨内核互转等边界仍可供新方案参考。能力不足、未知提交、外部轨迹缺失和取消结果应如实呈现；现行规则需在[多内核设计](../08-runtime-interoperability.md)重审。

当时依据：[A2A 固定来源](../../research/10-a2a-interoperability.md)与[混合内核实验](../../../research/spikes/runtime-interoperability/README.md)。实验只覆盖 LangGraph 主流程→AgentScope 子 Agent；反向主从、双方可切换主 Agent 与完整工作流所有权仍未验证。

评审记录：2026-09-25 用户指出本提案仍把 AgentScope 限为子执行器，要求逐步重审文档。本提案未获批准即失效；研究和公开提交不等于批准完整产品开发。
