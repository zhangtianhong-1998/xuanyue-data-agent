"""Real LangGraph F01-F08, synthetic inputs, subprocesses, no LLM/API calls.

Application-owned pieces are explicit: message validation, public event schema,
branch labels/head references, route policy, local outbox and effect deduplication.
Runtime databases live in a TemporaryDirectory and are not publication artifacts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
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
from typing import Annotated, Literal, TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ROOT = Path(__file__).resolve().parent
CONTRACT = ROOT.parent / "scenario-v1.json"


class TextPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"]
    text: str


class ImagePart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["image"]
    artifact_ref: str
    media_type: Literal["image/png"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    role: Literal["user", "assistant", "tool"]
    parts: list[Annotated[TextPart | ImagePart, Field(discriminator="type")]]


class State(TypedDict):
    run_id: str
    branch_id: str
    messages: list[dict]
    input_value: int
    transformed: int
    simulated_route: str
    route: str
    observations: Annotated[list[dict], operator.add]
    approved: bool
    status: str
    effect_key: str


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def init_ledger(work):
    with sqlite3.connect(work / "ledger.sqlite") as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(key TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts(key TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS effects(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)


def event(work, state, node, attempt, kind, data):
    body = {"run_id": state["run_id"], "branch_id": state["branch_id"],
            "node_id": node, "attempt_id": attempt, "kind": kind,
            "pid": os.getpid(), "at_utc": datetime.now(timezone.utc).isoformat(), "data": data}
    with sqlite3.connect(work / "ledger.sqlite", timeout=20) as db:
        db.execute("INSERT INTO events(body) VALUES (?)", (canonical(body),))


def make_graph(checkpointer, work, crash=False):
    barrier = threading.Barrier(2, timeout=10)

    def observed(name, fn):
        def node(state):
            attempt = str(uuid4())
            event(work, state, name, attempt, "started", {"input": state})
            try:
                result = fn(state)
            except GraphInterrupt:
                event(work, state, name, attempt, "suspended", {"reason": "user_approval"})
                raise
            except Exception as exc:
                event(work, state, name, attempt, "error", {"type": type(exc).__name__, "message": str(exc)})
                raise
            event(work, state, name, attempt, "result", {"output": result})
            return result
        return node

    def normalize(state):
        validated = [Message.model_validate(m).model_dump(mode="json") for m in state["messages"]]
        return {"messages": validated, "transformed": state["input_value"] * 2}

    def router(state):
        # A scripted stand-in, deliberately NOT evidence about real model ability/cost.
        choice = state["simulated_route"]
        if choice not in {"analyze", "reject"}:
            raise ValueError("route_not_allowlisted: " + choice)
        return {"route": choice}

    def branch(name, increment):
        def calculate(state):
            begin = time.monotonic_ns()
            barrier.wait()
            return {"observations": [{"branch": name, "value": state["transformed"] + increment,
                                      "begin_ns": begin, "end_ns": time.monotonic_ns(),
                                      "thread": threading.get_ident()}]}
        return calculate

    def gate(state):
        approved = interrupt({"action": "synthetic_publish", "branch_id": state["branch_id"],
                              "outputs": state["observations"]})
        if not isinstance(approved, bool):
            raise TypeError("approval must be bool")
        return {"approved": approved}

    def reject(state):
        return {"status": "rejected"}

    def publish(state):
        if not state.get("approved"):
            raise PermissionError("publish requires approval")
        key = state["run_id"] + ":" + state["branch_id"] + ":publish:v1"
        payload = canonical({"value": state["transformed"], "observations": state["observations"]})
        with sqlite3.connect(work / "ledger.sqlite", timeout=20) as db:
            db.execute("INSERT OR IGNORE INTO outbox VALUES (?, ?, 'pending')", (key, payload))
            stored = db.execute("SELECT payload FROM outbox WHERE key=?", (key,)).fetchone()[0]
            if stored != payload:
                raise ValueError("idempotency_key_payload_conflict")
        # Local fake receiver: its unique key is the deduplication guarantee.
        # A remote API would need its own idempotency/query/reconciliation contract.
        with sqlite3.connect(work / "ledger.sqlite", timeout=20) as db:
            db.execute("INSERT INTO attempts VALUES (?)", (key,))
            db.execute("INSERT OR IGNORE INTO effects VALUES (?, ?)", (key, payload))
            db.execute("UPDATE outbox SET status='delivered' WHERE key=?", (key,))
        if crash:
            os._exit(73)  # Effect committed, node result/checkpoint not yet written.
        return {"status": "completed", "effect_key": key}

    graph = StateGraph(State)
    for name, fn in {"normalize": normalize, "router": router,
                     "a": branch("a", 1), "b": branch("b", 2),
                     "gate": gate, "reject": reject, "publish": publish}.items():
        graph.add_node(name, observed(name, fn))
    graph.add_edge(START, "normalize")
    graph.add_edge("normalize", "router")
    graph.add_conditional_edges("router", lambda s: ["a", "b"] if s["route"] == "analyze" else "reject")
    graph.add_edge(["a", "b"], "gate")
    graph.add_conditional_edges("gate", lambda s: "publish" if s["approved"] else "reject")
    graph.add_edge("publish", END)
    graph.add_edge("reject", END)
    return graph.compile(checkpointer=checkpointer)


def snapshot(s):
    return {"values": s.values, "next": list(s.next), "config": s.config,
            "parent_config": s.parent_config, "metadata": s.metadata,
            "tasks": [{"id": t.id, "name": t.name, "error": t.error,
                       "interrupts": [{"id": i.id, "value": i.value} for i in t.interrupts]} for t in s.tasks]}


def sample(case, route="analyze"):
    return {"run_id": case, "branch_id": "original", "input_value": 5,
            "messages": [{"id": "synthetic-message", "role": "user", "parts": [
                {"type": "text", "text": "Synthetic question"},
                {"type": "image", "artifact_ref": "artifact://synthetic-placeholder",
                 "media_type": "image/png", "sha256": "0" * 64}]}],
            "simulated_route": route, "observations": [], "status": "running"}


def worker(args):
    work = Path(args.work)
    init_ledger(work)
    request = json.loads((work / "request.json").read_text())
    config = request.get("config", {"configurable": {"thread_id": args.case}, "max_concurrency": 2})
    with SqliteSaver.from_conn_string(str(work / "checkpoints.sqlite")) as saver:
        graph = make_graph(saver, work, args.mode == "approve-crash")
        payload = {"pid": os.getpid(), "mode": args.mode, "case": args.case}
        if args.mode == "inspect":
            payload["snapshot"] = snapshot(graph.get_state(config))
        else:
            if args.mode == "fork":
                config = graph.update_state(config, request.get("values", {"input_value": 7, "branch_id": request["branch_id"]}), as_node=request.get("as_node", START))
                payload["fork_config"] = config
                payload["fork_snapshot"] = snapshot(graph.get_state(config))
                value = None
            else:
                value = {"start": request.get("input", sample(args.case)),
                         "approve": Command(resume=True), "deny": Command(resume=False),
                         "approve-crash": Command(resume=True), "recover": None}[args.mode]
            try:
                payload["updates"] = list(graph.stream(value, config, stream_mode="updates", durability="sync"))
            except Exception as exc:
                payload["error"] = {"type": type(exc).__name__, "message": str(exc)}
            latest = {"configurable": {"thread_id": args.case}}
            payload["snapshot"] = snapshot(graph.get_state(latest))
        payload["history"] = [snapshot(s) for s in graph.get_state_history({"configurable": {"thread_id": args.case}})]
        print(json.dumps(payload, ensure_ascii=False, default=lambda v: getattr(v, "value", str(v))))


def suite(output):
    records, checks, failure_log = [], {}, []
    results = {r["id"]: {"name": r["name"], "status": "not_tested"}
               for r in json.loads(CONTRACT.read_text())["requirements"]}
    with tempfile.TemporaryDirectory(prefix="xuanyue-langgraph-comparison-") as temp:
        work = Path(temp)

        def invoke(case, mode, request=None, expected=0):
            (work / "request.json").write_text(json.dumps(request or {}))
            p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--work", str(work),
                                "--case", case, "--mode", mode], capture_output=True, text=True, timeout=35)
            rec = {"case": case, "mode": mode, "returncode": p.returncode,
                   "expected_returncode": expected, "stderr": p.stderr}
            if p.returncode == 0:
                rec["result"] = json.loads(p.stdout)
            else:
                rec["stdout"] = p.stdout
            records.append(rec)
            if p.returncode != expected:
                raise AssertionError(rec)
            return rec.get("result")

        def set_result(key, names, origin, ownership):
            results[key].update(status="passed" if all(checks[n] for n in names) else "failed",
                                implementation_origin=origin, checks=names, ownership=ownership)

        try:
            first = invoke("rejected", "start")
            first_state = first["snapshot"]["values"]
            reread = invoke("rejected", "inspect", {"config": first["snapshot"]["config"]})
            checks["message_roundtrip_new_process"] = (reread["pid"] != first["pid"] and
                reread["snapshot"]["values"]["messages"] == sample("rejected")["messages"])
            try:
                Message.model_validate({"id": "bad", "role": "user", "parts": [{"type": "video"}]})
                checks["unknown_modality_rejected"] = False
            except ValidationError:
                checks["unknown_modality_rejected"] = True
            set_result("F01", ["message_roundtrip_new_process", "unknown_modality_rejected"], "framework_plus_adapter",
                       "Pydantic application message; native checkpoint serialization. Image bytes/model not tested.")
            denied = invoke("rejected", "deny")
            checks["deterministic_transform"] = first_state["transformed"] == 10
            checks["allowed_route_parallel_only"] = first_state["route"] == "analyze" and len(first_state["observations"]) == 2
            route_reject = invoke("route-reject", "start", {"input": sample("route-reject", "reject")})
            invalid = invoke("invalid-route", "start", {"input": sample("invalid-route", "delete_everything")})
            checks["router_reject_skips_analysis"] = route_reject["snapshot"]["values"]["status"] == "rejected" and not route_reject["snapshot"]["values"]["observations"]
            checks["invalid_route_rejected"] = invalid.get("error", {}).get("message") == "route_not_allowlisted: delete_everything"
            set_result("F02", ["deterministic_transform", "allowed_route_parallel_only", "router_reject_skips_analysis", "invalid_route_rejected"],
                       "framework_plus_adapter", "Native conditional edges; app allowlist and simulated router. Zero real LLM calls.")
            obs = first_state["observations"]
            checks["parallel_overlap_and_join"] = (Counter(o["branch"] for o in obs) == {"a": 1, "b": 1} and
                len({o["thread"] for o in obs}) == 2 and max(o["begin_ns"] for o in obs) < min(o["end_ns"] for o in obs))
            set_result("F03", ["parallel_overlap_and_join"], "framework_native", "Native fanout/join; barrier is observation aid.")
            checks["pause_before_effect"] = first["snapshot"]["next"] == ["gate"] and "effect_key" not in first_state
            checks["new_process_resumes_preserving_results"] = denied["pid"] != first["pid"] and denied["snapshot"]["values"]["observations"] == obs
            set_result("F04", ["pause_before_effect", "new_process_resumes_preserving_results"], "framework_native",
                       "SqliteSaver + interrupt + Command(resume); explicit sync durability.")

            completed_start = invoke("completed", "start")
            completed = invoke("completed", "approve")
            fork_rows = []
            for case, original in [("rejected", denied), ("completed", completed)]:
                before = original["snapshot"]
                base = next(h for h in original["history"] if h["next"] == ["normalize"])
                fork = invoke(case, "fork", {"config": base["config"], "branch_id": "fork-1"})
                fork_done = invoke(case, "approve")
                old = invoke(case, "inspect", {"config": before["config"]})
                old_ids = {h["config"]["configurable"]["checkpoint_id"] for h in original["history"]}
                new_ids = {h["config"]["configurable"]["checkpoint_id"] for h in old["history"]}
                good = (fingerprint(before) == fingerprint(old["snapshot"]) and old_ids <= new_ids and
                        fork_done["snapshot"]["values"]["transformed"] == 14 and
                        fork_done["snapshot"]["values"]["branch_id"] == "fork-1" and
                        fork["fork_snapshot"]["parent_config"] == base["config"] and
                        before["config"] != fork_done["snapshot"]["config"])
                checks[case + "_fork_preserves_original"] = good
                fork_rows.append({"case": case, "parent_checkpoint": base["config"],
                                  "original_head": before["config"], "fork_head": fork_done["snapshot"]["config"],
                                  "original_snapshot_sha256_before": fingerprint(before),
                                  "original_snapshot_sha256_after": fingerprint(old["snapshot"])})
            # Fork at a non-initial checkpoint: retain normalize result, change route only.
            middle = next(h for h in completed["history"] if h["next"] == ["router"])
            mid_fork = invoke("completed", "fork", {"config": middle["config"], "branch_id": "fork-middle",
                "as_node": "normalize", "values": {"simulated_route": "reject", "branch_id": "fork-middle"}})
            old_again = invoke("completed", "inspect", {"config": completed["snapshot"]["config"]})
            mid_updates = {k for update in mid_fork["updates"] for k in update}
            checks["middle_checkpoint_preserves_prior_and_reexecutes_downstream"] = (
                mid_fork["snapshot"]["values"]["transformed"] == 10 and
                mid_fork["snapshot"]["values"]["status"] == "rejected" and
                mid_updates == {"router", "reject"} and
                fingerprint(completed["snapshot"]) == fingerprint(old_again["snapshot"]))
            set_result("F05", ["rejected_fork_preserves_original", "completed_fork_preserves_original",
                "middle_checkpoint_preserves_prior_and_reexecutes_downstream"],
                       "framework_plus_adapter", "Native update_state/history/checkpoint DAG; app names branches and pins branch heads.")

            crash_start = invoke("crash", "start")
            invoke("crash", "approve-crash", expected=73)
            recovered = invoke("crash", "recover")
            with sqlite3.connect(work / "ledger.sqlite") as db:
                attempts = dict(db.execute("SELECT key, COUNT(*) FROM attempts GROUP BY key"))
                effects = dict(db.execute("SELECT key, COUNT(*) FROM effects GROUP BY key"))
                outbox = [dict(zip(["key", "status"], row)) for row in db.execute("SELECT key,status FROM outbox ORDER BY key")]
                events = [dict(seq=row[0], **json.loads(row[1])) for row in db.execute("SELECT seq,body FROM events ORDER BY seq")]
            checks["rejected_original_no_publish"] = (denied["snapshot"]["values"]["status"] == "rejected" and
                "rejected:original:publish:v1" not in effects and "route-reject:original:publish:v1" not in effects)
            checks["approved_branch_has_one_effect"] = effects.get("rejected:fork-1:publish:v1") == 1
            set_result("F06", ["rejected_original_no_publish", "approved_branch_has_one_effect"], "framework_plus_adapter",
                       "App approval and branch gate; native conditional edges. No automatic reversal of previous effects.")
            checks["crash_recovered_new_process"] = (recovered["pid"] != crash_start["pid"] and recovered["snapshot"]["values"]["status"] == "completed")
            checks["effect_attempted_twice_committed_once"] = attempts.get("crash:original:publish:v1") == 2 and effects.get("crash:original:publish:v1") == 1
            checks["outbox_delivered"] = all(row["status"] == "delivered" for row in outbox)
            set_result("F07", ["crash_recovered_new_process", "effect_attempted_twice_committed_once", "outbox_delivered"],
                       "application_custom", "Native recovery replays node; local outbox + receiver unique key prevent duplicate effect.")
            fields = {"run_id", "node_id", "attempt_id", "branch_id", "kind", "pid", "data"}
            checks["persisted_public_event_identity"] = bool(events) and all(fields <= set(e) for e in events)
            checks["error_and_suspension_visible"] = {"started", "result", "error", "suspended"} <= {e["kind"] for e in events}
            checks["replay_attempts_distinct"] = len({e["attempt_id"] for e in events if e["run_id"] == "crash" and e["node_id"] == "publish" and e["kind"] == "started"}) == 2
            set_result("F08", ["persisted_public_event_identity", "error_and_suspension_visible", "replay_attempts_distinct"],
                       "framework_plus_adapter", "Native stream/history + custom durable public node events. Abrupt exit leaves a started event with no result.")
        except Exception as exc:
            failure_log.append(repr(exc))
            fork_rows, attempts, effects, outbox, events = [], {}, {}, [], []

    report = {"schema_version": 1, "scenario_id": "kernel-workflow-v1", "framework": "langgraph",
              "executed_at_utc": datetime.now(timezone.utc).isoformat(),
              "status": "passed" if all(r["status"] == "passed" for r in results.values()) and not failure_log else "failed",
              "environment": {"python": platform.python_version(), "os": platform.system(), "architecture": platform.machine()},
              "packages": {n: importlib.metadata.version(n) for n in ["langgraph", "langgraph-checkpoint", "langgraph-checkpoint-sqlite", "langchain-core", "pydantic"]},
              "file_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), CONTRACT, ROOT / "uv.lock", ROOT / "pyproject.toml"]},
              "requirements": results, "checks": checks, "failure_log": failure_log,
              "expected_failure_cases": ["invalid-route ValueError", "process exit 73 after committed effect before node completion"],
              "branch_registry_observations": fork_rows, "effect_attempts": attempts, "effect_counts": effects,
              "outbox": outbox, "public_events": events, "records": records,
              "not_tested": ["real LLM or cheap-model route accuracy/cost", "image decoding/model vision quality", "workflow visual editor", "subgraph persistence", "graph/schema version migration", "cancellation", "OS sandbox", "remote API idempotency or compensation", "concurrent writers on same thread", "Windows/Linux packaging", "memory quality and user profile maintenance", "power-loss durability"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "requirements": results, "checks": checks, "failure_log": failure_log}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--work")
    parser.add_argument("--case")
    parser.add_argument("--mode")
    parser.add_argument("--output", type=Path, default=ROOT / "results/2026-09-25-macos-arm64.json")
    args = parser.parse_args()
    if args.worker:
        worker(args)
    else:
        sys.exit(suite(args.output))
