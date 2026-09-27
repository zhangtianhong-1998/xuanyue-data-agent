# 用 CLI 连续测试 Agent 对话

用户在终端选择 `agentscope` 或 `langgraph`，输入自己的问题并继续追问。两种框架分别作为根 Agent，读取同一种 `Task`，调用同一个产品模型和授权工具接口。本段只验证进程内的文字多轮会话；桌面界面、持久记忆和数据分析尚未实现。

## 配置后开始对话

在仓库根目录安装依赖。首次配置时，可将[配置样例](../../xuanyue.example.toml)复制为被 Git 忽略的 `xuanyue.toml`，填写实际服务地址和上游模型名；已有本机配置不要覆盖。密钥放在进程环境变量或配置文件同目录的 `.env`，不要写进 TOML。

```bash
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python -e '.[agentscope,langgraph,live-model]'
.venv/bin/xuanyue --kernel agentscope
.venv/bin/xuanyue --kernel langgraph
```

在终端直接运行时，默认模式会读取配置并连接真实模型。用户每输入一轮，就授权向配置的服务发送本轮文字、此前已完成的用户与助手文字，以及完成任务所需的系统提示和工具结果。回答后可继续提问；空行不创建任务，输入 `/exit` 或 `/quit`，或按 Ctrl-D，结束当前会话。管道等非终端输入必须额外指定 `--allow-remote`，避免脚本在无人确认时调用远端。`--events` 同时显示公开事件 JSON，便于检查模型与工具调用；事件可能含用户输入和工具参数，分享日志前应脱敏。CLI 显示的文字片段来自完整模型回复，不是实时 token。

会话历史只在运行中的 CLI 进程里保存**已完成的用户和助手文字**。失败的一轮不加入后续上下文；退出后不会保存会话，也不会恢复旧工具调用的细节或框架检查点。这是验证追问是否真的带上前文的最小行为，并不等于产品记忆能力。

## 配置真实模型

配置分两处：`providers` 指定协议、`base_url` 和密钥环境变量名；`models` 给每个产品模型 ID 绑定供应商与上游模型名。`default_model` 是未传 `--model` 时的选项。可登记多个供应商和模型，例如一个远端模型与一个本地模型；当前能调用的协议只有 `openai_chat_completions`，Claude 原生 Messages 仍未接入。

`api_key_env` 指向的密钥由进程环境变量或配置同目录的 `.env` 提供；进程环境变量优先，显式空值会报错。CLI 接受 HTTPS 服务地址，也接受本机回环地址的 HTTP；不固定火山引擎域名。配置决定密钥和会话文字发往哪个地址，因此开始对话前应核对 `base_url`。配置字段不完整、模型未登记、协议不支持或 URL 不合规则会直接失败。`--config PATH` 指定另一份配置，`--model ID` 选择其中一个产品模型。

## 固定验收与一次性调用

原来的 21×2 脚本模型仍可显式运行，用于不读配置、不发网络请求地检查两种引擎的工具往返：

```bash
.venv/bin/xuanyue --kernel agentscope --mode synthetic --events
.venv/bin/xuanyue --kernel langgraph --mode synthetic --events
```

脚本模型先要求调用 `multiply({"orders":21,"units_per_order":2})`，收到工具结果后回答 `42`。`--events` 的每条公开事件带 `run_id`、序号、类型和载荷；固定任务通过时才以退出码 0 结束。它不是自由对话模型，不能用合成结果证明真实提问有效。

保留一次性真实模型调用；该模式仍须显式写 `--allow-remote`：

```bash
.venv/bin/xuanyue --kernel agentscope --mode live --allow-remote
.venv/bin/xuanyue --kernel langgraph --mode live --allow-remote
```

`--text '你的文字任务'` 可以替换一次性 live 模式的默认合成问题。工具目前只有一个本地纯计算函数；自定义任务不必调用它，因此下面的固定结果只适用于默认问题。两种模式都可用 `--events` 查看轨迹。常规失败汇总只含异常类型；引擎重名时还会报告冲突名称。

