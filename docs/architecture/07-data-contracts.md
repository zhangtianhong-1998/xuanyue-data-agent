# Data 领域协议（D1 预留）

[返回设计入口](../00-discovery-summary.md) · 通用对象见[核心对象](03-core-contracts.md)。

以下从首轮 BI 研究移入，作为领域协议唯一草案；D1 再评审，尚未实现。

以下均为**设计建议**，用于下一轮架构评审；代码片段只是协议草案，尚未实现或编译检查。

## 1 QuerySpec：先定义问题的计算口径

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

## 2 ChartSpec：绑定查询结果，不承载事实生成

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

## 3 Drill path 与 filter lineage：每一步都可解释、可回退

建议区分三种交互：

1. **层级下钻**：区域 → 城市 → 门店，或年 → 月 → 日。层级定义需用户确认或来源系统声明，地名相同不代表成员相同。
2. **切片探索**：仍在同一粒度，增加渠道/商品等筛选维度；不自动宣称上下级关系。
3. **查看明细**：从某聚合结果回到参与计算的记录。需要明确时间和过滤范围；distinct、比率、多表聚合不能仅用图元文字生成 `WHERE`。

每次交互生成 `AnalysisState`：`stateId`、`parentStateId`、数据快照、QuerySpec、`selection`、过滤条件增加/删除记录、metric version、结果 ID 和来源动作。回退恢复上一状态，而非尝试反向删除某个临时 WHERE 条件。重选筛选会形成分支，旧报告仍引用原来的状态。

高风险细节应进入验收：不同城市同名门店；NULL 成员；日历周与财年；维度已过滤又下钻；Top K 的“其余”集合；跨时间段门店新增/停业；比例不能对各门店百分比求平均；去重人数跨组不可直接相加。

## 4 数据级 provenance：一个结论能追到哪一层

| 对象 | 最少保存内容 | 用户用途 |
| --- | --- | --- |
| 导入数据快照 | 原文件 SHA-256、逻辑名称、导入版本、sheet/区域、列类型与转换、坏行/缺失记录、行数、导入时间 | 知道结果算自哪个文件版本 |
| 计算结果 | QuerySpec、SQL/参数摘要、引擎版本、输入 snapshot、输出 schema、行数、完整/采样标记、结果 hash、异常 | 能复算，能分清全量结果和展示抽样 |
| 图表 | ChartSpec、resultId、图表版本、筛选状态 | 图与表使用同一份数据 |
| 报告结论 | claimId、结论类型、引用的 result/单元格、指标口径、方法与假设、未解决问题 | 点一句话查看证据 |
| 原始行定位 | 稳定导入行 ID 到 source file/sheet/row 的映射；CSV 逻辑记录与物理行号区别需处理 | 有权限时查看来源记录 |

聚合值通常对应许多原始记录，不要伪造“一格对应一个来源行”。建议保存快照加可复算的 lineage 查询；用户查看明细时分页求取成员集合。多表或去重计算需要更严格的成员说明。公开轨迹记录计划、工具输入输出、状态、证据和异常，不记录模型隐藏推理。
