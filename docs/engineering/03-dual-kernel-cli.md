# 用同一个命令行任务测试两种主内核

这段代码回答一个具体问题：用户指定 `agentscope` 或 `langgraph` 时，两种框架能否分别作为根 Agent，读取同一种 `Task`，调用同一个产品模型接口和授权工具，并输出可查看的事件？目前测试范围是文字任务与一次函数工具往返。

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

CLI 的 `--kernel` 只改变 `Task.kernel` 和选中的适配器。系统提示词、任务文字、产品 `ModelClient`、`ToolService` 与工具授权规则相同。`Runtime` 找不到指定内核时会拒绝执行，不改选另一内核。LangGraph 使用 LangChain 的 [`create_agent`](https://docs.langchain.com/oss/python/langchain/agents) 构建框架 Agent；产品 `BaseChatModel` 适配器把模型请求交给 `ModelClient`，工具仍经产品逐次校验和授权。AgentScope 也通过自己的模型接口接入同一个产品服务。两者都独立启动根任务。

要用本机配置的火山引擎模型测试，把 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 三项放在仓库根目录 `.env`，或作为环境变量整组提供。命令从**当前工作目录**读取 `.env`，所以使用文件配置时须先进入仓库根目录。CLI 只接受 `https://ark.cn-beijing.volces.com/api/coding/v3` 这个 Coding Plan 地址；失败汇总只含异常类型。`--events` 会输出工具参数和结果等任务相关内容，分享日志前须脱敏。真实调用会向模型服务发送任务文字、提示词和工具结果，必须显式写 `--allow-remote`：

```bash
.venv/bin/xuanyue --kernel agentscope --mode live --allow-remote
.venv/bin/xuanyue --kernel langgraph --mode live --allow-remote
```

`--text '你的文字任务'` 可以替换 live 模式的默认合成问题。工具只有一个本地纯计算函数；自定义任务不必调用它，因此下面的固定结果只用于默认问题。当前 CLI 不是通用数据分析入口，也不支持 Claude Messages 格式。

## 本机结果：2026-09-27

同一台 macOS 机器、同一套本机模型配置和合成任务，分别选择两种主内核运行。没有把密钥、模型名、原始输入输出或供应商报错写进仓库。

| 检查 | AgentScope | LangGraph |
| --- | --- | --- |
| 脚本模型合成任务 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 |
| 配置的真实模型，默认合成问题 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 | 答案 `42`，2 次模型调用、1 次工具执行，正常完成 |
| 适配器报告的 `coverage_gap` 事件 | 本次为 0 | 本次为 0 |

产品测试为 15 项通过；`src/` 与 `tests/` 的 Ruff 检查、格式检查通过。[双内核测试](../../tests/test_langgraph.py)检查两边的模型与工具接口、工具结果回传和事件编号，也验证 LangGraph 收到无效工具参数或产品拒绝授权时不会执行工具。真实模型结果是当前配置下的一次窄范围验收，不证明其他供应商或其他任务也会成功。

## 当前限制

- 两种适配器都只接文字和函数工具。LangGraph 桥只实现异步模型调用；它不提供同步调用或实时 token 流。`--events` 中的文字和工具参数来自完整模型回复。
- 任务事件尚未持久化，也没有检查点、节点回溯、等待输入、取消或失败终态。异常时可能只留下 `started` 事件；CLI 的失败汇总只有非零退出码和异常**类型**，框架自身日志仍需在正式产品中单独审查和脱敏。
- LangGraph 本段固定递归上限为 8，AgentScope 本段最多 3 轮 ReAct。它们不是同一种预算；长任务、并发、故障恢复和输出质量还没有对照。
- 模型 token 用量均标为未知；未测延迟、费用、多模态、不同模型的工具协议差异和 Windows/Linux 安装。

因此，这次结果只说明两套框架可以从同一产品输入分别跑完这个最短工具回合。完整的双主内核 MVP 验收线仍以[内核选型](../architecture/02-kernel-selection.md#4-自主任务的双主-mvp-验收线)为准。

依据：本仓库的固定依赖、[LangChain Agents 官方文档](https://docs.langchain.com/oss/python/langchain/agents)及本机运行；文档与版本核对于 2026-09-27。旧的[AgentScope 单内核真实模型验收](02-live-model-smoke.md)保留为上一段的历史记录。
