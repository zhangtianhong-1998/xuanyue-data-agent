# 用 CLI 测试 Agent 引擎和模型配置

用户指定 `agentscope` 或 `langgraph` 时，两种框架分别作为根 Agent，读取同一种 `Task`，调用同一个产品模型接口和授权工具，并输出可查看的事件。现在还可以登记第三方引擎，并从配置文件选择模型。测试范围仍是文字任务与一次函数工具往返。

## 直接运行

在仓库根目录安装依赖。`pyproject.toml` 固定 AgentScope 2.0.8、LangChain 1.4.0 和 LangGraph 1.2.12；以下命令在 macOS、Python 3.11.15 上核对过。

```bash
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python -e '.[agentscope,langgraph,live-model]'
.venv/bin/xuanyue --kernel agentscope --events
.venv/bin/xuanyue --kernel langgraph --events
.venv/bin/python -m unittest discover -s tests -v
```

默认 `synthetic` 模式不读密钥、不发网络请求。脚本模型先要求调用 `multiply({"orders":21,"units_per_order":2})`，收到工具结果后回答 `42`。每条事件都带 `run_id`、序号、类型和公开载荷；最后一行是汇总。退出码为 0 才表示通过当前合成验收。

CLI 的 `--kernel` 只改变 `Task.kernel` 和选中的引擎。系统提示词、任务文字、产品 `ModelClient`、`ToolService` 与工具授权规则相同。`Runtime` 找不到指定引擎时会拒绝执行，不改选另一内核。LangGraph 使用 LangChain 的 [`create_agent`](https://docs.langchain.com/oss/python/langchain/agents) 构建框架 Agent；产品 `BaseChatModel` 适配器把模型请求交给 `ModelClient`，工具仍经产品逐次校验和授权。AgentScope 也通过自己的模型接口接入同一个产品服务。两者都独立启动根任务。

## 配置真实模型

复制根目录的 [`xuanyue.example.toml`](../../xuanyue.example.toml) 为被 Git 忽略的 `xuanyue.toml`，再填写实际服务地址和上游模型名：

```bash
cp xuanyue.example.toml xuanyue.toml
```

配置分两处：`providers` 指定协议、`base_url` 和密钥环境变量名；`models` 给每个产品模型 ID 绑定供应商与上游模型名。`default_model` 是未传 `--model` 时的选项。可登记多个供应商和模型，例如一个远端模型与一个本地模型；当前能调用的协议只有 `openai_chat_completions`，Claude 原生 Messages 仍未接入。

TOML 不存密钥。将 `api_key_env` 所指向的密钥放到进程环境变量，或配置文件同目录的 `.env`；进程环境变量优先，显式空值会报错。CLI 接受 HTTPS 服务地址，也接受本机回环地址的 HTTP；不再固定火山引擎域名。配置决定密钥发往哪个地址，因此真实调用前要核对 `base_url`。配置字段不完整、模型未登记、协议不支持或 URL 不合规则会直接失败。默认合成模式不读取配置文件或密钥。

真实调用会向配置中的服务发送任务文字、提示词和工具结果，必须显式写 `--allow-remote`。下面用默认模型分别测试；`--config PATH` 指定另一份配置，`--model ID` 选择其中一个产品模型：

```bash
.venv/bin/xuanyue --kernel agentscope --mode live --allow-remote
.venv/bin/xuanyue --kernel langgraph --mode live --allow-remote
```

`--text '你的文字任务'` 可以替换 live 模式的默认合成问题。工具只有一个本地纯计算函数；自定义任务不必调用它，因此下面的固定结果只用于默认问题。常规失败汇总只含异常类型；引擎重名时还会报告冲突名称。`--events` 会输出工具参数和结果等任务相关内容，分享日志前须脱敏。

## 增加一个 Agent 引擎

`AgentScopeKernel` 和 `LangGraphKernel` 本身就是两种引擎适配器；没有再包一层同样的运行接口。它们都实现 [`AgentKernel`](../../src/xuanyue/interfaces.py)：接收 `Task`，返回 `Event` 流。新增框架时，实现这个接口，在构造函数中接收产品的 `ModelClient`、`ToolService` 和系统提示词，并负责该框架的消息、工具和公开事件转换。工具仍须经 `ToolService` 调用。

安装的扩展包可在自己的 `pyproject.toml` 中登记工厂或类：

```toml
[project.entry-points."xuanyue.agent_engines"]
my_core = "my_package.engine:MyCoreEngine"
```

`MyCoreEngine(model, tools, system_prompt)` 的 `id` 必须是 `my_core`。安装该包后，CLI 从入口点发现名称；只有选中它时才加载扩展代码。`EngineRegistry` 拒绝重复名称、返回错误名称的工厂和未知引擎，不会自动改选。重名时 CLI 报告冲突的引擎名称。`Runtime` 检查扩展输出的事件是否属于本次任务、序号是否连续、载荷形状是否正确；事件内容和隐藏推理过滤仍由适配器负责。代码已用一个假的第三方引擎验证登记、发现、根任务派发及错误事件拒绝；**尚未用真实的第三方 Agent 框架或独立安装包验收**。安装包与引擎运行在同一 Python 进程，当前没有扩展代码隔离。

## 本机结果：2026-09-27

同一台 macOS 机器、同一套本机模型配置和合成任务，分别选择两种主内核运行。本轮把原先写在 CLI 里的地址和模型选择移到被忽略的 `xuanyue.toml` 后，又分别跑了一次真实模型任务。没有把密钥、本机模型配置、原始输入输出或供应商报错写进仓库。

| 检查 | AgentScope | LangGraph |
| --- | --- | --- |
| 脚本模型合成任务 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 |
| 配置的真实模型，默认合成问题 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 |
| 适配器报告的 `coverage_gap` 事件 | 本次为 0 | 本次为 0 |

产品测试为 29 项通过；`src/` 与 `tests/` 的 Ruff 检查、格式检查通过。[双内核测试](../../tests/test_langgraph.py)检查两边的模型与工具接口、工具结果回传和事件编号；[引擎测试](../../tests/test_engines.py)检查扩展登记和错误事件；[配置测试](../../tests/test_config.py)检查多个模型、密钥来源和拒绝错误配置；[CLI 测试](../../tests/test_cli.py)确认合成任务不需要配置、远端调用先要求显式授权。真实模型结果是当前配置下的一次窄范围验收，不证明其他供应商或其他任务也会成功。

## 当前限制

- 两种适配器都只接文字和函数工具。LangGraph 桥只实现异步模型调用；它不提供同步调用或实时 token 流。`--events` 中的文字和工具参数来自完整模型回复。
- 任务事件尚未持久化，也没有检查点、节点回溯、等待输入、取消或失败终态。异常时可能只留下 `started` 事件；CLI 的失败汇总仅报告异常**类型**，引擎重名时另报冲突名称。框架自身日志仍需在正式产品中单独审查和脱敏。
- LangGraph 本段固定递归上限为 8，AgentScope 本段最多 3 轮 ReAct。它们不是同一种预算；长任务、并发、故障恢复和输出质量还没有对照。
- 新引擎目前只可接入文字任务接口。入口点提供发现与构造，不替扩展实现权限、轨迹、恢复或沙盒；这些仍需逐项验收。
- 模型 token 用量均标为未知；未测延迟、费用、多模态、不同模型的工具协议差异和 Windows/Linux 安装。

因此，这次结果只说明两套框架可以从同一产品输入分别跑完这个最短工具回合。完整的双主内核 MVP 验收线仍以[内核选型](../architecture/02-kernel-selection.md#4-自主任务的双主-mvp-验收线)为准。

依据：本仓库的固定依赖、本机运行、[LangChain Agents 文档](https://docs.langchain.com/oss/python/langchain/agents)、[PyPA 入口点规范](https://packaging.python.org/en/latest/specifications/entry-points/)与 [Python 3.11 `tomllib` 文档](https://docs.python.org/3.11/library/tomllib.html)；资料访问和代码核对于 2026-09-27。旧的[AgentScope 单内核真实模型验收](02-live-model-smoke.md)保留为上一段的历史记录。
