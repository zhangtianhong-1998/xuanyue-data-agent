# Data 领域协议：口径、取数与证据（草案）

[返回设计入口](../00-discovery-summary.md) · 通用对象见[核心对象](03-core-contracts.md)。

修订：2026-09-26；状态：需求澄清中，尚未确定连接器实现和交付阶段。本页回答两件事：**业务指标的计算口径由谁定义；数据在哪里查询，哪些内容需要留在本地。**

用户问“这个月收入为什么下降”，数据可能来自上传的文件、直连数据库，也可能来自已有的数据服务。文件可以用 DuckDB 读取或导入；数据库和数据服务可以在原处查询，只取分析所需的结果。保存图表和报告时，要记录所用口径、请求和结果的来源。若来源不提供稳定版本或原始明细，界面应说明旧结果能否复算、下钻能到哪一层。

按问题查阅：第 1 节讲口径归属；第 2 节讲文件、数据库和服务的取数方式；第 3 节给查询草案；第 4、5 节讲图表与下钻；第 6 节讲报告证据。现有 D1 计划先做 CSV，XLSX/Parquet 在 D2；直连数据库和服务接入的阶段待讨论。本页保留的 TypeScript 是讨论用草案，尚未编译检查。

## 1. 计算口径由谁负责

**建议按指标明确归属，不在 Data Agent 里另建一套企业指标真源。**

| 指标从哪里来 | 谁维护定义 | Data Agent 保存什么 |
| --- | --- | --- |
| 数据源没有受管理指标，例如上传文件或只有原始表的数据库 | 用户在项目中确认字段映射和临时口径；Data Agent 保存这份项目定义 | 定义版本、公式、单位、时间字段、聚合规则及确认记录；标为项目口径 |
| 已有数据服务明确发布并管理业务指标 | 该服务维护指标定义 | 服务身份、指标 ID、可取得的定义版本、请求和返回证据 |
| 以后接入语义层，且它管理当前数据和指标 | 语义层维护受管理口径；由用户或项目配置选定其权威范围 | 指标引用、版本或定义摘要、允许的维度与执行入口；运行时可留只读定义快照供核对 |

