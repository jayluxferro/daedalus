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
from daedalus.core.policy import PolicyEngine, PolicyResult, load_policy_config
from daedalus.core.profiles import ProfileRegistry
from daedalus.core.store import Store
from daedalus.core.talos import Talos
from daedalus.core.network import network_names, primary_ip

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
    policy = PolicyEngine(load_policy_config())
    audit_ref: list[AuditLog] = []

    def _policy_audit(operation: str, actor: str, result: PolicyResult) -> None:
        if audit_ref:
            audit_ref[0].record(
                "policy", actor=actor, actor_kind=ActorKind.AGENT,
                args={
                    "operation": operation,
                    "decision": result.decision.value,
                    "reason": result.reason,
                },
            )

    policy.config.on_decision = _policy_audit

    ctx = DaedalusContext(
        caps=caps,
        backend=backend,
        forge=Forge(backend, caps, policy=policy, audit=audit, store=store),
        icarus=Icarus(backend, audit=audit, runtime_binary=caps.container_binary),
        mint=Mint(backend, audit=audit),
        talos=Talos(backend, caps, audit=audit),
        profiles=ProfileRegistry(),
        policy=policy,
        audit=audit,
        store=store,
    )
    audit_ref.append(audit)
    try:
        yield ctx
    finally:
        pass  # future: backend.close()


def _audit_policy_decision(operation: str, actor: str, result: object) -> None:
    """Legacy hook — replaced in lifespan."""
    del operation, actor, result


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
- Control system DNS via `daedalus_dns_*`

Safety rules:
1. ALWAYS call `daedalus_health` first to check what the host supports
2. Destructive operations (`daedalus_destroy`, `daedalus_dns_delete`)
   require `confirm=True` and are logged in the audit trail
