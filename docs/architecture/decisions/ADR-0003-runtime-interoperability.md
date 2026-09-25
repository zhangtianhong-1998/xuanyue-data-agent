# ADR-0003：多内核任务接口与 A2A 委派

- 日期：2026-09-25。
- 状态：**Proposed，方案待评审**；多内核、混合父子与 A2A 是用户新增需求。
- 对应：US-043–050；修订 ADR-0002 中关于单内核接入的限制。

建议将旧 RuntimeAdapter 拆为 WorkflowRuntime、AgentTaskRuntime 与 AgentTransport。LangGraph 先作为默认编排器；子 Agent 可绑定 LangGraph、AgentScope 等实现，跨进程通过 A2A 委派。产品持有规范对象、能力档位、身份与委派账本。

正常操作保持统一界面；任务开始后绑定内核版本。原生 checkpoint 不承诺跨内核互转，能力不足、未知提交、外部轨迹缺失和取消结果必须如实呈现。完整规则只在[多内核设计](../08-runtime-interoperability.md)维护。

依据：[A2A 固定来源](../../research/10-a2a-interoperability.md)与[混合内核实验](../../../research/spikes/runtime-interoperability/README.md)。后续新增完整流程引擎、状态迁移器或外部服务强保证需单独验收。

评审记录：待确认具体实现范围；研究和公开提交不等于批准完整产品开发。
