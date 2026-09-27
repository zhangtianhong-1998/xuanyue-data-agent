# 真实模型最短路径验收

这是 AgentScope 单内核接入时的历史记录。现行 CLI 的模型地址和上游模型名已移到 [TOML 配置](03-dual-kernel-cli.md#配置真实模型)；下文命令与 `.env` 说明保留当时的验证条件。

本页只回答一个问题：**现有 AgentScope 主任务能否通过产品模型接口调用本机配置的火山引擎模型，完成一次文字与工具回合？** 本次使用合成数据，不代表已有可用客户端或数据分析能力。

## 怎样复现

仓库根目录的 `.env` 已被 Git 忽略。测试只读取 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`；也可以用三项同名环境变量**整组**提供，不能与 `.env` 逐项混用。这次验收只允许 Coding Plan 的 `https://ark.cn-beijing.volces.com/api/coding/v3` 地址，避免把密钥发往其他主机。不要把密钥写进命令、代码、截图或提交记录。测试程序不会打印原始请求、原始回复和供应商错误正文。

```bash
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python -e '.[agentscope,live-model]'
.venv/bin/python scripts/live_agent_smoke.py
```

当时的 12 项测试结果保留在下表。现行全量测试已包含 LangGraph 和配置模块，需按[当前安装命令](03-dual-kernel-cli.md#直接运行)装齐依赖后运行。

测试程序固定询问合成的“21 单、每单 2 件”，要求 Agent 调用只读 `multiply` 工具。工具仅授权参数 `orders=21, units_per_order=2`，没有真实数据或外部写入。只有恰好两次模型调用、一次工具执行、工具结果成功、最后回答恰为 `42` 且任务正常结束，脚本才返回成功。

## 本机结果：2026-09-27

运行环境为 macOS Apple Silicon、Python 3.11.15、AgentScope 2.0.8、OpenAI Python SDK 3.19.2。本机 `.env` 指向火山引擎 Coding Plan 的 OpenAI 兼容地址；模型名从 `LLM_MODEL` 读取，未写入公开结果。

| 检查 | 结果 |
| --- | --- |
| 直接调用模型回答合成算术问题 | 接口成功，返回 `5` |
| AgentScope 经产品模型接口调用真实模型 | 2 次模型请求 |
| 模型调用合成只读工具 | 1 次，工具结果成功 |
| Agent 完成答复 | 答复恰为 `42`，以 `completed` 结束 |
| 公开事件覆盖缺口 | 本次为 0 |
| 产品代码测试 | 12 项通过 |
| Ruff 检查与格式检查 | 通过 |

将模型代码拆为路由与 OpenAI 兼容适配器，并显式登记上游模型名后，重新运行同一脱敏验收脚本：2 次模型调用、1 次工具执行、答案 `42`、正常结束、覆盖缺口 0。把产品包改为绝对导入后再次复验，结果相同。这些复验只证明改动没有破坏该合成任务；Claude Messages 尚未接入。

首次在旧研究虚拟环境直接初始化 SDK 时，因本机 SOCKS 代理缺少 `socksio` 而失败；安装该依赖后调用成功。正式复现命令使用仓库根目录 `.venv`，`live-model` 可选依赖已包含它。失败没有被算作模型验证通过。

## 接入边界

产品的 `_AgentScopeModel` 继承 AgentScope `ChatModelBase`，把模型请求交给 `ModelClient`；这不是 AgentScope 内置的 OpenAI 模型客户端。`ChatCompletionsClient` 只处理文字和函数工具。AgentScope 会把历史工具调用与结果累积到一条消息；客户端在发给 Chat Completions 前按顺序拆成 `assistant` 调用和 `tool` 结果。供应商异常会抛给调用方；目前 Agent 事件可能停在 `model_call_started`，没有失败终态，所以验收脚本单独报告脱敏错误类别。产品事件里的用量仍标为未知。双内核的实际兼容状态见[模型接入边界](../../README.md#模型接入边界)。

本次没有验证真实业务数据、长对话、取消与恢复、不同模型的兼容性、图片或其他模态、并发稳定性和账单。部分模型在工具多轮对话中可能要求回传加密思考字段；现有产品消息不保存这种字段，本次成功不能推广到所有模型。是否将 Coding Plan 用于正式 Data Agent 服务，以及正式服务的套餐与费用，需要另行确认。

## 协议依据

以下均为火山引擎官方资料，访问于 2026-09-27：

- [Coding Plan 的 OpenAI 兼容地址及模型配置](https://docs.volcengine.com/docs/ark/coding-plan-personal-ai-zcode?lang=zh)：当时 `.env` 的 `/api/coding/v3` 属于 Coding Plan；不要擅自改为普通 `/api/v3`，后者可能另行计费。具体套餐额度以控制台为准。
- [Coding Plan 的 Chat API 与工具调用示例](https://docs.volcengine.com/docs/ark/coding-plan-personal-ai-workbuddy?lang=zh)：接入示例启用工具调用。
- [Chat API 参数](https://docs.volcengine.com/docs/ark/chat-api?lang=zh)：函数工具请求、`tool_calls` 回复和 `tool_call_id` 回传格式。
- [上线排查说明](https://docs.volcengine.com/docs/ark/go-live-faq?lang=zh)：多轮工具调用时的上下文回传要求。

这些文档说明协议用法；上表的通过结果只来自本机当前配置的一次合成验收。
