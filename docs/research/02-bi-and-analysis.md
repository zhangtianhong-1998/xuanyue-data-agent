# BI、数据分析与 workflow 组件调研

状态：D1 组件候选，待评审；内核先行，范围见[产品需求](../product/01-product-brief.md)。访问日期：2026-09-24（Asia/Shanghai）。

本轮围绕已确认的D1 业务版场景：个人在桌面端导入经营数据，发现异常、下钻核查、生成报告；数据保存在本地，向云模型发送必要内容须经授权。本文的组件建议尚未成为实施决定。

## 1. 建议先采用的组合

**建议：以 DuckDB 本地查询、ECharts 结果图表、TanStack Table 明细表作为D1 业务版主线。** 将指标口径、查询、筛选历史和证据保存为应用自己的协议。可视分析编辑器后续在 Perspective 与 Graphic Walker 之间做一次小规模对比；两套组件不同时成为D1 业务版依赖。React Flow 用于展示和编辑分析步骤，任务执行、权限和恢复交给 Agent runtime。

这项建议依据个人开发的成本与经营分析场景作出，并非组件性能测试结论：

- 用户首先需要核对“为什么下降、哪些门店贡献最大、数据是否完整”，结果图、明细表和可回退的下钻已能覆盖主要路径。
- 将完整 BI 平台打包进桌面客户端，会增加独立服务、身份认证、元数据存储和升级维护。Superset、Metabase 更适合作为未来已有 BI 系统的接入对象。
- 完整拖拽分析器有现成能力，但它的查询协议、字段类型、计算方式和品牌条款会影响产品。先验证这些边界，再决定是否纳入主界面。

D1 业务版应交付一个有证据的分析任务，而不是图表数量清单。例如：“9 月收入下降 12%，其中华东区金额减少最多”必须同时给出比较期、指标公式、筛选范围、样本完整性、查询结果和可点开的明细。这里的“金额减少最多”属于分解结果，不是业务因果结论。

## 2. 组件分工与候选比较

下表的“事实”来自本轮官方资料或源码；“判断”是本项目的选型意见。未进行跨平台运行、性能或完整许可证依赖审计。

