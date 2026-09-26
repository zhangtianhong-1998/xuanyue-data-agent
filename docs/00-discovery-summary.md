# 设计导航

当前是需求和架构评审阶段，尚无完整客户端。用户可以直接发起自主 Agent 任务，选择主智能体及其内核；LangGraph 和 AgentScope 都要能承担这个角色，并能组成跨内核父子任务。Skill 指导 Agent，但由 Agent 自行判断行动。用户可另外编排规则更固定的工作流，在编排时指定主 Agent 及其内核；流程可以包含向子 Agent 派发任务的步骤。自主 Agent 调用确定性工作流是后期候选。接入方式和固定流程的调度方式仍在验证。

## 先看哪三篇

- [产品目标与阶段](product/01-product-brief.md)：用户要完成什么，现在推进到哪一步。
- [整体架构](architecture/01-candidate-architecture.md)：产品怎样接入不同内核，以及哪些职责由产品统一管理。
- [研发流程](engineering/01-development-process.md)：下一步做什么实验，结果怎样影响技术选择。

这三篇读完即可了解项目全貌；下面的资料按问题查阅。

## 常用词语

| 词语 | 在本项目中的意思 |
| --- | --- |
| 主智能体 | 一次任务的负责人：接收目标、推进计划、委派子任务并整理结果。自主任务在创建时选择，工作流在编排时选择。LangGraph 和 AgentScope 都要能运行这个角色。 |
| 内核 | 负责运行 Agent 的框架，例如 LangGraph 或 AgentScope。内核选择与模型选择是两件事。 |
| 转接口 | 把产品约定的任务、消息、工具调用和事件接到某个内核的代码。换内核时，界面和历史仍读产品保存的记录。具体接口待评审。 |
| Skill | 供 Agent 阅读的做事方法和资源；Agent 判断何时、如何采用，不等于固定流程。 |
| 确定性工作流 | 用户保存步骤、输入输出和分支规则，并指定主 Agent 及内核。固定的是流程规则；流程仍可让主 Agent 派发子 Agent，子任务也可选自己的内核。 |
| A2A | Agent 之间交换任务、消息和结果的协议候选；它不能自动转移框架内部状态。 |

## 要查什么

| 问题 | 打开这里 |
| --- | --- |
| 具体用户操作如何验收？ | [用户故事](product/02-user-stories.md) |
| 两种内核已有证据和未决选择是什么？ | [内核比较](architecture/02-kernel-selection.md) |
| 消息、任务、产物有哪些字段？ | [核心对象](architecture/03-core-contracts.md) |
| 跨内核委派和切换怎么处理？ | [多内核与 A2A](architecture/08-runtime-interoperability.md) |
| 工作流、轨迹与历史节点重跑怎么设计？ | [工作流与轨迹](architecture/04-workflow-and-trace.md) |
| 生成代码怎样隔离？用户偏好怎样保存？ | [沙盒](architecture/05-sandbox.md) · [长期画像](architecture/06-profile-memory.md) |
| 经营数据分析以后怎么接入？ | [分析方法](architecture/09-data-analysis-methods.md) · [Data 协议](architecture/07-data-contracts.md) |
| 过去提出过哪些路线，当前是否批准？ | [决策记录](architecture/decisions/ADR-0004-dual-primary-kernel.md)；其中链接了失效的旧提案 |

## 当前结论与历史

已有的[单向 A2A 实验](../research/spikes/runtime-interoperability/README.md)完成 10 项检查：LangGraph 担任主智能体，通过 A2A 调用 AgentScope 子智能体。它没有验证 AgentScope 主任务或反向委派。下一步需用同类合成任务补齐这两个方向，并保留失败和不支持的结果。

[框架对照](research/09-framework-comparison.md)记录了 LangGraph、AgentScope 和手写固定流程在锁定版本下的局部机制结果；[沙盒探测](../research/spikes/sandbox-probe/README.md)只检查本机可用性。完整客户端、真实沙盒隔离、模型质量、画像效果、图表交互和三平台安装仍未验证。[数据与 MCP 实验](research/04-experiment-results.md)和[BI 组件研究](research/02-bi-and-analysis.md)留作后续业务阶段的依据。

旧版“LangGraph 固定为主、AgentScope 只做子智能体”的提案已撤回。当前要求是两者均能担任主智能体；具体实现尚未批准。[决策记录](architecture/decisions/ADR-0004-dual-primary-kernel.md)保留了这段变更历史。
