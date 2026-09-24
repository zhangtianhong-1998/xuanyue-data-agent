# Agent 执行恢复实验

状态：研究实验，未批准为产品实现。执行日期：2026-09-24（Asia/Shanghai）。

本实验使用真实的 LangGraph 和 SQLite checkpointer。两个分析节点返回写死的合成值，不调用模型、不访问外部服务、不读取业务数据。目标是验证任务执行合同，不能证明分析结论正确。

## 复现

```sh
cd research/spikes/kernel-contract
uv run --frozen --python 3.12 run_experiment.py
```

Python 范围锁为 3.12；本次实际版本为 3.12.13。直接依赖为 `langgraph==1.2.12`、`langgraph-checkpoint-sqlite==3.1.1`，完整传递依赖和制品哈希见 [uv.lock](uv.lock)。依赖环境 `.venv/` 不入 Git。

运行会创建临时 SQLite 文件，退出时清理；仓库仅保存自编代码、锁文件和合成结果。重复运行默认覆盖同名结果文件，可用 `--output results/<name>.json` 保留多次结果。

## 本次结果

[2026-09-24-macos-arm64.json](results/2026-09-24-macos-arm64.json) 中 8 项检查全部通过：

1. `region`、`product` 两节点各产出一次可见结果；不同线程通过屏障相遇，执行区间存在重叠。
2. 报告写入之前触发 `interrupt()`，SQLite 保存待审批状态。
3. 首个 Python 进程退出后，另一个 Python 进程批准并恢复同一任务。
4. 恢复后已完成的两个分析节点没有重复产出。
5. 另一个任务拒绝审批后结束，无报告标记。
6. 报告节点完成模拟外部作用、尚未返回节点结果时，用 `os._exit(73)` 注入突然退出；新进程可从 checkpoint 恢复。
7. 该节点被执行两次，但应用自己定义的 `run_id:report:v1` 唯一键使报告只有一份。
8. 拒绝审批的任务没有在独立作用账本留下报告。

结果保留每个子进程的退出码、公开节点输出、checkpoint ID、最终状态、环境和脚本 SHA-256。`73` 是预期故障注入，不是未记录的失败；若出现任何非预期异常，程序会保留已有记录并以失败退出。

## 验证范围

已验证：当前 macOS ARM64、当前锁定包版本下的并行分支、SQLite 持久化、跨进程审批恢复、指定故障点的重放，以及应用层幂等效果。

未验证：LLM 质量、多 Agent 协作质量、MCP 协议、系统沙箱、Windows/Linux 安装、断电与磁盘损坏、分布式租约、数据库迁移、整棵 Agent 树取消、长期记忆准确性。

恢复可能从节点开头重跑。这里的报告去重来自实验自编的数据库唯一键，不能归因于 LangGraph 提供了所有外部动作的 exactly-once 保证。远程接口、文件导出、云模型调用仍需逐个定义幂等或补偿策略。

参考：[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)。在线文档访问日期均为 2026-09-24；复现以锁文件和本地源码为准。
