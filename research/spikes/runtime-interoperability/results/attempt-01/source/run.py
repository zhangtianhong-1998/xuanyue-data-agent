"""Bounded local interoperability probe. Run with Python 3.12 after uv sync."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def py(environment):
    return HERE / environment / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    results = {"experiment": "runtime-interoperability-v1", "date": "2026-09-25",
        "platform": platform.platform(), "python": platform.python_version(),
        "input_kind": "synthetic", "external_llm_calls": 0, "checks": [], "failures": [],
        "versions": {"langgraph": "1.2.12", "agentscope": "2.0.8", "a2a-sdk": "1.1.5", "wire_protocol": "1.0"},
        "hashes": {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in [HERE / "run.py", HERE / "parent/worker.py", HERE / "child/server.py", HERE / "parent/uv.lock", HERE / "child/uv.lock"]},
        "limitations": [
            "Synthetic model only: no task-quality, real cost or provider compatibility conclusion.",
            "Server InMemoryTaskStore: parent-only recovery; child server crash recovery not tested.",
            "Application mapping saved after first server Task. Send-before-ID uncertainty is unresolved; no exactly-once claim.",
            "Structured artifact, correlation, capability gate and status projection are application adapters.",
            "Input-required is an A2A adapter pause plus LangGraph interrupt, not AgentScope internal checkpoint portability.",
            "New task from boundary input is not cross-runtime checkpoint migration or arbitrary historical fork.",
            "Cancel test covers a cooperative async synthetic tool only, not subprocess trees or irreversible effects.",
            "Local loopback no authentication, TLS, remote retry, sandbox or production stream gap recovery.",
            "Internal AgentScope events recorded locally are not automatically exposed by base A2A.",
        ]}

    def check(ident, name, passed, origin, evidence):
        results["checks"].append({"id": ident, "name": name, "status": "passed" if passed else "failed", "origin": origin, "evidence": evidence})
        if not passed:
            raise AssertionError(f"{ident}: {name}")

    with tempfile.TemporaryDirectory(prefix="xuanyue-interop-") as temporary:
        directory = Path(temporary)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        # Avoid inherited provider secrets in child processes; only runtime essentials are retained.
        environment = {name: value for name, value in os.environ.items() if name in {
            "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "WINDIR"}}
        environment.update({"PYTHONUNBUFFERED": "1", "DO_NOT_TRACK": "1", "OTEL_SDK_DISABLED": "true"})
        log = (directory / "server.log").open("w+")
        server = subprocess.Popen([str(py("child")), str(HERE / "child/server.py"), "--port", str(port),
            "--directory", str(directory)], stdout=log, stderr=subprocess.STDOUT, env=environment)

        def worker(run_id, action="run", seed=3, expected=0):
            result_path = directory / f"worker-{run_id}-{action}.json"
            completed = subprocess.run([str(py("parent")), str(HERE / "parent/worker.py"), "--url", url,
                "--directory", str(directory), "--run-id", run_id, "--action", action, "--seed", str(seed),
                "--output", str(result_path)], capture_output=True, text=True, timeout=45, env=environment)
            if completed.returncode != expected:
                raise RuntimeError(f"worker {run_id}/{action} exit {completed.returncode}: {completed.stderr[-7000:]}")
            return read(result_path) if result_path.exists() else {"returncode": completed.returncode}

        def child_events():
            return read_lines(directory / "child-events.jsonl")

        def task_events(task_id, kind):
            return [event for event in child_events() if event.get("task_id") == task_id and event["kind"] == kind]

        try:
            ready = False
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("Child server exited during startup")
                try:
                    with urllib.request.urlopen(f"{url}/.well-known/agent-card.json", timeout=0.25) as response:
                        card = json.load(response)
                        ready = True
                        break
                except OSError:
                    time.sleep(0.1)
            assert ready
            results["agent_card"] = card
            ordinary = worker("normal")
            mapping = read(directory / "mapping-normal.json")
            task_id = mapping["task_id"]
            artifact = ordinary["normalized"]["artifacts"][0]
            check("I01", "real LangGraph parent delegates real AgentScope child over A2A HTTP", 
                ordinary["normalized"]["status"] == "completed" and len(task_events(task_id, "tool_completed")) == 1,
                "framework_plus_application_adapter", {"task_id": task_id, "tool_calls": len(task_events(task_id, "tool_completed"))})
            check("I02", "text and structured artifact mapped into canonical projection",
                artifact["parts"] == [{"kind": "text", "text": "Synthetic result: 6"},
                    {"kind": "structured", "value": {"seed": 3.0, "doubled": 6.0, "synthetic": True}}],
                "a2a_native_parts_plus_application_projection", artifact)
            child_call = task_events(task_id, "execute")[0]
            check("I03", "parent run, delegation, task and context identity correlate",
                child_call["parent_run_id"] == "normal" and child_call["delegation_id"] == mapping["delegation_id"]
                and child_call["context_id"] == mapping["context_id"], "application_metadata_and_ledger", mapping)
            wire = [event for event in child_events() if event["kind"] == "wire_request" and event.get("method")]
            check("I04", "SDK sends actual A2A 1.0 methods and version header",
                {event["a2a_version"] for event in wire} == {"1.0"}
                and {event["method"] for event in wire} >= {"SendStreamingMessage", "GetTask"},
                "official_sdk", wire)

            paused = worker("input", "pause")
            before_mapping = read(directory / "mapping-input.json")
            continued = worker("input", "resume-input", seed=5)
            check("I05", "input-required crosses process restart and continues same task",
                paused["paused"] and not continued["paused"]
                and paused["parent_pid"] != continued["parent_pid"]
                and before_mapping == read(directory / "mapping-input.json")
                and continued["normalized"]["artifacts"][0]["parts"][1]["value"]["doubled"] == 10,
                "a2a_input_required_plus_langgraph_interrupt_plus_application_adapter",
                {"task_id": before_mapping["task_id"], "paused_next_nodes": paused["next_nodes"], "different_parent_processes": True})

            crashed = worker("recover", "crash", expected=73)
            recovery_mapping = read(directory / "mapping-recover.json")
            before_exec = len(task_events(recovery_mapping["task_id"], "execute"))
            recovered = worker("recover", "recover")
            after_exec = len(task_events(recovery_mapping["task_id"], "execute"))
            check("I06", "parent crash recovery queries saved child task without duplicate execution",
                crashed["returncode"] == 73 and before_exec == after_exec == 1
                and recovered["normalized"]["child_task_id"] == recovery_mapping["task_id"],
                "langgraph_checkpoint_plus_application_ledger_plus_a2a_get_task",
                {"child_execute_before": before_exec, "child_execute_after": after_exec,
                 "task_id": recovery_mapping["task_id"], "crash_after": "child completed, before parent node checkpoint"})

            old_task = worker("normal", "query")
            alternative = worker("new-attempt", seed=7)
            still_old = worker("normal", "query")
            check("I07", "new task at boundary preserves earlier completed task",
                old_task == still_old and old_task["id"] != alternative["normalized"]["child_task_id"]
                and alternative["normalized"]["artifacts"][0]["parts"][1]["value"]["doubled"] == 14,
                "application_new_run_plus_a2a_task_identity", {"old_task": old_task["id"], "new_task": alternative["normalized"]["child_task_id"], "old_unchanged": True})

            worker("cancel", "slow")
            cancel_mapping = read(directory / "mapping-cancel.json")
            cancel_id = cancel_mapping["task_id"]
            for _ in range(100):
                if task_events(cancel_id, "tool_started"):
                    break
                time.sleep(0.02)
            assert task_events(cancel_id, "tool_started")
            canceled = worker("cancel", "cancel")
            time.sleep(0.2)
            canceled_query = worker("cancel", "query")
            check("I08", "cancel interrupts executing cooperative AgentScope tool",
                canceled["status"]["state"] == canceled_query["status"]["state"] == "TASK_STATE_CANCELED"
                and len(task_events(cancel_id, "execute_cancelled")) == 1
                and not task_events(cancel_id, "tool_completed"),
                "a2a_sdk_cancellation_plus_application_cancel_handler",
                {"actual_cancelled_coroutine": True, "tool_completion_count": len(task_events(cancel_id, "tool_completed")), "task_id": cancel_id})

            denied = worker("normal", "fork")
            check("I09", "unsupported historical fork explicitly rejected",
                denied["status"] == "unsupported" and "runtime_capability_unavailable:fork" in denied["error"],
                "application_capability_gate", denied)
            results["examples"] = {"normal": ordinary, "input_resumed": continued, "recovered": recovered, "new_attempt": alternative}
        except Exception as exc:
            results["failures"].append({"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()})
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            log.close()
            replacements = {str(directory): "<temporary-runtime>", str(HERE): "<spike>", str(Path.home()): "<home>", str(port): "<loopback-port>"}

            def sanitized(value):
                encoded = json.dumps(value, ensure_ascii=False)
                for old, replacement in replacements.items():
                    encoded = encoded.replace(old, replacement)
                return json.loads(encoded)

            results["summary"] = {"passed": sum(c["status"] == "passed" for c in results["checks"]),
                "failed": sum(c["status"] == "failed" for c in results["checks"]), "planned_checks": 9,
                "not_reached": 9 - len(results["checks"]), "all_passed": len(results["checks"]) == 9 and not results["failures"]}
            save(output / "results.json", sanitized(results))
            save(output / "child-events.json", sanitized(child_events()))
            save(output / "parent-events.json", sanitized(read_lines(directory / "parent-events.jsonl")))
            save(output / "server-log.json", sanitized({"text": (directory / "server.log").read_text()}))
    print(json.dumps(results["summary"]))
    return 0 if results["summary"]["all_passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.exit(run(args.output))
