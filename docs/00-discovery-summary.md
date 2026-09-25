# 设计入口：双主智能体内核

日期：2026-09-25。状态：LangGraph、AgentScope 都须能担任主智能体，这是已确认需求；技术实现与工作流调度归属待验证、待评审。当前处于 K0a 文档纠偏，尚未开发完整客户端。

本轮撤回“LangGraph 固定主编排、AgentScope 仅作子执行器”的旧建议。主智能体负责用户目标、计划、委派和结果整合；用户工作流的节点调度是单独的设计问题。先用同一合成任务验证两种主智能体，再比较调度层的实现方式。工作流、轨迹、画像和沙盒仍属于内核 MVP 的目标；完整 Data 分析随后接入。

## 按这个顺序读

| 顺序 | 文档 | 这篇只回答什么 |
| --- | --- | --- |
| 1，先读 | [产品目标与阶段](product/01-product-brief.md) | 双主智能体要达到什么行为，本轮只到哪一步 |
| 2 | [双主内核选型](architecture/02-kernel-selection.md) | 现有证据、两条工作流路线与下一验证门槛 |
| 3 | [核心对象](architecture/03-core-contracts.md) | 主任务、流程、子任务、消息和产物怎样统一 |
| 4 | [多内核与 A2A](architecture/08-runtime-interoperability.md) | 主内核切换、双向父子委派、哪些状态可移交 |
| 5 | [工作流与轨迹](architecture/04-workflow-and-trace.md) | 大小模型编排、公开过程、历史分叉 |
| 6 | [执行沙盒](architecture/05-sandbox.md) / [长期画像](architecture/06-profile-memory.md) | 执行隔离和习惯更新的约束 |
| 7，核对验收 | [50 条用户故事](product/02-user-stories.md) | 用户操作与验收条件 |

只想看系统分工时打开[整体架构](architecture/01-candidate-architecture.md)。需要查证时再打开[对照实验](research/09-framework-comparison.md)、[AgentScope 证据](research/06-agentscope-evidence.md)、[LangGraph 证据](research/07-langgraph-evidence.md)、[沙盒证据](research/08-sandbox-profile-evidence.md)和[A2A 证据](research/10-a2a-interoperability.md)。

## 需求对应哪些说明

| 反馈 | 权威说明 | 验收故事 |
| --- | --- | --- |
| 双主内核先验证 | 阅读顺序 1–2；[ADR-0004](architecture/decisions/ADR-0004-dual-primary-kernel.md) 记录评审状态 | K0a/K0b；US-011、027、038、043、050 |
| 混合 workflow / 小模型路由 | [工作流](architecture/04-workflow-and-trace.md) | US-026、028、034、041 |
| 使用习惯和长期画像 | [画像](architecture/06-profile-memory.md) | US-015、035、040 |
| 核心对象 / 多模态 | 阅读顺序 2 | US-016、017、036 |
| 全轨迹 / 节点回溯 | [轨迹与回溯](architecture/04-workflow-and-trace.md) | US-037、038、042 |
| 执行沙盒 | [沙盒](architecture/05-sandbox.md) | US-039 |
| 多内核切换、混合父子与 A2A | [多内核设计](architecture/08-runtime-interoperability.md)；[ADR-0004](architecture/decisions/ADR-0004-dual-primary-kernel.md) | US-043–050 |
| 清楚的设计路径 | 本页；[文档维护规则](engineering/01-development-process.md#2-每种信息只在一处维护) | 目录、跳转与一致性检查 |

## 已有证据与新增验证

- LangGraph 共同 8 类机制通过，复核共 20 条断言；追加了中间检查点分叉。
- AgentScope 发布包 7 类通过，历史节点分叉未实现；固定 main 的 SOP 另有 6 条检查通过，不能与发布包混称。
- 手写固定图 8 类通过，但全部为自建机制，不证明维护成本更低。
- 沙盒只完成可用性探测；真实模型路由成本、用户画像效果、UI 和跨平台发行仍未验证。

已有[单向混合内核实验](../research/spikes/runtime-interoperability/README.md)：真实 LangGraph 主流程通过 A2A 1.0 调用 AgentScope 子 Agent，10 项检查通过；包括结构化产物、等待输入、父进程恢复查询原任务、协作式取消及不支持能力拒绝。**它没有验证 AgentScope 主任务或反向委派。** 子服务重启、未知提交去重、完整远端轨迹与跨内核 checkpoint 迁移也未验证，取消适配的首轮失败继续保留。

原有 [Data 查询实验及 MCP 实验](research/04-experiment-results.md)、[BI 组件研究](research/02-bi-and-analysis.md)、[数据分析方法](architecture/02-analysis-methods.md)保留供 D1 使用。原有内核/桌面研究保留来源证据，重复的现行设计已改为跳转。

## 本轮停在哪里

K0a 只纠正需求、接口和故事，不定 LangGraph 或 AgentScope 的默认主内核，也不开始 K1 产品实现。下一道 K0b 门槛是 AgentScope 独立主任务，以及 AgentScope 主→LangGraph 子的 A2A 合成实验；失败和不支持项保留。取得证据后再评审工作流由产品调度还是由两套框架分别适配。沙盒安装、行业样例和 BI 组件在相应阶段分别讨论。
