# 设计入口：先确定 Agent 内核

日期：2026-09-25。状态：第二轮研究与机制实验完成，**方案待评审**；尚未开发完整客户端。

本轮按你的七项反馈调整：先评审 Agent 内核，工作流、轨迹、画像和沙盒提前进入内核 MVP；完整 Data 分析随后接入。推荐 LangGraph 作为可恢复调度器，统一协议与控制服务由产品持有。理由和反例集中在第一篇，不需要通读所有研究附件才能讨论。

## 按这个顺序读

| 顺序 | 文档 | 这篇只回答什么 |
| --- | --- | --- |
| 1，先读 | [内核选型](architecture/02-kernel-selection.md) | AgentScope / LangGraph / 手写怎么选，依据和代价是什么 |
| 2 | [核心对象](architecture/03-core-contracts.md) | 消息、媒体、运行、分支、工具和产物怎样统一 |
| 3 | [工作流与轨迹](architecture/04-workflow-and-trace.md) | 怎样编排大小模型，查看完整公开过程，从旧节点分叉重跑 |
| 4 | [执行沙盒](architecture/05-sandbox.md) | 生成代码在哪运行，文件/网络/资源/取消怎样限制 |
| 5 | [长期画像](architecture/06-profile-memory.md) | 如何记习惯、更新、纠正和遗忘 |
| 6，核对范围 | [产品阶段](product/01-product-brief.md) / [42 条用户故事](product/02-user-stories.md) | 哪个阶段交付什么，如何验收 |

只想看系统分工时打开[整体架构](architecture/01-candidate-architecture.md)。需要查证时再打开[对照实验](research/09-framework-comparison.md)、[AgentScope 证据](research/06-agentscope-evidence.md)、[LangGraph 证据](research/07-langgraph-evidence.md)和[沙盒证据](research/08-sandbox-profile-evidence.md)。

## 七项反馈落在哪里

| 反馈 | 权威说明 | 验收故事 |
| --- | --- | --- |
| 内核先选型 | 阅读顺序 1；[ADR-0002](architecture/decisions/ADR-0002-kernel-selection.md) 记录评审状态 | K0 评审；US-011、027、038 |
| 混合 workflow / 小模型路由 | 阅读顺序 3 | US-026、028、034、041 |
| 使用习惯和长期画像 | 阅读顺序 5 | US-015、035、040 |
| 核心对象 / 多模态 | 阅读顺序 2 | US-016、017、036 |
| 全轨迹 / 节点回溯 | 阅读顺序 3，包括分支图和操作语义 | US-037、038、042 |
| 执行沙盒 | 阅读顺序 4 | US-039 |
| 清楚的设计路径 | 本页；[文档维护规则](engineering/01-development-process.md#2-每种信息只在一处维护) | 目录、跳转与一致性检查 |

## 本轮证据状态

- LangGraph 共同 8 类机制通过，复核共 20 条断言；追加了中间检查点分叉。
- AgentScope 发布包 7 类通过，历史节点分叉未实现；固定 main 的 SOP 另有 6 条检查通过，不能与发布包混称。
- 手写固定图 8 类通过，但全部为自建机制，不证明维护成本更低。
- 沙盒只完成可用性探测；真实模型路由成本、用户画像效果、UI 和跨平台发行仍未验证。

原有 [Data 查询实验及 MCP 实验](research/04-experiment-results.md)、[BI 组件研究](research/02-bi-and-analysis.md)、[数据分析方法](architecture/02-analysis-methods.md)保留供 D1 使用。原有内核/桌面研究保留来源证据，重复的现行设计已改为跳转。

## 本次评审只讨论三件事

1. 是否接受 LangGraph 承担 K1 调度，产品持有统一对象、权限、画像与事件协议？
2. 是否按 K1a 可运行内核 → K1b 可编辑工作台 → D1 数据分析推进？
3. 个人预览版是否可依赖本地容器/虚拟机沙盒；若要求零额外安装，先增加嵌入式隔离实验再定发行路线。

这些仍是待确认方案。下一步在你确认方向后开始 K1 的垂直实现，先做一次可演示的任务，再逐步补齐故事；不一次铺开整套产品。
