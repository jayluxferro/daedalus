"""MCP tool smoke tests via stdio — exercises each read-safe tool."""

from __future__ import annotations

import json
import sys

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests.test_mcp import _EXPECTED_TOOLS

_READ_CALLS: list[tuple[str, dict]] = [
    ("daedalus_health", {}),
    ("daedalus_list", {"all": True}),
    ("daedalus_profiles", {}),
    ("daedalus_system_status", {}),
    ("daedalus_audit", {"limit": 5}),
    ("daedalus_experiments", {}),
    ("daedalus_dns_list", {}),
    ("daedalus_image_list", {}),
    ("daedalus_builder_status", {}),
    ("daedalus_image_prune", {}),
]


async def _call_tool(name: str, args: dict) -> dict | list:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "daedalus.mcp.server"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
            text = result.content[0].text if result.content else "{}"
            return json.loads(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", _READ_CALLS)
async def test_mcp_read_tool(tool: str, args: dict) -> None:
    assert tool in _EXPECTED_TOOLS
    data = await _call_tool(tool, args)
    assert data is not None
    if tool == "daedalus_health":
        assert data.get("ok") is True or "host_arch" in data or "runtime_features" in data


@pytest.mark.asyncio
async def test_mcp_inspect_container() -> None:
    containers = await _call_tool("daedalus_list", {"all": True})
    if not isinstance(containers, list) or not containers:
        pytest.skip("no containers")
    cid = containers[0]["id"] if isinstance(containers[0], dict) else containers[0].get("id")
    data = await _call_tool("daedalus_inspect", {"container_id": cid})
    assert data is not None
