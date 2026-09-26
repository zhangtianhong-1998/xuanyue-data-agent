# AgentScope 独立主任务：第一段代码

本实验属于 K0b 的第一个小增量。用户指定 AgentScope 后，真实的 AgentScope `Agent` 接收合成目标，调用一个本地合成工具，并输出公开文字。模型回复由脚本固定，不调用模型服务。它只验证主任务的最短完成路径，不代表双主内核或完整 Data Agent 已可用。

依赖沿用[AgentScope 发布包对照实验](../framework-comparison/agentscope/README.md)锁定的 `agentscope==2.0.8` 和 Python 3.11 环境。在仓库根运行，输出请使用新文件名，保留旧结果：

```sh
research/spikes/framework-comparison/agentscope/.venv/bin/python research/spikes/agentscope-primary/run.py --kernel agentscope --output research/spikes/agentscope-primary/results/local-rerun.json
```

验收包括：实际根对象来自 AgentScope；一次工具调用得到 42；不支持的内核选择明确拒绝，不改用其他内核；合成的 ThinkingBlock 确实经过 AgentScope 事件流，但不进入公开记录。最终文字由脚本预设，尚不能证明真实模型会根据工具结果形成结论。结果中的事件是供这次实验检查的公开片段，不是完整轨迹库；字段白名单也不能代替敏感值脱敏或真实模型的隐藏推理审计。真实模型自主规划、用户补充输入、取消与恢复、子 Agent 委派、A2A 和历史节点分叉尚未测试。下一段代码须经本段评审后再确定。

[正向运行记录](results/review-02.json)为 `passed`；[选择未注册内核](results/review-02-unsupported.json)返回 `unsupported` 和退出码 2，这是预期拒绝，不表示该实验的 AgentScope 路径失败。两份记录都写了脚本和依赖锁的哈希、运行环境及未测试项。

第一次运行在导入阶段失败：脚本误从 `agentscope.tool` 导入 `ToolResultState`，发布包实际从 `agentscope.message` 导出。修正后才开始执行根任务；这次失败只说明实验脚本写错了导入，不说明 AgentScope 不支持该行为。[失败记录](results/attempt-01-failure.json)保留原错误类型和修正，不公开本机绝对路径。
