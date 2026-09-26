# 可复现实验索引

这些实验使用合成输入，结果只证明各自列出的行为。各实验说明页写明运行命令、失败记录和未验证范围；过去的结果与源码快照保留原样。

| 实验 | 要回答的问题 | 当前结果；失败与限制 |
| --- | --- | --- |
| [本地数据分析契约](data-analysis-contract/README.md) | 查询、贡献计算和下钻能否共用确定性数据契约 | [11 项检查通过](data-analysis-contract/results.json)；未绘制真实图表，未证明因果或分析质量 |
| [Agent 执行恢复](kernel-contract/README.md) | LangGraph 的检查点、跨进程恢复和应用层副作用去重如何工作 | [8 项检查通过](kernel-contract/results/2026-09-24-macos-arm64-review.json)；恢复可能重跑节点，去重由实验代码负责 |
| [本地 MCP](mcp-local-contract/README.md) | 独立进程工具的协议、权限检查、结构化返回和取消如何工作 | [12 项检查通过](mcp-local-contract/results.json)；[首次失败](mcp-local-contract/failures/attempt-01-untyped-status.json)及范围见实验说明 |
| [三种内核对照](framework-comparison/scenario-v1.json) | 同一组固定场景在 LangGraph、AgentScope 和手写执行器中分别由谁实现 | [LangGraph](framework-comparison/langgraph/README.md)、[AgentScope](framework-comparison/agentscope/README.md)、[手写](framework-comparison/handwritten/README.md)各有结果和失败说明；它们不能直接证明完整产品可互换 |
| [AgentScope 独立主任务（第一段）](agentscope-primary/README.md) | 用户指定 AgentScope 后，根任务是否实际由它运行并留下受控公开事件 | [正向通过](agentscope-primary/results/review-02.json)，[未注册内核按预期拒绝](agentscope-primary/results/review-02-unsupported.json)；不覆盖委派、恢复或真实模型 |
| [单向 A2A 委派](runtime-interoperability/README.md) | LangGraph 主流程调用 AgentScope 子 Agent 时，状态与产物如何映射 | [10 项检查通过](runtime-interoperability/results/review-05/results.json)；[首次失败](runtime-interoperability/results/attempt-01/results.json)和其他限制见实验说明。**尚未证明 AgentScope 主→LangGraph 子或完整双主任务可用** |
| [沙盒可用性探测](sandbox-probe/README.md) | 当前 Mac 上能找到哪些候选工具 | [探测记录](sandbox-probe/results.json)；隔离负测未运行，不能据此选择沙盒后端 |

第三方源码的获取方式见[上游说明](../upstreams/README.md)；各实验依赖及锁定版本见对应实验的说明页。
