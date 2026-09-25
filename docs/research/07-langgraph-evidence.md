# LangGraph：检查点与历史分叉证据

[选型结论](../architecture/02-kernel-selection.md) · [共同实验结果](09-framework-comparison.md)

访问/实验日期：2026-09-25。使用 LangGraph 1.2.12、checkpoint-sqlite 3.1.1、Python 3.12.13。源码 tag `1.2.12` 锁到 `49cce0ca852be4cfb567a1cbe0e511ff325a1682`；[依赖与许可清单](../../research/upstreams/framework-sources.json)。

## 官方机制与产品责任

[官方 persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 提供持久状态机制；[官方 time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel) 区分 replay 与 fork：从旧检查点继续会重新执行后续节点；update_state 创建新检查点，原历史保持。中断和外部调用可再次触发，不能把它理解成缓存展示或副作用撤销。子图内部回溯粒度取决于检查点配置，本轮未复现子图内部回溯。

固定源码对应 [Pregel 主实现](https://github.com/langchain-ai/langgraph/blob/49cce0ca852be4cfb567a1cbe0e511ff325a1682/libs/langgraph/langgraph/pregel/main.py) 与 [SQLite checkpointer](https://github.com/langchain-ai/langgraph/tree/49cce0ca852be4cfb567a1cbe0e511ff325a1682/libs/checkpoint-sqlite)。根许可证 MIT；单独模块/传递依赖仍应按发行包核对。

本项目使用原生 StateGraph/conditional edges、SqliteSaver、interrupt/Command、get_state_history、update_state。规范消息、路由允许列表、分支身份、应用公开事件、outbox 和接收方去重由实验代码提供。LangGraph checkpoint 本身不是长期用户画像或沙盒。

## 本机复现

[代码与命令](../../research/spikes/framework-comparison/langgraph/README.md)；首轮 [19 项断言](../../research/spikes/framework-comparison/langgraph/results/2026-09-25-macos-arm64.json)，追加中间节点分叉的 [复核结果](../../research/spikes/framework-comparison/langgraph/results/review-02.json)。共同 F01–F08 均通过。

- 两个分支用线程屏障验证真实重叠，join 收齐两个结果。
- 审批前中断；新 OS 进程恢复后，已完成分支结果仍在。
- 对原 rejected 和 completed 两种状态，从输入检查点修改输入并重跑；原 head 快照哈希保持，历史 ID 仍可查询。
- 追加从 normalize 之后、router 之前的检查点修改路由：normalize 结果保留，只有 router/reject 执行；原完成分支不变。
- 在副作用落盘后、节点完成前强制退出 73；恢复时尝试 2 次，本地接收方唯一键使效果为 1 次。去重归功于应用协议，不归功于调度器。
- 规范消息拒绝未知模态；公开事件带运行/节点/尝试/分支身份；人为非法路由与中断保留状态。

## 外推边界

实验采用单 worker 顺序控制一个 thread 的分支 head，没有证明并发编辑同一 thread 安全。分支标签仍共享实验 case 的 run_id，正式产品需由适配器建立独立 Run/Branch 映射；当前实验只证明底层历史机制。

模型是模拟的，图片是引用占位，不涉及视觉理解。没有验证可视工作流编译、流式模型取消、图版本迁移、断电恢复、远程 exactly-once、实际沙盒、费用节省或 Windows/Linux。不能由本轮通过推出“任意节点均可回溯”或“完整内核已完成”。
