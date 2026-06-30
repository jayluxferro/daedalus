"""MCP server smoke tests."""

from __future__ import annotations

import pytest

from daedalus.mcp import server as mcp_server

_EXPECTED_TOOLS = {
    "daedalus_health", "daedalus_run", "daedalus_create", "daedalus_list", "daedalus_inspect",
    "daedalus_start", "daedalus_stop", "daedalus_kill", "daedalus_destroy",
    "daedalus_exec", "daedalus_logs",
    "daedalus_image_pull", "daedalus_image_list", "daedalus_image_delete",
    "daedalus_image_inspect", "daedalus_image_push", "daedalus_image_build",
    "daedalus_image_load", "daedalus_image_save", "daedalus_image_tag",
    "daedalus_image_prune",
    "daedalus_system_status", "daedalus_system_restart",
    "daedalus_system_start", "daedalus_system_stop", "daedalus_system_logs",
    "daedalus_system_kernel_set",
    "daedalus_builder_status", "daedalus_builder_start", "daedalus_builder_stop",
    "daedalus_builder_delete",
    "daedalus_registry_login", "daedalus_registry_logout",
    "daedalus_registry_default_inspect", "daedalus_registry_default_set",
    "daedalus_registry_default_unset",
    "daedalus_audit", "daedalus_experiments",
    "daedalus_dns_list", "daedalus_dns_create", "daedalus_dns_delete",
    "daedalus_profiles",
}


def test_mcp_exports_all_tools() -> None:
    missing = _EXPECTED_TOOLS - set(dir(mcp_server))
    assert not missing, f"missing MCP tools: {missing}"


def test_mcp_tool_count() -> None:
    present = [n for n in dir(mcp_server) if n in _EXPECTED_TOOLS]
    assert len(present) == len(_EXPECTED_TOOLS)


def test_mcp_tool_manager_registers_all() -> None:
    registered = set(mcp_server.mcp._tool_manager._tools.keys())
    missing = _EXPECTED_TOOLS - registered
    assert not missing, f"unregistered MCP tools: {missing}"
    assert len(registered) == len(_EXPECTED_TOOLS)


@pytest.mark.asyncio
async def test_mcp_stdio_lists_tools() -> None:
    """MCP stdio handshake exposes every registered tool."""
    import sys

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "daedalus.mcp.server"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            missing = _EXPECTED_TOOLS - names
            assert not missing, f"stdio session missing tools: {missing}"
