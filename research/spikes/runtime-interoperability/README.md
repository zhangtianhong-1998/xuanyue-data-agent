# LangGraph 主流程调用 AgentScope 子 Agent：单向互通实验

本实验只验证 **LangGraph 主流程→AgentScope 子 Agent** 这一方向。父流程使用真实 LangGraph 1.2.12，子进程使用真实 AgentScope 2.0.8 Agent 与工具循环，两端通过官方 `a2a-sdk==1.1.5` 的 HTTP JSON-RPC 通信。模型响应固定，工具只计算合成整数，没有外部模型调用。AgentScope 担任主 Agent、委派 LangGraph 子 Agent 的反向调用尚未实验；当前待评审要求见 [ADR-0004](../../../docs/architecture/decisions/ADR-0004-dual-primary-kernel.md)。

最新结果：[review-05/results.json](results/review-05/results.json)，10 项通过。这个结果支持一个方向的跨内核委派，不证明反向委派、双主 Agent 切换，或两个框架的工作流、检查点和内部状态可以互换。

## 运行

需要 `uv` 和 Python 3.12。在本目录执行：

```sh
uv sync --project parent --frozen --python 3.12
uv sync --project child --frozen --python 3.12
uv run --python 3.12 --no-project run.py --output results/local-rerun-01
```

输出目录必须不存在，避免覆盖过去的失败结果。首次同步需要下载依赖；实验运行只访问 `127.0.0.1` 随机端口。父、子使用独立 Python 环境和操作系统进程。临时 SQLite、映射文件和原始日志位于系统临时目录，结束后清理；结果中的本机路径和端口经过替换。子进程只继承运行必需的环境变量，不继承模型服务密钥。

## 验证了什么

| 检查 | 实际行为 | 能力来自哪里 |
|---|---|---|
| I01 | LangGraph 节点通过 HTTP 调用 AgentScope；子 Agent 真实执行工具并完成 | 两个框架原生循环 + 应用适配 |
| I02 | A2A Artifact 包含 text 与 structured data；父端得到统一产物 | A2A Part + 应用投影 |
| I03 | 父 run、delegation、子 task/context 的 ID 能关联 | 应用 metadata 和映射文件 |
| I04 | 服务端实际观察到 `A2A-Version: 1.0`、`SendStreamingMessage`、`GetTask` | 官方 SDK |
| I05 | 子端请求输入，父端停在 LangGraph interrupt；新的父进程补充输入，继续同一子 task | A2A input-required + LangGraph interrupt + 应用适配 |
| I06 | 子任务完成后、父节点检查点写入前，父进程以 73 退出；恢复时查询原 task，子 Agent execute 次数仍为 1 | LangGraph 检查点 + 应用映射 + GetTask |
| I07 | 用新的边界输入创建新 run/task，原任务查询内容保持不变 | 应用新建尝试 + A2A task identity |
| I08 | 取消正在等待的工具协程；观察到工具收到取消、执行器确认、工具未完成 | SDK 取消 + AgentScope 处理 + 应用取消标记 |
| I09 | 请求未声明支持的历史节点 fork，返回明确的不支持错误 | 应用能力检查 |
| I10 | 无效结构化字段、未注册的图片 Part 转换均被拒绝 | 应用 schema 校验和模态检查 |

实现入口：父端 [worker.py](parent/worker.py)、子端 [server.py](child/server.py)、实验调度与验收 [run.py](run.py)。父端有意使用一个小型固定适配器；尚未实现产品运行时接口的全部功能。

## 失败与修正

- [attempt-01](results/attempt-01/results.json)：7 项通过，取消检查失败，最后 1 项未执行。当时外层等待 `CancelledError`；AgentScope 内部接收取消后会结束事件流，外层没有得到预期异常，并且错误地进入成功产物检查。保留了事件、错误和当时的三个[源文件](results/attempt-01/source/run.py)。该轮取消证据中的 `actual_cancelled_coroutine: true` 是当时脚本的固定标签，不能用来证明工具确实停止；以 `status: failed` 和原始事件为准。
- [attempt-02](results/attempt-02/results.json)：在工具协程直接观察取消，增加执行器取消标记，阻止产生成功产物；9 项通过。保留这一轮[源文件](results/attempt-02/source/run.py)。
- [attempt-03](results/attempt-03/results.json)：在相同验收基础上增加结构化数据 schema 与未知模态拒绝，10 项通过；实际安装版本另行读取包元数据记录，源文件和锁文件的 SHA-256 在结果中。
- [review-04](results/review-04/results.json) 与 [review-05](results/review-05/results.json)：交付前分别重跑，均为 10 项通过。两轮之间只移除调度脚本的一处行末空格；旧脚本另存快照，最新结果的源码哈希对应当前文件。历史快照保留当时内容，不作格式整理。

取消修正没有把“收到取消请求”当作“工具已停止”。本实验分别记录了它们。但 A2A SDK 的取消处理会先调用应用 `cancel()`，随后取消执行；本适配器在 `cancel()` 中发送 canceled 状态。因此，它仍不满足产品层“先确认整个执行树停止，再显示已取消”的完整约束。

## 尚未验证

- 子服务使用 SDK `InMemoryTaskStore`。恢复实验只重启父进程，子服务保持运行；不支持据此声称子端崩溃后也能恢复。
- 映射文件普通写入，没有原子替换、fsync、事务或重入锁。这里只验证文件已写完后的指定崩溃点。请求已发出但尚未取得 task ID 的结果不明窗口仍存在；SDK `message_id` 未在本实验中提供幂等去重保证。
- `input-required` 是 A2A 执行器主动停下，与父端 interrupt 配合；不是把 AgentScope 原生检查点迁移到 LangGraph。
- I07 是用新输入开始新任务。没有验证跨内核检查点迁移、任意历史节点 fork、两套完整工作流互换或运行中更换内核。
- 所有父级步骤均由 LangGraph 承担。没有验证 AgentScope 作为主 Agent 接收目标、委派 LangGraph 子 Agent、恢复父任务与汇总结果；也没有验证新任务在两种主 Agent 间选择并维持同一产品契约。
- 取消只覆盖可响应 `asyncio` 取消的合成工具。没有验证取消与完成的竞争、不可中断计算、操作系统子进程树或外部副作用撤销。
- 子端可见事件存在本地实验日志中；它们没有自动变成 A2A 对外事件。远程 Agent 的内部全轨迹仍需约定扩展，不能由收到 task/artifact 推定内部步骤完整。
- 未验证鉴权、TLS、远程重连与事件缺口恢复、沙盒、预算共享、画像写入、真实模型质量或费用。

## 版本依据

访问日期：2026-09-25。实际运行时间见结果 `date`。

- [官方 Python SDK 发布 1.1.5](https://github.com/a2aproject/a2a-python/releases/tag/v1.1.5)，固定 commit `9f0f00cb0417cb59958d3186d81651be7d9b9d59`；本实验安装 PyPI 发布包，两份 `uv.lock` 锁定依赖与制品哈希。
- [官方服务器教程](https://a2a-protocol.org/latest/tutorials/python/5-start-server/)与[客户端教程](https://a2a-protocol.org/latest/tutorials/python/6-interact-with-server/)提供 SDK 1.x 接入方式；本实验的网关、关联和恢复逻辑由项目自行实现。
- [A2A v1.0.1 规范](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)；协议协商使用 Major.Minor `1.0`，不把 SDK 包版本 `1.1.5` 当作协议版本。

主仓库没有复制 A2A、LangGraph 或 AgentScope 的第三方源码。实验结果是工程证据，不代表业务分析正确，也不构成正式的生产可用认证。