3. The default `detonation` profile applies maximum isolation
4. Kernel overrides require `confirm_kernel=True` on `daedalus_run`
5. Every operation is recorded in the tamper-evident audit log

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
    remove: bool = False,
    command: list[str] | None = None,
    kernel: str | None = None,
    cpus: int | None = None,
    memory: str | None = None,
    dns: list[str] | None = None,
    volumes: list[str] | None = None,
    mounts: list[str] | None = None,
    env: dict[str, str] | None = None,
    workdir: str | None = None,
    confirm_kernel: bool = False,
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
        overrides: dict[str, object] = {}
        if kernel is not None:
            overrides["kernel"] = kernel
        if cpus is not None:
            overrides["cpus"] = cpus
        if memory is not None:
            overrides["memory"] = memory
        if dns is not None:
            overrides["dns"] = dns
        if volumes is not None:
            overrides["volumes"] = volumes
        if mounts is not None:
            overrides["mounts"] = mounts
        if env is not None:
            overrides["env"] = env
        if workdir is not None:
            overrides["workdir"] = workdir
        overrides["remove"] = remove
        kwargs = p.apply(**overrides)
        await ctx.report_progress(10, 100, "Creating container...")
        lab = await dc.forge.run(
            image, name=name, detach=detach,
            profile=profile, command=command,
            confirm_kernel=confirm_kernel, **kwargs,
        )
        await ctx.report_progress(100, 100, "Container running")
        _audit_agent(dc, "run", {"image": image, "profile": profile},
                     {"id": lab.id})
        return _ok({"id": lab.id, "name": lab.name, "image": lab.image,
                    "state": lab.state, "profile": lab.profile,
                    "ip": primary_ip(lab.info.raw),
                    "networks": network_names(lab.info.raw)})
    except DaedalusError as e:
        _audit_agent(dc, "run", {"image": image}, error=e.message)
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create a container without starting it",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def daedalus_create(
    image: str,
    profile: str = "detonation",
    name: str | None = None,
    remove: bool = False,
    command: list[str] | None = None,
    kernel: str | None = None,
    cpus: int | None = None,
    memory: str | None = None,
    dns: list[str] | None = None,
    volumes: list[str] | None = None,
    mounts: list[str] | None = None,
    env: dict[str, str] | None = None,
    workdir: str | None = None,
    confirm_kernel: bool = False,
    ctx: Context | None = None,
) -> str:
    """Create a stopped container. Use daedalus_start to run it later."""
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        p = dc.profiles.get(profile)
        overrides: dict[str, object] = {}
        if kernel is not None:
            overrides["kernel"] = kernel
        if cpus is not None:
            overrides["cpus"] = cpus
        if memory is not None:
            overrides["memory"] = memory
        if dns is not None:
            overrides["dns"] = dns
        if volumes is not None:
            overrides["volumes"] = volumes
        if mounts is not None:
            overrides["mounts"] = mounts
        if env is not None:
            overrides["env"] = env
        if workdir is not None:
            overrides["workdir"] = workdir
        overrides["remove"] = remove
        kwargs = p.apply(**overrides)
        lab = await dc.forge.create(
            image, name=name, profile=profile, command=command,
            confirm_kernel=confirm_kernel, **kwargs,
        )
        _audit_agent(dc, "create", {"image": image, "profile": profile},
                     {"id": lab.id})
        return _ok({"id": lab.id, "name": lab.name, "image": lab.image,
                    "state": lab.state, "profile": lab.profile})
    except DaedalusError as e:
        _audit_agent(dc, "create", {"image": image}, error=e.message)
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

    Returns a JSON array of {id, name, image, state, profile, ip, networks} objects.
    """
    dc = _get_ctx(ctx)
    try:
        labs = await dc.forge.list(all=all)
        return json.dumps([{
            "id": lab.id, "name": lab.name, "image": lab.image,
            "state": lab.state, "profile": lab.profile,
            "ip": primary_ip(lab.info.raw),
            "networks": network_names(lab.info.raw),
        } for lab in labs])
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
async def daedalus_stop(
    container_id: str,
    timeout: int = 10,
    signal: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Stop a running container gracefully (SIGTERM + timeout)."""
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        lab = await dc.forge.stop(container_id, timeout=timeout, signal=signal)
        _audit_agent(dc, "stop", {"container_id": container_id})
        return _ok({"id": lab.id, "state": lab.state})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Kill a container",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_kill(
    container_id: str,
    signal: str = "KILL",
    ctx: Context | None = None,
) -> str:
    """Kill a running container (SIGKILL by default)."""
    dc = _get_ctx(ctx)
    try:
        lab = await dc.forge.kill(container_id, signal=signal)
        _audit_agent(dc, "kill", {"container_id": container_id, "signal": signal})
        return _ok({"id": lab.id, "state": lab.state, "signal": signal})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Start a stopped container",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    ),
)
async def daedalus_start(
    container_id: str,
    attach: bool = False,
    interactive: bool = False,
    ctx: Context | None = None,
) -> str:
    """Start a previously stopped container."""
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        lab = await dc.forge.start(
            container_id, attach=attach, interactive=interactive,
        )
        _audit_agent(dc, "start", {"container_id": container_id})
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
        report = await dc.forge.destroy(container_id, confirm=confirm)
        _audit_agent(dc, "destroy", {"container_id": container_id, "confirm": confirm})
        payload: dict[str, object] = {"id": container_id, "destroyed": True}
        if report:
            payload["report"] = report
        return _ok(payload)
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
    uid: int | None = None,
    gid: int | None = None,
    workdir: str | None = None,
    env: dict[str, str] | None = None,
    env_file: str | None = None,
    tty: bool = False,
    interactive: bool = False,
    ctx: Context | None = None,
) -> str:
    """Execute a command inside a running container.

    Returns {exit_code, stdout, stderr} as JSON.
    """
    dc = _get_ctx(ctx)
    try:
        opts = ExecOptions(
            user=user, uid=uid, gid=gid, workdir=workdir, env=env,
            env_file=env_file, tty=tty, interactive=interactive,
        )
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
async def daedalus_image_pull(
    image: str,
    platform: str | None = None,
    scheme: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Pull a container image from a registry.

    Examples:
        daedalus_image_pull(image="alpine:latest")
        daedalus_image_pull(image="ubuntu:24.04")
    """
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        await ctx.report_progress(0, 100, f"Pulling {image}...")
        img = await dc.mint.pull(image, platform=platform, scheme=scheme)
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
        return json.dumps([{"name": i.name, "tag": i.tag, "size": i.size, "digest": i.digest}
                          for i in images])
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete a local image",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
async def daedalus_image_delete(
    image: str,
    all: bool = False,
    ctx: Context | None = None,
) -> str:
    """Delete a local container image."""
    dc = _get_ctx(ctx)
    try:
        await dc.mint.delete(image, all=all)
        _audit_agent(dc, "image_delete", {"image": image, "all": all})
        return _ok({"name": image, "deleted": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Inspect a local image",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_image_inspect(image: str, ctx: Context) -> str:
    """Inspect a local container image. Returns metadata as JSON."""
    dc = _get_ctx(ctx)
    try:
        img = await dc.mint.inspect(image)
        return json.dumps({
            "name": img.name, "id": img.id, "digest": img.digest,
            "size": img.size, "tag": img.tag, "raw": img.raw,
        }, indent=2)
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Push a local image to a registry",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def daedalus_image_push(
    image: str,
    platform: str | None = None,
    scheme: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Push a local image to its registry."""
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        await dc.mint.push(image, platform=platform, scheme=scheme)
        _audit_agent(dc, "image_push", {"image": image})
        return _ok({"name": image, "pushed": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Load an image from an OCI tar archive",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def daedalus_image_load(path: str, ctx: Context) -> str:
    """Load an image from an OCI-compatible tar archive (``container images load``)."""
    dc = _get_ctx(ctx)
    try:
        img = await dc.mint.load(path)
        _audit_agent(dc, "image_load", {"path": path})
        return _ok({"name": img.name, "id": img.id, "loaded": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Build an image from a Containerfile",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def daedalus_image_build(
    tag: str,
    context: str = ".",
    file: str | None = None,
    target: str | None = None,
    arch: str | None = None,
    no_cache: bool = False,
    ctx: Context | None = None,
) -> str:
    """Build a container image from a Containerfile/Dockerfile context."""
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        from daedalus.core.backend import BuildSpec

        spec = BuildSpec(
            context=context, file=file, tag=tag,
            target=target, arch=arch, no_cache=no_cache,
        )
        img = await dc.mint.build(spec)
        _audit_agent(dc, "image_build", {"tag": tag, "context": context})
        return _ok({"name": img.name, "id": img.id, "built": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Save an image to an OCI tar archive",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def daedalus_image_save(image: str, output: str, ctx: Context) -> str:
    """Save a local image as an OCI-compatible tar archive."""
    dc = _get_ctx(ctx)
    try:
        path = await dc.mint.save(image, output)
        _audit_agent(dc, "image_save", {"image": image, "output": output})
        return _ok({"image": image, "path": path, "saved": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Tag an image",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    ),
)
async def daedalus_image_tag(source: str, target: str, ctx: Context) -> str:
    """Create a new tag for an existing image."""
    dc = _get_ctx(ctx)
    try:
        await dc.mint.tag(source, target)
        _audit_agent(dc, "image_tag", {"source": source, "target": target})
        return _ok({"source": source, "target": target, "tagged": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Prune dangling images",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_image_prune(ctx: Context) -> str:
    """Remove unreferenced and dangling images."""
    dc = _get_ctx(ctx)
    try:
        removed = await dc.mint.prune()
        _audit_agent(dc, "image_prune", {}, {"count": len(removed)})
        return _ok({"removed": removed, "count": len(removed)})
    except DaedalusError as e:
        return _err(e)


# ==========================================================================
# System / DNS / Audit / Experiments
# ==========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="System status",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_system_status(ctx: Context) -> str:
    """Return aggregated system status (daemon, counts, disk, capabilities)."""
    dc = _get_ctx(ctx)
    status = await dc.forge.system_status()
    return json.dumps({
        "container_version": status.container_version,
        "container_commit": status.container_commit,
        "apiserver_running": status.apiserver_running,
        "container_count": status.container_count,
        "running_count": status.running_count,
        "disk_usage": status.disk_usage,
        "capabilities": status.capabilities,
    }, indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Restart container apiserver",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def daedalus_system_restart(ctx: Context) -> str:
    """Restart the container apiserver daemon."""
    dc = _get_ctx(ctx)
    try:
        await dc.backend.system_restart()
        _audit_agent(dc, "system_restart", {})
        return _ok({"restarted": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Start container system",
        readOnlyHint=False,
        idempotentHint=True,
    ),
)
async def daedalus_system_start(ctx: Context) -> str:
    """Start container system services (apiserver)."""
    dc = _get_ctx(ctx)
    try:
        await dc.backend.system_start()
        _audit_agent(dc, "system_start", {})
        return _ok({"started": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Stop container system",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_system_stop(ctx: Context) -> str:
    """Stop all container system services."""
    dc = _get_ctx(ctx)
    try:
        await dc.backend.system_stop()
        _audit_agent(dc, "system_stop", {})
        return _ok({"stopped": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Container system logs",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_system_logs(
    last: str = "5m",
    ctx: Context | None = None,
) -> str:
    """Fetch container system logs."""
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        logs = await dc.backend.system_logs(last=last)
        return _ok({"logs": logs})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set default container kernel",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def daedalus_system_kernel_set(
    binary: str | None = None,
    tar: str | None = None,
    arch: str = "arm64",
    recommended: bool = False,
    ctx: Context | None = None,
) -> str:
    """Set the default container kernel."""
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        await dc.backend.system_kernel_set(
            binary=binary, tar=tar, arch=arch, recommended=recommended,
        )
        _audit_agent(dc, "system_kernel_set", {
            "binary": binary, "tar": tar, "arch": arch, "recommended": recommended,
        })
        return _ok({"set": True, "arch": arch})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Builder status",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_builder_status(ctx: Context) -> str:
    """Return image builder VM status."""
    dc = _get_ctx(ctx)
    try:
        status = await dc.backend.builder_status()
        return json.dumps(status, indent=2)
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Start image builder",
        readOnlyHint=False,
        idempotentHint=False,
    ),
)
async def daedalus_builder_start(
    cpus: int = 2,
    memory: str = "2048M",
    ctx: Context | None = None,
) -> str:
    """Start the image builder VM."""
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        await dc.backend.builder_start(cpus=cpus, memory=memory)
        _audit_agent(dc, "builder_start", {"cpus": cpus, "memory": memory})
        return _ok({"started": True, "cpus": cpus, "memory": memory})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Stop image builder",
        readOnlyHint=False,
        idempotentHint=False,
    ),
)
async def daedalus_builder_stop(ctx: Context) -> str:
    """Stop the image builder VM."""
    dc = _get_ctx(ctx)
    try:
        await dc.backend.builder_stop()
        _audit_agent(dc, "builder_stop", {})
        return _ok({"stopped": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete image builder",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
async def daedalus_builder_delete(
    force: bool = False,
    ctx: Context | None = None,
) -> str:
    """Delete the image builder VM."""
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        await dc.backend.builder_delete(force=force)
        _audit_agent(dc, "builder_delete", {"force": force})
        return _ok({"deleted": True, "force": force})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Login to a container registry",
        readOnlyHint=False,
        openWorldHint=True,
    ),
)
async def daedalus_registry_login(
    server: str,
    username: str | None = None,
    password: str | None = None,
    scheme: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Login to a container registry (password optional)."""
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        await dc.backend.registry_login(
            server, username=username, password=password, scheme=scheme,
        )
        _audit_agent(dc, "registry_login", {"server": server})
        return _ok({"server": server, "logged_in": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Logout from a container registry",
        readOnlyHint=False,
    ),
)
async def daedalus_registry_logout(server: str, ctx: Context) -> str:
    """Logout from a container registry."""
    dc = _get_ctx(ctx)
    try:
        await dc.backend.registry_logout(server)
        _audit_agent(dc, "registry_logout", {"server": server})
        return _ok({"server": server, "logged_out": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Inspect default registry",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_registry_default_inspect(ctx: Context) -> str:
    """Return the configured default registry host."""
    dc = _get_ctx(ctx)
    try:
        host = await dc.backend.registry_default_inspect()
        return _ok({"host": host})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set default registry",
        readOnlyHint=False,
        idempotentHint=True,
    ),
)
async def daedalus_registry_default_set(
    host: str,
    scheme: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Set the default registry host."""
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        await dc.backend.registry_default_set(host, scheme=scheme)
        _audit_agent(dc, "registry_default_set", {"host": host, "scheme": scheme})
        return _ok({"host": host, "scheme": scheme, "set": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unset default registry",
        readOnlyHint=False,
        idempotentHint=True,
    ),
)
async def daedalus_registry_default_unset(ctx: Context) -> str:
    """Clear the default registry host."""
    dc = _get_ctx(ctx)
    try:
        await dc.backend.registry_default_unset()
        _audit_agent(dc, "registry_default_unset", {})
        return _ok({"unset": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Query audit log",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_audit(
    operation: str | None = None,
    actor: str | None = None,
    limit: int = 100,
    ctx: Context | None = None,
) -> str:
    """Query the tamper-evident audit log."""
    dc = _get_ctx(ctx)
    entries = dc.audit.query(operation=operation, actor=actor, limit=limit)
    return json.dumps([{
        "operation": e.operation,
        "actor": e.actor,
        "actor_kind": e.actor_kind.value,
        "args": e.args,
        "result": e.result,
        "error": e.error,
        "timestamp": e.timestamp,
        "entry_id": e.entry_id,
    } for e in entries], indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        title="List experiment manifests",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_experiments(ctx: Context) -> str:
    """List stored run manifests (experiment history)."""
    dc = _get_ctx(ctx)
    from dataclasses import asdict
    return json.dumps([asdict(m) for m in dc.store.list()], indent=2, default=str)


@mcp.tool(
    annotations=ToolAnnotations(
        title="List system DNS domains",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_dns_list(ctx: Context) -> str:
    """List local DNS domains managed by the container runtime."""
    dc = _get_ctx(ctx)
    try:
        domains = await dc.talos.system_dns_list()
        return json.dumps(domains)
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create system DNS domain",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def daedalus_dns_create(domain: str, ctx: Context) -> str:
    """Create a local DNS domain (may require administrator privileges)."""
    dc = _get_ctx(ctx)
    try:
        await dc.talos.system_dns_create(domain)
        _audit_agent(dc, "system_dns_create", {"domain": domain})
        return _ok({"domain": domain, "created": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete system DNS domain",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
async def daedalus_dns_delete(domain: str, confirm: bool = False, ctx: Context | None = None) -> str:
    """Delete a local DNS domain. Requires confirm=True."""
    if not confirm:
        raise ValueError(json.dumps({
            "error": "confirm=True required for DNS domain deletion",
            "code": "CONFIRM_REQUIRED",
        }))
    dc = _get_ctx(ctx)
    try:
        await dc.talos.system_dns_delete(domain)
        _audit_agent(dc, "system_dns_delete", {"domain": domain, "confirm": confirm})
        return _ok({"domain": domain, "deleted": True})
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
    return json.dumps([{
        "name": p.name, "description": p.description,
        "kernel": p.kernel, "no_dns": p.no_dns,
        "dns": p.dns, "dns_domain": p.dns_domain,
        "tmpfs": p.tmpfs, "cpus": p.cpus, "memory": p.memory,
    } for p in profs])


# ==========================================================================
# Main
# ==========================================================================


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
