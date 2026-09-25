# ADR-0001：桌面外壳、运行时与分析层分离

- 提出：2026-09-24；复审：2026-09-25。
- 状态：**Superseded proposal（旧提案已被新版替代，未曾批准）**。

第一轮将 Electron/React、Python/LangGraph、DuckDB 作为组合候选，并按数据分析原型安排先后。用户要求先严肃评审 Agent 内核，把工作流、全轨迹、画像与沙盒提前。因此旧的整套技术栈提案不再作为实施依据。

当前 Agent 决策见 [ADR-0002](ADR-0002-kernel-selection.md)；外壳与数据组件仍为[整体架构](../01-candidate-architecture.md)中的候选。历史论证和实验保留在 Git 与第一轮研究记录中。没有自动接受任何候选。
