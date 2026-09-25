"""AgentScope 2.0.8 release probe. Synthetic model; no provider/network calls."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.credential import CredentialBase
from agentscope.event import ConfirmResult, RequireUserConfirmEvent, UserConfirmResultEvent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import DataBlock, Msg, TextBlock, ToolCallBlock, ToolResultState, URLSource, UserMsg
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, ToolChunk, Toolkit

HERE = Path(__file__).resolve().parent


class ScriptedModel(ChatModelBase):
    """Only the model boundary is replaced. Agent/tool/permission loop is real."""

    def __init__(self, replies, name="synthetic-model"):
        super().__init__(CredentialBase(), name, self.Parameters(), stream=False, max_retries=0)
        self.formatter = OpenAIChatFormatter()
        self.replies = iter(replies)

    async def _call_api(self, *args, **kwargs):
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        return ChatResponse(content=value, is_last=True)


def agent(replies, tools=(), state=None, name="synthetic-model"):
    return Agent("synthetic-agent", "Use only the supplied synthetic tools.",
                 model=ScriptedModel(replies, name), toolkit=Toolkit(tools=list(tools)),
                 state=state, injection_config=InjectionConfig(inject_runtime_state=False),
                 react_config=ReActConfig(max_iters=5, stop_on_reject=True))


async def collect(target, inputs, run="run-1", branch="main", attempt=1):
    """Application event identity adapter; provider thinking is excluded."""
    events = []
    try:
        async for event in target.reply_stream(inputs):
            payload = event.model_dump(mode="json")
            if "THINKING" in str(payload.get("type", "")):
                continue
            events.append({"run_id": run, "node_id": payload.get("tool_call_id", "agent"),
                           "attempt_id": attempt, "branch_id": branch, "event": payload})
    except Exception as exc:
        # The framework propagates failures; the application must persist them.
        events.append({"run_id": run, "node_id": "agent", "attempt_id": attempt,
                       "branch_id": branch, "event": {"type": "APPLICATION_ERROR",
                       "error": f"{type(exc).__name__}: {exc}"}})
        if run != "errors":
            raise
    return events


def tool(func, name, *, ask=False, readonly=True):
    permission = PermissionDecision(behavior=PermissionBehavior.ASK if ask else PermissionBehavior.ALLOW, message="Synthetic experiment policy")
    return FunctionTool(func=func, name=name, description="Synthetic local experiment operation.",
                        is_read_only=readonly, is_concurrency_safe=readonly, permission=permission)


def call(name, ident, **kwargs):
    return ToolCallBlock(name=name, id=ident, input=json.dumps(kwargs))


def chunk(text):
    return ToolChunk(content=[TextBlock(text=text)], state=ToolResultState.SUCCESS)


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


async def initial_pause(directory):
    async def transform():
        return chunk("prior-result=42")

    async def publish():
        raise AssertionError("Effect executed before approval")

    obj = agent([[call("transform", "transform-1")], [call("publish", "publish-1")]],
                [tool(transform, "transform"), tool(publish, "publish", ask=True, readonly=False)])
    events = await collect(obj, UserMsg("user", "Transform then publish."))
    pending = next(e["event"] for e in events if e["event"]["type"] == "REQUIRE_USER_CONFIRM")
    save(directory / "paused-state.json", obj.state.model_dump(mode="json"))
    save(directory / "pending.json", pending)
    return events


async def resume_worker(directory, denied=False, crash=False, idempotent=False):
    state = AgentState.model_validate_json((directory / "paused-state.json").read_text())
    pending = RequireUserConfirmEvent.model_validate_json((directory / "pending.json").read_text())

    async def publish():
        db = sqlite3.connect(directory / "effects.sqlite")
        db.execute("CREATE TABLE IF NOT EXISTS effects (id TEXT)")
        if not idempotent or not db.execute("SELECT 1 FROM effects WHERE id=?", ("publish-1",)).fetchone():
            db.execute("INSERT INTO effects VALUES (?)", ("publish-1",))
        db.commit()
        db.close()
        if crash:
            os._exit(73)  # committed effect, no completed tool event/checkpoint
        return chunk("synthetic-published")

    obj = agent([[TextBlock(text="done")]], [tool(publish, "publish", ask=True, readonly=False)], state)
    answer = UserConfirmResultEvent(reply_id=pending.reply_id,
        confirm_results=[ConfirmResult(confirmed=not denied, tool_call=c) for c in pending.tool_calls])
    events = await collect(obj, answer)
    save(directory / "resumed-events.json", events)
    save(directory / "resumed-state.json", obj.state.model_dump(mode="json"))


def worker(directory, *flags):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(directory), *flags],
                            capture_output=True, text=True, timeout=30)
    return {"returncode": result.returncode, "stderr": result.stderr[-1800:]}


def effects(directory):
    if not (directory / "effects.sqlite").exists():
        return 0
    with sqlite3.connect(directory / "effects.sqlite") as db:
        return db.execute("SELECT count(*) FROM effects").fetchone()[0]


async def run(output):
    report = {"scenario_id": "kernel-workflow-v1", "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "framework": "AgentScope", "distribution": importlib.metadata.version("agentscope"),
              "source": "PyPI released wheel, not current main", "python": platform.python_version(),
              "platform": platform.platform(), "no_external_llm_calls": True,
              "file_sha256": {str(p.relative_to(HERE.parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__).resolve(), HERE / "requirements.lock", HERE.parent / "scenario-v1.json"]},
              "capability_probe": {k: importlib.util.find_spec(k) is not None for k in
                 ["agentscope.sop", "agentscope.middleware._model_router"]}, "results": []}
    traces = []

    async def case(ident, origin, operation):
        try:
            detail = await operation()
            row = {"id": ident, "status": "passed", "implementation_origin": origin, "evidence": detail}
        except Exception as exc:
            row = {"id": ident, "status": "failed", "implementation_origin": origin,
                   "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        report["results"].append(row)

    async def multimodal():
        msg = UserMsg("user", [TextBlock(text="synthetic picture"),
              DataBlock(source=URLSource(url="artifact://synthetic/chart-001", media_type="image/png"))])
        restored = Msg.model_validate_json(msg.model_dump_json())
        assert restored.model_dump() == msg.model_dump()
        return {"block_types": [b.type for b in restored.content], "artifact_reference": str(restored.content[1].source.url),
                "boundary": "Schema serialization only; artifact resolution/model vision not tested."}

    async def routing():
        # Fixed operation and allowlist selection are application code.
        value = 21 * 2
        allow = {"fast": "synthetic-fast", "review": "synthetic-review"}
        observed = []
        for choice in ["fast", "review", "untrusted-node"]:
            selected = choice if choice in allow else "review"
            obj = agent([[TextBlock(text=f"{value}:{selected}")]], name=allow[selected])
            es = await collect(obj, UserMsg("user", f"route={choice}"), run="routing", branch=selected)
            traces.extend(es)
            names = [e["event"]["model_name"] for e in es if e["event"]["type"] == "MODEL_CALL_START"]
            assert names == [allow[selected]], names
            observed.append({"input": choice, "selected": selected, "model_calls": names})
        return {"fixed_result": value, "routes": observed,
                "boundary": "Application allowlist and scripted cheap-model output; no native workflow router claimed."}

    async def parallel():
        timing = {}
        async def branch(label: str):
            timing[label] = {"start": time.monotonic()}
            await asyncio.sleep(0.12)
            timing[label]["end"] = time.monotonic()
            return chunk(label)
        obj = agent([[call("branch", "branch-a", label="a"), call("branch", "branch-b", label="b")],
                     [TextBlock(text="joined")]], [tool(branch, "branch")])
        es = await collect(obj, UserMsg("user", "Run two branches."), run="parallel")
        traces.extend(es)
        assert max(v["start"] for v in timing.values()) < min(v["end"] for v in timing.values())
        results = [e["event"]["tool_call_id"] for e in es if e["event"]["type"] == "TOOL_RESULT_END"]
        assert set(results) == {"branch-a", "branch-b"}
        return {"overlap": True, "completed_tool_ids": results,
                "boundary": "Native concurrent tools and join inside one agent reply; no persisted arbitrary DAG join claimed."}

    async def recovery():
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            es = await initial_pause(directory)
            traces.extend(es)
            before = effects(directory)
            child = worker(directory)
            assert child["returncode"] == 0, child
            state = json.loads((directory / "resumed-state.json").read_text())
            assert before == 0 and effects(directory) == 1
            assert "prior-result=42" in json.dumps(state)
            traces.extend(json.loads((directory / "resumed-events.json").read_text()))
            return {"separate_os_process": True, "prior_result_retained": True, "effect_count": 1,
                    "boundary": "Native AgentState/HITL resume + application JSON storage; not workflow-node checkpoint history."}

    async def gate():
        counts = []
        for denied in [True, False]:
            with tempfile.TemporaryDirectory() as d:
                directory = Path(d)
                await initial_pause(directory)
                child = worker(directory, *(["--denied"] if denied else []))
                assert child["returncode"] == 0, child
                counts.append(effects(directory))
        assert counts == [0, 1], counts
        return {"rejected_effects": counts[0], "approved_effects": counts[1],
                "boundary": "Native tool-confirmation gate. Product branch-selection policy still application-owned."}

    async def replay():
        observations = []
        for dedup in [False, True]:
            with tempfile.TemporaryDirectory() as d:
                directory = Path(d)
                await initial_pause(directory)
                flags = ["--idempotent"] if dedup else []
                crashed = worker(directory, "--crash", *flags)
                assert crashed["returncode"] == 73, crashed
                resumed = worker(directory, *flags)
                assert resumed["returncode"] == 0, resumed
                observations.append(effects(directory))
        assert observations == [2, 1], observations
        return {"without_application_dedup": observations[0], "with_application_dedup": observations[1],
                "injected_exit_code": 73, "boundary": "Single-worker SQLite check+insert demonstrates application dedup only; not a distributed exactly-once guarantee."}

    async def events():
        obj = agent([RuntimeError("synthetic-model-error")])
        es = await collect(obj, UserMsg("user", "Fail deliberately."), run="errors", branch="rejected")
        traces.extend(es)
        assert any(e["event"].get("error") for e in es), es
        assert traces and all(all(k in e for k in ["run_id", "node_id", "attempt_id", "branch_id"]) for e in traces)
        return {"event_count": len(traces), "error_observed": True,
                "boundary": "Native event payload + application identity envelope and JSON persistence. No hidden reasoning collected."}

    await case("F01", "framework_native", multimodal)
    await case("F02", "application_custom", routing)
    await case("F03", "framework_native", parallel)
    await case("F04", "framework_plus_adapter", recovery)
    report["results"].append({"id": "F05", "status": "unsupported_without_custom_code",
        "implementation_origin": "application_custom", "evidence":
        "Released AgentState restores one current agent. No native node-history/fork API found in reviewed pipeline/state modules; copying JSON alone does not meet F05."})
    await case("F06", "framework_plus_adapter", gate)
    await case("F07", "application_custom", replay)
    await case("F08", "framework_plus_adapter", events)
    output.mkdir(parents=True, exist_ok=True)
    save(output / "results.json", report)
    save(output / "events.json", traces)
    print(json.dumps({"results": [{"id": r["id"], "status": r["status"]} for r in report["results"]]}))
    return int(any(r["status"] == "failed" for r in report["results"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "results" / "release-2.0.8")
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--denied", action="store_true")
    parser.add_argument("--crash", action="store_true")
    parser.add_argument("--idempotent", action="store_true")
    args = parser.parse_args()
    if args.worker:
        asyncio.run(resume_worker(args.worker, args.denied, args.crash, args.idempotent))
    else:
        sys.exit(asyncio.run(run(args.output)))
