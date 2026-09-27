# 双主内核与模型配置

状态：本机界面预览中的现行接入；内核选型和扩展能力仍待逐段评审。用户创建会话时选择 AgentScope 或 LangGraph 主内核，再选本机已登记的模型。后续提问沿用这两个选择。两种框架各自运行根 Agent，产品负责模型路由、工具授权和公开事件保存。界面的操作与验收见[本机会话预览](04-local-session-ui.md)。

此前为验证双内核和短文字追问写过终端 CLI；**该入口现已移除**。下文保留当时的验收结果作为历史证据，现行入口是 `xuanyue-app`。

## 配置真实模型

界面侧栏底部的“模型设置”可添加供应商接口、登记模型、指定默认模型和填写新密钥。供应商填写 OpenAI Chat Completions 兼容接口地址；模型填写产品内使用的 ID 和供应商要求的上游模型名。保存后，新会话的模型菜单立即使用新目录。已有会话保存的是创建时选定的产品模型 ID；若修改该 ID 对应的接口或上游模型，后续提问会使用新配置。设置页不能删除仍被会话引用的模型。

也可从[配置样例](../../xuanyue.example.toml)复制出被 Git 忽略的 `xuanyue.toml` 手动填写。`providers` 指定协议、`base_url` 和密钥环境变量名；`models` 将产品模型 ID 绑定到供应商与上游模型名；`default_model` 用于未显式选模型的新会话。界面保存时会重新生成这份 TOML，手写注释不会保留。

原有密钥可以留在进程环境变量或配置同目录的 `.env`。界面只显示密钥是否可读取，不返回原值；新填的密钥写入同目录、被 Git 忽略的 `.env.<配置名>.models` 私有文件，现有 `.env` 不被覆盖。留空密钥栏会保留原绑定。API 密钥不会写入 TOML。

当前只接入 `openai_chat_completions`。服务地址须为 HTTPS，或本机回环地址的 HTTP。对于手写配置，进程环境变量优先于 `.env`；显式空值、未知模型、无效 URL 或缺失字段会报错。界面的“已配置”只表示可读取密钥及所需 SDK，不证明服务连通或模型接受本次请求。手工删掉会话绑定的模型后，该会话运行会失败，不会改用默认模型。`xuanyue-app --config PATH` 可指定另一份配置。

模型**明确支持当前图片请求**时，才在该模型条目写 `image_input = true`。省略时默认为 `false`，界面不开放图片按钮；产品不会根据模型名称猜测视觉能力。此开关是本机使用者的配置声明，实际供应商是否接受 PNG/JPEG 图片还须单独验证。图片入口的大小、保存和回放范围见[本机会话预览](04-local-session-ui.md#图片输入这一段)。

## 两种内核怎样接入

`AgentScopeKernel` 和 `LangGraphKernel` 都实现 [`AgentKernel`](../../src/xuanyue/interfaces.py)，接收产品 `Task`，返回公开 `Event` 流。[`EngineRegistry`](../../src/xuanyue/engines/registry.py)按名称创建选定的内核；[`Runtime`](../../src/xuanyue/runtime.py)按任务中的 `kernel` 精确派发，不会自动替换。两种框架通过各自的模型桥调用产品 `ModelClient`，函数工具都经 `ToolService` 校验与授权。[`ModelRouter`](../../src/xuanyue/llm/router.py)再把产品模型 ID 转成供应商模型名，交给当前唯一的 [`ChatCompletionsClient`](../../src/xuanyue/llm/openai_compatible.py)。

当前消息可带文字、单张用户 PNG/JPEG 图片和函数工具往返。图片只在运行内存中转成 Chat Completions 的图像内容块；数据库与公开事件只保存附件引用和元数据。AgentScope 桥依赖固定版本 2.0.8 的 `ChatModelBase` 扩展点；LangGraph 适配器使用 LangChain 1.4.0 的 `create_agent` 和 `BaseChatModel`。升级框架需要重跑相同任务。模型桥不返回实测 token 用量，也没有完整供应商参数、同步模型调用、持久检查点或节点恢复。

## 增加一个 Agent 引擎

引擎代码放在 `src/xuanyue/engines/`，模型协议代码放在 `src/xuanyue/llm/`。新框架实现 `AgentKernel`，在构造函数中接收 `ModelClient`、`ToolService` 和系统提示词，负责转换该框架的消息、工具调用和公开事件。工具仍须经 `ToolService` 执行。

安装的扩展包可用入口点登记工厂或类：

```toml
[project.entry-points."xuanyue.agent_engines"]
my_core = "my_package.engine:MyCoreEngine"
```

`MyCoreEngine(model, tools, system_prompt)` 的 `id` 必须是 `my_core`。只有选中该名称时才加载扩展代码。注册器拒绝重复名称、返回错误名称的工厂和未知引擎；`Runtime` 还检查事件归属、序号和载荷形状。已有假第三方引擎测试覆盖登记、发现、根任务派发与错误事件拒绝；真实第三方框架、独立安装包和扩展代码隔离尚未验收。入口点只负责发现与构造，不能代替权限、轨迹和沙盒实现。

## 验收证据与边界

2026-09-27 的早期验收曾用已移除的 CLI 入口：同一合成算术任务分别由两种内核完成，答案均为 `42`，各有两次模型调用和一次只读工具执行；本机配置的真实模型在默认合成问题上得到相同结果。当时还在终端分别完成两轮短文字追问，第二轮均回答前一轮给出的暗号“蓝鲸七号”。这些结果只说明当时的短任务可运行；CLI 和相应交互测试现已删除。现行本机界面的验收记录集中在[会话预览](04-local-session-ui.md#本机验收记录)。

[双内核测试](../../tests/test_langgraph.py)覆盖两种适配器的模型与工具接口、工具结果及事件编号；[引擎测试](../../tests/test_engines.py)覆盖扩展登记和错误事件；[配置测试](../../tests/test_config.py)覆盖模型目录、密钥来源与错误配置。当前图文切片另检查两种主内核把同一图片交给假模型，以及已完成图片轮次在下一轮的回放。当前真实视觉模型、不同供应商的图像兼容行为、图片语义质量、长会话、并发和成本仍未验收。

完整双主内核任务还须验证等待输入、取消、重启恢复与反向委派；已有短文字、工具和图片请求不能替代这些验收。[内核选型](../architecture/02-kernel-selection.md#4-自主任务的双主-mvp-验收线)列出待验证行为。Claude 原生 Messages 仍是后续候选，它的消息格式与 Chat Completions 不同，需独立适配；[协议说明](https://platform.claude.com/docs/en/api/messages/create)和[兼容层限制](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)是后续评审依据。
