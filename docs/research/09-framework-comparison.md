# Agent 内核对照实验

[返回设计入口](../00-discovery-summary.md) · 取舍只在[内核选型](../architecture/02-kernel-selection.md)维护。

日期：2026-09-25。三条路线共享 [F01–F08 场景契约](../../research/spikes/framework-comparison/scenario-v1.json)，独立实现各自适配代码。本轮是执行机制验证，不是模型能力或性能竞赛。

## 结果与能力归属

“原生”表示主要执行机制来自框架；“适配”表示原生机制与应用代码共同完成；“自建”表示通过不应记为框架特性。

| 共同要求 | AgentScope 发布包 2.0.8 | LangGraph 1.2.12 | 手写固定图 |
| --- | --- | --- | --- |
| F01 文字+图片引用往返 | 通过 / 原生 blocks | 通过 / 适配 | 通过 / 自建 |
| F02 固定逻辑+受限路由 | 通过 / 自建 | 通过 / 适配 | 通过 / 自建 |
| F03 并行并收齐结果 | 通过 / 原生并发工具 | 通过 / 原生图节点 | 通过 / 自建 |
| F04 新进程恢复 | 通过 / state+应用存盘 | 通过 / 原生 checkpointer | 通过 / 自建 |
| F05 从历史状态分叉且保留原记录 | 未实现，需自建或另验源码预览 | 通过 / checkpoint+分支适配 | 通过 / 自建快照复制 |
| F06 拒绝阻止副作用 | 通过 / 工具审批+适配 | 通过 / 适配 | 通过 / 自建 |
| F07 崩溃后副作用不重复 | 通过 / 自建去重 | 通过 / 自建 outbox/接收方去重 | 通过 / 自建去重 |
| F08 公开事件身份和错误 | 通过 / 原生事件+适配 | 通过 / stream/history+适配 | 通过 / 自建 |

F03 的执行单元、F04 的状态粒度、F05 的可选检查点不同，不能把“7 对 8 对 8”当产品评分。AgentScope 的 F05 没有被证明不可能，只是本轮发布版没有实现等价路径。手写的所有通过项都局限于固定图。

## 结果文件与失败

| 路线 | 代码、环境和结果 | 说明 |
| --- | --- | --- |
| AgentScope | [README](../../research/spikes/framework-comparison/agentscope/README.md) | Python 3.11.15，锁文件；attempt-01/02 的实验错误保留，attempt-03 修正后 7 passed、1 unsupported_without_custom_code |
| LangGraph | [README](../../research/spikes/framework-comparison/langgraph/README.md) | Python 3.12.13，uv.lock；首轮 19 断言，复核追加中间节点分叉；非法路由和退出 73 是预设故障 |
| 手写 | [README](../../research/spikes/framework-comparison/handwritten/README.md) | Python 3.12.13 标准库；8 个共同场景通过；所有机制自建 |
| 沙盒可用性 | [README](../../research/spikes/sandbox-probe/README.md) | Docker CLI 存在，daemon 不可连接；五类隔离测试未运行 |

共同约束：合成输入；无外部模型调用；无真实业务数据；运行时数据库放临时目录，不上传；公开输出保留版本、可见事件、失败和验证边界。脚本/锁文件/场景哈希绑定各次结果；后续修改脚本不能冒用旧通过记录。

## 尚不能回答的问题

这轮无法回答实际小模型能省多少钱、多 Agent 是否提高分析质量、用户是否能理解画布、所有模态是否可用、三系统的安装体验，以及沙盒是否真的阻止越权。后续验证顺序见[研发流程](../engineering/01-development-process.md)，不将未测内容算入通过率。

## 后续跨内核验证

新增需求的 A2A 源码依据见[协议证据](10-a2a-interoperability.md)，真实父 LangGraph→子 AgentScope 的调用与失败记录见[独立实验](../../research/spikes/runtime-interoperability/README.md)。它不改变本页 F01–F08 的历史结果，也不证明两套完整工作流可以互换。
