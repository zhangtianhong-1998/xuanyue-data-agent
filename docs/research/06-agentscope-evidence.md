# AgentScope：发布包与源码证据

[选型结论](../architecture/02-kernel-selection.md) · [共同实验结果](09-framework-comparison.md)

访问/实验日期：2026-09-25。范围：PyPI 发布包实际调用与固定源码审阅，不包含真实 LLM、云服务或完整产品。

## 版本不能混用

| 证据对象 | 版本 | 实际核对 |
| --- | --- | --- |
| 已安装发布包 | agentscope 2.0.8，Python 3.11.15；传递依赖见 requirements.lock | 真实 Agent/tool/state/event API；`find_spec` 未找到 `agentscope.sop` 和 `agentscope.middleware._model_router` |
| 上游源码快照 | commit `a38821287f35e9e45ed193d9d864cb46f263c946`；版本文件仍为 2.0.8 | 存在 SOPEngine、SOPRunState、ModelRouterMiddleware；另做 SOP 合成步骤试验；未执行真实 Agent verifier 或 ModelRouter 实验 |

[上游仓库](https://github.com/agentscope-ai/agentscope)、[固定依赖声明](https://github.com/agentscope-ai/agentscope/blob/a38821287f35e9e45ed193d9d864cb46f263c946/pyproject.toml)、[清单](../../research/upstreams/framework-sources.json)。同一版本字符串不能证明 main 与 wheel 内容相同。

## 源码观察

- [SOPEngine](https://github.com/agentscope-ai/agentscope/blob/a38821287f35e9e45ed193d9d864cb46f263c946/src/agentscope/sop/_engine.py) 顺序推进步骤，把恢复事件送回等待步骤；构造器检查步骤数，说明 Agent 自己的状态需由构建方恢复。
- [SOPRunState](https://github.com/agentscope-ai/agentscope/blob/a38821287f35e9e45ed193d9d864cb46f263c946/src/agentscope/sop/_state.py) 明确区分定义与一次运行，保留步骤状态及验证记录。这是值得参考的设计，不能描述成“AgentScope 没有工作流状态”。但它不自动证明任意历史节点分叉、图编译或跨版本恢复符合本项目要求。
- [ModelRouterMiddleware](https://github.com/agentscope-ai/agentscope/blob/a38821287f35e9e45ed193d9d864cb46f263c946/src/agentscope/middleware/_model_router.py) 接受候选模型和分类器，每次 reply 选择模型，恢复时保留选择，失败/未知候选回到 Agent 自身模型。产品仍需检查这条回退路径的数据授权和预算。
- 当前源码依赖声明含 `mcp<2.0.0`；本轮发布包环境锁到 MCP 1.30.0。不能与旧 MCP 2.2.0 实验直接混装，协议互通需独立验证。

根许可证 Apache-2.0 已记录哈希；不代表完整依赖许可审计完成。

## 担任主智能体的依据与缺口

发布包实验已实际调用 Agent 的模型/工具循环、状态和事件接口；固定源码还包含 [Agent](https://github.com/agentscope-ai/agentscope/blob/a38821287f35e9e45ed193d9d864cb46f263c946/src/agentscope/agent/_agent.py) 与 [A2AAgent](https://github.com/agentscope-ai/agentscope/blob/a38821287f35e9e45ed193d9d864cb46f263c946/src/agentscope/agent/_a2a_agent.py)。官方滚动教程展示了[主 Agent 用工具创建并调用工作 Agent](https://doc.agentscope.io/tutorial/workflow_handoffs.html)与[路由](https://doc.agentscope.io/tutorial/workflow_routing.html)。这支持“AgentScope 可以作为主智能体候选”的判断；教程使用的类和版本不作为本轮发布包的实测证据。

截至本页的 2026-09-25 实验，尚未单独验证本产品的 AgentScope 根任务。2026-09-26 新增的[最短路径实验](../../research/spikes/agentscope-primary/README.md)已验证合成根任务和本地工具调用；通过 A2A 委派 LangGraph、根任务跨进程恢复，以及用户可见的历史节点分叉仍未验证。现有 F05 缺口不能据此写成不可能，也不能省略共同验收。下一验证门槛见[双主选型](../architecture/02-kernel-selection.md#5-下一次只推进一个验证门槛)。

## 发布包实验

[脚本与运行说明](../../research/spikes/framework-comparison/agentscope/README.md)；[修正后原始结果](../../research/spikes/framework-comparison/agentscope/results/attempt-03/results.json)。

实际通过：消息 blocks 往返、应用路由、同一 Agent 的两个并发工具、工具审批暂停后另进程恢复、拒绝/批准、崩溃后的应用去重、事件身份包装。F05 标记 `unsupported_without_custom_code`：在已审阅的发布版 state/pipeline 模块中没有找到与 LangGraph checkpoint history/fork 等价的路径；没有用“复制 JSON”冒充完成。

F03 是同一 Agent 的工具并发，不是持久化多 Agent DAG。F04 用应用代码保存/恢复 AgentState，不是任意节点历史。F07 未加去重时模拟副作用两次，加单 worker SQLite 去重后一次；不是分布式 exactly-once。F08 的异常在框架外被捕获并落盘，属于应用适配，不是框架自动保存完整轨迹。

## 保留的失败

- attempt-01：实验报告直接序列化 AnyUrl，导致 harness 失败；不是 Msg 序列化能力失败。
- attempt-02：实验遗漏 PermissionDecision 必填 message；错误路径没有捕获框架抛出的异常。原脚本、错误、事件保留，发布时只将本机工作目录替换为 `$PROJECT_ROOT`。
- attempt-03：修正实验调用和异常适配后重跑，7 项通过，F05 仍未实现；未修改共同验收条件。

另行完成固定 main 的[独立 SOP 实验](../../research/spikes/framework-comparison/agentscope/results/source-preview.json)：6 条检查通过，覆盖暂停、新进程恢复、已完成步骤保留/不重跑、拒绝次数上限和阻止下游。步骤为自编合成实现，JSON 持久化由应用负责；没有因此补齐发布包 F05。

尚未验证：main 的真实 Agent SOP/ModelRouter、任意流程编译、节点历史迁移、共享预算/取消、真实多 Agent 效果、沙盒与跨平台分发。采用稳定版本之前应重新核对发布内容。
