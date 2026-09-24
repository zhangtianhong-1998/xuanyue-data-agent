# BI、数据分析与 workflow 组件调研

状态：技术路线候选，待评审。访问日期：2026-09-24（Asia/Shanghai）。

本轮围绕已确认的首版场景：个人在桌面端导入经营数据，发现异常、下钻核查、生成报告；数据保存在本地，向云模型发送必要内容须经授权。本文的组件建议尚未成为实施决定。

## 1. 建议先采用的组合

**建议：以 DuckDB 本地查询、ECharts 结果图表、TanStack Table 明细表作为首版主线。** 将指标口径、查询、筛选历史和证据保存为应用自己的协议。可视分析编辑器后续在 Perspective 与 Graphic Walker 之间做一次小规模对比；两套组件不同时成为首版依赖。React Flow 用于展示和编辑分析步骤，任务执行、权限和恢复交给 Agent runtime。

这项建议依据个人开发的成本与经营分析场景作出，并非组件性能测试结论：

- 用户首先需要核对“为什么下降、哪些门店贡献最大、数据是否完整”，结果图、明细表和可回退的下钻已能覆盖主要路径。
- 将完整 BI 平台打包进桌面客户端，会增加独立服务、身份认证、元数据存储和升级维护。Superset、Metabase 更适合作为未来已有 BI 系统的接入对象。
- 完整拖拽分析器有现成能力，但它的查询协议、字段类型、计算方式和品牌条款会影响产品。先验证这些边界，再决定是否纳入主界面。

首版应交付一个有证据的分析任务，而不是图表数量清单。例如：“9 月收入下降 12%，其中华东区金额减少最多”必须同时给出比较期、指标公式、筛选范围、样本完整性、查询结果和可点开的明细。这里的“金额减少最多”属于分解结果，不是业务因果结论。

## 2. 组件分工与候选比较

下表的“事实”来自本轮官方资料或源码；“判断”是本项目的选型意见。未进行跨平台运行、性能或完整许可证依赖审计。

