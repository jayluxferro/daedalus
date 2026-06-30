"""Live MCP integration — exercise all 42 tools against the real container CLI.

Run with::

    DAEDALUS_LIVE=1 pytest tests/integration/test_mcp_live.py -v

Containers and test image tags created during the run are always destroyed
afterward (even on failure). If cleanup times out, ``container system restart``
is attempted before a final delete pass.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from daedalus.core.capabilities import CapabilityManifest, probe
from daedalus.core.cli_backend import CliBackend
from tests.test_mcp import _EXPECTED_TOOLS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("DAEDALUS_LIVE") != "1",
        reason="set DAEDALUS_LIVE=1 to run live MCP integration",
    ),
]

TEST_IMAGE = "alpine:latest"
TEST_TAG = "daedalus-mcp-live:latest"
TEST_DNS = "daedalus-mcp-live.test"
FAKE_REGISTRY = "registry.example.invalid"


@dataclass
class LiveTracker:
    """Resources created during the live pass — cleaned up in ``finally``."""

    container_ids: list[str] = field(default_factory=list)
    image_tags: list[str] = field(default_factory=list)
    dns_domains: list[str] = field(default_factory=list)
    tmp_paths: list[str] = field(default_factory=list)
    called_tools: set[str] = field(default_factory=set)
    allowed_errors: list[str] = field(default_factory=list)


def _skip_if_no_container(caps: CapabilityManifest) -> None:
    if not caps.container_found:
        pytest.skip("container binary not found")
    if not caps.apiserver_running:
        pytest.skip("container API server not running — start with: container system start")


def _policy_blocked_messages(tracker: LiveTracker) -> bool:
    return any("POLICY" in msg for msg in tracker.allowed_errors)


def _parse_response(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def _mcp_call(
    session: ClientSession,
    tracker: LiveTracker,
    name: str,
    args: dict[str, Any],
    *,
    allow_error: bool = False,
) -> Any:
    """Invoke one MCP tool; record it and optionally tolerate failures."""
    assert name in _EXPECTED_TOOLS
    tracker.called_tools.add(name)
    result = await session.call_tool(name, args)
    text = result.content[0].text if result.content else ""
    if result.isError:
        if allow_error:
            tracker.allowed_errors.append(f"{name}: {text[:300]}")
            return None
        pytest.fail(f"{name} returned error: {text[:500]}")
    try:
        return _parse_response(text)
    except Exception as exc:
        if allow_error:
            tracker.allowed_errors.append(f"{name}: {exc}")
            return None
        raise


async def _cleanup(backend: CliBackend, tracker: LiveTracker) -> None:
    """Remove every resource the live pass may have created."""
    with suppress(Exception):
        await backend.system_start()

    for domain in tracker.dns_domains:
        with suppress(Exception):
            await backend.system_dns_delete(domain)

    for cid in list(dict.fromkeys(tracker.container_ids)):
        with suppress(Exception):
            await backend.kill(cid, signal="KILL")
        with suppress(Exception):
            await backend.stop(cid, timeout=3)
        with suppress(Exception):
            await backend.delete(cid, force=True)

    for tag in tracker.image_tags:
        with suppress(Exception):
            await backend.image_delete(tag, all=True)

    for path in tracker.tmp_paths:
        with suppress(OSError):
            os.unlink(path)

    # Stuck VM cleanup — restart apiserver then retry deletes.
    remaining = []
    with suppress(Exception):
        remaining = await backend.list(all=True)
    orphans = [c.id for c in remaining if c.id != "buildkit"]
    if orphans:
        with suppress(Exception):
            await backend.system_restart()
        await asyncio.sleep(2)
        for cid in orphans:
            with suppress(Exception):
                await backend.delete(cid, force=True)

    with suppress(Exception):
        await backend.system_start()


@pytest.mark.asyncio
async def test_mcp_all_tools_live() -> None:
    """Call every registered MCP tool once; auto-clean afterward."""
    caps = probe()
    _skip_if_no_container(caps)
    backend = CliBackend(caps)
    tracker = LiveTracker()
    tracker.image_tags.append(TEST_TAG)

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "daedalus.mcp.server"],
    )

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = {t.name for t in (await session.list_tools()).tools}
                missing = _EXPECTED_TOOLS - listed
                assert not missing, f"MCP server missing tools: {missing}"

                # --- read-only ------------------------------------------------
                health = await _mcp_call(session, tracker, "daedalus_health", {})
                assert isinstance(health, dict)
                assert health.get("container_version") or health.get("host_arch")

                profiles = await _mcp_call(session, tracker, "daedalus_profiles", {})
                assert isinstance(profiles, list)

                status = await _mcp_call(session, tracker, "daedalus_system_status", {})
                assert isinstance(status, dict)

                containers = await _mcp_call(
                    session, tracker, "daedalus_list", {"all": True},
                )
                assert isinstance(containers, list)

                images = await _mcp_call(session, tracker, "daedalus_image_list", {})
                assert isinstance(images, list)

                await _mcp_call(session, tracker, "daedalus_registry_default_inspect", {})

                audit = await _mcp_call(
                    session, tracker, "daedalus_audit", {"limit": 5},
                )
                assert isinstance(audit, list)

                experiments = await _mcp_call(session, tracker, "daedalus_experiments", {})
                assert isinstance(experiments, list)

                await _mcp_call(session, tracker, "daedalus_dns_list", {})

                builder = await _mcp_call(session, tracker, "daedalus_builder_status", {})
                assert isinstance(builder, dict)

                logs = await _mcp_call(
                    session, tracker, "daedalus_system_logs", {"last": "1m"},
                )
                assert logs is not None

                # --- container lifecycle (create → start → … → destroy) -----
                created = await _mcp_call(session, tracker, "daedalus_create", {
                    "image": TEST_IMAGE,
                    "profile": "bench",
                }, allow_error=True)
                if created is None:
                    if _policy_blocked_messages(tracker):
                        pytest.skip("disk policy blocked container create on this host")
                    pytest.fail("daedalus_create failed unexpectedly")
                assert created.get("ok") is True
                cid = str(created["id"])
                tracker.container_ids.append(cid)

                started = await _mcp_call(
                    session, tracker, "daedalus_start", {"container_id": cid},
                )
                assert started and started.get("ok") is True

                inspected = await _mcp_call(
                    session, tracker, "daedalus_inspect", {"container_id": cid},
                )
                assert isinstance(inspected, dict)

                executed = await _mcp_call(session, tracker, "daedalus_exec", {
                    "container_id": cid,
                    "command": ["echo", "mcp-live"],
                })
                assert executed and executed.get("exit_code") == 0

                await _mcp_call(session, tracker, "daedalus_logs", {
                    "container_id": cid,
                    "tail": 20,
                })

                await _mcp_call(session, tracker, "daedalus_logs_all", {
                    "include_system": False,
                })

                stopped = await _mcp_call(
                    session, tracker, "daedalus_stop", {"container_id": cid},
                )
                assert stopped and stopped.get("ok") is True

                await _mcp_call(
                    session, tracker, "daedalus_start", {"container_id": cid},
                )

                await _mcp_call(
                    session, tracker, "daedalus_kill",
                    {"container_id": cid, "signal": "KILL"},
                )

                # --- daedalus_run + destroy ---------------------------------
                run_result = await _mcp_call(session, tracker, "daedalus_run", {
                    "image": TEST_IMAGE,
                    "profile": "bench",
                    "command": ["sleep", "30"],
                    "detach": True,
                })
                if run_result is None:
                    if _policy_blocked_messages(tracker):
                        pytest.skip("disk policy blocked container run on this host")
                    pytest.fail("daedalus_run failed unexpectedly")
                assert run_result.get("ok") is True
                run_id = str(run_result["id"])
                tracker.container_ids.append(run_id)

                destroyed = await _mcp_call(session, tracker, "daedalus_destroy", {
                    "container_id": run_id,
                    "confirm": True,
                })
                assert destroyed and destroyed.get("ok") is True
                with suppress(ValueError):
                    tracker.container_ids.remove(run_id)

                destroyed2 = await _mcp_call(session, tracker, "daedalus_destroy", {
                    "container_id": cid,
                    "confirm": True,
                })
                assert destroyed2 and destroyed2.get("ok") is True
                with suppress(ValueError):
                    tracker.container_ids.remove(cid)

                # --- images -------------------------------------------------
                await _mcp_call(session, tracker, "daedalus_image_inspect", {
                    "image": TEST_IMAGE,
                })

                await _mcp_call(session, tracker, "daedalus_image_tag", {
                    "source": TEST_IMAGE,
                    "target": TEST_TAG,
                })

                with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
                    save_path = tmp.name
                tracker.tmp_paths.append(save_path)
                saved = await _mcp_call(session, tracker, "daedalus_image_save", {
                    "image": TEST_IMAGE,
                    "output": save_path,
                })
                assert saved and saved.get("ok") is True

                await _mcp_call(session, tracker, "daedalus_image_pull", {
                    "image": "nonexistent-mcp-live-xyz:latest",
                    "scheme": "docker",
                }, allow_error=True)

                await _mcp_call(session, tracker, "daedalus_image_push", {
                    "image": TEST_TAG,
                }, allow_error=True)

                await _mcp_call(session, tracker, "daedalus_image_prune", {})

                await _mcp_call(session, tracker, "daedalus_image_delete", {
                    "image": TEST_TAG,
                })
                with suppress(ValueError):
                    tracker.image_tags.remove(TEST_TAG)

                await _mcp_call(session, tracker, "daedalus_image_load", {
                    "path": "/nonexistent/daedalus-mcp-live.tar",
                }, allow_error=True)

                await _mcp_call(session, tracker, "daedalus_image_build", {
                    "context": "/nonexistent/daedalus-mcp-live-ctx",
                    "tag": "daedalus-mcp-live-build:latest",
                }, allow_error=True)

                # --- registry -----------------------------------------------
                await _mcp_call(session, tracker, "daedalus_registry_login", {
                    "server": FAKE_REGISTRY,
                    "username": "test",
                    "password": "test",
                    "scheme": "docker",
                }, allow_error=True)

                await _mcp_call(session, tracker, "daedalus_registry_logout", {
                    "server": FAKE_REGISTRY,
                }, allow_error=True)

                await _mcp_call(session, tracker, "daedalus_registry_default_set", {
                    "host": "docker.io",
                    "scheme": "docker",
                }, allow_error=True)

                await _mcp_call(session, tracker, "daedalus_registry_default_unset", {})

                # --- DNS (may need sudo) ------------------------------------
                created_dns = await _mcp_call(session, tracker, "daedalus_dns_create", {
                    "domain": TEST_DNS,
                }, allow_error=True)
                if created_dns and created_dns.get("ok"):
                    tracker.dns_domains.append(TEST_DNS)

                await _mcp_call(session, tracker, "daedalus_dns_delete", {
                    "domain": TEST_DNS,
                    "confirm": True,
                }, allow_error=True)
                if TEST_DNS in tracker.dns_domains:
                    tracker.dns_domains.remove(TEST_DNS)

                # --- builder ------------------------------------------------
                await _mcp_call(session, tracker, "daedalus_builder_start", {
                    "cpus": 1,
                    "memory": "1024M",
                }, allow_error=True)

                await _mcp_call(session, tracker, "daedalus_builder_stop", {}, allow_error=True)

                await _mcp_call(session, tracker, "daedalus_builder_delete", {
                    "force": True,
                }, allow_error=True)

                # --- system (last — may disrupt apiserver) ------------------
                await _mcp_call(session, tracker, "daedalus_system_restart", {})

                await _mcp_call(session, tracker, "daedalus_system_start", {})

                await _mcp_call(session, tracker, "daedalus_system_stop", {}, allow_error=True)

                await _mcp_call(session, tracker, "daedalus_system_kernel_set", {
                    "recommended": False,
                }, allow_error=True)

                not_called = _EXPECTED_TOOLS - tracker.called_tools
                assert not not_called, f"tools never called: {not_called}"

    finally:
        await _cleanup(backend, tracker)
        leftover = []
        with suppress(Exception):
            await backend.system_start()
            leftover = await backend.list(all=True)
        test_left = [c.id for c in leftover if c.id != "buildkit"]
        assert not test_left, f"cleanup left containers: {test_left}"
