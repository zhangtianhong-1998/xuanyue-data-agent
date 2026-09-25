# 玄月 Data Agent

这是一个跨平台桌面 Data Agent 项目。先在 Mac 上开发个人可用版本，再验证 Windows 和 Linux。首个业务场景是导入经营数据、发现异常、下钻查看并生成有来源的报告。数据默认留在本地；只有经过授权，才向云模型发送必要内容。

**当前在需求和技术路线评审阶段，尚无完整客户端。** 已确认 LangGraph 和 AgentScope 都要能担任主智能体，也要为更多内核留下接入位置。可插拔接口、工作流调度和各项能力的实现方式仍需逐步验证。

## 项目目标：让仓库本身容易阅读

第一次打开仓库的人，应能在十分钟内找到产品目标、当前阶段、下一步工作和相关证据。设计文档先讲用户会做什么、系统怎样回应；字段、协议和实验细节放在对应的参考页。每项建议标明状态，旧提案保留历史记录，不让读者误认为已经定案。

先读这三篇，其余文件按遇到的问题查阅：

1. [产品目标与阶段](docs/product/01-product-brief.md)：要做什么，先做到哪一步。
2. [整体架构](docs/architecture/01-candidate-architecture.md)：不同 Agent 内核怎样接入同一个产品。
3. [研发流程](docs/engineering/01-development-process.md)：下一步验证什么，如何记录结果。

想查某个术语、技术细节或实验，请从[设计导航](docs/00-discovery-summary.md)进入。

## 目录地图

```text
docs/
  product/       产品目标、阶段和用户故事
  architecture/  架构设计与待评审决策
  research/      上游资料、比较和证据解释
  engineering/   研发步骤与文档维护规则
research/
  spikes/        本项目编写的可复现实验
  upstreams/     单独下载的上游源码，不提交整仓
apps/            客户端边界，方案评审后再实现
packages/        公共模块边界，方案评审后再实现
tests/           产品测试边界，方案评审后再扩充
scripts/         获取和核验上游源码等脚本
```

## 已有实验

[实验索引](research/spikes/README.md)列出可复现的检查、失败和限制。其中，[跨内核 A2A 委派实验](research/spikes/runtime-interoperability/README.md)用合成输入完成了 LangGraph 主智能体调用 AgentScope 子智能体的 10 项检查。它尚未验证 AgentScope 担任主智能体或反向委派。实验通过只说明已测行为，不代表模型质量、产品性能、沙盒隔离或三平台交付已经验收。

普通发布包实验无需下载参考项目。可选运行 `python3 scripts/upstreams.py --fetch` 获取固定版本源码并核验；运行 `python3 scripts/upstreams.py` 只核验已有文件。AgentScope 固定 main 版本的 SOP 预览实验另有源码要求，不能与发布包实验混为一谈。

## Git 与公开范围

设计文档、自编实验、锁文件、合成输入和可公开结果由 Git 管理。第三方整仓、依赖环境、真实业务数据、密钥、运行数据库及用户附件不上传。来源和固定 commit 见[上游说明](research/upstreams/README.md)。

本项目尚未选定开源许可证；上游及依赖许可需分别核验。
