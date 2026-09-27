# 产品交互参考与本项目取舍

日期：2026-09-24。范围：官方产品说明与交互目标研究，没有完整试用评测，也不推断产品内部架构。

## 参考对象提供了什么启发

用户提出 Codex、WorkBuddy、千问办公作为形态参考。本项目采用“项目/任务导航 + 对话与公开计划 + 产物工作区”的候选布局；这是设计建议，不声称复制它们的内部实现或全量功能。

WorkBuddy 官方产品页描述了任务拆解、工具与本地文件操作，以及项目空间中的多 Agent。我们借鉴的是任务交付与可验收产物的交互目标，不把其宣传中的效率数值当作本项目收益。[腾讯官方产品页](https://cloud.tencent.com/product/workbuddy)

千问办公的桌面帮助页列出文件、技能、连接器、模型选择等入口，同时对部分企业能力的桌面开放范围作了说明。我们借鉴设置与扩展入口的组织方式；不据此推断每个功能在全部平台、全部版本都已可用。[官方桌面帮助](https://qwenwork.cn/docs/desktop)

本轮未对 Codex 作独立竞品能力核验，仅将用户描述的使用体验作为界面目标。对三个产品的性能、市场份额、价格和模型质量不作排序。

## DSH 的对话过程和右侧刻度

2026-09-27 核对了 [DSH Desktop](https://github.com/dataelement/dsh-desktop/tree/eec5d57e658f63431ab312dae3dd30d9d03c4cd4) 当前源码及其依赖的 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/tree/477b4f420553e8a52c2fbccc464d7561b239c443) UI 包。本机 `research/upstreams/dsh-desktop` 锁定在 `69705b23117801389aafb1eab35055dd20744312`，对应较早的 UI 包；下面的判断以当前上游源码为准，**没有运行当前版桌面应用**。

截图中的对话过程分三层：

1. **一轮对话：**运行中显示过程和计时；成功后默认折叠过程，保留用时；失败或取消的过程保持展开。见[展示策略](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/client/ui-chat/src/client/presentation-policy.ts#L24-L53)、[轮次组件](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/client/ui-chat/src/client/chat/ChatNodeSeat.tsx#L62-L110)、[用时显示](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/client/ui-chat/src/client/chat/TurnProcessNodeView.tsx#L14-L65)。
2. **一轮内的过程段：**连续节点被分成多组，每组可独立折叠；展开区域有限高，内容多时在组内滚动。这些组是展示分段，不自动等于 Agent 自己命名的分析步骤。见[分组规则](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/client/ui-chat/src/client/conversation-nodes/process-groups.ts#L119-L169)、[分组组件](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/client/ui-chat/src/client/chat/ChatGroupSeat.tsx#L128-L185)。
3. **具体调用：**工具和 Skill 行默认显示状态与摘要，展开后查看输入输出。见[工具行](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/client/ui-tool/src/client/tool/components/ToolRow.tsx#L149-L171)、[Skill 行](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/client/ui-skill/src/client/SkillRow.tsx#L113-L164)。

右侧短横刻度是[轮次导航](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/client/ui-chat/src/client/chat/TurnNavigator.tsx#L110-L176)：每轮一个刻度，悬停预览，点击跳到该轮；长会话在刻度条内滚动。它不按耗时比例排列。另一个“轨迹”标签页才展示独立的[时间线和事件表](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/client/ui-trajectory/src/client/TrajectoryView.tsx#L510-L575)。

本项目已用 Run 时间和公开事件显示每轮用时、可折叠的模型调用与工具输入输出，并加了按轮次排列的右侧刻度；实现范围见[本机界面](../engineering/04-local-session-ui.md)。现有事件尚无稳定的分析步骤 ID、父步骤 ID 和来源，真实子任务、Skill 与文件事件仍要先由两种内核适配器记录，再决定如何展示。不能把现有扁平事件按顺序硬缩进成子智能体树。这里说的“过程”只包括公开计划、可见进展和工具记录；[本项目的轨迹范围](../architecture/04-workflow-and-trace.md#4-全轨迹展示的内容和界面)不收集或展示模型隐藏推理。

## 当前交互设计

本页保留竞品源码与产品说明的证据。现行布局、分支树、节点详情、回溯与选择操作统一见[工作流与轨迹](../architecture/04-workflow-and-trace.md)。Data 图表与下钻在 D1 接入，产品范围见[产品需求](../product/01-product-brief.md)。