CLI 的 `--kernel` 指定产品 `Task.kernel`，不会自动改选另一内核。LangGraph 使用 LangChain 的 [`create_agent`](https://docs.langchain.com/oss/python/langchain/agents) 构建框架 Agent；产品模型适配器把请求交给 `ModelClient`，工具仍经产品逐次校验和授权。AgentScope 也通过自己的模型接口接入同一个产品服务。两者都独立启动根任务。

## 增加一个 Agent 引擎

引擎代码与模型接入代码一样，分别放在 `src/xuanyue/engines/` 和 `src/xuanyue/llm/`。引擎包里的 [`registry.py`](../../src/xuanyue/engines/registry.py)负责登记和创建；[`agentscope.py`](../../src/xuanyue/engines/agentscope.py)与 [`langgraph.py`](../../src/xuanyue/engines/langgraph.py)分别转换框架消息、工具和公开事件。包入口只导出注册器，选中引擎时才导入对应框架适配器。产品层的 [`interfaces.py`](../../src/xuanyue/interfaces.py)与 [`runtime.py`](../../src/xuanyue/runtime.py)留在 `src/xuanyue/`。这次只是整理文件位置，不改变 CLI 命令、接口或运行行为。

`AgentScopeKernel` 和 `LangGraphKernel` 本身就是两种引擎适配器；没有再包一层同样的运行接口。它们都实现 `AgentKernel`：接收 `Task`，返回 `Event` 流。新增框架时，实现这个接口，在构造函数中接收产品的 `ModelClient`、`ToolService` 和系统提示词，并负责该框架的消息、工具和公开事件转换。工具仍须经 `ToolService` 调用。

安装的扩展包可在自己的 `pyproject.toml` 中登记工厂或类：

```toml
[project.entry-points."xuanyue.agent_engines"]
my_core = "my_package.engine:MyCoreEngine"
```

`MyCoreEngine(model, tools, system_prompt)` 的 `id` 必须是 `my_core`。安装该包后，CLI 从入口点发现名称；只有选中它时才加载扩展代码。`EngineRegistry` 拒绝重复名称、返回错误名称的工厂和未知引擎，不会自动改选。重名时 CLI 报告冲突的引擎名称。`Runtime` 检查扩展输出的事件是否属于本次任务、序号是否连续、载荷形状是否正确；事件内容和隐藏推理过滤仍由适配器负责。代码已用一个假的第三方引擎验证登记、发现、根任务派发及错误事件拒绝；**尚未用真实的第三方 Agent 框架或独立安装包验收**。安装包与引擎运行在同一 Python 进程，当前没有扩展代码隔离。

## 已完成的单次任务验收：2026-09-27

同一台 macOS 机器、同一套本机模型配置和合成任务，分别选择两种主内核运行。上一段把原先写在 CLI 里的地址和模型选择移到被忽略的 `xuanyue.toml` 后，又分别跑了一次真实模型任务。没有把密钥、本机模型配置、原始输入输出或供应商报错写进仓库。

| 检查 | AgentScope | LangGraph |
| --- | --- | --- |
| 脚本模型合成任务 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 |
| 配置的真实模型，默认合成问题 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 |
| 适配器报告的 `coverage_gap` 事件 | 本次为 0 | 本次为 0 |

当时产品测试为 29 项通过；`src/` 与 `tests/` 的 Ruff 检查、格式检查通过。[双内核测试](../../tests/test_langgraph.py)检查两边的模型与工具接口、工具结果回传和事件编号；[引擎测试](../../tests/test_engines.py)检查扩展登记和错误事件；[配置测试](../../tests/test_config.py)检查多个模型、密钥来源和拒绝错误配置。这些是引入交互会话前的单次任务结果，不能用来证明连续追问已经通过。真实模型结果只覆盖当前配置下的默认问题，不证明其他供应商或其他任务也会成功。

## 本段多轮对话验收：2026-09-27

在同一台 Mac 上，分别以 AgentScope 和 LangGraph 作为主内核，向当前配置的真实模型连续输入两轮：先请它记住“蓝鲸七号”，再追问刚才的暗号。两种内核都先答“收到”，第二轮回答“蓝鲸七号”。在终端直接运行 `.venv/bin/xuanyue --kernel agentscope --events`，也已看到输入提示、当前轮事件和助手答复；这条命令不再自动执行固定的 21×2 题目。此例只验证当前模型和短文字历史，不能证明长会话或其他任务的表现。

当前 34 项产品测试和 Ruff 检查、格式检查通过。[会话历史测试](../../tests/test_conversation.py)检查两套真实适配器的第二轮模型请求都按顺序包含旧用户文字、旧助手文字和新问题；[交互测试](../../tests/test_chat_cli.py)检查空行、失败轮、空答复与退出；[CLI 测试](../../tests/test_cli.py)检查显式离线模式和远端调用边界。失败轮次不会进入后续历史。

本次引擎目录整理后，重新运行 34 项产品测试及 Ruff 检查、格式检查，均通过。AgentScope 与 LangGraph 的显式合成模式 CLI 各得到 `42`、2 次模型调用和 1 次工具执行；单独导入 `xuanyue.engines` 不会预先加载两套框架 SDK。`uv build --wheel` 成功，构建清单包含 `engines/` 下的四个 Python 文件。这些复验只证明整理后现有合成路径与打包仍可运行，不是新增的业务能力验收。

## 当前限制

- 两种适配器都只接文字和函数工具。LangGraph 桥只实现异步模型调用；它不提供同步调用或实时 token 流。`--events` 中的文字和工具参数来自完整模型回复。
- 会话只在当前进程里保留已完成的用户与助手文字，内核在启动 CLI 时选定，本段尚不能在同一会话中切换；也不保留先前工具调用细节。任务事件未持久化，没有检查点、节点回溯、Agent 执行中暂停等待补充输入、取消或失败终态。异常时可能只留下 `started` 事件；CLI 的失败汇总仅报告异常**类型**，引擎重名时另报冲突名称。框架自身日志仍需在正式产品中单独审查和脱敏。
- LangGraph 本段固定递归上限为 8，AgentScope 本段最多 3 轮 ReAct。它们不是同一种预算；长任务、并发、故障恢复和输出质量还没有对照。
- 新引擎目前只可接入文字任务接口。入口点提供发现与构造，不替扩展实现权限、轨迹、恢复或沙盒；这些仍需逐项验收。
- 模型 token 用量均标为未知；未测延迟、费用、多模态、不同模型的工具协议差异和 Windows/Linux 安装。

先前结果只说明两套框架可以从同一产品输入分别跑完最短工具回合；新增的进程内文字追问也不能代替完整的双主内核 MVP。[内核选型](../architecture/02-kernel-selection.md#4-自主任务的双主-mvp-验收线)列出仍需验证的行为。

依据：本仓库的固定依赖、本机运行、[LangChain Agents 文档](https://docs.langchain.com/oss/python/langchain/agents)、[PyPA 入口点规范](https://packaging.python.org/en/latest/specifications/entry-points/)与 [Python 3.11 `tomllib` 文档](https://docs.python.org/3.11/library/tomllib.html)；资料访问和代码核对于 2026-09-27。旧的[AgentScope 单内核真实模型验收](02-live-model-smoke.md)保留为上一段的历史记录。
