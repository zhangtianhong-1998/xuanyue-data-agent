# 双主内核与模型配置

状态：本机界面预览中的现行接入；内核选型和扩展能力仍待逐段评审。用户创建会话时选择 AgentScope 或 LangGraph 主内核，再选本机已登记的模型。后续可在输入框右下角切换模型与推理选项，下一轮生效；主内核保持原选择。两种框架各自运行根 Agent，产品负责模型路由、工具授权和公开事件保存。界面的操作与验收见[本机会话预览](04-local-session-ui.md)。

此前为验证双内核和短文字追问写过终端 CLI；**该入口现已移除**。下文保留当时的验收结果作为历史证据，现行入口是 `xuanyue-app`。

## 配置模型并在会话中切换

例如，先用文字模型讨论分析任务，再选图文模型查看截图。点击输入框右下角的“模型名＋推理选项”可搜索、切换模型，或选择当前模型的推理选项。菜单优先向上展开；管理供应商和型号的“模型设置”位于菜单底部，侧栏底部也保留入口。两个主内核共用这套配置。

设置页左侧选择或添加供应商，右侧填写供应商名称、API 地址和 API Key，然后在该供应商下添加模型。供应商名称可自行填写，例如“火山引擎”，不区分本地或云端类别。模型 ID 填供应商实际提供的型号或推理接入点；显示名称可另填，留空时使用模型 ID。用于保存会话的内部标识由产品生成，修改显示名称不会改变模型归属、密钥引用或已有会话绑定。

模型类型分为“文字 LLM”和“图文 VLM”：前者只接收文字，后者允许用户 PNG/JPEG 图片。这里的 VLM 只指图文输入，不包含音频、视频或图像生成。模型类型与推理选项分别配置，产品不会根据名称猜测能力。模型列表默认折叠，展开后编辑类型、容量和推理选项。同一供应商可以配置多个模型，不同供应商也可提供相同型号。

已有会话可以在两轮之间切换模型。切换后采用目标模型的默认推理选项，不沿用旧模型的强度；用户也可从当前模型登记的选项中另选。运行中不能切换。会话已有完成的图片问答时，不能改为文字模型，否则下一轮会丢失需要回放的图片；输入框仍有待发送图片时，界面同样禁用文字模型。

### 输入容量与输出上限

每个模型可填写“最大输入 token”和“最大输出 token”，均为可选正整数；留空不替用户猜测模型规格。

- **最大输入 token** 保存为 `max_input_tokens`，表示用户声明的模型输入容量。本段尚未接入各供应商的精确分词或运行前容量检查，填写它不会限制本地输入长度，也不会自动压缩或截断历史。完整请求超限时仍由供应商拒绝。它不等于包含输入和输出的总上下文窗口。
- **最大输出 token** 保存为 `max_output_tokens`，会作用于每次模型请求，包括工具续接和自动标题。“高级设置”中的 `output_token_parameter` 决定发送 `max_tokens` 还是 `max_completion_tokens`，只发送所选的一项；默认字段为 `max_tokens`。应按具体型号的协议选择，产品不根据供应商名称猜测。未填写输出数值时，两项都不发。

两种输出参数的计数口径不一定相同；例如 OpenAI 的 `max_completion_tokens` 包含可见输出和推理 token。配置界面不将它们换算为相同的回答长度。供应商以 `finish_reason=length` 结束时，该轮仍按不完整回复处理，不能显示为成功完成。运行期间改配置，当前回合及其标题继续使用启动时的值，下一轮才采用新值。

本段不会为已有火山引擎型号补填未经核实的容量或推理选项。容量声明、输出配额和实际模型支持范围需要分别核对。

### 推理选项怎么配置

各家供应商甚至同一家不同型号的参数都可能不同。设置页允许为每个模型登记若干选项，每项包含显示名、稳定 ID 和要发送的原生参数。例如，将显示名“高”绑定到该型号支持的 `effort = "high"`；只有供应商明确支持时才登记它。**登记成功只表示配置合法，不代表服务已验证支持。**

