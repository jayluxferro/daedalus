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
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import (
    MachineCreateSpec,
    MachineInfo,
    MachineSetSettings,
    NetworkInfo,
    NetworkSpec,
    VolumeInfo,
    VolumeSpec,
)
from daedalus.core.capabilities import ensure_daemon, probe
from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import DaedalusError, ValidationError
from daedalus.core.forge import Forge
from daedalus.core.icarus import ExecOptions, Icarus
from daedalus.core.machina import Machina
from daedalus.core.mint import Mint
from daedalus.core.policy import PolicyEngine, PolicyResult, load_policy_config
from daedalus.core.profiles import ProfileRegistry
from daedalus.core.store import Store
from daedalus.core.talos import Talos
from daedalus.core.volume import Volumes

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
    machina: Machina
    mint: Mint
    talos: Talos
    volumes: Volumes
    profiles: ProfileRegistry
    policy: PolicyEngine
    audit: AuditLog
    store: Store


@asynccontextmanager
async def daedalus_lifespan(server: FastMCP) -> AsyncIterator[DaedalusContext]:
    """Initialise DAEDALUS subsystems on server start."""
    ensure_daemon()
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
        icarus=Icarus(backend, audit=audit, store=store, runtime_binary=caps.container_binary),
        machina=Machina(backend, caps, policy=policy, audit=audit),
        mint=Mint(backend, audit=audit),
        talos=Talos(backend, caps, policy=policy, audit=audit),
        volumes=Volumes(backend, caps, policy=policy, audit=audit),
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


def _network_dict(n: NetworkInfo) -> dict[str, Any]:
    """Serialise a ``NetworkInfo`` as a flat dict."""
    return {
        "id": n.id,
        "name": n.name,
        "plugin": n.plugin,
        "mode": n.mode,
        "subnet": n.subnet,
        "subnet_v6": n.subnet_v6,
        "gateway": n.gateway,
    }


def _machine_dict(info: MachineInfo) -> dict[str, Any]:
    """Serialise a ``MachineInfo`` as a flat dict (SPEC §2.1)."""
    return {
        "id": info.id,
        "status": info.status,
        "cpus": info.cpus,
        "memory_bytes": info.memory_bytes,
        "disk_size_bytes": info.disk_size_bytes,
        "default": info.default,
        "created_date": info.created_date,
        "home_mount": info.home_mount,
        "image_ref": info.image_ref,
        "platform_arch": info.platform_arch,
        "platform_os": info.platform_os,
        "user_username": info.user_username,
        "user_uid": info.user_uid,
        "user_gid": info.user_gid,
        "warning": info.warning,
    }


def _volume_dict(v: VolumeInfo) -> dict[str, Any]:
    """Serialise a ``VolumeInfo`` as a flat dict."""
    return v.to_dict()


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
- Detonate a host-local sample and get a behavioral report + risk score via `daedalus_detonate`
- Execute commands inside containers via `daedalus_exec`
- Pull and manage OCI container images via `daedalus_image_*`
- Sample resource usage via `daedalus_stats`
- Copy files and export filesystems via `daedalus_copy` / `daedalus_export`
- Manage container networks via `daedalus_network_*`
- Control system DNS via `daedalus_dns_*`
- Manage persistent container machines via `daedalus_machine_*`
- Manage named volumes (persistent disk images) via `daedalus_volume_*`
- Set the default container kernel via `daedalus_kernel_set`

Multi-arch guests: pass `arch="x86_64"` to `daedalus_run`/`daedalus_create`
to run amd64 guests on Apple silicon (optionally `rosetta=True` for the
Rosetta environment); expanded run options include platform, os, ssh,
init, init_image, kernel_arg, masked_path, read_only_path, shm_size,
ulimit, publish_socket, virtualization, runtime. `daedalus_stop` and
`daedalus_destroy` accept `all=True` (destroy still requires confirm).

