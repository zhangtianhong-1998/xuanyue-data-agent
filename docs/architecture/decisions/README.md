# 架构决策记录

先看[当前设计入口](../../00-discovery-summary.md)。用户已明确要求 LangGraph 和 AgentScope 都能担任主 Agent；具体实现路线尚未批准。这里的 **Proposed** 表示待评审方案，**Superseded** 表示已失效的旧提案。提交到 Git 不等于批准实施。

| 状态 | 记录 | 读它是为了什么 |
| --- | --- | --- |
| 当前：Proposed | [ADR-0004：双主 Agent](ADR-0004-dual-primary-kernel.md) | 查看已确认的需求、待验证问题和下一道实验门槛 |
| 历史：Superseded | [ADR-0001：桌面、运行时与分析层](ADR-0001-runtime-route.md) | 了解第一轮整套技术栈提案为何失效 |
| 历史：Superseded | [ADR-0002：LangGraph 先承担调度](ADR-0002-kernel-selection.md) | 了解固定 LangGraph 主流程的旧提案 |
| 历史：Superseded | [ADR-0003：LangGraph 主流程下的委派](ADR-0003-runtime-interoperability.md) | 了解只验证单向委派时的旧安排 |

历史记录保留原编号，供追溯使用；现行设计细节请从[多内核设计](../08-runtime-interoperability.md)进入。