| 组件 | 已核验的事实与用途 | 本项目判断及接入成本 | 当前处理 |
| --- | --- | --- | --- |
| Apache ECharts | 图表事件、dataset 和编码映射可用于交互图表。点击后的查询与状态切换由应用处理。[事件](https://echarts.apache.org/handbook/en/concepts/event/)；[dataset](https://echarts.apache.org/handbook/en/concepts/dataset/) | 适合趋势、对比、贡献分解等结果视图；应用自己持有 drill path。需实现一层 ChartSpec 适配器，禁止模型直接注入函数型 option | 首版首选 |
| Vega-Lite | 声明式可视化规范；参数支持点选、框选以及联动过滤。[规范](https://vega.github.io/vega-lite/docs/spec.html)；[参数](https://vega.github.io/vega-lite/docs/parameter.html) | 适合规则化生成、分面、小多图和可视探索。它的表达式、数据 URL、变换仍需限制。不要首版同时维护 ECharts 与 Vega-Lite 全量语法 | 保留为替代 renderer；若确定 Graphic Walker，随其引入 |
| Perspective | 固定源码 v5.5.1 存在分组、透视、过滤、保存/恢复及 DuckDB Virtual Server 适配路径；可将查询下推给外部引擎。[Virtual Servers](https://github.com/perspective-dev/perspective/blob/1fc0c8373574a478195ebc69302cafeb924f7876/docs/md/explanation/virtual_servers.md) | 适合透视表优先的交互分析。跨组件状态同步、桌面 WebView 的 WASM、IPC、查询审计需要实测。优先评估复用本地 DuckDB 的方案，避免再维护一份独立数据副本 | 首轮可视分析 spike 的优先候选 |
| Graphic Walker | React 可嵌入分析器；固定源码公开 computation(payload) 接口，支持过滤、变换、聚合和排序；提供图表规范及拖拽字段交互。[仓库](https://github.com/Kanaries/graphic-walker) | 适合接近 Tableau 的手工探索；需映射查询 DSL、限制外部调用并确认品牌许可。内置 AI 不应绕过产品统一模型网关 | 次选候选；许可和外发边界通过后再考虑 |
| PyGWalker | Python/DataFrame 到交互分析界面的入口，与 Graphic Walker 生态相关。[官方 README](https://github.com/Kanaries/pygwalker/blob/main/README.md) | 适合 Notebook/研究实验；桌面主界面若已是 React，直接嵌入 Graphic Walker 可减少一层 Python UI 桥接。不能将 PyGWalker 根许可证推及所有打包资源 | 研究工具备选，不作主 UI |
| DuckDB | 进程内分析数据库，支持扩展；官方采用 MIT。[项目说明](https://www.duckdb.org/why_duckdb) | 适合本地文件聚合与复算。具体 Node/Python/Rust binding 跟随内核路线评审；先统一查询协议，不同时引入多个查询引擎 | 首版查询引擎首选 |
| TanStack Table | Headless 表格，提供状态和逻辑，DOM、样式由应用实现。[文档](https://tanstack.com/table/v8/docs/overview) | 适合可控的明细表、分页、排序、筛选；虚拟滚动、键盘交互、导出与透视 UI 需要另做。首版只做高频功能 | 首版表格首选；该证据是 v8 文档，实施前重锁版本 |
| AG Grid | Community 和 Enterprise 的许可不同；行分组、透视、集成图表等属于 Enterprise。[功能表](https://www.ag-grid.com/javascript-data-grid/key-features/)；[透视](https://www.ag-grid.com/javascript-data-grid/pivoting/) | 若之后需要复杂表格且接受采购，可能节省交互开发时间。不能用 Community 免费许可估算完整 BI 功能成本 | 暂不选；保留采购路线 |
| React Flow | 自定义节点与边，支持图中数据更新和节点连接读取；官方还提供浏览器内 computing flows 示例。[指南](https://reactflow.dev/learn/advanced-use/computing-flows) | 可以画工作流和编辑连接；持久任务、重试、取消、并发、权限、断点恢复需应用实现。不要把 UI 节点回调当生产执行器 | workflow 编辑器候选 |
| Apache Superset | Embedded SDK 面向已有 Superset 仪表板；需要开启嵌入、配置来源域、由服务端获取 guest token。[嵌入文档](https://superset.apache.org/user-docs/using-superset/embedding/) | 对首版个人桌面产品偏重。未来企业若已有 Superset，可接其服务；单独抽取插件还需检查其依赖及适配成本 | 接入对象，不内置整个系统 |
| Metabase | 官方完整应用嵌入和模块化 React SDK 属于 Pro/Enterprise；完整应用嵌入还涉及身份与 SSO。[完整应用](https://www.metabase.com/docs/latest/embedding/full-app-embedding)；[SDK](https://www.metabase.com/docs/latest/embedding/dashboard?use_case=ea) | 有现成问数/下钻体验，但服务、许可和身份体系都要接入。个人首版不适合依赖其完整嵌入方案 | 接入对象，不作核心 |

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

## 4. 分析协议必须由产品持有

以下均为**设计建议**，用于下一轮架构评审；代码片段只是协议草案，尚未实现或编译检查。

### 4.1 QuerySpec：先定义问题的计算口径

查询应引用稳定的 dataset / field / metric ID；展示名称可以变化，ID 不随图表排序或语言改变。指标定义包含公式、单位、粒度、过滤条件和版本。首版可由用户确认字段/指标映射，未来再接语义层 MCP；现在不实现本体服务。

```ts
type Scalar = string | number | boolean | null;
type Filter =
  | { fieldId: string; op: "eq" | "neq" | "gt" | "gte" | "lt" | "lte"; value: Scalar }
  | { fieldId: string; op: "in"; values: Scalar[] }
  | { fieldId: string; op: "is_null" | "is_not_null" }
  | { op: "and" | "or"; children: Filter[] };

type QuerySpec = {
  schemaVersion: 1;
  dataset: { id: string; snapshotId: string };
  metricRefs: { id: string; version: string }[];
  groupBy: { fieldId: string; timeGrain?: "day" | "week" | "month" | "year" }[];
  rowFilter?: Filter;
  // 指向已定义的聚合输出，不与行过滤混用。
  aggregateFilter?: Filter;
  timezone: string;
  orderBy: { resultFieldId: string; direction: "asc" | "desc"; nulls: "first" | "last" }[];
  resultMode: "complete" | "top_k" | "sample" | "page";
  resultLimit?: number;
  policy: { maxDurationMs: number; maxResultBytes: number };
};
```

还需在正式 schema 补齐参数、比较期、时间闭开区间、错误类型和分页 token。此草案刻意只支持单一已注册数据集；后续多表查询必须增加明确的 join contract：键、基数预期、去重策略及连接前后行数，不允许模型凭同名列自动连接。

执行边界建议：Agent 提交受限 QuerySpec，查询服务验证字段、指标、权限与预算，再编译参数化 SQL。SQL 的 `SELECT` 前缀检查不足以阻止任意文件读取、远程表函数或扩展调用。文件注册、可信导入、分析查询应分开；不同操作分别配置 DuckDB 能力。官方明确扩展有父进程权限，且可自动安装/加载，需根据本地 worker 设计关闭不需要的能力并锁定配置。[DuckDB 扩展安全](https://duckdb.org/docs/current/operations_manual/securing_duckdb/securing_extensions)

### 4.2 ChartSpec：绑定查询结果，不承载事实生成

```ts
type ChartSpec = {
  schemaVersion: 1;
  id: string;
  resultId: string;
  kind: "bar" | "line" | "scatter" | "heatmap" | "waterfall" | "table";
  encoding: {
    x?: string; y?: string; series?: string; color?: string;
    // 字段必须存在于 result schema；不得引用原始路径或任意 URL。
  };
  title: string;
  interaction: { selection: "none" | "point" | "range"; drillPathId?: string };
  display: { unit?: string; decimals?: number; maxMarks: number };
};
```

适配器将 ChartSpec 转成 ECharts option 或受限 Vega-Lite spec。规范校验之后还要校验字段类型、时间轴、聚合方式、单位和负值：例如零基线柱状图、比例分母和瀑布图加总应有明确规则。MVP 不接受模型提供 JS 回调、任意 HTML、外部数据 URL、插件路径或可执行表达式。

当点数过多时，提供“按时间聚合 / Top K + 其余 / 明细表”选择，并在结果上显示处理方式；禁止默默截断后仍声称全量。无法绘制时显示同一 `resultId` 的表格，结果计算失败时保留失败状态，不能生成一张看起来正常的空图。

### 4.3 Drill path 与 filter lineage：每一步都可解释、可回退

建议区分三种交互：

1. **层级下钻**：区域 → 城市 → 门店，或年 → 月 → 日。层级定义需用户确认或来源系统声明，地名相同不代表成员相同。
2. **切片探索**：仍在同一粒度，增加渠道/商品等筛选维度；不自动宣称上下级关系。
3. **查看明细**：从某聚合结果回到参与计算的记录。需要明确时间和过滤范围；distinct、比率、多表聚合不能仅用图元文字生成 `WHERE`。

每次交互生成 `AnalysisState`：`stateId`、`parentStateId`、数据快照、QuerySpec、`selection`、过滤条件增加/删除记录、metric version、结果 ID 和来源动作。回退恢复上一状态，而非尝试反向删除某个临时 WHERE 条件。重选筛选会形成分支，旧报告仍引用原来的状态。

高风险细节应进入验收：不同城市同名门店；NULL 成员；日历周与财年；维度已过滤又下钻；Top K 的“其余”集合；跨时间段门店新增/停业；比例不能对各门店百分比求平均；去重人数跨组不可直接相加。

### 4.4 数据级 provenance：一个结论能追到哪一层

| 对象 | 最少保存内容 | 用户用途 |
| --- | --- | --- |
| 导入数据快照 | 原文件 SHA-256、逻辑名称、导入版本、sheet/区域、列类型与转换、坏行/缺失记录、行数、导入时间 | 知道结果算自哪个文件版本 |
| 计算结果 | QuerySpec、SQL/参数摘要、引擎版本、输入 snapshot、输出 schema、行数、完整/采样标记、结果 hash、异常 | 能复算，能分清全量结果和展示抽样 |
| 图表 | ChartSpec、resultId、图表版本、筛选状态 | 图与表使用同一份数据 |
| 报告结论 | claimId、结论类型、引用的 result/单元格、指标口径、方法与假设、未解决问题 | 点一句话查看证据 |
| 原始行定位 | 稳定导入行 ID 到 source file/sheet/row 的映射；CSV 逻辑记录与物理行号区别需处理 | 有权限时查看来源记录 |

聚合值通常对应许多原始记录，不要伪造“一格对应一个来源行”。建议保存快照加可复算的 lineage 查询；用户查看明细时分页求取成员集合。多表或去重计算需要更严格的成员说明。公开轨迹记录计划、工具输入输出、状态、证据和异常，不记录模型隐藏推理。

## 5. 复杂分析能力分阶段实现

以下是建议的产品能力边界，不是对任一模型的能力承诺。

| 能力 | 可先实现的方法 | 需要的前提与产物 | 不应作出的结论 |
| --- | --- | --- | --- |
| 数据体检 | 类型、缺失、重复、时间覆盖、单位和主键候选检查 | 用户确认字段含义；保留失败行和转型记录 | 自动识别类型不等于理解业务指标 |
| 异常检测 | 固定阈值、同比/环比、稳健残差；稳定周期数据可评估 STL | 展示周期、窗口、阈值、缺失处理和触发证据；STL 只负责趋势/季节/残差分解，[官方 API](https://www.statsmodels.org/stable/generated/statsmodels.tsa.seasonal.STL.html) | 单次异常分数不证明业务发生故障 |
| 下钻与贡献分解 | 对可加指标逐维分组，计算两期差额及贡献；筛选后复算 | 完整分组、加总守恒、比较期一致；比例/乘法指标另用明确分解公式 | 贡献最大者不一定是可干预原因 |
| 候选解释排序 | 根据异常覆盖、贡献、支持样本和反例排序；限制组合搜索预算 | 给出覆盖范围、剩余解释量、重叠与样本数；重复探索注意多重比较 | 搜索到的相关切片不等于因果 |
| 因果分析/根因研究 | 在已确认因果图、干预或准实验条件下做专门分析 | 说明识别假设、估计方法、敏感性与反驳检查；DoWhy 明确建模并检验假设，[官方仓库](https://github.com/py-why/dowhy)；分布变化归因依赖机制模型，[官方方法](https://www.pywhy.org/dowhy/main/user_guide/causal_tasks/root_causing_and_explaining/distribution_change.html) | 一个通用 Agent 不应仅凭上传表格宣称找到真实根因 |
| 深度研究 | 将内部指标证据与获授权的外部资料分开检索、交叉核对、形成引用报告 | 外部 URL、日期、引用位置；区分观测事实、作者主张、假设、未知 | 新闻与指标同期变化不等于因果机制 |
| 报告生成 | 从可引用结果组装结论、图表、方法和待核实项 | 每个关键数字可追到结果；失败和缺失仍可见 | 文笔流畅不证明数字或业务判断正确 |

对“根因”的界面命名建议先用“变化贡献”和“待核实解释”。用户可以在确认业务证据后补充“促销停止”“门店停业”等信息；Agent 据此继续检验，不能将记忆中的业务猜测自动提升为事实。

多 Agent 在这里的合理分工是计划、查询执行、证据核查、报告编排。执行器以同一数据快照和协议工作。核查 Agent 应独立检查分母、单位、加总、过滤和反例；再调用一次同模型重复解释，不足以成为独立验证。是否常开多 Agent，应按实际收益、成本和延迟实验决定。

## 6. 分析 workflow 的职责

建议先提供“系统生成步骤 → 用户检查/修改参数 → 执行 → 查看每步证据”，把自由拖拽画布放到第二阶段。首版模板可覆盖：导入 → 数据体检 → 指标确认 → 比较期 → 异常检测 → 维度分解 → 核查 → 报告。

工作流定义 `WorkflowDefinition` 与每次运行 `WorkflowRun` 分开。图定义保存节点类型、版本、输入输出 schema、参数和连接；运行记录保存输入快照、节点状态、产物、耗时和异常。图上展示可公开的分析意图与步骤。

建议的执行约束：

- 第一版限制 DAG；不允许无限循环。条件分支必须有类型化条件和明确的跳过状态。
- 节点至少区分 `pending/running/succeeded/failed/skipped/cancelled/awaiting_input`，取消向查询 worker 传播。
- 重试只对可重放操作自动进行；对外发送、覆盖文件等操作要使用幂等键或单独授权，防止恢复时重复执行。
- 缓存键包含数据 hash、节点版本、参数、指标口径和权限范围；更换数据文件不能悄悄复用旧答案。
- 编辑运行中的 workflow 产生新版本；报告固定引用一次 run，不与后续修改混用。
- React Flow 的节点位置、连线和选中状态可以持久化，但执行状态的真实来源是 runtime 事件。关闭页面或重启应用后的恢复不能依赖 React component 仍在内存。

这些是本产品需实现的执行要求。React Flow 官方指南展示的是 UI 内的数据流计算示例，不足以作为上述持久工作流能力已经具备的证据。

## 7. 下一轮小实验及停止条件

本轮完成官方资料与固定源码核验，**没有完成下表的 UI/性能实验**。进入实现前先选定最影响方案的两项。所有输入用合成数据，结果保留失败与未验证项。

| Spike | 要回答的问题 | 最小实验 | 建议通过条件 | 当前状态 |
| --- | --- | --- | --- | --- |
| BI-01 可视分析适配 | Perspective 或 Graphic Walker 能否全部走产品查询服务？ | 固定相同经营数据；执行 group/filter/sort/pivot/保存恢复；记录实际请求；对照 DuckDB 结果 | 聚合和空值语义一致；不支持功能显式禁用；每次交互可定位 QuerySpec；无未经授权的外发 | 待做 |
| BI-02 图表下钻一致性 | 图表、明细、面包屑、报告能否引用同一结果？ | ECharts + TanStack Table：区域→门店→明细→返回；插入 NULL、同名门店、退款、零分母、重复订单 | 守恒规则通过；回退恢复原状态；比率/去重不错误加总；报告数字完全可追溯 | 待做；可接主实验 `data-analysis-contract` 的合成输入 |
| BI-03 桌面性能和离线 | 目标桌面环境是否够用？ | 在记录硬件上测试 10 万/100 万行、宽表和高基数透视；冷启动、峰值 RSS、首次图、筛选 p50/p95、取消耗时；断网运行 | 指标先记录再协商预算；超预算有分页/缩减/拒绝；资源本地打包；macOS/Windows/Linux 分别记录，未跑的平台标未验证 | 待做 |
| BI-04 分析正确性 | 系统会不会给错归因？ | 固定盲测集含缺失数据、季节变化、结构变化、重叠切片、Simpson 悖论和真实无异常案例 | 分开统计计算正确率、异常误报漏报、解释证据支持率；不能用单一总分代替 | 待做 |
| BI-05 workflow 恢复 | 中断后是否重复执行或引用旧数据？ | 在查询、外发授权、报告写入处故障注入；重启后恢复；更改数据 hash 再运行 | 已成功产物可核验；失败/取消/跳过保留；无重复副作用；失效缓存不命中 | 待做 |

若 Graphic Walker 的品牌范围无法满足产品定位，继续用 ECharts/TanStack 或评估 Perspective，不需要为了一个可视编辑器改变整体许可策略。若 Perspective 的本地 IPC/受控查询适配成本过高，首版保留结果图与基础下钻即可。

## 8. 本轮证据范围

- 现场核验：以上官方文档、许可证页面；两个固定 commit 的接口、实现路径及静态外发线索。
- 现场获取：Graphic Walker 和 Perspective 两个浅克隆，连接性、origin、工作树、版本和源码位置已记录。
- 未验证：完整依赖许可证、组件运行表现、UI 美观度、性能、跨平台打包、商业采购条款的法律适用、分析方法在真实经营数据上的效果。
- 浮动页面：除两个固定源码仓库外，官方文档链接多数是 `latest/main/master`；使用时以本轮访问日期为准，实施阶段必须锁版本并重查。
- 文档读取遇到 DuckDB 旧 FAQ 路径不可访问，改用当前官方 `/faq` 与原始 LICENSE 核验；未将访问失败当作组件或许可证不存在。

评审需要确认的核心方向只有两项：首版是否先以结果图和可解释下钻为中心，以及自由拖拽透视/工作流画布是否延后到基础分析路径验证之后。
