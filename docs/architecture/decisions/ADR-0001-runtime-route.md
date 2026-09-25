# ADR-0001：桌面外壳、运行时与分析层分离

- 提出：2026-09-24；复审：2026-09-25。
- 状态：**Superseded proposal（旧提案已被新版替代，未曾批准）**。

第一轮将 Electron/React、Python/LangGraph、DuckDB 作为组合候选，并按数据分析原型安排先后。用户要求先严肃评审 Agent 内核，把工作流、全轨迹、画像与沙盒提前。因此旧的整套技术栈提案不再作为实施依据。

随后提出的 [ADR-0002](ADR-0002-kernel-selection.md) 和 [ADR-0003](ADR-0003-runtime-interoperability.md) 也未获批准，已因用户明确要求 LangGraph、AgentScope 均可担任主 Agent 而失效。当前待评审的问题见 [ADR-0004](ADR-0004-dual-primary-kernel.md)；外壳与数据组件仍为[整体架构](../01-candidate-architecture.md)中的候选。历史论证和实验保留在 Git 与第一轮研究记录中。没有自动接受任何候选。