| 配置字段 | 当前含义 |
| --- | --- |
| `reasoning_options[].id` / `label` | 本模型内的选项 ID 与菜单显示名；ID 不可使用保留值 `default` |
| `reasoning_options[].effort` | 原样发送为 Chat Completions 顶层 `reasoning_effort`，不转换为其他供应商的同名档位 |
| `reasoning_options[].thinking` | 经 OpenAI SDK 的 `extra_body` 发送为 `thinking.type`，可填 `enabled`、`disabled`、`auto` |
| `default_reasoning` | 新会话选中该模型、或已有会话切到该模型时使用的选项 ID；省略时为 `default` |

本切片接受的 effort 字段值是 `none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`。这是字段校验范围；聊天菜单只列出该模型实际登记的选项。配置校验会拒绝空选项、重复 ID、未知值以及 `thinking = "disabled"` 搭配非 `none` 强度等冲突。供应商是否支持其余组合，仍要核对具体型号的协议。

菜单里的“供应商默认”不会发送这两个参数，**不等于关闭思考**。需要关闭时，须登记供应商明确支持的关闭参数。当前不支持任意 JSON 扩展参数、按 token 设置思考预算、自动发现模型能力，或把各家参数统一换算为低中高。

设置保存后，模型菜单更新。若删掉会话已选的推理选项，下一次发送会明确报错，用户须重新选择；不会静默换档。每个新 Run 保存所选产品模型 ID、推理选项 ID 和当次参数，后续改配置不会改写它们。旧 Run 没有的字段记为默认选项和空参数，不能据此推断当时供应商实际用了哪个档位。

这些记录**只是本切片的参数快照**，尚未保存完整的接口地址、上游型号、配置版本和内核版本，也未完成[修复计划 B1 的运行绑定快照](05-code-review-remediation.md)。同一产品模型 ID 的供应商地址或上游型号被修改后，旧会话后续提问仍会使用新绑定；外发目的地授权另按 B2 处理。

### 配置文件与密钥

也可从[配置样例](../../xuanyue.example.toml)复制出被 Git 忽略的 `xuanyue.toml` 手动填写。`providers` 指定协议、`base_url` 和密钥环境变量名；`models` 将产品模型 ID 绑定到供应商与上游模型名；`default_model` 用于未显式选模型的新会话。图片能力使用 `image_input = true`，省略时为 `false`。设置页会重新生成 TOML，手写注释不会保留。

在已登记的模型条目中，可按供应商实际支持的字段增加选项；下面仅演示结构，不能直接作为任意型号的兼容配置：

```toml
[models.example]
provider = "example"
upstream_model = "replace-with-provider-model-id"
image_input = false
default_reasoning = "default"

[[models.example.reasoning_options]]
id = "high"
label = "高"
effort = "high"
```

原有密钥可以留在进程环境变量或配置同目录的 `.env`。界面只显示密钥是否可读取，不返回原值；新填的密钥写入同目录、被 Git 忽略的 `.env.<配置名>.models` 私有文件，现有 `.env` 不被覆盖。留空密钥栏会保留原绑定。API 密钥不会写入 TOML。

