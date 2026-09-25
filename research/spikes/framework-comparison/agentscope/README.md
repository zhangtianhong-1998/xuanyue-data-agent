# AgentScope 发布包对照实验

固定 `agentscope==2.0.8`，传递依赖在 `requirements.lock`。实际记录环境为 Python 3.11.15；不与 Python 3.12 的其他路线比较性能。无模型密钥，ScriptedModel 只替换模型边界，真实 Agent、工具、权限、事件和 State API 仍被调用。

在仓库根运行：

```sh
uv venv --python 3.11 research/spikes/framework-comparison/agentscope/.venv
uv pip sync --python research/spikes/framework-comparison/agentscope/.venv/bin/python research/spikes/framework-comparison/agentscope/requirements.lock
research/spikes/framework-comparison/agentscope/.venv/bin/python research/spikes/framework-comparison/agentscope/run.py --output research/spikes/framework-comparison/agentscope/results/local-rerun
```

Windows 的 venv Python 路径不同；本轮仅 Mac 验证。锁文件固定版本，尚未为 wheel 增加逐文件下载哈希。`--output` 用新目录保留历史。

当前有效结果为 `results/attempt-03/`：7 passed，F05 为 unsupported_without_custom_code。attempt-01 的 AnyUrl 报告序列化错误、attempt-02 的 PermissionDecision 调用错误及未捕获模型异常保留；这些是实验适配问题，不是上游功能缺陷证明。attempt-02/run.py 是当时脚本快照；attempt-01 未保存完整脚本，只保存明确错误记录。公开错误记录中本地工程绝对路径已替换为 `$PROJECT_ROOT`。

F03 只验证同一 Agent 的两个工具并发，F04 是应用保存 AgentState 后恢复。F07 单 worker 去重不是通用 exactly-once。F08 用应用身份信封捕获异常并保存，不能称框架自带全轨迹产品。图片只序列化引用，未解析像素。

固定 main 源码有 SOP/ModelRouter，不是本实验发布包功能。源码事实见[证据页](../../../../docs/research/06-agentscope-evidence.md)。

## 固定 main 的独立 SOP 预览

先按上游清单获取源码，再运行：

```sh
research/spikes/framework-comparison/agentscope/.venv/bin/python research/spikes/framework-comparison/agentscope/probe_source_preview.py
```

脚本检查固定 commit，并从源码导入 SOPEngine；使用发布包实验的锁定依赖环境。6 条检查通过，结果 `results/source-preview.json`。Step 是自编合成执行器；没有验证真实模型 verifier、ModelRouter、任意 DAG 或历史分叉。该结果与 wheel F01–F08 分开报告。
