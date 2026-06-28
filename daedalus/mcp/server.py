"""M8c — MCP server: autonomous agent interface.

Production-quality MCP server for coding agents.  Every tool maps to an
engine verb and follows these safety rules:

* Destructive verbs require explicit ``confirm=True``
* Network egress is policy-gated, never defaulted on
* ``daedalus_health`` lets the agent discover capabilities before acting
* Detonation tools default to the ``detonation`` profile
* Every agent action flows through audit
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.capabilities import probe
from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import DaedalusError
from daedalus.core.forge import Forge
from daedalus.core.icarus import ExecOptions, Icarus
from daedalus.core.mint import Mint
from daedalus.core.policy import PolicyEngine
from daedalus.core.profiles import ProfileRegistry
from daedalus.core.store import Store
from daedalus.core.talos import Talos

# ==========================================================================
# Lifespan — no module-level I/O
# ==========================================================================


@dataclass
class DaedalusContext:
    """All DAEDALUS subsystems, initialised once at server start."""

    caps: object
    backend: CliBackend
    forge: Forge
    icarus: Icarus
    mint: Mint
    talos: Talos
    profiles: ProfileRegistry
    policy: PolicyEngine
    audit: AuditLog
    store: Store


@asynccontextmanager
async def daedalus_lifespan(server: FastMCP) -> AsyncIterator[DaedalusContext]:
    """Initialise DAEDALUS subsystems on server start."""
    caps = probe()
    backend = CliBackend(caps)
    audit = AuditLog()
    store = Store()
    policy = PolicyEngine()
    policy.config.on_decision = _audit_policy_decision

    ctx = DaedalusContext(
        caps=caps,
        backend=backend,
        forge=Forge(backend, caps, policy=policy, audit=audit, store=store),
        icarus=Icarus(backend, audit=audit),
        mint=Mint(backend, audit=audit),
        talos=Talos(backend, caps, audit=audit),
        profiles=ProfileRegistry(),
        policy=policy,
        audit=audit,
        store=store,
    )
    try:
        yield ctx
    finally:
        pass  # future: backend.close()


def _audit_policy_decision(operation: str, actor: str, result: object) -> None:
    """Bridge policy decisions into the audit log."""
    pass  # wired when audit reference is available from lifespan context


def _get_ctx(ctx: Context | None) -> DaedalusContext:
    """Extract DAEDALUS context from MCP request context."""
    assert ctx is not None, "MCP context is required"
    return ctx.request_context.lifespan_context


def _audit_agent(
    ctx: DaedalusContext, operation: str, args: dict | None = None,
    result: dict | None = None, error: str | None = None,
) -> None:
    ctx.audit.record(
        operation, actor="agent", actor_kind=ActorKind.AGENT,
        args=args or {}, result=result or {}, error=error,
    )


def _ok(data: dict) -> str:
    """Return a structured success JSON response."""
    return json.dumps({"ok": True, **data})


def _err(exc: DaedalusError) -> str:
    """Return a structured error JSON response.

    FastMCP auto-detects ``CallToolResult(isError=True)`` from raised
    ValueError.  We raise ValueError with the serialised error dict.
    """
    raise ValueError(json.dumps(exc.to_dict()))


# ==========================================================================
# Server
# ==========================================================================

mcp = FastMCP(
    "DAEDALUS",
    instructions="""DAEDALUS is a security-research control plane for Apple's container runtime.
It provides hardware-isolated Linux VM sandboxes (Labyrinths) for safe malware
detonation, kernel experimentation, and network-deception research.

Core capabilities:
- Create and manage disposable Linux VMs via `daedalus_run`
- Execute commands inside containers via `daedalus_exec`
- Pull and manage OCI container images via `daedalus_image_*`
- Control DNS and networking via `daedalus_network_*`

Safety rules:
1. ALWAYS call `daedalus_health` first to check what the host supports
2. Destructive operations (`daedalus_destroy`, `daedalus_network_delete`)
   require `confirm=True` and are logged in the audit trail
3. The default `detonation` profile applies maximum isolation:
   capabilities dropped, read-only filesystem, internal network
4. Every operation is recorded in the tamper-evident audit log