| 组件 | 已核验的事实与用途 | 本项目判断及接入成本 | 当前处理 |
| --- | --- | --- | --- |
| Apache ECharts | 图表事件、dataset 和编码映射可用于交互图表。点击后的查询与状态切换由应用处理。[事件](https://echarts.apache.org/handbook/en/concepts/event/)；[dataset](https://echarts.apache.org/handbook/en/concepts/dataset/) | 适合趋势、对比、贡献分解等结果视图；应用自己持有 drill path。需实现一层 ChartSpec 适配器，禁止模型直接注入函数型 option | D1 业务版首选 |
| Vega-Lite | 声明式可视化规范；参数支持点选、框选以及联动过滤。[规范](https://vega.github.io/vega-lite/docs/spec.html)；[参数](https://vega.github.io/vega-lite/docs/parameter.html) | 适合规则化生成、分面、小多图和可视探索。它的表达式、数据 URL、变换仍需限制。不要D1 业务版同时维护 ECharts 与 Vega-Lite 全量语法 | 保留为替代 renderer；若确定 Graphic Walker，随其引入 |
| Perspective | 固定源码 v5.5.1 存在分组、透视、过滤、保存/恢复及 DuckDB Virtual Server 适配路径；可将查询下推给外部引擎。[Virtual Servers](https://github.com/perspective-dev/perspective/blob/1fc0c8373574a478195ebc69302cafeb924f7876/docs/md/explanation/virtual_servers.md) | 适合透视表优先的交互分析。跨组件状态同步、桌面 WebView 的 WASM、IPC、查询审计需要实测。优先评估复用本地 DuckDB 的方案，避免再维护一份独立数据副本 | 首轮可视分析 spike 的优先候选 |
| Graphic Walker | React 可嵌入分析器；固定源码公开 computation(payload) 接口，支持过滤、变换、聚合和排序；提供图表规范及拖拽字段交互。[仓库](https://github.com/Kanaries/graphic-walker) | 适合接近 Tableau 的手工探索；需映射查询 DSL、限制外部调用并确认品牌许可。内置 AI 不应绕过产品统一模型网关 | 次选候选；许可和外发边界通过后再考虑 |
| PyGWalker | Python/DataFrame 到交互分析界面的入口，与 Graphic Walker 生态相关。[官方 README](https://github.com/Kanaries/pygwalker/blob/main/README.md) | 适合 Notebook/研究实验；桌面主界面若已是 React，直接嵌入 Graphic Walker 可减少一层 Python UI 桥接。不能将 PyGWalker 根许可证推及所有打包资源 | 研究工具备选，不作主 UI |
| DuckDB | 进程内分析数据库，支持扩展；官方采用 MIT。[项目说明](https://www.duckdb.org/why_duckdb) | 适合本地文件聚合与复算。具体 Node/Python/Rust binding 跟随内核路线评审；先统一查询协议，不同时引入多个查询引擎 | D1 业务版查询引擎首选 |
| TanStack Table | Headless 表格，提供状态和逻辑，DOM、样式由应用实现。[文档](https://tanstack.com/table/v8/docs/overview) | 适合可控的明细表、分页、排序、筛选；虚拟滚动、键盘交互、导出与透视 UI 需要另做。D1 业务版只做高频功能 | D1 业务版表格首选；该证据是 v8 文档，实施前重锁版本 |
| AG Grid | Community 和 Enterprise 的许可不同；行分组、透视、集成图表等属于 Enterprise。[功能表](https://www.ag-grid.com/javascript-data-grid/key-features/)；[透视](https://www.ag-grid.com/javascript-data-grid/pivoting/) | 若之后需要复杂表格且接受采购，可能节省交互开发时间。不能用 Community 免费许可估算完整 BI 功能成本 | 暂不选；保留采购路线 |
| React Flow | 自定义节点与边，支持图中数据更新和节点连接读取；官方还提供浏览器内 computing flows 示例。[指南](https://reactflow.dev/learn/advanced-use/computing-flows) | 可以画工作流和编辑连接；持久任务、重试、取消、并发、权限、断点恢复需应用实现。不要把 UI 节点回调当生产执行器 | workflow 编辑器候选 |
| Apache Superset | Embedded SDK 面向已有 Superset 仪表板；需要开启嵌入、配置来源域、由服务端获取 guest token。[嵌入文档](https://superset.apache.org/user-docs/using-superset/embedding/) | 对D1 业务版个人桌面产品偏重。未来企业若已有 Superset，可接其服务；单独抽取插件还需检查其依赖及适配成本 | 接入对象，不内置整个系统 |
| Metabase | 官方完整应用嵌入和模块化 React SDK 属于 Pro/Enterprise；完整应用嵌入还涉及身份与 SSO。[完整应用](https://www.metabase.com/docs/latest/embedding/full-app-embedding)；[SDK](https://www.metabase.com/docs/latest/embedding/dashboard?use_case=ea) | 有现成问数/下钻体验，但服务、许可和身份体系都要接入。个人D1 业务版不适合依赖其完整嵌入方案 | 接入对象，不作核心 |

### 2.1 已核验的许可证边界

这是工程选型记录，**未进行法律审核**。只确认以下文件或官方页面的声明，不代表已经审核传递依赖、字体、商标、地图、付费模板、云服务或安装包再分发条款。实际选定版本后应生成依赖清单与 notices。

| 项目 | 本轮看到的声明 | 证据与限制 |
| --- | --- | --- |
| ECharts | Apache-2.0；LICENSE 另列子组件条款 | [LICENSE](https://github.com/apache/echarts/blob/master/LICENSE)，master 浮动引用 |
| Vega-Lite | BSD-3-Clause | [LICENSE](https://github.com/vega/vega-lite/blob/main/LICENSE)，main 浮动引用 |
| Perspective | Apache-2.0 | [固定 LICENSE.md](https://github.com/perspective-dev/perspective/blob/1fc0c8373574a478195ebc69302cafeb924f7876/LICENSE.md) |
| Graphic Walker | LICENSE 为 Apache-2.0；另有 GWBL LICENSE2 | [固定 LICENSE](https://github.com/Kanaries/graphic-walker/blob/f936a695321ad5dd34e0f7c785b6e928bd3f4334/LICENSE)；[固定 LICENSE2](https://github.com/Kanaries/graphic-walker/blob/f936a695321ad5dd34e0f7c785b6e928bd3f4334/LICENSE2)。README 将 LICENSE2 指向 logos，但 LICENSE2 正文涉及软件品牌展示、logo 保留和白标授权；适用范围需要确认，不能只写“Apache，可随意白标” |
| PyGWalker | 根 LICENSE 为 Apache-2.0 | [LICENSE](https://github.com/Kanaries/pygwalker/blob/main/LICENSE)；打包的 Graphic Walker 资源仍需单查 |
| DuckDB | MIT | [LICENSE 正文](https://raw.githubusercontent.com/duckdb/duckdb/main/LICENSE)；[官方 FAQ](https://duckdb.org/faq) |
| TanStack Table | MIT | [LICENSE](https://github.com/TanStack/table/blob/main/LICENSE) |
| AG Grid | Community 为 MIT，Enterprise 需商业 EULA | [Community LICENSE](https://raw.githubusercontent.com/ag-grid/ag-grid/latest/packages/ag-grid-community/LICENSE.txt)；[官方区分](https://www.ag-grid.com/vue-data-grid/community-vs-enterprise/)；未记录价格，采购时另核 |
| React Flow | 主仓库 MIT | [LICENSE](https://github.com/xyflow/xyflow/blob/main/LICENSE)；[官方示例页](https://reactflow.dev/examples)另区分免费和 Pro 示例 |
| Superset | Apache-2.0，LICENSE 另列字体子组件 | [LICENSE.txt](https://github.com/apache/superset/blob/master/LICENSE.txt) |
| Metabase | 开源版 AGPL；iframe 嵌入另有嵌入许可/商业许可选择；Enterprise 为商业许可 | [官方许可页](https://www.metabase.com/license)。不在本轮推断 AGPL 对本产品具体传播范围 |

## 3. 两个下载仓库的源码检查

源码只保存于 `research/upstreams/`，各自保留 Git 历史。主仓库只保存 [bi-sources.json](../../research/upstreams/bi-sources.json) 中的锁定信息。两个浅克隆都已退出成功，`git fsck --connectivity-only` 通过、工作树干净、origin 匹配；没有 `.gitmodules`。未安装依赖、未执行上游构建脚本，也未宣称组件运行验证通过。

### 3.1 Graphic Walker：可接自有查询，但 AI 和品牌需要单独检查

- 版本：package 声明 `0.5.2`；commit `f936a695321ad5dd34e0f7c785b6e928bd3f4334`；提交时间 `2026-09-08T03:36:03-07:00`。这是源码快照，不等于已核验 npm 同版本内容一致。
- `packages/graphic-walker/src/interfaces.ts:468` 定义 `IComputationFunction`；`:604–631` 将查询表示为 filter / transform / view / sort 步骤及 limit / offset；`:685–697` 定义持久图表结构。这给本地查询适配留出入口。[固定源码](https://github.com/Kanaries/graphic-walker/blob/f936a695321ad5dd34e0f7c785b6e928bd3f4334/packages/graphic-walker/src/interfaces.ts#L468)
- `packages/graphic-walker/src/components/dataBoard.tsx:15–44` 将选中图元形成的过滤条件与已有 viewFilters 合并后再调用 computation。这个机制可支持从图到明细，但未证明复杂聚合、空值、多表关联下的所有 drill-through 都符合我们的口径。[固定源码](https://github.com/Kanaries/graphic-walker/blob/f936a695321ad5dd34e0f7c785b6e928bd3f4334/packages/graphic-walker/src/components/dataBoard.tsx#L15)
- `packages/graphic-walker/src/store/visualSpecStore.ts:583–600` 创建时间下钻字段并传入时区偏移。需要用月底、跨年、夏令时的测试值验证，不能只凭轴标签判断时间聚合正确。[固定源码](https://github.com/Kanaries/graphic-walker/blob/f936a695321ad5dd34e0f7c785b6e928bd3f4334/packages/graphic-walker/src/store/visualSpecStore.ts#L583)
- `packages/graphic-walker/src/components/askViz/index.tsx:13–31` 有 Kanaries 生产 API 地址，请求包含字段元数据与用户问题；`components/chat/index.tsx:18–29,170` 也包含 chat API 路径。**这是条件调用路径的源码事实，不能推断“组件一加载就上传数据”。** 若采用，禁用这些入口或接统一模型网关，并实测网络请求；字段名和聊天内容也属于外发范围。[AskViz](https://github.com/Kanaries/graphic-walker/blob/f936a695321ad5dd34e0f7c785b6e928bd3f4334/packages/graphic-walker/src/components/askViz/index.tsx#L13)；[Chat](https://github.com/Kanaries/graphic-walker/blob/f936a695321ad5dd34e0f7c785b6e928bd3f4334/packages/graphic-walker/src/components/chat/index.tsx#L18)
- README 的 Data Explainer 能力是项目方声明。本轮没有验证解释质量，不据此承诺“自动根因发现”。

### 3.2 Perspective：优先验证透视和查询下推，不能绕过查询服务

- 版本和 tag：`5.5.1` / `v5.5.1`；commit `1fc0c8373574a478195ebc69302cafeb924f7876`；提交时间 `2026-09-17T21:12:44-04:00`。
- `rust/perspective-client/src/rust/config/view_config.rs:163–216` 定义 group_by、split_by、columns、filter 等更新结构；`rust/perspective-viewer/src/rust/custom_elements/viewer.rs:1091,1331` 提供 restore/save。保存组件配置仍需附带我们自己的 dataset snapshot、指标版本和过滤历史。[ViewConfig](https://github.com/perspective-dev/perspective/blob/1fc0c8373574a478195ebc69302cafeb924f7876/rust/perspective-client/src/rust/config/view_config.rs#L163)；[Viewer](https://github.com/perspective-dev/perspective/blob/1fc0c8373574a478195ebc69302cafeb924f7876/rust/perspective-viewer/src/rust/custom_elements/viewer.rs#L1091)
- `docs/md/explanation/virtual_servers.md:3–14,45–48` 说明可把分组、筛选等查询下推给外部引擎，且按 handler 声明的能力调整 UI。它提供设计方向，不代表我们已经测过本地 IPC。[固定文档](https://github.com/perspective-dev/perspective/blob/1fc0c8373574a478195ebc69302cafeb924f7876/docs/md/explanation/virtual_servers.md#L3)
- `rust/perspective-python/perspective/virtual_servers/duckdb.py:238–266` 构造视图并执行 SQL、把查询结果转 Arrow IPC；`:332–347` 的错误/debug 日志会记录 SQL。若复用这条路径，查询授权、资源配额、日志脱敏和取消需要覆盖到 handler，不能让 UI 直接取得无限制数据库连接。[固定源码](https://github.com/perspective-dev/perspective/blob/1fc0c8373574a478195ebc69302cafeb924f7876/rust/perspective-python/perspective/virtual_servers/duckdb.py#L238)
- 当前适配存在视图创建/删除，不能简单假定“把连接设为只读就全部能用”。候选做法是受控分析 worker 内维护临时视图，限制数据集和操作；持久导入数据由单独的文件导入流程管理。具体方案待 spike。
- `docs/md/how_to/javascript/virtual_server/duckdb.md:30–48` 的浏览器示例从 CDN 取 WASM，并专门设置空值排序。产品应打包本地资源，明确排序与类型语义，再验证离线和三平台行为。[固定文档](https://github.com/perspective-dev/perspective/blob/1fc0c8373574a478195ebc69302cafeb924f7876/docs/md/how_to/javascript/virtual_server/duckdb.md#L30)

## 4. 领域协议

QuerySpec、ChartSpec、下钻状态和来源字段的详细草案已移至[Data 领域协议](../architecture/07-data-contracts.md)，本页只保留组件证据。

## 5. 分析方法与工作流

分析方法见[专页](../architecture/02-analysis-methods.md)，工作流编排见[专页](../architecture/04-workflow-and-trace.md)。BI 是 D1 扩展；可视流程编排属于 K1，不能因 BI 后置而推迟。

## 7. 下一轮小实验及停止条件

本轮完成官方资料与固定源码核验，**没有完成下表的 UI/性能实验**。进入实现前先选定最影响方案的两项。所有输入用合成数据，结果保留失败与未验证项。

| Spike | 要回答的问题 | 最小实验 | 建议通过条件 | 当前状态 |
| --- | --- | --- | --- | --- |
| BI-01 可视分析适配 | Perspective 或 Graphic Walker 能否全部走产品查询服务？ | 固定相同经营数据；执行 group/filter/sort/pivot/保存恢复；记录实际请求；对照 DuckDB 结果 | 聚合和空值语义一致；不支持功能显式禁用；每次交互可定位 QuerySpec；无未经授权的外发 | 待做 |
| BI-02 图表下钻一致性 | 图表、明细、面包屑、报告能否引用同一结果？ | ECharts + TanStack Table：区域→门店→明细→返回；插入 NULL、同名门店、退款、零分母、重复订单 | 守恒规则通过；回退恢复原状态；比率/去重不错误加总；报告数字完全可追溯 | 待做；可接主实验 `data-analysis-contract` 的合成输入 |
| BI-03 桌面性能和离线 | 目标桌面环境是否够用？ | 在记录硬件上测试 10 万/100 万行、宽表和高基数透视；冷启动、峰值 RSS、首次图、筛选 p50/p95、取消耗时；断网运行 | 指标先记录再协商预算；超预算有分页/缩减/拒绝；资源本地打包；macOS/Windows/Linux 分别记录，未跑的平台标未验证 | 待做 |
| BI-04 分析正确性 | 系统会不会给错归因？ | 固定盲测集含缺失数据、季节变化、结构变化、重叠切片、Simpson 悖论和真实无异常案例 | 分开统计计算正确率、异常误报漏报、解释证据支持率；不能用单一总分代替 | 待做 |
| BI-05 workflow 恢复 | 中断后是否重复执行或引用旧数据？ | 在查询、外发授权、报告写入处故障注入；重启后恢复；更改数据 hash 再运行 | 已成功产物可核验；失败/取消/跳过保留；无重复副作用；失效缓存不命中 | 待做 |

若 Graphic Walker 的品牌范围无法满足产品定位，继续用 ECharts/TanStack 或评估 Perspective，不需要为了一个可视编辑器改变整体许可策略。若 Perspective 的本地 IPC/受控查询适配成本过高，D1 业务版保留结果图与基础下钻即可。

## 8. 本轮证据范围

- 现场核验：以上官方文档、许可证页面；两个固定 commit 的接口、实现路径及静态外发线索。
- 现场获取：Graphic Walker 和 Perspective 两个浅克隆，连接性、origin、工作树、版本和源码位置已记录。
- 未验证：完整依赖许可证、组件运行表现、UI 美观度、性能、跨平台打包、商业采购条款的法律适用、分析方法在真实经营数据上的效果。
- 浮动页面：除两个固定源码仓库外，官方文档链接多数是 `latest/main/master`；使用时以本轮访问日期为准，实施阶段必须锁版本并重查。
- 文档读取遇到 DuckDB 旧 FAQ 路径不可访问，改用当前官方 `/faq` 与原始 LICENSE 核验；未将访问失败当作组件或许可证不存在。

本页的 BI 组件取舍在 D1 评审；K1 的可视工作流范围见[工作流与轨迹](../architecture/04-workflow-and-trace.md)，不再以基础分析路径完成作为画布开发前提。
