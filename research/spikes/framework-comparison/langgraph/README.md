# LangGraph 对照实验

依赖、Python 要求见 `pyproject.toml` / `uv.lock`。从仓库根运行：

```sh
uv run --project research/spikes/framework-comparison/langgraph --frozen --python 3.12 research/spikes/framework-comparison/langgraph/run_experiment.py --output research/spikes/framework-comparison/langgraph/results/local-rerun.json
```

真实 LangGraph 1.2.12、SQLite checkpointer 3.1.1；所有模型与数据为合成值。每阶段使用独立 OS 进程，数据库用临时目录，结束删除。输出目录保存公开事件、版本、脚本/场景/锁文件哈希和所有异常状态。

首轮 `results/2026-09-25-macos-arm64.json` 有 19 项断言，代码快照 `results/initial-run.py`；`results/review-02.json` 增加中间检查点分叉，当前 run_experiment.py 对应这次复核。预设非法路由和 exit 73 用来证明失败与恢复，不是隐藏的成功路径。

F05 同时检查已拒绝和已完成分支历史保留，以及中间 checkpoint 路由修改。规范消息、事件、分支命名、允许路由、outbox/去重都是应用代码。并发分支的重叠用屏障验证。没有真实模型或沙盒，也未验证任意动态图、同 thread 并发编辑、所有子图回溯、Windows/Linux。
