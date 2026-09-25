# DeepSeek Harness 如何做到可插拔

状态：研究证据，非本项目的架构决定。访问日期：2026-09-25。核查对象是 [DeepSeek Harness 官方仓库](https://github.com/deepseek-ai/deepseek-harness)的固定提交 `46a7f68b0922371ce7144b668b90e377d8e799f4`（提交日期 2026-09-23）。本次只审查该版本文档和源码，未运行其产品级验证。

## 上游实际做法

应用由 Cordis 插件树组成。插件可以提供服务、事件和可撤销的注册；profile 组合 bundle 与 patch，后续 patch 能替换配置项。一个可替换的能力由**服务定义、提供方、消费者**组成。消费者按服务名调用，通过 `inject` 等待依赖就绪；提供方卸载时，依赖它的插件会卸载，服务恢复后再启动。[架构说明](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/docs/architecture.md#L9-L41)、[服务教程](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/docs/cordis-tutorial/03-services.md#L1-L78)、[接缝定义](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/docs/architecture.md#L130-L133)。

## 不能直接类推的部分

- **主 Agent：**`AgentFactory` 定义创建和恢复，上层通过 `ctx.agents` 调用，不直接依赖默认循环；但注册表只有一个 factory 槽位，重复注册会报错。这证明默认循环可替换，不能证明同一 Context 已能按任务选择多个主内核。[接口](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/core/agent/src/index.ts#L165-L203)、[注册源码](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/core/agent/src/index.ts#L245-L248)、[重复注册检查](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/core/agent/src/index.ts#L350-L373)。
- **子 Agent：**不同提供方按名称注册，可以共存；服务在启动前核对能力，缺少必需能力就报错。这是最接近本项目多内核选择需求的接法，但它针对的是子任务。[提供方接口](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/subagent/subagent/src/types.ts#L336-L389)、[注册和分派](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/subagent/subagent/src/index.ts#L505-L570)、[能力预检](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/subagent/subagent/src/index.ts#L643-L660)。
- **工作流：**`workflowEngine` 是每个 Context 一个服务，没有具名提供方注册表。它支持替换引擎，但不能据此宣称每个任务能单独选择引擎。[接缝清单](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/docs/capability-seams.md#L652)、[接口](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/workflow/workflow/src/index.ts#L150-L168)。

**对本项目的推断：**可借鉴稳定接口、可替换提供方和调用前能力核对。用户要求 LangGraph、AgentScope 都能担任主 Agent，未来还能加入其他内核，因此本项目仍需设计任务级多内核注册与绑定，以及统一任务对象和转换边界。哪些边界要独立适配、运行中的任务如何切换，留给后续设计与实验验证。
