# 玄月 Data Agent

跨平台桌面 Data Agent 研究项目。先确定并验证 Agent 内核，再接入经营数据分析。个人使用优先，数据本地保存，必要内容经授权调用云模型。

**当前阶段：双主智能体需求核对与可复现实验，尚无完整客户端。** LangGraph 和 AgentScope 都须能担任主智能体；工作流调度实现待双主实验后评审，不把单向委派结果视为已批准路线。

## 从这里读

[设计入口：产品目标 → 双主内核选型 → 核心对象 → 多内核与 A2A → 工作流与轨迹](docs/00-discovery-summary.md)

入口同时链接阶段范围、50 条用户故事、实验依据和待讨论事项。每个主题有一处详细定义，其他页面只引用。

## 复现实验

新增[跨内核 A2A 委派实验](research/spikes/runtime-interoperability/README.md)。原有 [AgentScope / LangGraph / 手写对照](docs/research/09-framework-comparison.md)及[沙盒可用性探测](research/spikes/sandbox-probe/README.md)各有独立命令和依赖说明。均使用合成输入，不需要模型密钥。

第一轮 [Data 查询、内核恢复、MCP 实验](docs/research/04-experiment-results.md)保留。实验通过只证明声明的局部行为，不代表模型质量、产品性能、沙盒隔离或三系统验收已完成。

普通发布包实验无需下载参考项目。可选执行 `python3 scripts/upstreams.py --fetch` 获取固定源码并核验；`python3 scripts/upstreams.py` 只核验已有 checkout。AgentScope main 的独立 SOP 预览实验需要相应源码，不能与其发布包混称。

## 公开范围

设计、自编实验、锁文件、合成输入和公开结果由 Git 管理。第三方整仓、依赖环境、真实业务数据、密钥、运行数据库及用户附件不上传。来源和固定 commit 见[上游说明](research/upstreams/README.md)。

本项目尚未选定开源许可证；上游及依赖许可需分别核验。
