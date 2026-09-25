# ADR-0002：内核优先、LangGraph 先承担调度（历史提案）

- 日期：2026-09-25。
- 状态：**Superseded proposal，未曾批准**。用户明确要求 LangGraph、AgentScope 均可成为主 Agent，原提案以 LangGraph 固定主流程的范围已失效；当前待评审方案见 [ADR-0004](ADR-0004-dual-primary-kernel.md)。
- 对应：US-011、026–028、034–042。
- 当时拟替代：ADR-0001 中关于 Agent 路线和研发先后的旧提案；本条自身没有成为已批准决定。

问题：用户需要编排混合流程、持久恢复、历史节点分叉和多 Agent 协作，单纯从经营分析场景推选完整技术栈不足以支撑决定。

当时建议：用 LangGraph 承担 K1 可恢复调度；产品持有核心对象、授权/预算、事件、画像、产物和副作用协议。AgentScope 仅作为子执行器候选，完整主流程能力留到以后。[ADR-0003](ADR-0003-runtime-interoperability.md) 虽撤回了“不同时接入”的限制，仍延续这一不对称安排。用户已否定该安排，本段仅保留决策历史，不作为实施依据。

当时的比较与代价见[内核选型](../02-kernel-selection.md)；固定版本验证范围见[对照实验](../../research/09-framework-comparison.md)。这些证据仍有效，但没有比较 AgentScope 与 LangGraph 互换主 Agent 后的完整行为。

评审记录：2026-09-25 用户指出两种内核均应可成为主 Agent，要求逐步重审文档。本提案未获批准即失效。文档提交/公开发布不代表架构提案获批准。
