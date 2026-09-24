"""Synthetic LangGraph experiment. No LLM, network tool, or business data.

Each worker command starts a separate Python process. SQLite contains graph
checkpoints; a separate ledger gives the report operation an idempotency key.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import operator
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict):
    run_id: str
    observations: Annotated[list[dict], operator.add]
    approved: bool
    report: str


def make_graph(checkpointer: SqliteSaver, work: Path, crash: bool):
    # Both graph nodes must meet the barrier: proves overlapping node execution,
    # not just two sequential outputs. It is an experiment-only observation aid.
    barrier = threading.Barrier(2, timeout=10)

    def branch(name: str, amount: int):
        def calculate(state: State):
            started = time.monotonic_ns()
            barrier.wait()
            return {"observations": [{
                "branch": name, "synthetic_contribution": amount,
                "thread": threading.get_ident(), "started_ns": started,
                "finished_ns": time.monotonic_ns(),
                "source": "hardcoded-synthetic-v1",
            }]}
        return calculate

    def approval(state: State):
        approved = interrupt({
            "action": "write_synthetic_report",
            "run_id": state["run_id"],
            "observation_count": len(state["observations"]),
            "external_send": False,
        })
        if not isinstance(approved, bool):
            raise TypeError("approval must be a bool")
        return {"approved": approved}

    def report(state: State):
        report_id = state["run_id"] + ":report:v1"
        text = json.dumps(sorted(state["observations"], key=lambda v: v["branch"]), sort_keys=True)
        with sqlite3.connect(work / "effects.sqlite") as db:
            db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS effects (id TEXT PRIMARY KEY, report TEXT)")
            db.execute("INSERT INTO attempts VALUES (?)", (report_id,))
            db.execute("INSERT OR IGNORE INTO effects VALUES (?, ?)", (report_id, text))
            db.commit()
        if crash:
            # Simulate abrupt exit after the external effect is committed but
            # before this node returns and receives its completion checkpoint.
            os._exit(73)
        return {"report": report_id}

    graph = StateGraph(State)
    graph.add_node("region", branch("region", -30))
    graph.add_node("product", branch("product", -20))
    graph.add_node("approval", approval)
    graph.add_node("report", report)
    graph.add_edge(START, "region")
    graph.add_edge(START, "product")
    graph.add_edge(["region", "product"], "approval")
    graph.add_conditional_edges("approval", lambda s: "report" if s["approved"] else END)
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer)


def worker(args):
    work = Path(args.work)
    config = {"configurable": {"thread_id": args.case}, "max_concurrency": 2}
    with SqliteSaver.from_conn_string(str(work / "checkpoints.sqlite")) as saver:
        graph = make_graph(saver, work, args.mode == "approve-crash")
        inputs = {
            "start": {"run_id": args.case, "observations": []},
            "approve": Command(resume=True),
            "deny": Command(resume=False),
            "approve-crash": Command(resume=True),
            "recover": None,
        }
        updates = []
        for update in graph.stream(inputs[args.mode], config, stream_mode="updates"):
            # Public tool/node results only; no model reasoning exists here.
            updates.append(json.loads(json.dumps(update, default=lambda v: getattr(v, "value", str(v)))))
        snapshot = graph.get_state(config)
        payload = {
            "pid": os.getpid(), "mode": args.mode, "case": args.case,
            "updates": updates, "state": snapshot.values,
            "next": list(snapshot.next),
            "checkpoint_id": snapshot.config["configurable"]["checkpoint_id"],
        }
        print(json.dumps(payload, ensure_ascii=False))


def run_suite(output: Path):
    checks: dict[str, bool] = {}
    records: list[dict] = []
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="xuanyue-kernel-contract-") as temp:
        work = Path(temp)

        def invoke(case, mode, expected=0):
            p = subprocess.run([
                sys.executable, __file__, "--worker", "--work", str(work),
                "--case", case, "--mode", mode,
            ], capture_output=True, text=True, timeout=30)
            record = {"case": case, "mode": mode, "returncode": p.returncode,
                      "expected_returncode": expected, "stderr": p.stderr}
            if p.returncode == 0:
                record["result"] = json.loads(p.stdout)
            else:
                record["stdout"] = p.stdout
            records.append(record)
            if p.returncode != expected:
                raise AssertionError(record)
            return record.get("result")

        try:
            first = invoke("approved", "start")
            observations = first["state"]["observations"]
            checks["two_parallel_branches_visible"] = (
                Counter(o["branch"] for o in observations) == {"region": 1, "product": 1}
                and len({o["thread"] for o in observations}) == 2
                and max(o["started_ns"] for o in observations) < min(o["finished_ns"] for o in observations)
            )
            checks["interrupt_before_report"] = first["next"] == ["approval"] and "report" not in first["state"]
            second = invoke("approved", "approve")
            checks["resume_in_new_process"] = first["pid"] != second["pid"] and second["state"]["report"] == "approved:report:v1"
            checks["completed_branches_not_repeated"] = second["state"]["observations"] == observations
            invoke("denied", "start")
            denied = invoke("denied", "deny")
            checks["denial_ends_without_report"] = denied["next"] == [] and "report" not in denied["state"]
            invoke("crashed", "start")
            invoke("crashed", "approve-crash", expected=73)
            recovered = invoke("crashed", "recover")
            checks["abrupt_exit_recovers_from_sqlite"] = recovered["state"]["report"] == "crashed:report:v1" and recovered["next"] == []
            with sqlite3.connect(work / "effects.sqlite") as db:
                counts = dict(db.execute("SELECT id, COUNT(*) FROM attempts GROUP BY id"))
                effects = dict(db.execute("SELECT id, COUNT(*) FROM effects GROUP BY id"))
            checks["replayed_effect_deduplicated_by_application_key"] = counts.get("crashed:report:v1") == 2 and effects.get("crashed:report:v1") == 1
            checks["denied_case_has_no_external_effect"] = "denied:report:v1" not in effects
        except Exception as exc:
            failures.append(repr(exc))
        result = {
            "schema_version": 1, "executed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "passed" if checks and all(checks.values()) and not failures else "failed",
            "environment": {"python": platform.python_version(), "os": platform.system(), "architecture": platform.machine()},
            "packages": {name: importlib.metadata.version(name) for name in ["langgraph", "langgraph-checkpoint", "langgraph-checkpoint-sqlite"]},
            "input": "hardcoded synthetic branch values; no real business data",
            "checks": checks, "failures": failures, "records": records,
            "not_tested": ["LLM reasoning", "multi-agent answer quality", "MCP handshake", "OS sandbox", "Windows/Linux packaging", "power loss", "distributed execution", "schema migration", "tree cancellation", "memory correctness"],
            "important_limit": "The graph may repeat a node after a crash. Exactly-once effect here depends on the experiment's SQLite unique key, not on LangGraph alone.",
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "checks": checks, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--work")
    parser.add_argument("--case")
    parser.add_argument("--mode")
    parser.add_argument("--output", type=Path, default=Path("results/2026-09-24-macos-arm64.json"))
    args = parser.parse_args()
    if args.worker:
        worker(args)
    else:
        sys.exit(run_suite(args.output))