服务地址须为 HTTPS，或本机回环地址的 HTTP。对于手写配置，进程环境变量优先于 `.env`；显式空值、未知模型、无效 URL 或缺失字段会报错。界面的“已配置”只表示可读取密钥及所需 SDK，不证明服务连通、图文能力或推理选项可用。设置页不能删除仍被会话引用的模型；手工删除后，该会话运行会失败，不会改用默认模型。`xuanyue-app --config PATH` 可指定另一份配置。图片大小、保存和回放范围见[本机会话预览](04-local-session-ui.md#图片输入这一段)。

### 当前协议限制与参考依据

本切片只支持 `openai_chat_completions`；Claude 原生 Messages、OpenAI Responses 和供应商私有续接内容尚未接入。不能由“OpenAI 兼容”推导出任意供应商、任意型号都兼容两套 Agent 的工具调用。

尤其要区分“发送推理参数”和“正确完成带工具的连续对话”。当前消息模型不保存私有 `reasoning_content` 或 `encrypted_content`。带工具的请求一旦返回非空 `reasoning_content`，或任意请求返回 `encrypted_content`，客户端会明确停止并让 Run 记为失败，界面提示该续接形式尚不支持；不会丢掉这些字段后继续调用，也不会把它们显示在公开轨迹中。因此，登记思考选项并不意味着 DeepSeek、方舟等思考模型的工具续接已经可用。纯文字无工具请求不回放原始推理，但这不能替代 Agent 场景的兼容验收。

本次参考日期为 **2026-10-07**，范围为上游源码与官方协议阅读，未调用真实供应商：

- **DeepSeek Harness**：复核本地固定 commit `46a7f68b0922371ce7144b668b90e377d8e799f4`。[模型目录](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/llm/llm-pi-ai/src/catalog.ts#L578-L609)将输入模态和推理档位分开声明，并注明能力声明不等于接口探测；[适配器](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/llm/llm-pi-ai/src/adapter.ts#L157-L200)拒绝未支持的档位，[会话控制](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/acp/acp/src/model-control.ts#L99-L132)按当前模型提供选项。本项目借鉴这三项行为，尚未移植它的模型目录、协议覆盖或完整交互。
- **OpenAI**：[Chat Completions 参数](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)使用顶层 `reasoning_effort`；[推理模型说明](https://developers.openai.com/api/docs/guides/reasoning)明确可选档位依型号而异，部分型号的工具调用须用 Responses。应按实际型号核对，不能把字段枚举当成全型号支持表。
- **DeepSeek**：[API 参数](https://api-docs.deepseek.com/api/create-chat-completion/)与[思考模式说明](https://api-docs.deepseek.com/guides/thinking_mode/)区分 `thinking.type` 开关和 effort；原生档位为 `none/low/high/max`，兼容输入可能映射到相同档位。带 `tools` 的后续请求要求完整回传历史 `reasoning_content`，包括没有调用工具的轮次；缺失可返回 400。当前产品尚不满足这项续接要求。
- **火山方舟**：[深度思考说明](https://docs.volcengine.com/docs/ark/deep-thinking?lang=zh)按型号列出 thinking、effort 与兼容映射。启用 thinking summary 的型号可能返回摘要与 `encrypted_content`；工具续接优先使用加密块，省略可能不报错但影响效果。当前产品对这类返回明确失败，未宣称支持。

供应商设置这一段另参考 DSH 同一固定 commit 的 [ProviderEditor](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/client/ui-settings-models/src/client/ProviderEditor.tsx) 和 [ModelRow](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/client/ui-settings-models/src/client/ModelRow.tsx)：供应商连接与其模型列表放在一起，型号、显示名、容量和输入类型在单模型内编辑。本项目使用自己的配置格式，没有移植上游模型目录或协议覆盖。输出字段依据 [OpenAI Chat API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[火山 Chat API](https://docs.volcengine.com/docs/ark/chat-api?lang=zh)和[上下文管理](https://docs.volcengine.com/docs/ark/context-management?lang=zh)核对；查阅日期 2026-10-07，火山网页正文由官方搜索结果读取，直接打开仅返回脚本页面。

## 两种内核怎样接入

`AgentScopeKernel` 和 `LangGraphKernel` 都实现 [`AgentKernel`](../../src/xuanyue/interfaces.py)，接收产品 `Task`，返回公开 `Event` 流。[`EngineRegistry`](../../src/xuanyue/engines/registry.py)按名称创建选定的内核；[`Runtime`](../../src/xuanyue/runtime.py)按任务中的 `kernel` 精确派发，不会自动替换。两种框架通过各自的模型桥调用产品 `ModelClient`，函数工具都经 `ToolService` 校验与授权。[`ModelRouter`](../../src/xuanyue/llm/router.py)再把产品模型 ID 转成供应商模型名，交给当前唯一的 [`ChatCompletionsClient`](../../src/xuanyue/llm/openai_compatible.py)。

当前消息可带文字、最多四张用户 PNG/JPEG 图片和函数工具往返。图片只在运行内存中转成 Chat Completions 的图像内容块；数据库与公开事件只保存附件引用和元数据。AgentScope 桥依赖固定版本 2.0.8 的 `ChatModelBase` 扩展点；LangGraph 适配器使用 LangChain 1.4.0 的 `create_agent` 和 `BaseChatModel`。升级框架需要重跑相同任务。推理参数由产品会话服务交给 LLM 客户端，两套框架桥无需各写一套供应商映射。模型桥尚不返回实测 token 用量，也没有同步模型调用、持久检查点或节点恢复。

## 增加一个 Agent 引擎

引擎代码放在 `src/xuanyue/engines/`，模型协议代码放在 `src/xuanyue/llm/`。新框架实现 `AgentKernel`，在构造函数中接收 `ModelClient`、`ToolService` 和系统提示词，负责转换该框架的消息、工具调用和公开事件。工具仍须经 `ToolService` 执行。

安装的扩展包可用入口点登记工厂或类：

```toml
[project.entry-points."xuanyue.agent_engines"]
my_core = "my_package.engine:MyCoreEngine"
```

`MyCoreEngine(model, tools, system_prompt)` 的 `id` 必须是 `my_core`。只有选中该名称时才加载扩展代码。注册器拒绝重复名称、返回错误名称的工厂和未知引擎；`Runtime` 还检查事件归属、序号和载荷形状。已有假第三方引擎测试覆盖登记、发现、根任务派发与错误事件拒绝；真实第三方框架、独立安装包和扩展代码隔离尚未验收。入口点只负责发现与构造，不能代替权限、轨迹和沙盒实现。

## 验收证据与边界

### 2026-10-07 按供应商配置模型与容量

状态：**已实现，合成验收通过，待用户审阅**。131 项 Python 测试、6 项既有前端测试、Ruff、格式检查、TypeScript 和构建通过。设置页按供应商组织模型；名称可编辑，接口和密钥由供应商保存，输入/输出容量由模型保存。新 Run 还保存当轮的模型显示名，后续改名不改写旧回复；旧记录没有名称时保留内部模型 ID，不用今天的目录补造名称。这仍不等于完整的运行绑定快照。

- [供应商配置测试](../../tests/test_provider_config.py)覆盖中文名称保存重读、改名后的稳定绑定、两供应商同型号独立路由、公开配置脱敏、容量非法值不修改配置或凭据、运行名称冻结及旧库迁移。
- [输出上限测试](../../tests/test_model_limits.py)核对 SDK 实际编码的 HTTP JSON，覆盖流式和非流式、两种输出字段、留空不发送、输出截断失败及上下文拒绝。AgentScope、LangGraph 分别完成工具回合，检查工具续接与标题参数，且运行中改配置不影响本轮，下一轮采用新值。输入容量不外发、不触发历史截断。
- 隔离浏览器完成新增供应商、添加图文模型、设置 token 上限、改名、保存重开和聊天切换；新模型的合成算术任务回答 `42`。非法 `0`、`1e` 被拒绝，切换供应商不丢失无效草稿。390px 窗口下表单和底部按钮可使用。

未调用真实供应商，也未测输入容量的精确预检。用户现有火山引擎配置仅补供应商显示名称，地址、密钥引用、型号与原有能力声明保持原值，未加入测试模型。模型设置界面能读取这份配置，不代表真实模型连通性或参数兼容性已验收。

### 2026-10-07 输入框模型菜单调整

状态：**已实现并完成本机界面验收，待用户审阅**。本段只改前端：将输入框上方的模型、强度和设置三个入口合并到右下角，放在圆形发送按钮之前；附件按钮留在左下角。重选当前模型保留已选档位，切换到其他模型仍采用目标默认项。供应商默认时，按钮只显示模型名，具体选项在菜单中查看。

布局对照用户提供的 Codex、DSH 截图，并复核 DSH 固定 commit `46a7f68b0922371ce7144b668b90e377d8e799f4` 的 [InputBar](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/client/ui-conversation/src/client/skeleton/InputBar.tsx#L449-L455) 与 [ModelSelect](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/client/ui-model-selection/src/client/ModelSelect.tsx#L1-L18)，查阅日期为 2026-10-07。本段采用单个弹层展示模型列表和当前模型的推理选项，没有照搬 DSH 的多级菜单。

- TypeScript、生产构建及 6 项既有前端测试通过；本段未改 Python，也未重跑上一段的 114 项 Python 测试。
- 隔离浏览器使用临时配置、临时数据库和本机假供应商，验证搜索、键盘选择、Escape 关闭、设置入口、缺密钥选项禁用，以及保存后的焦点恢复。重选同一模型不会重置档位。
- 390px 宽窗口下，长模型名省略显示，发送按钮保留，菜单没有超出窗口；模型列表可以滚动。
- 两种主内核通过新入口切换模型后，分别发送“21 单，每单 2 件，一共多少件？”，均显示 `42`、2 次模型调用、1 次工具调用。运行中不能切换模型，完成后恢复。

[本次实际界面截图](assets/composer-model-menu-20261007.jpg)使用合成模型名称。本段只覆盖模型选择交互，未调用真实供应商，未增加协议或模型能力。供应商参数与私有续接的限制仍按下文记录。

### 2026-10-07 模型配置与切换验收

状态：**本切片已实现，合成验收通过，待用户审阅**。114 项 Python 测试、6 项既有前端测试、Ruff、格式检查、TypeScript 与构建通过。使用临时配置、临时数据库和本机假供应商，未读取真实密钥或调用云模型。

- [HTTP 参数测试](../../tests/test_inference.py)检查流式与非流式请求真正发送的 JSON：默认不带参数，effort 原值保留，thinking 位于顶层对象；非法或冲突组合提前拒绝。
- [双内核集成测试](../../tests/test_chat_service.py)分别让 AgentScope、LangGraph 完成带工具的回合，再换到另一模型发图片。后续请求使用新型号与新参数，并保留历史文字；旧 Run 的模型和参数不变。工具等待期间修改配置，续接仍使用该轮启动时的值；标题请求也使用同一组参数。工具模式返回私有推理时，两内核都保存为 failed，未执行工具，公开记录没有合成私有标记。
- [配置和 API 测试](../../tests/test_config.py)、[会话接口测试](../../tests/test_server.py)、[存储测试](../../tests/test_storage.py)覆盖保存后重读、非法输入不改文件、运行中切换被拒绝、图片历史阻止切文字模型，以及旧库和半迁移库恢复。
- 隔离浏览器中，模型菜单区分文字 LLM 与图文 VLM；切到 VLM 后图片按钮可用，推理菜单采用该模型默认项。修改选项显示名并保存后，AgentScope 的新答复显示新名称；LangGraph 的旧答复保留旧选择。两者均显示 `42`、2 次模型调用和 1 次工具调用。运行时选择控件禁用，完成后恢复。

这些结果证明本切片的配置传递和状态保存，不证明真实模型接受所选参数、图片理解正确或推理强度带来质量提升。未测真实供应商费用、延迟和兼容性，未实现私有续接、Claude 原生协议或 Responses。[原审查复核脚本](../../research/spikes/design-alignment-audit/README.md)本次重跑后，A1 长历史仍完整；A2 工具异常正文与 A3 多记调用仍可复现，继续留在修复计划中。

### 历史验收

2026-09-27 的早期验收曾用已移除的 CLI 入口：同一合成算术任务分别由两种内核完成，答案均为 `42`，各有两次模型调用和一次只读工具执行；本机配置的真实模型在默认合成问题上得到相同结果。当时还在终端分别完成两轮短文字追问，第二轮均回答前一轮给出的暗号“蓝鲸七号”。这些结果只说明当时的短任务可运行；CLI 和相应交互测试现已删除。现行本机界面的验收记录集中在[会话预览](04-local-session-ui.md#本机验收记录)。

[双内核测试](../../tests/test_langgraph.py)覆盖两种适配器的模型与工具接口、工具结果及事件编号；[引擎测试](../../tests/test_engines.py)覆盖扩展登记和错误事件；[配置测试](../../tests/test_config.py)覆盖模型目录、密钥来源与错误配置。图文测试检查两种主内核收到图片的顺序，以及已完成图文轮次在下一轮的回放。真实视觉模型、不同供应商的图像兼容行为、图片语义质量、长会话、并发和成本仍未验收。

完整双主内核任务还须验证等待输入、取消、重启恢复与反向委派；已有短文字、工具和图片请求不能替代这些验收。[内核选型](../architecture/02-kernel-selection.md#4-自主任务的双主-mvp-验收线)列出待验证行为。Claude 原生 Messages 仍是后续候选，它的消息格式与 Chat Completions 不同，需独立适配；[协议说明](https://platform.claude.com/docs/en/api/messages/create)和[兼容层限制](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)是后续评审依据。