同名“收入”可能有不同的退款、税费或时间规则。若项目口径与服务口径冲突，界面应分别显示并请用户选择，不能按名称自动合并。服务若只返回数值、没有发布指标定义，就标为来源报告值和口径未明，不能擅自称它为权威指标。语义层只管它实际覆盖的数据和指标；它不认识的上传文件仍可使用项目口径。是否由语义层直接执行指标查询，取决于该服务提供的能力；只拿到指标说明，并不等于本地已经可以正确计算它。[dbt Semantic Layer 的 2023 年官方示例](https://docs.getdbt.com/blog/product-analytics-pipeline-with-dbt-semantic-layer)展示了集中定义指标并通过服务查询的做法；这是边界参考，不是本产品已选定的语义层实现。

Data Agent 负责选分析方法、核对结果、下钻和生成图表/报告。例如计算两期差额时，它要引用选定的“收入”口径；若服务只给分组后的比率，没有分子和分母或重新聚合能力，就不能自行把组比率相加当作总体结果。未来语义层 MCP 仍只预留接口，本轮不开发本体或语义层服务。

## 2. 数据在哪里查，什么时候落到本地

| 数据入口 | 可选执行方式 | 本地保留什么 | 待核对的限制 |
| --- | --- | --- | --- |
| CSV、XLSX、Parquet 等文件 | DuckDB 直接读文件，或导入为本地表 | 文件引用、哈希、解析记录；按用户选择保留本地分析副本 | 文件格式、公式缓存、坏行和转换是否改变结果 |
| 直连数据库 | 用该库的只读连接在源端查询；部分数据库也可比较 DuckDB 的连接扩展 | 连接配置的安全引用、查询和必要结果；默认不复制整库 | 支持的 SQL、权限、查询下推、源端扫描量及一致性 |
| 现有数据服务（HTTP API 或 MCP） | 调用已登记的查询/报表操作，或读取受控资源；结果可即取即用 | 服务身份、受控请求、返回时间及必要的结果/证据；原始数据不必整体落地 | 服务是否支持筛选、聚合、分页、明细、版本和稳定快照 |

API 和 MCP 是调用服务的方式，不保证背后有可查询的明细表。MCP 允许调用工具或读取资源；工具可声明输入 schema，结构化输出则是可选项。协议不规定每个服务都能筛选、分页、下钻或提供数据快照。因此每个服务接入时都要声明并实测自己的能力，不能仅因接入成功就把它当成完整的 BI 数据源。[MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)、[MCP Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)（2025-11-25 版）

服务返回的文字或文件可以作为报告引用的材料，但不能未经解析和校验就当作可计算的数据表。数值计算与结构化图表只接收通过 schema 和范围校验的结果。数据源适配器由产品的数据工具管理，两种主 Agent 内核都经同一[工具入口](08-runtime-interoperability.md#2-每处框架边界都有转接口)访问。

DuckDB 也不要求把文件先复制成表：官方文档展示了直接查询 CSV/Parquet、通过 Excel 扩展直接读 XLSX，以及建表导入；该扩展不支持旧 `.xls` 格式。其 PostgreSQL 扩展可以只读连接并在查询时读取远端数据，复制为本地表是另一项选择；这不能推广为任意数据库都能以同样方式连接或完成全部计算下推。[DuckDB 文件查询](https://duckdb.org/docs/current/data/overview)、[Excel 扩展](https://duckdb.org/docs/current/core_extensions/excel)、[PostgreSQL 扩展](https://duckdb.org/docs/current/core_extensions/postgres/overview)

**建议的默认策略：**文件走本地分析路径；远端数据优先在来源处执行获准的查询，只取有范围和大小限制的结果。来源不支持所需计算时，可以按项目已授权的取数范围、体量和保存期限，把完整的所需范围交给本地 DuckDB 临时计算或持久保存；不能悄悄下载整库。若服务只能返回部分页，而无法取齐计算所需范围，就缩小问题或明确失败，不能把部分数据写成全量结论。项目可以只保存报告所需的最小聚合结果，而不保存原始数据；若项目不允许保存这些结果，则仅提供当次查看，不能生成承诺事后可核对的持久数值报告。重新取数应标为新结果，不能冒充原值。

## 3. QuerySpec：表达查询意图，不绑定 DuckDB

有查询能力的数据源可以接收受限的 `QuerySpec`。来源、数据集、字段和指标使用稳定 ID；展示名称变化不改 ID，指标 ID 还须带定义来源。读取方式明确是固定快照、来源版本、指定时点，还是实时读取。只有数据源实际支持相应方式，才能选它。对于只提供固定操作的 API/MCP 服务，使用已登记操作及其参数 schema；不强行把操作伪装成 SQL 查询。两类请求都返回带来源、范围和完整性标记的结果。

```ts
type Scalar = string | number | boolean | null;
type Filter =
  | { fieldId: string; op: "eq" | "neq" | "gt" | "gte" | "lt" | "lte"; value: Scalar }
  | { fieldId: string; op: "in"; values: Scalar[] }
  | { fieldId: string; op: "is_null" | "is_not_null" }
  | { op: "and" | "or"; children: Filter[] };

type ReadContext =
  | { kind: "snapshot" | "source_version"; ref: string }
  | { kind: "as_of"; time: string }
  | { kind: "live" };

type QuerySpec = {
  schemaVersion: 1;
  dataset: { sourceId: string; id: string; read: ReadContext };
  metricRefs: { authorityId: string; id: string; version?: string }[];
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

`snapshot` 可指已冻结的本地文件或源端快照；`source_version`、`as_of` 要有来源服务支持才有效；`live` 表示结果可能随时间变化，不假称有快照。`version` 取不到时，证据中记为未知。正式 schema 还需补参数、比较期、时间闭开区间、错误类型和分页 token。此草案仍只引用单一已注册数据集；后续跨源或多表查询须明确连接键、基数、去重策略和完整输入范围，不能凭同名列自动连接。

执行前，数据源适配器报告可用字段、指标、筛选/分组、分页、下钻、版本和物化能力。产品核对请求、授权、结果大小和来源能力，再选择本地 SQL、源端 SQL 或服务操作；无法准确表达时就拒绝或请用户调整范围。对于由外部管理的指标，仅有文字说明时不能据此生成本地 SQL；须有权威服务的查询入口，或另行验证可执行定义及其适用数据。`maxResultBytes` 限制返回量，不代表已经限制远端扫描量或费用。对于本地 DuckDB，SQL 的 `SELECT` 前缀检查不足以阻止任意文件读取、远程表函数或扩展调用；文件注册、可信导入、分析查询要分开授权并限制扩展。[DuckDB 扩展安全](https://duckdb.org/docs/current/operations_manual/securing_duckdb/securing_extensions)

## 4. ChartSpec：绑定查询结果，不承载事实生成

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

## 5. 下钻与返回：来源能力决定能走多深

建议区分三种交互：

1. **层级下钻**：区域 → 城市 → 门店，或年 → 月 → 日。层级定义需用户确认或来源系统声明，地名相同不代表成员相同。
2. **切片探索**：仍在同一粒度，增加渠道/商品等筛选维度；不自动宣称上下级关系。
3. **查看明细**：从某聚合结果回到参与计算的记录。需要明确时间和过滤范围；distinct、比率、多表聚合不能仅用图元文字生成 `WHERE`。

每次交互生成 `AnalysisState`：`stateId`、`parentStateId`、数据源及读取方式、QuerySpec 或已登记服务操作、`selection`、过滤条件增加/删除记录、指标定义引用、结果 ID 和来源动作。回退恢复上一状态，而非尝试反向删除某个临时筛选条件。重选筛选会形成分支，旧报告仍引用原来的状态。远端服务若只给汇总值、不提供明细，就只能下钻到它支持的层级；实时来源若没有稳定版本，重新取数后的值可能变化，要显示这是一次新查询。

高风险细节应进入验收：不同城市同名门店；NULL 成员；日历周与财年；维度已过滤又下钻；Top K 的“其余”集合；跨时间段门店新增/停业；比例不能对各门店百分比求平均；去重人数跨组不可直接相加。

## 6. 报告证据：按来源说明能追到哪一层

| 对象 | 最少保存内容 | 用户用途 |
| --- | --- | --- |
| 本地文件来源 | 原文件 SHA-256、逻辑名称、导入或直接读取版本、sheet/区域、列类型与转换、坏行/缺失记录、行数 | 知道结果算自哪个文件版本 |
| 数据库或服务来源 | 来源身份、已登记的数据集/操作、执行时刻、请求参数、源端版本/快照令牌（若有）、连接器版本 | 知道结果来自哪次远端请求；未给版本时明确无法严格复算 |
| 计算结果 | QuerySpec 或服务操作、执行位置与版本、口径归属/版本或未知、输出 schema、行数、完整/采样/分页状态、保留的结果引用或不保留原因、异常 | 分清全量结果与局部返回；知道旧结果能否再看、能否重算 |
| 图表 | ChartSpec、resultId、图表版本、筛选状态 | 图与表使用同一份数据 |
| 报告结论 | claimId、结论类型、引用的 result/单元格、指标口径、方法与假设、未解决问题 | 点一句话查看证据 |
| 原始记录定位（若来源支持） | 文件可记录 sheet/row；数据库或服务可记录其允许的主键/明细查询引用 | 有权限时查看参与计算的记录；来源只给汇总时不虚构行级追溯 |

聚合值通常对应许多原始记录，不要伪造“一格对应一个来源行”。有权限且来源支持时，用户查看明细再按同一口径和范围分页查询；若来源只有当前值或无法提供稳定快照，持久数值报告须保留当时获准保存的结果，并标出不能严格复算。数据库在常见的 Read Committed 隔离级别下，两次查询也可能看到不同数据，不能仅凭 SQL 相同就宣称重现。[PostgreSQL 事务隔离说明](https://www.postgresql.org/docs/current/transaction-iso.html) 公开轨迹记录请求、返回范围、证据和异常，不记录模型隐藏推理。

以上官方资料于 2026-09-26 查阅；DuckDB 页面当时标注 1.5 current，PostgreSQL 页面标注 18 current，MCP 引用 2025-11-25 版规范。核对范围仅包括文件直读、PostgreSQL 只读连接、MCP 工具/资源接口及数据库连续读取的一致性说明。dbt 文章仅作集中管理指标的产品例子。本产品尚未验证数据库连接器、外部服务能力、跨源计算、缓存策略或远端数据的下钻与复算。
