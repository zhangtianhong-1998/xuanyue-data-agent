"""Handwritten stdlib execution spike, not a production scheduler or sandbox."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
SCENARIO = HERE.parent / "scenario-v1.json"
REQUIRED_IDS = [item["id"] for item in json.loads(SCENARIO.read_text())["requirements"]]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, state TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, run TEXT NOT NULL, node TEXT NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS effects(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS effect_attempts(key TEXT NOT NULL);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def event(self, run, node, phase, attempt=1, branch="main", payload=None):
        with self.connect() as db:
            db.execute("INSERT INTO events(payload) VALUES (?)", (json.dumps({
                "event_id": uuid.uuid4().hex, "run_id": run, "node_id": node,
                "phase": phase, "attempt": attempt, "branch_id": branch,
                "payload": payload, "pid": os.getpid(),
            }),))

    def save(self, run, state, status):
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,status=excluded.status",
                       (run, json.dumps(state), status))

    def load(self, run):
        with self.connect() as db:
            state, status = db.execute("SELECT state,status FROM runs WHERE id=?", (run,)).fetchone()
        return json.loads(state), status

    def checkpoint(self, run, node, state):
        checkpoint_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO checkpoints VALUES (?,?,?,?)", (checkpoint_id, run, node, json.dumps(state)))
        return checkpoint_id

    def history(self, run):
        with self.connect() as db:
            state = db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchall()
            checkpoints = db.execute("SELECT * FROM checkpoints WHERE run=? ORDER BY id", (run,)).fetchall()
            events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM events ORDER BY seq")]
        return {"state": state, "checkpoints": checkpoints, "events": [e for e in events if e["run_id"] == run]}


def message():
    return {"schema_version": 1, "id": "message-synthetic", "role": "user", "content": [
        {"type": "text", "text": "Compare two synthetic branches"},
        {"type": "image", "artifact_id": "synthetic-image-reference", "mime_type": "image/png", "sha256": "0" * 64},
    ]}


def route(value):
    if value not in {"parallel", "escalate"}:
        raise ValueError("Route not in workflow allowlist")
    return value


def route_node(store, run, value):
    store.event(run, "route", "started")
    try:
        chosen = route(value)
    except ValueError as exc:
        store.event(run, "route", "error", payload={"type": type(exc).__name__, "message": str(exc)})
        raise
    store.event(run, "route", "completed", payload={"provider": "simulator", "route": chosen})
    return chosen


def start(store, run, seed=3, source=None):
    if source:
        with store.connect() as db:
            row = db.execute("SELECT run,state FROM checkpoints WHERE id=?", (source,)).fetchone()
        state = json.loads(row[1])
        state.update(seed=seed, derived_from={"run_id": row[0], "checkpoint_id": source})
        state.pop("observations", None)
    else:
        state = {"message": message(), "seed": seed}
    state["run_id"] = run
    store.event(run, "transform", "started")
    state["normalized"] = int(state["seed"]) * 2
    store.event(run, "transform", "completed", payload={"value": state["normalized"], "llm_calls": 0})
    state["route"] = route_node(store, run, "parallel")  # No real small model.
    source_checkpoint = store.checkpoint(run, "before_parallel", state)
    barrier = threading.Barrier(2, timeout=5)

    def branch(name, weight):
        store.event(run, name, "started", branch=name)
        began = time.monotonic_ns()
        barrier.wait()
        result = {"branch": name, "value": state["normalized"] * weight, "thread": threading.get_ident(),
                  "start_ns": began, "end_ns": time.monotonic_ns()}
        store.event(run, name, "completed", branch=name, payload=result)
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(branch, "candidate_a", 2), pool.submit(branch, "candidate_b", 3)
        state["observations"] = [a.result(), b.result()]
    state["fork_checkpoint_id"] = source_checkpoint
    store.checkpoint(run, "before_review", state)
    store.save(run, state, "waiting_for_review")
    store.event(run, "review", "waiting")
    return {"pid": os.getpid(), "status": "waiting_for_review", "state": state}


def resume(store, run, approve, crash=False):
    state, previous = store.load(run)
    if previous not in {"waiting_for_review", "publishing"}:
        raise ValueError("Run is not resumable here")
    store.event(run, "review", "approved" if approve else "rejected")
    if not approve:
        store.save(run, state, "rejected")
        return {"pid": os.getpid(), "status": "rejected", "state": state}
    key = f"{run}:publish:v1"
    store.save(run, state, "publishing")
    with store.connect() as db:
        attempt = db.execute("SELECT count(*) FROM effect_attempts WHERE key=?", (key,)).fetchone()[0] + 1
    store.event(run, "publish", "started", attempt=attempt)
    with store.connect() as db:
        db.execute("INSERT INTO effect_attempts VALUES (?)", (key,))
        db.execute("INSERT OR IGNORE INTO effects VALUES (?,?)", (key, json.dumps(state["observations"])))
    if crash:
        os._exit(73)
    store.event(run, "publish", "completed", attempt=attempt)
    store.save(run, state, "completed")
    return {"pid": os.getpid(), "status": "completed", "state": state}


def suite():
    checks, records, failures = {}, [], []
    with tempfile.TemporaryDirectory(prefix="xuanyue-handwritten-") as temp:
        database = Path(temp) / "runtime.sqlite"
        store = Store(database)

        def invoke(run, mode, expected=0, source=None, seed=3):
            args = [sys.executable, str(Path(__file__).resolve()), "--database", str(database), "--run", run,
                    "--mode", mode, "--seed", str(seed)]
            if source:
                args.extend(["--source", source])
            result = subprocess.run(args, text=True, capture_output=True, timeout=20)
            record = {"run": run, "mode": mode, "returncode": result.returncode,
                      "expected_returncode": expected, "stderr": result.stderr}
            if result.stdout.strip():
                record["output"] = json.loads(result.stdout)
            records.append(record)
            if result.returncode != expected:
                raise RuntimeError(record)
            return record.get("output")

        try:
            initial = invoke("original", "start")
            state = initial["state"]
            checks["F01"] = json.loads(json.dumps(state["message"])) == message()
            try:
                route_node(store, "invalid-route", "run_arbitrary_command")
            except ValueError:
                invalid_rejected = True
            else:
                invalid_rejected = False
            checks["F02"] = state["normalized"] == 6 and state["route"] == "parallel" and invalid_rejected
            obs = state["observations"]
            checks["F03"] = len({o["thread"] for o in obs}) == 2 and max(o["start_ns"] for o in obs) < min(o["end_ns"] for o in obs)
            rejected = invoke("original", "reject")
            checks["F04"] = rejected["pid"] != initial["pid"] and rejected["state"]["observations"] == obs
            prior_digest = digest(store.history("original"))
            forked = invoke("forked", "start", source=state["fork_checkpoint_id"], seed=4)
            accepted = invoke("forked", "approve")
            checks["F05"] = (digest(store.history("original")) == prior_digest and
                              forked["state"]["normalized"] == 8 and accepted["status"] == "completed")
            with store.connect() as db:
                effects = dict(db.execute("SELECT key,count(*) FROM effects GROUP BY key"))
            checks["F06"] = "original:publish:v1" not in effects and effects.get("forked:publish:v1") == 1
            invoke("crash", "start")
            invoke("crash", "crash", expected=73)
            invoke("crash", "approve")
            with store.connect() as db:
                attempts = db.execute("SELECT count(*) FROM effect_attempts WHERE key='crash:publish:v1'").fetchone()[0]
                count = db.execute("SELECT count(*) FROM effects WHERE key='crash:publish:v1'").fetchone()[0]
                events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM events ORDER BY seq")]
            checks["F07"] = attempts == 2 and count == 1
            checks["F08"] = {"started", "completed", "error"} <= {e["phase"] for e in events} and all({"event_id", "run_id", "node_id", "attempt", "branch_id", "phase"} <= e.keys() for e in events)
            records.append({"event_log": events, "original_trajectory_digest_unchanged": prior_digest})
        except Exception as exc:
            failures.append(repr(exc))
    results = [{"id": ident, "status": "passed" if checks.get(ident) else "failed" if ident in checks else "not_tested",
                "implementation_origin": "application_custom"} for ident in REQUIRED_IDS]
    report = {"scenario": "kernel-workflow-v1", "backend": "handwritten-stdlib",
              "timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "environment": {"python": platform.python_version(), "platform": platform.platform()},
              "scenario_sha256": hashlib.sha256(SCENARIO.read_bytes()).hexdigest(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "results": results, "records": records, "failures": failures,
              "limits": ["Fixed graph only; no general workflow compiler", "No distributed leases or arbitrary graph migrations",
                         "No streaming LLM or tool SDK", "No shared tree budget/cancellation", "No actual sandbox",
                         "No old-state authorization revalidation or sensitive-data redaction", "Image reference only; no image model"],
              "passed": len(checks) == 8 and all(checks.values()) and not failures}
    (HERE / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"passed": report["passed"], "results": results, "failures": failures}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database")
    parser.add_argument("--run")
    parser.add_argument("--mode", choices=["start", "approve", "reject", "crash"])
    parser.add_argument("--source")
    parser.add_argument("--seed", type=int, default=3)
    arguments = parser.parse_args()
    if arguments.database:
        runtime = Store(arguments.database)
        output = start(runtime, arguments.run, arguments.seed, arguments.source) if arguments.mode == "start" else resume(runtime, arguments.run, arguments.mode != "reject", arguments.mode == "crash")
        print(json.dumps(output))
    else:
        sys.exit(suite())
