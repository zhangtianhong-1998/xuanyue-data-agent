# DeepSeek Harness 如何做到可插拔

状态：研究证据，非本项目的架构决定。访问日期：2026-09-25。核查对象是 [DeepSeek Harness 官方仓库](https://github.com/deepseek-ai/deepseek-harness)的固定提交 `46a7f68b0922371ce7144b668b90e377d8e799f4`（提交日期 2026-09-23）。本次只审查该版本文档和源码，未运行其产品级验证。

## 上游实际做法

应用由 Cordis 插件树组成。插件可以提供服务、事件和可撤销的注册；profile 组合 bundle 与 patch，后续 patch 能替换配置项。一个可替换的能力由**服务定义、提供方、消费者**组成。消费者按服务名调用，通过 `inject` 等待依赖就绪；提供方卸载时，依赖它的插件会卸载，服务恢复后再启动。[架构说明](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/docs/architecture.md#L9-L41)、[服务教程](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/docs/cordis-tutorial/03-services.md#L1-L78)、[接缝定义](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/docs/architecture.md#L130-L133)。

## 不能直接类推的部分

- **主 Agent：**`AgentFactory` 定义创建和恢复，上层通过 `ctx.agents` 调用，不直接依赖默认循环；但注册表只有一个 factory 槽位，重复注册会报错。这证明默认循环可替换，不能证明同一 Context 已能按任务选择多个主内核。[接口](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/core/agent/src/index.ts#L165-L203)、[注册源码](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/core/agent/src/index.ts#L245-L248)、[重复注册检查](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/core/agent/src/index.ts#L350-L373)。
- **子 Agent：**不同提供方按名称注册，可以共存；服务在启动前核对能力，缺少必需能力就报错。这是最接近本项目多内核选择需求的接法，但它针对的是子任务。[提供方接口](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/subagent/subagent/src/types.ts#L336-L389)、[注册和分派](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/subagent/subagent/src/index.ts#L505-L570)、[能力预检](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/subagent/subagent/src/index.ts#L643-L660)。
- **工作流：**`workflowEngine` 是每个 Context 一个服务，没有具名提供方注册表。它支持替换引擎，但不能据此宣称每个任务能单独选择引擎。[接缝清单](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/docs/capability-seams.md#L652)、[接口](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/workflow/workflow/src/index.ts#L150-L168)。

**对本项目的推断：**可借鉴稳定接口、可替换提供方和调用前能力核对。用户要求 LangGraph、AgentScope 都能担任主 Agent，未来还能加入其他内核，因此本项目仍需设计任务级多内核注册与绑定，以及统一任务对象和转换边界。哪些边界要独立适配、运行中的任务如何切换，留给后续设计与实验验证。

## 旧运行能否交给新内核（2026-09-26 补充）

本节另核查官方 `master` 固定提交 `477b4f420553e8a52c2fbccc464d7561b239c443`；本机锁定的旧检出仍为上文的 `46a7f68`。以下是源码和上游测试的结果，没有执行“两个不同主内核接力”的本地实验。

- **切模型：能沿用会话历史。**同一个 Agent Loop 更换模型供应商或模型后，后续请求从已有消息历史继续；这不是换 Agent 内核。[模型切换说明](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/core/agent/README.md#L137-L164)
- **切 Agent 预设：主要用于新任务。**预设改变工具、提示词与 Skill 的组合，仍共用 Host 的 Agent Loop；已开始的会话不允许重新选择预设。现有 Agent 在进程内保留原修订，但重启后按预设 ID 使用当前定义，旧修订的实现不会永久保存。[预设说明](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/preset/agent-preset-registry/README.md#L29-L65)、[首次运行后锁定的检查](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/preset/agent-preset-registry/src/index.ts#L313-L327)
- **同一内核恢复旧会话：已有实现与测试。**`ctx.agents.resume({ resumeSessionId })` 委托给当前注册的 `AgentFactory`；随仓库提供的 Agent Loop 从持久化事件日志重建会话，已完成的轮次不会从头执行。上游测试验证新 Context 载入旧历史后继续下一轮。若上次在工具调用中断，恢复会把该调用记为结果未知并结束未完成轮次，而不是从工具调用中间无缝续跑。[工厂接口](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/core/agent/src/index.ts#L166-L203)、[默认恢复实现](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/core/agent-loop/src/index.ts#L801-L889)、[上游恢复测试](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/core/agent-loop/tests/resume.spec.ts#L479-L537)、[续接历史测试](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/core/agent-loop/tests/resume.spec.ts#L979-L1013)
- **不同 Agent 内核接同一旧运行：目前没有现成保证。**`AgentFactory` 可替换，但每个 Context 只有一个注册槽位；恢复调用会交给当时注册的工厂。上述恢复测试使用的仍是同一个 Agent Loop，不能证明另一个实现能读取它的私有执行状态、从原节点继续或正确处理在途副作用。[单工厂注册及恢复转发](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/core/agent/src/index.ts#L345-L415)

**对本项目的建议：**“不能原位接管”不等于“必须把以前所有步骤重跑”。新内核可以在另行设计的安全交接点读取已确认的消息和产物，创建**新的后续运行**；能否保留哪些状态、避免重复副作用，必须通过跨内核实验验证，不能从 DSH 的单内核 `resume()` 测试推断出来。DSH 的会话事件格式迁移针对存储版本，也不是异构 Agent 内核状态迁移。[会话恢复与 fork 的区别](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/docs/subsystems/persistence.md#L193-L230)
