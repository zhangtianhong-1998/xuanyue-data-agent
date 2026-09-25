# 设计入口：先确定 Agent 内核

日期：2026-09-25。状态：已补充多内核与 A2A 设计，混合调用实验已完成，**方案待评审**；尚未开发完整客户端。

本轮按你的七项反馈调整：先评审 Agent 内核，工作流、轨迹、画像和沙盒提前进入内核 MVP；完整 Data 分析随后接入。推荐 LangGraph 作为默认编排器，同时支持不同内核的子 Agent。统一协议、委派关系与控制服务由产品持有；新增的 A2A 通信和切换边界单独说明。理由和反例集中在第一篇，不需要通读所有研究附件才能讨论。

## 按这个顺序读

| 顺序 | 文档 | 这篇只回答什么 |
| --- | --- | --- |
| 1，先读 | [内核选型](architecture/02-kernel-selection.md) | 默认编排器怎么选，依据和代价是什么 |
| 2 | [核心对象](architecture/03-core-contracts.md) | 消息、运行、绑定、委派、产物怎样统一 |
| 3，新增 | [多内核与 A2A](architecture/08-runtime-interoperability.md) | 怎样换内核、混合父子 Agent，哪些状态可以移交 |
| 4 | [工作流与轨迹](architecture/04-workflow-and-trace.md) | 大小模型编排、公开过程、历史分叉 |
| 5 | [执行沙盒](architecture/05-sandbox.md) | 生成代码的文件/网络/资源/取消限制 |
| 6 | [长期画像](architecture/06-profile-memory.md) | 如何记习惯、更新、纠正和遗忘 |
| 7，核对范围 | [产品阶段](product/01-product-brief.md) / [50 条用户故事](product/02-user-stories.md) | 分期交付与验收 |

只想看系统分工时打开[整体架构](architecture/01-candidate-architecture.md)。需要查证时再打开[对照实验](research/09-framework-comparison.md)、[AgentScope 证据](research/06-agentscope-evidence.md)、[LangGraph 证据](research/07-langgraph-evidence.md)、[沙盒证据](research/08-sandbox-profile-evidence.md)和[A2A 证据](research/10-a2a-interoperability.md)。

## 需求对应哪些说明

| 反馈 | 权威说明 | 验收故事 |
| --- | --- | --- |
| 内核先选型 | 阅读顺序 1；[ADR-0002](architecture/decisions/ADR-0002-kernel-selection.md) 记录评审状态 | K0 评审；US-011、027、038 |
| 混合 workflow / 小模型路由 | [工作流](architecture/04-workflow-and-trace.md) | US-026、028、034、041 |
| 使用习惯和长期画像 | [画像](architecture/06-profile-memory.md) | US-015、035、040 |
| 核心对象 / 多模态 | 阅读顺序 2 | US-016、017、036 |
| 全轨迹 / 节点回溯 | [轨迹与回溯](architecture/04-workflow-and-trace.md) | US-037、038、042 |
| 执行沙盒 | [沙盒](architecture/05-sandbox.md) | US-039 |
| 多内核切换、混合父子与 A2A | [多内核设计](architecture/08-runtime-interoperability.md)；[ADR-0003](architecture/decisions/ADR-0003-runtime-interoperability.md) | US-043–050 |
| 清楚的设计路径 | 本页；[文档维护规则](engineering/01-development-process.md#2-每种信息只在一处维护) | 目录、跳转与一致性检查 |

## 已有证据与新增验证

- LangGraph 共同 8 类机制通过，复核共 20 条断言；追加了中间检查点分叉。
- AgentScope 发布包 7 类通过，历史节点分叉未实现；固定 main 的 SOP 另有 6 条检查通过，不能与发布包混称。
- 手写固定图 8 类通过，但全部为自建机制，不证明维护成本更低。
- 沙盒只完成可用性探测；真实模型路由成本、用户画像效果、UI 和跨平台发行仍未验证。

新增[混合内核实验](../research/spikes/runtime-interoperability/README.md)：真实 LangGraph 父流程通过 A2A 1.0 调用 AgentScope 子 Agent，10 项检查通过；包括结构化产物、等待输入、父进程恢复查询原任务、协作式取消及不支持能力拒绝。子服务重启、未知提交去重、完整远端轨迹与跨内核 checkpoint 迁移未验证，保留取消适配的首轮失败。

原有 [Data 查询实验及 MCP 实验](research/04-experiment-results.md)、[BI 组件研究](research/02-bi-and-analysis.md)、[数据分析方法](architecture/02-analysis-methods.md)保留供 D1 使用。原有内核/桌面研究保留来源证据，重复的现行设计已改为跳转。

## 本次评审只讨论三件事

1. 是否接受“LangGraph 默认编排 + 多内核子执行器”的职责划分；完整第二编排器与任意状态迁移以后分别验收？
2. 是否按 K1a 可运行内核 → K1b 可编辑工作台 → D1 数据分析推进？
3. 个人预览版是否可依赖本地容器/虚拟机沙盒；若要求零额外安装，先增加嵌入式隔离实验再定发行路线。

这些仍是待确认方案。下一步在你确认方向后开始 K1 的垂直实现，先做一次可演示的任务，再逐步补齐故事；不一次铺开整套产品。
