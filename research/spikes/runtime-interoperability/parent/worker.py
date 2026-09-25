"""Real LangGraph parent, application delegation ledger, official A2A client."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sqlite3
from typing import Literal, TypedDict
import uuid

import httpx
from google.protobuf.json_format import MessageToDict
from a2a.client import ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import CancelTaskRequest, GetTaskRequest, Role, SendMessageRequest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, ValidationError


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class State(TypedDict, total=False):
    run_id: str
    seed: int
    mode: str
    task: dict
    normalized: dict


class SyntheticResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int
    doubled: int
    synthetic: Literal[True]


def project_part(part):
    if "text" in part:
        return {"kind": "text", "text": part["text"]}
    if "data" in part:
        return {"kind": "structured", "value": SyntheticResult.model_validate(part["data"]).model_dump()}
    raise ValueError("unsupported_part: no registered adapter for this content")


class Gateway:
    """Fixed experiment adapter, not a complete portable runtime implementation."""
    capabilities = {"fork": False, "query": True, "continue": True, "cancel": True}

    def __init__(self, url, directory):
        self.url, self.directory = url, directory

    def record(self, kind, **values):
        with (self.directory / "parent-events.jsonl").open("a") as stream:
            stream.write(json.dumps({"kind": kind, "pid": os.getpid(), **values}) + "\n")

    async def client(self, streaming=True, polling=False):
        http_client = httpx.AsyncClient(timeout=40, trust_env=False)
        return await create_client(self.url, ClientConfig(streaming=streaming, polling=polling, httpx_client=http_client))

    def mapping(self, run_id):
        path = self.directory / f"mapping-{run_id}.json"
        return json.loads(path.read_text()) if path.exists() else None

    async def send(self, run_id, payload, existing=None, polling=False):
        client = await self.client(streaming=not polling, polling=polling)
        message = new_text_message(json.dumps(payload), role=Role.ROLE_USER)
        if existing:
            message.task_id, message.context_id = existing["task_id"], existing["context_id"]
        delegation_id = existing["delegation_id"] if existing else f"delegation:{run_id}:child:1"
        request = SendMessageRequest(message=message, metadata={"parent_run_id": run_id, "delegation_id": delegation_id})
        self.record("send_message", run_id=run_id, delegation_id=delegation_id, continuation=bool(existing))
        mapping = existing
        try:
            async for event in client.send_message(request):
                event_dict = MessageToDict(event)
                self.record("a2a_event", run_id=run_id, event=event_dict)
                if event.HasField("task"):
                    mapping = {"task_id": event.task.id, "context_id": event.task.context_id,
                               "delegation_id": delegation_id, "parent_run_id": run_id}
                    # Product-owned durable correlation. This does not close the pre-response uncertainty gap.
                    save(self.directory / f"mapping-{run_id}.json", mapping)
            assert mapping is not None
            task = await client.get_task(GetTaskRequest(id=mapping["task_id"]))
            return MessageToDict(task)
        finally:
            await client.close()

    async def query(self, mapping):
        client = await self.client()
        try:
            self.record("get_task", run_id=mapping["parent_run_id"], task_id=mapping["task_id"])
            return MessageToDict(await client.get_task(GetTaskRequest(id=mapping["task_id"])))
        finally:
            await client.close()

    async def cancel(self, mapping):
        client = await self.client()
        try:
            return MessageToDict(await client.cancel_task(CancelTaskRequest(id=mapping["task_id"])))
        finally:
            await client.close()

    def fork(self, mapping):
        if not self.capabilities["fork"]:
            raise NotImplementedError("runtime_capability_unavailable:fork; choose a new task from boundary input")


def run(args):
    gateway = Gateway(args.url, args.directory)
    if args.action == "mapping-negative":
        rejected = []
        for label, part in [("invalid_schema", {"data": {"seed": "invalid", "doubled": 6, "synthetic": True}}),
                            ("unknown_modality", {"url": "https://invalid.example/synthetic-image", "mediaType": "image/png"})]:
            try:
                project_part(part)
            except (ValidationError, ValueError):
                rejected.append(label)
        return {"rejected": rejected, "network_fetches": 0}
    if args.action == "query":
        return asyncio.run(gateway.query(gateway.mapping(args.run_id)))
    if args.action == "slow":
        return asyncio.run(gateway.send(args.run_id, {"seed": args.seed, "mode": "slow"}, polling=True))
    if args.action == "cancel":
        return asyncio.run(gateway.cancel(gateway.mapping(args.run_id)))
    if args.action == "fork":
        try:
            gateway.fork(gateway.mapping(args.run_id))
        except NotImplementedError as error:
            return {"status": "unsupported", "error": str(error)}
        raise AssertionError("Unsupported fork was accepted")

    def prepare(state):
        gateway.record("prepare", run_id=state["run_id"])
        return state

    def delegate(state):
        existing = gateway.mapping(state["run_id"])
        if existing:
            result = asyncio.run(gateway.query(existing))
        else:
            result = asyncio.run(gateway.send(state["run_id"], {"seed": state["seed"], "mode": state["mode"]}))
            if args.action == "crash":
                os._exit(73)  # child completed and mapping persisted; parent node has no completed checkpoint
        return {"task": result}

    def review(state):
        if state["task"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
            answer = interrupt({"kind": "child_input_required", "task_id": state["task"]["id"]})
            task = asyncio.run(gateway.send(state["run_id"], {"seed": answer["seed"], "mode": "normal"},
                existing=gateway.mapping(state["run_id"])))
            return {"task": task}
        return {}

    def normalize(state):
        task = state["task"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        artifacts = [{"artifact_id": a["artifactId"], "parts": [project_part(part) for part in a["parts"]]} for a in task["artifacts"]]
        return {"normalized": {"run_id": state["run_id"], "child_task_id": task["id"],
            "status": "completed", "artifacts": artifacts}}

    builder = StateGraph(State)
    for name, node in [("prepare", prepare), ("delegate", delegate), ("review", review), ("normalize", normalize)]:
        builder.add_node(name, node)
    for source, dest in [(START, "prepare"), ("prepare", "delegate"), ("delegate", "review"), ("review", "normalize"), ("normalize", END)]:
        builder.add_edge(source, dest)
    with SqliteSaver.from_conn_string(str(args.directory / "parent.sqlite")) as checkpointer:
        graph = builder.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": args.run_id}}
        if args.action == "resume-input":
            inputs = Command(resume={"seed": args.seed})
        elif args.action == "recover":
            inputs = None
        else:
            inputs = {"run_id": args.run_id, "seed": args.seed, "mode": "require-input" if args.action == "pause" else "normal"}
        output = graph.invoke(inputs, config)
        result = {key: value for key, value in output.items() if key != "__interrupt__"}
        result["paused"] = bool(output.get("__interrupt__"))
        result["parent_pid"] = os.getpid()
        result["next_nodes"] = list(graph.get_state(config).next)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--action", choices=["run", "crash", "recover", "pause", "resume-input", "query", "slow", "cancel", "fork", "mapping-negative"], default="run")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    save(args.output, run(args))
