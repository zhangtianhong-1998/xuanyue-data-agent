"""Synthetic local experiment; no LLM, production sandbox, or causal inference."""
from __future__ import annotations

import csv
import hashlib
import json
import platform
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DIMENSIONS = {"region", "channel"}
METRICS = {
    "revenue": "SUM(revenue)",
    "avg_order_value": "SUM(revenue) / NULLIF(SUM(orders), 0)",
}


def stable_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class QuerySpec:
    snapshot: str
    metric: str = "revenue"
    metric_version: str = "1"
    dimensions: tuple[str, ...] = ()
    filters: tuple[tuple[str, str], ...] = ()
    periods: tuple[str, ...] = ("base", "current")


def fixture(path: Path, balanced: bool = False) -> None:
    baseline = [1000, 500, 500, 1000]
    current = [400, 400, 800, 1400] if balanced else [400, 400, 650, 1150]
    groups = [("east", "web"), ("east", "store"), ("west", "web"), ("west", "store")]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["period", "region", "channel", "revenue", "orders"])
        for period, amounts, orders in [("base", baseline, [10, 25, 20, 10]),
                                        ("current", current, [8, 20, 13, 23])]:
            for (region, channel), amount, count in zip(groups, amounts, orders):
                writer.writerow([period, region, channel, amount, count])


class Engine:
    def __init__(self, path: Path):
        self.snapshot = hashlib.sha256(path.read_bytes()).hexdigest()
        self.db = duckdb.connect(":memory:")
        self.db.execute("SET memory_limit = '256MB'")
        self.db.execute("SET threads = 2")
        self.db.execute("""CREATE TABLE facts AS SELECT * FROM read_csv(?,
            columns = {'period':'VARCHAR','region':'VARCHAR','channel':'VARCHAR',
                       'revenue':'DECIMAL(18,2)','orders':'BIGINT'}, header=true)""", [str(path)])
        # Defense in depth for this fixed fixture. Not a production sandbox.
        self.db.execute("SET enable_external_access = false")

    def query(self, spec: QuerySpec) -> dict:
        if spec.snapshot != self.snapshot or spec.metric_version != "1":
            raise ValueError("Unknown data or metric version")
        if spec.metric not in METRICS:
            raise ValueError("Unknown metric")
        if len(set(spec.dimensions)) != len(spec.dimensions):
            raise ValueError("Duplicate dimensions")
        if any(dim not in DIMENSIONS for dim in spec.dimensions):
            raise ValueError("Unknown dimension")
        if any(key not in DIMENSIONS or not isinstance(value, str) for key, value in spec.filters):
            raise ValueError("Invalid filter")
        if spec.periods != ("base", "current"):
            raise ValueError("This spike requires the frozen comparison window")
        group_columns = ["period", *spec.dimensions]
        selected = ", ".join(group_columns)
        clauses = ["period IN (?, ?)"]
        parameters = list(spec.periods)
        for key, value in spec.filters:
            clauses.append(f"{key} = ?")
            parameters.append(value)
        sql = (f"SELECT {selected}, {METRICS[spec.metric]} AS value FROM facts "
               f"WHERE {' AND '.join(clauses)} GROUP BY {selected} ORDER BY {selected}")
        cursor = self.db.execute(sql, parameters)
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return {"query_id": stable_hash(asdict(spec)), "spec": asdict(spec),
                "sql": sql, "parameters": parameters, "rows": rows}

    def close(self) -> None:
        self.db.close()


def decompose(result: dict, dimension: str) -> dict:
    if result["spec"]["metric"] != "revenue":
        raise ValueError("Additive decomposition requires an additive metric")
    grouped: dict[str, dict] = {}
    for row in result["rows"]:
        grouped.setdefault(row[dimension], {})[row["period"]] = row["value"]
    deltas = {key: value.get("current", Decimal(0)) - value.get("base", Decimal(0))
              for key, value in grouped.items()}
    net = sum(deltas.values(), Decimal(0))
    return {"delta": net, "groups": [
        {"group": key, "delta": delta,
         "share_of_net_delta": delta / net if net != 0 else None}
        for key, delta in sorted(deltas.items())],
        "interpretation": "descriptive_contribution_only; no causal claim"}


