"""Real stdio SDK roundtrip. Not an LLM, OAuth or OS sandbox test."""
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
from datetime import datetime, timezone

from mcp import Client, StdioServerParameters

HERE = Path(__file__).resolve().parent


async def main() -> int:
    report = {"executed_at_utc": datetime.now(timezone.utc).isoformat(),
              "environment": {"python": platform.python_version(), "platform": platform.platform(),
                              "mcp": importlib.metadata.version("mcp")},
              "checks": {}, "observations": {}, "failures": [],
              "not_tested": ["LLM tool selection", "remote MCP", "OAuth", "old-version interop",
                             "untrusted process isolation", "cloud egress authorization", "Windows/Linux"]}
    checks = report["checks"]
    observations = report["observations"]
    original = os.environ.get("XUANYUE_SPIKE_SENTINEL")
    os.environ["XUANYUE_SPIKE_SENTINEL"] = "non-secret-test-marker"
    child_pid = None
    try:
        parameters = StdioServerParameters(command=sys.executable, args=[str(HERE / "server.py")])
        async with Client(parameters) as client:
            observations["protocol_version"] = client.protocol_version
            checks["protocol_identified"] = bool(client.protocol_version)
            listing = await client.list_tools()
            tools = {tool.name: tool for tool in listing.tools}
            observations["tool_names"] = sorted(tools)
            checks["tools_discovered"] = set(tools) == {"contribution", "export_report", "slow_probe", "status_probe"}
            checks["tool_input_schema"] = tools["contribution"].input_schema["properties"]["region"]["enum"] == ["east", "west"]

            async def permitted_call(name, arguments):
                # An application-owned gate. MCP discovery is not authorization.
                if name not in {"contribution", "status_probe", "slow_probe"}:
                    raise PermissionError("Tool is outside this synthetic run grant")
                return await client.call_tool(name, arguments)

            result = await permitted_call("contribution", {"region": "east"})
            observations["contribution"] = result.model_dump(mode="json")
            checks["structured_result"] = not result.is_error and result.structured_content["delta"] == -700
            invalid = await permitted_call("contribution", {"region": "not_a_region"})
            observations["invalid_arguments"] = invalid.model_dump(mode="json")
            checks["invalid_argument_visible_error"] = bool(invalid.is_error)
            unknown = await client.call_tool("not_a_tool", {})
            observations["unknown_tool"] = unknown.model_dump(mode="json")
            checks["unknown_tool_visible_error"] = bool(unknown.is_error)
            try:
                await permitted_call("export_report", {})
            except PermissionError:
                checks["host_gate_denies_ungranted_tool"] = True
            else:
                checks["host_gate_denies_ungranted_tool"] = False
            # Wait for observable start before cancellation; avoids passing a
            # test that canceled before a request had actually reached the server.
            slow = asyncio.create_task(permitted_call("slow_probe", {}))
            try:
                for _ in range(50):
                    response = await permitted_call("status_probe", {})
                    if response.is_error or response.structured_content is None:
                        raise ValueError("status_probe did not return the required structured result")
                    state = response.structured_content
                    if state["slow_started"]:
                        break
                    await asyncio.sleep(0.02)
            finally:
                slow.cancel()
                try:
                    await slow
                except asyncio.CancelledError:
                    pass
            for _ in range(50):
                state = (await permitted_call("status_probe", {})).structured_content
                if state["slow_cancelled"]:
                    break
                await asyncio.sleep(0.02)
            observations["final_server_status"] = state
            child_pid = state["pid"]
            checks["separate_process"] = child_pid != os.getpid()
            checks["denied_tool_not_executed"] = state["exports"] == 0
            checks["cancel_reaches_running_server_tool"] = (state["slow_started"] == 1 and
                state["slow_cancelled"] == 1 and state["slow_completed"] == 0)
            checks["unlisted_fake_environment_not_inherited"] = not state["sentinel_inherited"]
        if child_pid:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                checks["server_process_closed"] = True
            else:
                checks["server_process_closed"] = False
    except Exception as exc:
        report["failures"].append(repr(exc))
    finally:
        if original is None:
            os.environ.pop("XUANYUE_SPIKE_SENTINEL", None)
        else:
            os.environ["XUANYUE_SPIKE_SENTINEL"] = original
    report["script_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in [HERE / "server.py", HERE / "run.py"]}
    report["passed"] = len(checks) == 12 and all(checks.values()) and not report["failures"]
    (HERE / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "checks": checks, "failures": report["failures"]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