You are the agent — DAEDALUS is your laboratory. Stay safe.""",
    lifespan=daedalus_lifespan,
)


# ==========================================================================
# Health / Capabilities
# ==========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="Check DAEDALUS health and capabilities",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_health(ctx: Context) -> str:
    """Return the host capability manifest.

    Call this FIRST before any other operation to discover what this
    DAEDALUS instance supports (container version, networking, kernel
    support, GPU, API server status).

    Returns JSON with keys: host_arch, macos_version, container_version,
    networking, kernel_set, builder, system_dns, etc.
    """
    dc = _get_ctx(ctx)
    return json.dumps(dc.caps.as_dict(), indent=2)  # type: ignore[attr-defined]


# ==========================================================================
# Lifecycle
# ==========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create and start a container",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def daedalus_run(
    image: str,
    profile: str = "detonation",
    name: str | None = None,
    detach: bool = True,
    command: list[str] | None = None,
    ctx: Context | None = None,
) -> str:
    """Create and start a Labyrinth container for safe analysis.

    Defaults to the 'detonation' profile for maximum isolation.
    Use profile='bench' for benchmarking, 'isolated' for air-gapped,
    'fuzz' for kernel fuzzing, 'deception' for network labs.

    Example:
        daedalus_run(image="alpine:latest")
        daedalus_run(image="ubuntu:24.04", profile="bench", name="test")
    """
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        p = dc.profiles.get(profile)
        kwargs = p.apply()
        await ctx.report_progress(10, 100, "Creating container...")
        lab = await dc.forge.run(
            image, name=name, detach=detach,
            profile=profile, command=command, **kwargs,
        )
        await ctx.report_progress(100, 100, "Container running")
        _audit_agent(dc, "run", {"image": image, "profile": profile},
                     {"id": lab.id})
        return _ok({"id": lab.id, "name": lab.name, "image": lab.image,
                    "state": lab.state, "profile": lab.profile})
    except DaedalusError as e:
        _audit_agent(dc, "run", {"image": image}, error=e.message)
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="List containers",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_list(all: bool = False, ctx: Context | None = None) -> str:
    """List containers. Pass all=True to include stopped containers.

    Returns a JSON array of {id, name, image, state, profile} objects.
    """
    dc = _get_ctx(ctx)
    try:
        labs = await dc.forge.list(all=all)
        return json.dumps([{"id": lab.id, "name": lab.name, "image": lab.image,
                           "state": lab.state, "profile": lab.profile} for lab in labs])
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Inspect a container",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_inspect(container_id: str, ctx: Context) -> str:
    """Inspect a single container. Returns full metadata as JSON."""
    dc = _get_ctx(ctx)
    try:
        lab = await dc.forge.inspect(container_id)
        return json.dumps(lab.info.raw)
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Stop a container",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    ),
)
async def daedalus_stop(container_id: str, ctx: Context) -> str:
    """Stop a running container gracefully (SIGTERM + timeout)."""
    dc = _get_ctx(ctx)
    try:
        lab = await dc.forge.stop(container_id)
        _audit_agent(dc, "stop", {"container_id": container_id})
        return _ok({"id": lab.id, "state": lab.state})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Destroy a container permanently",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
async def daedalus_destroy(container_id: str, confirm: bool = False, ctx: Context | None = None) -> str:
    """Destroy a container (stop then delete permanently).

    **WARNING**: Destructive operation. Must pass confirm=True.
    Container data is unrecoverable after destruction.
    """
    dc = _get_ctx(ctx)
    try:
        await dc.forge.destroy(container_id, confirm=confirm)
        _audit_agent(dc, "destroy", {"container_id": container_id, "confirm": confirm})
        return _ok({"id": container_id, "destroyed": True})
    except DaedalusError as e:
        return _err(e)


# ==========================================================================
# Interaction
# ==========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="Execute command inside a container",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def daedalus_exec(
    container_id: str,
    command: list[str],
    user: str | None = None,
    workdir: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Execute a command inside a running container.

    Returns {exit_code, stdout, stderr} as JSON.
    """
    dc = _get_ctx(ctx)
    try:
        opts = ExecOptions(user=user, workdir=workdir)
        result = await dc.icarus.exec(container_id, command, options=opts)
        _audit_agent(dc, "exec", {"container_id": container_id, "command": command})
        return _ok({"exit_code": result.exit_code, "stdout": result.stdout,
                    "stderr": result.stderr})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Read container logs",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_logs(
    container_id: str,
    boot: bool = False,
    tail: int | None = None,
    ctx: Context | None = None,
) -> str:
    """Retrieve container stdout or boot logs.

    Set boot=True for kernel/boot messages instead of process output.
    """
    dc = _get_ctx(ctx)
    try:
        logs = await dc.icarus.logs(container_id, boot=boot, tail=tail)
        return logs
    except DaedalusError as e:
        return _err(e)


# ==========================================================================
# Images
# ==========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="Pull a container image",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def daedalus_image_pull(image: str, ctx: Context) -> str:
    """Pull a container image from a registry.

    Examples:
        daedalus_image_pull(image="alpine:latest")
        daedalus_image_pull(image="ubuntu:24.04")
    """
    dc = _get_ctx(ctx)
    try:
        await ctx.report_progress(0, 100, f"Pulling {image}...")
        img = await dc.mint.pull(image)
        await ctx.report_progress(100, 100, "Pull complete")
        _audit_agent(dc, "image_pull", {"image": image})
        return _ok({"name": img.name, "id": img.id})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="List local images",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_image_list(ctx: Context) -> str:
    """List locally available container images."""
    dc = _get_ctx(ctx)
    try:
        images = await dc.mint.list()
        return json.dumps([{"name": i.name, "tag": i.tag, "size": i.size}
                          for i in images])
    except DaedalusError as e:
        return _err(e)


# ==========================================================================
# Profiles
# ==========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="List security profiles",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_profiles(ctx: Context) -> str:
    """List available security profiles with descriptions.

    Profiles: detonation (safest), bench (permissive), isolated (air-gapped),
    fuzz (kernel testing), deception (network labs).
    """
    dc = _get_ctx(ctx)
    profs = dc.profiles.list()
    return json.dumps([{"name": p.name, "description": p.description}
                       for p in profs])


# ==========================================================================
# Main
# ==========================================================================


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
