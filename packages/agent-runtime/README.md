# Agent 运行时：第一段产品代码

这里先实现一件事：调用方明确指定 `kernel_id`，产品按登记的内核启动一项文字主任务，并接收可展示的事件。当前只有 AgentScope 适配器；没有登记的内核会报错，不会换用其他内核。接口仍是内部试行版本，尚未覆盖工作流、恢复、委派或完整轨迹。

AgentScope 适配器要求调用方注入创建根 `Agent` 的工厂。模型、工具、权限及其配置由调用方提供；正式代码没有内置合成模型或固定任务。框架事件只提取明确允许展示的字段；未知事件产生不含原始内容的覆盖缺口提示。这个过滤不能替代真实数据脱敏和完整轨迹验收。

在仓库根目录，使用已有的 Python 3.11 / AgentScope 2.0.8 实验环境运行集成测试：

```sh
PYTHONPATH=packages/agent-runtime/src research/spikes/framework-comparison/agentscope/.venv/bin/python -m unittest discover -s tests/agent_runtime -v
```

独立安装时可用 `python -m pip install -e './packages/agent-runtime[agentscope]'`。合成模型、固定测试目标和测试工具只在 `tests/agent_runtime/`；原始可复现实验及失败记录仍在 `research/spikes/agentscope-primary/`。
