"""Synthetic MCP server, exclusively for the local protocol experiment."""
import asyncio
import os
from typing import Literal

from mcp.server import MCPServer
from pydantic import BaseModel

server = MCPServer("xuanyue-synthetic-analysis")
counters = {"exports": 0, "slow_started": 0, "slow_completed": 0, "slow_cancelled": 0}


class Contribution(BaseModel):
    region: str
    delta: int
    source: str = "synthetic-fixture-v1"
    interpretation: str = "descriptive; not causal"


@server.tool()
def contribution(region: Literal["east", "west"]) -> Contribution:
    """Read the frozen synthetic contribution for one region."""
    return Contribution(region=region, delta={"east": -700, "west": 300}[region])


@server.tool()
def export_report() -> dict:
    """Simulate an external side effect; no real file or network operation."""
    counters["exports"] += 1
    return {"exports": counters["exports"]}


@server.tool()
async def slow_probe() -> dict:
    """Wait so the client can test cancellation; no external side effects."""
    counters["slow_started"] += 1
    try:
        await asyncio.sleep(5)
        counters["slow_completed"] += 1
        return {"completed": True}
    except BaseException:
        counters["slow_cancelled"] += 1
        raise


@server.tool()
def status_probe() -> dict:
    """Return only experiment counters, process identity and a fake marker flag."""
    return {**counters, "pid": os.getpid(), "sentinel_inherited": "XUANYUE_SPIKE_SENTINEL" in os.environ}


if __name__ == "__main__":
    server.run(transport="stdio")
