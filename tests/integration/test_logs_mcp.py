"""Live MCP log tool tests — follow and logs_all."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from daedalus.core.capabilities import probe
from daedalus.core.cli_backend import CliBackend

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("DAEDALUS_LIVE") != "1",
        reason="set DAEDALUS_LIVE=1 to run live MCP log tests",
    ),
]


async def _call(session: ClientSession, name: str, args: dict) -> tuple[bool, str]:
    result = await session.call_tool(name, args)
    text = result.content[0].text if result.content else ""
    return not result.isError, text


@pytest.mark.asyncio
async def test_mcp_logs_follow_and_all() -> None:
    """Exercise daedalus_logs, daedalus_logs_all, and bounded follow."""
    caps = probe()
    if not caps.container_found or not caps.apiserver_running:
        pytest.skip("container daemon not available")

    backend = CliBackend(caps)
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "daedalus.mcp.server"],
    )
    cid: str | None = None
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                ok, text = await _call(session, "daedalus_run", {
                    "image": "alpine:latest",
                    "profile": "bench",
                    "command": ["sh", "-c", "echo mcp-logs-test && sleep 10"],
                    "detach": True,
                })
                if not ok and "POLICY" in text:
                    pytest.skip("disk policy blocked container create")
                assert ok, text
                cid = json.loads(text)["id"]

                await asyncio.sleep(2)

                ok, logs = await _call(session, "daedalus_logs", {
                    "container_id": cid,
                })
                assert ok
                assert "mcp-logs-test" in logs

                ok, _ = await _call(session, "daedalus_logs", {
                    "container_id": cid,
                    "follow": True,
                    "follow_seconds": 2,
                })
                assert ok

                ok, all_text = await _call(session, "daedalus_logs_all", {
                    "include_system": False,
                })
                assert ok
                all_data = json.loads(all_text)
                assert all_data.get("ok") is True
                assert any(c["id"] == cid for c in all_data["containers"])
    finally:
        if cid:
            try:
                await backend.kill(cid, signal="KILL")
            except Exception:
                pass
            try:
                await backend.delete(cid, force=True)
            except Exception:
                pass