def main() -> None:
    fixture_path = HERE / "synthetic_sales.csv"
    balanced_path = HERE / "synthetic_balanced.csv"
    fixture(fixture_path)
    fixture(balanced_path, balanced=True)
    engine = Engine(fixture_path)
    balanced_engine = Engine(balanced_path)
    checks: list[dict] = []

    def check(name: str, predicate: bool) -> None:
        checks.append({"name": name, "passed": bool(predicate)})

    def rejected(name: str, action) -> None:
        try:
            action()
        except ValueError:
            check(name, True)
        else:
            check(name, False)

    try:
        top = engine.query(QuerySpec(engine.snapshot))
        totals = {row["period"]: row["value"] for row in top["rows"]}
        check("frozen_totals", totals == {"base": Decimal(3000), "current": Decimal(2600)})
        regions = engine.query(QuerySpec(engine.snapshot, dimensions=("region",)))
        contribution = decompose(regions, "region")
        check("contribution_reconciles", contribution["delta"] == Decimal(-400))
        check("signed_offsets_preserved", [row["share_of_net_delta"] for row in contribution["groups"]]
              == [Decimal("1.75"), Decimal("-0.75")])
        drill_spec = QuerySpec(engine.snapshot, dimensions=("channel",), filters=(("region", "east"),))
        drill = engine.query(drill_spec)
        check("drill_preserves_parent_filter", decompose(drill, "channel")["delta"] == Decimal(-700)
              and drill["parameters"] == ["base", "current", "east"])
        restored = engine.query(QuerySpec(engine.snapshot, dimensions=("region",)))
        check("back_restores_query_and_results", restored == regions)
        ratios = engine.query(QuerySpec(engine.snapshot, metric="avg_order_value"))
        ratios_by_period = {row["period"]: row["value"] for row in ratios["rows"]}
        check("ratio_of_sums", abs(ratios_by_period["current"] - 40.625) < 1e-9
              and abs(ratios_by_period["base"] - 3000 / 65) < 1e-9)
        rejected("reject_nonadditive_contribution", lambda: decompose(ratios, "region"))
        balance = decompose(balanced_engine.query(QuerySpec(balanced_engine.snapshot,
                               dimensions=("region",))), "region")
        check("zero_net_preserves_offsets", balance["delta"] == 0
              and [row["delta"] for row in balance["groups"]] == [Decimal(-700), Decimal(700)]
              and all(row["share_of_net_delta"] is None for row in balance["groups"]))
        rejected("reject_unknown_dimension", lambda: engine.query(QuerySpec(engine.snapshot,
                 dimensions=("region; DROP TABLE facts",))))
        rejected("reject_wrong_snapshot", lambda: engine.query(QuerySpec("stale")))
        injection = engine.query(QuerySpec(engine.snapshot, filters=(("region", "' OR 1=1 --"),)))
        check("filter_values_are_bound", injection["rows"] == [] and "OR 1=1" not in injection["sql"])
        chart_contract = {"kind": "bar", "query_id": regions["query_id"],
                          "snapshot": engine.snapshot, "category": "region", "measure": "revenue",
                          "drill_dimensions": ["channel"], "data": regions["rows"]}
        report = {
            "schema_version": 1,
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "lock_sha256": hashlib.sha256((HERE / "uv.lock").read_bytes()).hexdigest(),
            "scope": "Synthetic contract feasibility only; no real LLM, UI, scale or OS isolation validation",
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "duckdb": duckdb.__version__},
            "checks": checks, "passed": sum(check["passed"] for check in checks), "total": len(checks),
            "top": top, "regions": regions, "contribution": contribution, "drill": drill,
            "ratio": ratios, "balanced": balance, "chart_contract_not_rendered": chart_contract,
        }
        (HERE / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        print(json.dumps({"passed": report["passed"], "total": report["total"],
                          "duckdb": duckdb.__version__}))
        if report["passed"] != report["total"]:
            raise SystemExit(1)
    finally:
        engine.close()
        balanced_engine.close()


if __name__ == "__main__":
    main()