Safety rules:
1. ALWAYS call `daedalus_health` first to check what the host supports
2. Destructive operations (`daedalus_destroy`, `daedalus_prune`,
   `daedalus_dns_delete`, `daedalus_network_delete`,
   `daedalus_machine_delete`, `daedalus_volume_delete`,
   `daedalus_volume_prune`)
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
    proxy: str | None = None,
    cert_path: str | None = None,
    no_proxy: str | None = None,
    workdir: str | None = None,
    arch: str | None = None,
    os: str | None = None,
    platform: str | None = None,
    rosetta: bool = False,
    ssh: bool = False,
    init: bool = False,
    init_image: str | None = None,
    kernel_arg: list[str] | None = None,
    masked_path: list[str] | None = None,
    read_only_path: list[str] | None = None,
    shm_size: str | None = None,
    ulimit: list[str] | None = None,
    publish_socket: list[str] | None = None,
    virtualization: bool = False,
    runtime: str | None = None,
    confirm_kernel: bool = False,
    ctx: Context | None = None,
) -> str:
    """Create and start a Labyrinth container for safe analysis.

    Defaults to the 'detonation' profile for maximum isolation.
    Use profile='bench' for benchmarking, 'isolated' for air-gapped,
    'fuzz' for kernel fuzzing, 'deception' for network labs.
    Pass --proxy host:port to route traffic through Burp/mitmproxy.
    Pass --cert /path/to/ca.pem to inject a CA certificate.

    Multi-arch guests: on Apple silicon, arch="x86_64" runs amd64
    images (verify with `uname -m` inside the container); rosetta=True
    runs them under the Rosetta environment instead of native
    emulation.  platform (os/arch/variant) takes precedence over
    os/arch when both are given.  Other expanded run options: ssh,
    init, init_image, kernel_arg (repeatable), masked_path (repeatable),
    read_only_path (repeatable), shm_size, ulimit (repeatable),
    publish_socket (repeatable), virtualization, runtime.

    Example:
        daedalus_run(image="alpine:latest")
        daedalus_run(image="ubuntu:24.04", profile="bench", name="test")
        daedalus_run(image="debian:latest", proxy="192.168.64.1:8083", cert_path="/tmp/burp-ca.pem")
        daedalus_run(image="alpine:latest", arch="x86_64", rosetta=True)
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
        if proxy is not None:
            overrides["proxy"] = proxy
        if cert_path is not None:
            overrides["cert_path"] = cert_path
        if no_proxy is not None:
            overrides["no_proxy"] = no_proxy
        if arch is not None:
            overrides["arch"] = arch
        if os is not None:
            overrides["os"] = os
        if platform is not None:
            overrides["platform"] = platform
        if rosetta:
            overrides["rosetta"] = True
        if ssh:
            overrides["ssh"] = True
        if init:
            overrides["init"] = True
        if init_image is not None:
            overrides["init_image"] = init_image
        if kernel_arg is not None:
            overrides["kernel_arg"] = kernel_arg
        if masked_path is not None:
            overrides["masked_path"] = masked_path
        if read_only_path is not None:
            overrides["read_only_path"] = read_only_path
        if shm_size is not None:
            overrides["shm_size"] = shm_size
        if ulimit is not None:
            overrides["ulimit"] = ulimit
        if publish_socket is not None:
            overrides["publish_socket"] = publish_socket
        if virtualization:
            overrides["virtualization"] = True
        if runtime is not None:
            overrides["runtime"] = runtime
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
                    "ip": "",
                    "networks": []})
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
    proxy: str | None = None,
    cert_path: str | None = None,
    no_proxy: str | None = None,
    workdir: str | None = None,
    arch: str | None = None,
    os: str | None = None,
    platform: str | None = None,
    rosetta: bool = False,
    ssh: bool = False,
    init: bool = False,
    init_image: str | None = None,
    kernel_arg: list[str] | None = None,
    masked_path: list[str] | None = None,
    read_only_path: list[str] | None = None,
    shm_size: str | None = None,
    ulimit: list[str] | None = None,
    publish_socket: list[str] | None = None,
    virtualization: bool = False,
    runtime: str | None = None,
    confirm_kernel: bool = False,
    ctx: Context | None = None,
) -> str:
    """Create a stopped container. Use daedalus_start to run it later.

    Accepts the same expanded run options as daedalus_run (arch, os,
    platform, rosetta, ssh, init, init_image, kernel_arg, masked_path,
    read_only_path, shm_size, ulimit, publish_socket, virtualization,
    runtime).  On Apple silicon, arch="x86_64" prepares an amd64 guest;
    rosetta=True runs it under the Rosetta environment.
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
        if proxy is not None:
            overrides["proxy"] = proxy
        if cert_path is not None:
            overrides["cert_path"] = cert_path
        if no_proxy is not None:
            overrides["no_proxy"] = no_proxy
        if arch is not None:
            overrides["arch"] = arch
        if os is not None:
            overrides["os"] = os
        if platform is not None:
            overrides["platform"] = platform
        if rosetta:
            overrides["rosetta"] = True
        if ssh:
            overrides["ssh"] = True
        if init:
            overrides["init"] = True
        if init_image is not None:
            overrides["init_image"] = init_image
        if kernel_arg is not None:
            overrides["kernel_arg"] = kernel_arg
        if masked_path is not None:
            overrides["masked_path"] = masked_path
        if read_only_path is not None:
            overrides["read_only_path"] = read_only_path
        if shm_size is not None:
            overrides["shm_size"] = shm_size
        if ulimit is not None:
            overrides["ulimit"] = ulimit
        if publish_socket is not None:
            overrides["publish_socket"] = publish_socket
        if virtualization:
            overrides["virtualization"] = True
        if runtime is not None:
            overrides["runtime"] = runtime
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
            "ip": "",
            "networks": [],
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
    all: bool = False,
    ctx: Context | None = None,
) -> str:
    """Stop a running container gracefully (SIGTERM + timeout).

    all=True stops every running container (stop -a; container_id is
    ignored in this mode).
    """
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        if all:
            # Forge.stop is single-container only (SPEC phase 7 §4) —
            # call the backend accessor directly for `stop -a`.
            await dc.backend.stop(container_id, timeout=timeout,
                                  signal=signal, all=True)
            _audit_agent(dc, "stop", {"container_id": container_id, "all": True})
            return _ok({"all": True, "state": "stopped"})
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
async def daedalus_destroy(
    container_id: str,
    confirm: bool = False,
    all: bool = False,
    ctx: Context | None = None,
) -> str:
    """Destroy a container (stop then delete permanently).

    **WARNING**: Destructive operation. Must pass confirm=True.
    Container data is unrecoverable after destruction.
    all=True destroys every container (delete -a; container_id is
    ignored) and stays confirm-gated: confirm=True is required.
    """
    if all and not confirm:
        raise ValueError(json.dumps({
            "error": "confirm=True required to destroy all containers",
            "code": "CONFIRM_REQUIRED",
        }))
    dc = _get_ctx(ctx)
    try:
        if all:
            # Forge.destroy is single-container only (SPEC phase 7 §4) —
            # call the backend accessor directly for `delete -a`, still
            # behind the confirm gate above.
            await dc.backend.delete(container_id, all=True)
            _audit_agent(dc, "destroy", {"container_id": container_id,
                                         "confirm": confirm, "all": True})
            return _ok({"all": True, "destroyed": True})
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
    detached: bool = False,
    ulimit: list[str] | None = None,
    ctx: Context | None = None,
) -> str:
    """Execute a command inside a running container.

    detached=True runs the exec in the background (exec -d).
    ulimit accepts repeatable resource specs (e.g. "nofile=1024").
    Returns {exit_code, stdout, stderr} as JSON.
    """
    dc = _get_ctx(ctx)
    try:
        opts = ExecOptions(
            user=user, uid=uid, gid=gid, workdir=workdir, env=env,
            env_file=env_file, tty=tty, interactive=interactive,
            detached=detached, ulimit=ulimit,
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
    follow: bool = False,
    follow_seconds: float = 15.0,
    ctx: Context | None = None,
) -> str:
    """Retrieve container stdout or boot logs.

    Set boot=True for kernel/boot messages instead of process output.
    Omit tail to return the full log buffer.  When follow=True, stream for
    up to follow_seconds (default 15) then return captured output.
    """
    dc = _get_ctx(ctx)
    try:
        logs = await dc.icarus.logs(
            container_id,
            boot=boot,
            tail=tail,
            follow=follow,
            follow_seconds=follow_seconds if follow else None,
        )
        return logs
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Read all container and system logs",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_logs_all(
    all_containers: bool = True,
    boot: bool = False,
    tail: int | None = None,
    include_system: bool = True,
    system_last: str = "5m",
    follow: bool = False,
    follow_seconds: float = 15.0,
    ctx: Context | None = None,
) -> str:
    """Fetch logs for every container plus container daemon system logs.

    Returns JSON with ``containers`` (list of {id, name, image, state, logs}),
    ``system`` (daemon logs when include_system=True), and ``count``.
    Omit tail to include full per-container log buffers.
    """
    dc = _get_ctx(ctx)
    try:
        payload = await dc.icarus.logs_all(
            all_containers=all_containers,
            boot=boot,
            tail=tail,
            include_system=include_system,
            system_last=system_last,
            follow=follow,
            follow_seconds=follow_seconds,
        )
        return _ok(payload)
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
    follow: bool = False,
    follow_seconds: float = 15.0,
    ctx: Context | None = None,
) -> str:
    """Fetch container system logs.

    Use last to widen the time window (e.g. "1h", "24h", "7d").
    When follow=True, stream for up to follow_seconds then return output.
    """
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        logs = await dc.backend.system_logs(
            last=last,
            follow=follow,
            follow_seconds=follow_seconds if follow else None,
        )
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
async def daedalus_dns_delete(
    domain: str,
    confirm: bool = False,
    ctx: Context | None = None,
) -> str:
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
# Stats / Copy / Export / Prune / Networks (container 1.2.2)
# ==========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="Container resource statistics",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_stats(
    container_id: str | None = None,
    ctx: Context | None = None,
) -> str:
    """One-shot resource statistics for a container (or all running).

    Omit container_id to sample every running container.  Returns a JSON
    array of samples with cumulative values: cpu_usage_usec,
    memory_usage_bytes, memory_limit_bytes, network_rx_bytes,
    network_tx_bytes, block_read_bytes, block_write_bytes,
    num_processes, sampled_at.
    """
    dc = _get_ctx(ctx)
    try:
        samples = await dc.forge.stats(container_id)
        return json.dumps([s.to_dict() for s in samples])
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Copy files between host and container",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    ),
)
async def daedalus_copy(
    container_id: str,
    source: str,
    destination: str,
    direction: str = "from",
    ctx: Context | None = None,
) -> str:
    """Copy a file or directory across the host—container boundary.

    direction="from" copies FROM the container (source = container path,
    destination = host path); direction="to" copies INTO the container
    (source = host path, destination = container path).  The container id
    is always taken from container_id — never from a user-supplied
    prefix.
    """
    dc = _get_ctx(ctx)
    if direction not in ("from", "to"):
        return _err(ValidationError(
            f"direction must be 'from' or 'to', got {direction!r}",
            direction=direction,
        ))
    try:
        if direction == "from":
            output = await dc.icarus.copy_from(container_id, source, destination)
        else:
            output = await dc.icarus.copy_to(container_id, source, destination)
        _audit_agent(dc, f"copy_{direction}", {
            "container_id": container_id,
            "source": source,
            "destination": destination,
        }, {"output": output})
        return _ok({
            "container_id": container_id,
            "direction": direction,
            "source": source,
            "destination": destination,
            "output": output,
        })
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Export container filesystem to tar archive",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def daedalus_export(
    container_id: str,
    output_path: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Export a container's full filesystem to a tar archive.

    Defaults to <store root>/<container_id>.fs.tar and indexes the
    archive as a store artifact (kind="fs").  Returns {path,
    container_id, artifact}.
    """
    dc = _get_ctx(ctx)
    try:
        result = await dc.icarus.export_fs(container_id, output_path)
        _audit_agent(dc, "export", {
            "container_id": container_id,
            "output_path": output_path,
        }, result)
        return _ok(result)
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Prune stopped containers",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
async def daedalus_prune(confirm: bool = False, ctx: Context | None = None) -> str:
    """Remove stopped containers and reclaim disk space.

    **WARNING**: Destructive operation. Must pass confirm=True.
    Returns {ids, message, count}.
    """
    if not confirm:
        raise ValueError(json.dumps({
            "error": "confirm=True required for container prune",
            "code": "CONFIRM_REQUIRED",
        }))
    dc = _get_ctx(ctx)
    try:
        result = await dc.backend.prune()
        payload = {
            "ids": result.ids,
            "message": result.message,
            "count": len(result.ids),
        }
        _audit_agent(dc, "prune", {"confirm": confirm}, payload)
        return _ok(payload)
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="List container networks",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_network_list(ctx: Context | None = None) -> str:
    """List container networks.

    Returns a JSON array of {id, name, plugin, mode, subnet, subnet_v6,
    gateway} objects.
    """
    dc = _get_ctx(ctx)
    try:
        networks = await dc.talos.network_list()
        return json.dumps([_network_dict(n) for n in networks])
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create a container network",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def daedalus_network_create(
    name: str,
    subnet: str | None = None,
    subnet_v6: str | None = None,
    internal: bool = False,
    plugin: str = "container-network-vmnet",
    ctx: Context | None = None,
) -> str:
    """Create a container network.

    **NOTE**: internal=False creates a host-routable network, which is
    policy-gated — it is rejected with an error unless the policy allows
    exposure.  Use internal=True for an air-gapped internal network.
    Returns {id, name, plugin, mode, subnet, subnet_v6, gateway}.
    """
    dc = _get_ctx(ctx)
    try:
        spec = NetworkSpec(
            name=name, subnet=subnet, subnet_v6=subnet_v6,
            internal=internal, plugin=plugin,
        )
        info = await dc.talos.network_create(spec)
        _audit_agent(dc, "network_create", {
            "name": name, "subnet": subnet, "subnet_v6": subnet_v6,
            "internal": internal, "plugin": plugin,
        }, _network_dict(info))
        return _ok(_network_dict(info))
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Inspect a container network",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_network_inspect(name: str, ctx: Context | None = None) -> str:
    """Inspect a single container network.

    Returns {id, name, plugin, mode, subnet, subnet_v6, gateway}.
    """
    dc = _get_ctx(ctx)
    try:
        info = await dc.talos.network_inspect(name)
        return _ok(_network_dict(info))
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete a container network",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
async def daedalus_network_delete(
    name: str,
    confirm: bool = False,
    ctx: Context | None = None,
) -> str:
    """Delete a container network. Requires confirm=True.

    **WARNING**: Destructive operation. Must pass confirm=True.
    """
    if not confirm:
        raise ValueError(json.dumps({
            "error": "confirm=True required for network deletion",
            "code": "CONFIRM_REQUIRED",
        }))
    dc = _get_ctx(ctx)
    try:
        await dc.talos.network_delete(name, confirm=True)
        _audit_agent(dc, "network_delete", {"name": name, "confirm": confirm})
        return _ok({"name": name, "deleted": True})
    except DaedalusError as e:
        return _err(e)


# ==========================================================================
# Machines (container 1.2.2 — persistent stateful VMs)
# ==========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="List container machines",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_machine_list(ctx: Context | None = None) -> str:
    """List persistent container machines.

    Returns a JSON array of {id, status, cpus, memory_bytes,
    disk_size_bytes, default, created_date, home_mount, image_ref,
    platform_arch, platform_os, user_username, user_uid, user_gid,
    warning} objects.
    """
    dc = _get_ctx(ctx)
    try:
        machines = await dc.machina.list()
        return json.dumps([_machine_dict(m) for m in machines])
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create a persistent container machine",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def daedalus_machine_create(
    image: str,
    name: str | None = None,
    cpus: int | None = None,
    memory: str | None = None,
    home_mount: str | None = None,
    virtualization: bool = False,
    kernel: str | None = None,
    set_default: bool = False,
    no_boot: bool = False,
    ctx: Context | None = None,
) -> str:
    """Create a persistent container machine (without booting it).

    **Init requirement**: machines boot the image's own /sbin/init as
    PID 1, so images without an init system (alpine, ubuntu, debian,
    node, ...) cannot boot.  A non-empty ``warning`` field in the result
    flags likely-initless images — prefer an init-capable image (or pass
    no_boot=True and prepare the image before booting).

    Example:
        daedalus_machine_create(image="my-systemd-image:latest", no_boot=True)
    """
    dc = _get_ctx(ctx)
    try:
        spec = MachineCreateSpec(
            image=image, name=name, cpus=cpus, memory=memory,
            home_mount=home_mount, virtualization=virtualization,
            kernel=kernel, set_default=set_default, no_boot=no_boot,
        )
        info = await dc.machina.create(spec)
        _audit_agent(dc, "machine_create", {
            "image": image, "name": name, "cpus": cpus, "memory": memory,
            "home_mount": home_mount, "virtualization": virtualization,
            "kernel": kernel, "set_default": set_default, "no_boot": no_boot,
        }, _machine_dict(info))
        return _ok(_machine_dict(info))
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Inspect a container machine",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_machine_inspect(
    machine_id: str, ctx: Context | None = None,
) -> str:
    """Inspect a single container machine.

    Returns the same flat fields as daedalus_machine_list plus
    inspect-only fields (home_mount, image_ref, platform, user mapping).
    """
    dc = _get_ctx(ctx)
    try:
        info = await dc.machina.inspect(machine_id)
        return _ok(_machine_dict(info))
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Update a container machine's settings",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    ),
)
async def daedalus_machine_set(
    machine_id: str,
    cpus: int | None = None,
    memory: str | None = None,
    home_mount: str | None = None,
    virtualization: bool | None = None,
    kernel: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Update a container machine's settings.

    Only the provided settings are changed (cpus, memory, home_mount,
    virtualization, kernel) and they apply after the machine restarts.
    Pass kernel="" to reset the kernel to the runtime default.  Returns
    {updated: [...]} listing the applied setting keys.
    """
    dc = _get_ctx(ctx)
    try:
        settings = MachineSetSettings(
            cpus=cpus, memory=memory, home_mount=home_mount,
            virtualization=virtualization, kernel=kernel,
        )
        applied = {k: v for k, v in asdict(settings).items() if v is not None}
        await dc.machina.set(machine_id, settings)
        _audit_agent(dc, "machine_set", {"machine_id": machine_id, **applied})
        return _ok({"updated": list(applied.keys())})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set the default container machine",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    ),
)
async def daedalus_machine_set_default(
    machine_id: str, ctx: Context | None = None,
) -> str:
    """Make a container machine the default (used when no id is given)."""
    dc = _get_ctx(ctx)
    try:
        await dc.machina.set_default(machine_id)
        _audit_agent(dc, "machine_set_default", {"machine_id": machine_id})
        return _ok({"machine_id": machine_id, "default": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Stop a container machine",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    ),
)
async def daedalus_machine_stop(
    machine_id: str, ctx: Context | None = None,
) -> str:
    """Stop a container machine."""
    dc = _get_ctx(ctx)
    try:
        await dc.machina.stop(machine_id)
        _audit_agent(dc, "machine_stop", {"machine_id": machine_id})
        return _ok({"machine_id": machine_id, "stopped": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete a container machine permanently",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
async def daedalus_machine_delete(
    machine_id: str,
    confirm: bool = False,
    ctx: Context | None = None,
) -> str:
    """Delete a container machine. Requires confirm=True.

    **WARNING**: Destructive operation. Must pass confirm=True.
    The machine's disk is unrecoverable after deletion.
    """
    if not confirm:
        raise ValueError(json.dumps({
            "error": "confirm=True required for machine deletion",
            "code": "CONFIRM_REQUIRED",
        }))
    dc = _get_ctx(ctx)
    try:
        await dc.machina.delete(machine_id, confirm=True)
        _audit_agent(dc, "machine_delete", {
            "machine_id": machine_id, "confirm": confirm,
        })
        return _ok({"machine_id": machine_id, "deleted": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Run a command inside a container machine",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def daedalus_machine_run(
    machine_id: str,
    command: str,
    root: bool = False,
    ulimit: list[str] | None = None,
    detached: bool = False,
    tty: bool = False,
    user: str | None = None,
    workdir: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Run a command inside a container machine (boots it if necessary).

    The command string is split into argv with shlex.  Returns
    {exit_code, stdout, stderr} as JSON.  When the machine fails to boot
    (non-zero exit code), an ``explanation`` field describes the likely
    cause (e.g. the image has no init system).

    Options: root=True runs as root, detached=True runs in the
    background, tty=True allocates a pseudo-TTY, user overrides the
    user, workdir sets the working directory, ulimit accepts repeatable
    resource specs (e.g. "nofile=1024").
    """
    dc = _get_ctx(ctx)
    try:
        argv = shlex.split(command)
        if not argv:
            return _err(ValidationError("command required"))
        # Machina.run does not thread root/ulimit/detached/tty/user/workdir
        # (SPEC phase 7 §4) — call the backend accessor directly; the MCP
        # layer audits below, so machina's own audit record is not lost.
        result = await dc.backend.machine_run(
            machine_id, argv,
            root=root, ulimit=ulimit, detached=detached,
            tty=tty, user=user, workdir=workdir,
        )
        _audit_agent(dc, "machine_run", {
            "machine_id": machine_id, "argv": argv,
        })
        payload: dict[str, object] = {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if result.exit_code != 0:
            try:
                explanation = await dc.machina.explain_failure(machine_id)
            except DaedalusError:
                explanation = None
            if explanation is not None:
                payload["explanation"] = explanation
        return _ok(payload)
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Read container machine logs",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_machine_logs(
    machine_id: str,
    boot: bool = False,
    follow: bool = False,
    tail: int | None = None,
    ctx: Context | None = None,
) -> str:
    """Retrieve a container machine's boot/stdout logs as raw text.

    boot=True returns kernel/boot messages; tail limits to the last N
    lines.  When follow=True, output is bounded to a short window
    (~15 s) like the container logs tool, then returned as a snapshot.
    """
    dc = _get_ctx(ctx)
    try:
        # Machina.logs does not thread boot/follow/tail (SPEC phase 7 §4)
        # — call the backend accessor directly (read-only, no audit).
        return await dc.backend.machine_logs(
            machine_id, boot=boot, follow=follow, tail=tail,
        )
    except DaedalusError as e:
        return _err(e)


# ==========================================================================
# Volumes (container 1.2.2 — persistent disk images)
# ==========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="List named volumes",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_volume_list(ctx: Context | None = None) -> str:
    """List named volumes (persistent disk images).

    Returns a JSON array of {id, name, driver, format, size_bytes,
    size_spec, source, created_date, labels, options} objects.
    """
    dc = _get_ctx(ctx)
    try:
        volumes = await dc.volumes.list()
        return json.dumps([_volume_dict(v) for v in volumes])
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create a named volume",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def daedalus_volume_create(
    name: str,
    size: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Create a named volume for persistent storage.

    Volumes are ext4 disk images that survive container deletion — use
    them for forensic evidence and lab data.  ``size`` is a human size
    spec (e.g. "100M", "1G"); omit for the runtime default.
    """
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        spec = VolumeSpec(name=name, size=size)
        info = await dc.volumes.create(spec)
        _audit_agent(dc, "volume_create", {"name": name, "size": size},
                     _volume_dict(info))
        return _ok(_volume_dict(info))
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Inspect a named volume",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
async def daedalus_volume_inspect(
    name: str, ctx: Context | None = None,
) -> str:
    """Inspect a single named volume.

    Returns the same flat fields as daedalus_volume_list plus
    created_date, labels and options.
    """
    dc = _get_ctx(ctx)
    try:
        info = await dc.volumes.inspect(name)
        return _ok(_volume_dict(info))
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete a named volume permanently",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
async def daedalus_volume_delete(
    name: str,
    confirm: bool = False,
    ctx: Context | None = None,
) -> str:
    """Delete a named volume. Requires confirm=True.

    **WARNING**: Destructive operation.  Volumes hold forensic artifacts;
    the backing disk image is unrecoverable after deletion.
    """
    if not confirm:
        raise ValueError(json.dumps({
            "error": "confirm=True required for volume deletion",
            "code": "CONFIRM_REQUIRED",
        }))
    dc = _get_ctx(ctx)
    try:
        await dc.volumes.delete(name, confirm=True)
        _audit_agent(dc, "volume_delete", {"name": name, "confirm": confirm})
        return _ok({"name": name, "deleted": True})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Prune unused volumes",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
async def daedalus_volume_prune(
    confirm: bool = False,
    ctx: Context | None = None,
) -> str:
    """Reclaim unused volumes and free disk space. Requires confirm=True.

    **WARNING**: Destructive operation. Must pass confirm=True.
    Returns {removed, count}.
    """
    if not confirm:
        raise ValueError(json.dumps({
            "error": "confirm=True required for volume prune",
            "code": "CONFIRM_REQUIRED",
        }))
    dc = _get_ctx(ctx)
    try:
        removed = await dc.volumes.prune(confirm=True)
        _audit_agent(dc, "volume_prune", {"confirm": confirm},
                     {"removed": removed})
        return _ok({"removed": removed, "count": len(removed)})
    except DaedalusError as e:
        return _err(e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set the default container kernel",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def daedalus_kernel_set(
    recommended: bool = False,
    binary: str | None = None,
    tar: str | None = None,
    arch: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Set the default container kernel.

    Pass recommended=True for the runtime's recommended kernel, or give
    either a ``binary`` path or a ``tar`` archive (with optional
    ``arch``, defaulting to arm64).  Returns {set, arch}.
    """
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        resolved_arch = arch or "arm64"
        await dc.backend.system_kernel_set(
            binary=binary, tar=tar, arch=resolved_arch,
            recommended=recommended,
        )
        _audit_agent(dc, "kernel_set", {
            "binary": binary, "tar": tar, "arch": arch,
            "recommended": recommended,
        })
        return _ok({"set": True, "arch": resolved_arch})
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
# Detonation
# ==========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="Detonate a sample in an isolated VM and get a behavioral report",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def daedalus_detonate(
    sample: str,
    image: str = "alpine:latest",
    profile: str = "detonation",
    name: str | None = None,
    command: str | None = None,
    observe_seconds: float = 30.0,
    stats_interval: float = 2.0,
    ctx: Context | None = None,
) -> str:
    """Detonate a host-local sample and get a behavioral report + risk score.

    This is the safe-detention verb: DAEDALUS takes a sample path from
    your host, delivers it into a disposable Labyrinth VM (policy-gated,
    hardware-isolated), executes it while capturing filesystem and
    resource-usage evidence, produces a behavioral report with a risk
    score, then destroys the VM.  Nothing survives on the host except the
    evidence (pre/post filesystem tars and the report in the store).

    **WARNING**: Destructive and non-idempotent — a VM is created and
    destroyed.  The call blocks until the observation window
    (``observe_seconds``, default 30 s) elapses; an exec that runs past
    the boundary reports exit code 124 (timeout), which is normal.
    ``profile`` resolves via the built-in registry (detonation, bench,
    isolated, fuzz, deception).  Pass ``command`` (space-separated argv,
    e.g. "sh /tmp/sample.sh --flag") to override how the sample is
    executed; by default it runs via ``sh``.

    Returns {run_id, report, report_path, risk_score, exit_code} as JSON.
    """
    if not sample.strip():
        return _err(ValidationError(
            "sample path must be non-empty",
            sample=sample,
        ))
    assert ctx is not None
    dc = _get_ctx(ctx)
    try:
        p = dc.profiles.get(profile)
        kwargs = p.apply()
        argv = shlex.split(command) if command is not None else None
        result = await dc.forge.detonate(
            sample,
            image=image,
            profile=profile,
            name=name,
            command=argv,
            observe_seconds=observe_seconds,
            stats_interval=stats_interval,
            profile_kwargs=kwargs,
        )
        payload = {
            "run_id": result["run_id"],
            "report": result["report"],
            "report_path": result["report_path"],
            "risk_score": result["risk_score"],
            "exit_code": result["exit_code"],
        }
        _audit_agent(dc, "detonate", {
            "sample": sample,
            "image": image,
            "profile": profile,
            "name": name,
            "command": argv,
            "observe_seconds": observe_seconds,
            "stats_interval": stats_interval,
        }, payload)
        return _ok(payload)
    except DaedalusError as e:
        _audit_agent(
            dc, "detonate",
            {"sample": sample, "image": image, "profile": profile},
            error=e.message,
        )
        return _err(e)


# ==========================================================================
# Main
# ==========================================================================


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
