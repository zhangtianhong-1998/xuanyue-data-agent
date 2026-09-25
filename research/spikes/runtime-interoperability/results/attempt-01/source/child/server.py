"""AgentScope executor behind the official A2A 1.x HTTP server. Synthetic only."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import uuid

import uvicorn
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value
from starlette.applications import Starlette
from a2a.helpers import new_task, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part, Role, TaskState
from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.credential import CredentialBase
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultState, UserMsg
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, ToolChunk, Toolkit


class ScriptedModel(ChatModelBase):
    """Only the model is scripted; the AgentScope Agent and tool loop are real."""
    def __init__(self, seed: int):
        super().__init__(CredentialBase(), "synthetic-no-api", self.Parameters(), stream=False, max_retries=0)
        self.formatter = OpenAIChatFormatter()
        self.replies = iter([
            [ToolCallBlock(name="double", id="synthetic-tool-call", input=json.dumps({"seed": seed}))],
            [TextBlock(text=f"Synthetic result: {seed * 2}")],
        ])

    async def _call_api(self, *args, **kwargs):
        return ChatResponse(content=next(self.replies), is_last=True)


class ScopeExecutor(AgentExecutor):
    def __init__(self, directory: Path):
        self.directory = directory

    def record(self, kind: str, **values):
        with (self.directory / "child-events.jsonl").open("a") as stream:
            stream.write(json.dumps({"kind": kind, **values}) + "\n")

    async def execute(self, context: RequestContext, event_queue):
        payload = json.loads(context.get_user_input())
        assert context.task_id and context.context_id
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        if context.current_task is None:
            await event_queue.enqueue_event(new_task(
                task_id=context.task_id, context_id=context.context_id,
                state=TaskState.TASK_STATE_SUBMITTED, history=[context.message]))
        self.record("execute", task_id=context.task_id, context_id=context.context_id,
                    delegation_id=context.metadata.get("delegation_id"),
                    parent_run_id=context.metadata.get("parent_run_id"),
                    mode=payload.get("mode", "normal"))
        if payload.get("mode") == "require-input":
            await updater.requires_input(new_text_message("Provide synthetic seed.", role=Role.ROLE_AGENT))
            return
        await updater.start_work()
        seed = payload["seed"]
        tool_result = {}

        async def double(seed: int):
            self.record("tool_started", task_id=context.task_id, seed=seed)
            if payload.get("mode") == "slow":
                # Cancellation must interrupt this actual tool coroutine, not only its label.
                await asyncio.sleep(30)
            tool_result.update({"seed": seed, "doubled": seed * 2, "synthetic": True})
            self.record("tool_completed", task_id=context.task_id, result=tool_result)
            return ToolChunk(content=[TextBlock(text=json.dumps(tool_result))], state=ToolResultState.SUCCESS)

        tool = FunctionTool(func=double, name="double", description="Double a synthetic integer.",
            is_read_only=True, is_concurrency_safe=True,
            permission=PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Synthetic local tool"))
        target = Agent("synthetic-child", "Call the synthetic double tool.",
            model=ScriptedModel(seed), toolkit=Toolkit(tools=[tool]),
            injection_config=InjectionConfig(inject_runtime_state=False),
            react_config=ReActConfig(max_iters=3))
        final_message = None
        try:
            async for event in target.reply_stream(UserMsg("parent", context.get_user_input()), yield_final_msg=True):
                if isinstance(event, Msg):
                    final_message = event
                    continue
                # Do not export hidden reasoning; persist a minimal visible event projection.
                content = event.model_dump(mode="json")
                if "THINKING" not in content.get("type", ""):
                    self.record("agentscope_event", task_id=context.task_id, event_type=content.get("type"))
        except asyncio.CancelledError:
            self.record("execute_cancelled", task_id=context.task_id)
            raise
        assert final_message is not None and tool_result
        text = "".join(item.text for item in final_message.content if isinstance(item, TextBlock))
        await updater.add_artifact([
            Part(text=text, media_type="text/plain"),
            Part(data=ParseDict(tool_result, Value()), media_type="application/json"),
        ], artifact_id=str(uuid.uuid4()), name="synthetic-result", last_chunk=True,
            metadata={"parent_run_id": context.metadata.get("parent_run_id", ""),
                      "delegation_id": context.metadata.get("delegation_id", "")})
        await updater.complete()
        self.record("completed", task_id=context.task_id)

    async def cancel(self, context, event_queue):
        self.record("cancel_called", task_id=context.task_id)
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


class WireRecorder:
    """ASGI observation of headers and actual JSON-RPC method names."""
    def __init__(self, app, executor):
        self.app, self.executor = app, executor

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks = []

        async def recorded_receive():
            message = await receive()
            if message["type"] == "http.request":
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    body = b"".join(chunks)
                    parsed = json.loads(body) if body else {}
                    headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
                    self.executor.record("wire_request", path=scope["path"],
                        method=parsed.get("method"), a2a_version=headers.get("a2a-version"))
            return message
        await self.app(scope, recorded_receive, send)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    executor = ScopeExecutor(args.directory)
    card = AgentCard(name="Synthetic AgentScope Child", description="Local interop experiment.",
        version="0.0.1", capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC",
            protocol_version="1.0", url=f"http://127.0.0.1:{args.port}/")],
        default_input_modes=["text/plain"], default_output_modes=["text/plain", "application/json"],
        skills=[AgentSkill(id="synthetic-double", name="Synthetic Double", description="Double an integer.", tags=["synthetic"])])
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    app = Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))
    uvicorn.run(WireRecorder(app, executor), host="127.0.0.1", port=args.port, log_level="error", access_log=False)


if __name__ == "__main__":
    main()
