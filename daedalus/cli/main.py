"""M8a — CLI: human-facing verb-first interface.

Every command maps to the Core Engine.  No business logic lives here.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import (
    MachineCreateSpec,
    MachineSetSettings,
    NetworkInfo,
    NetworkSpec,
    VolumeSpec,
)
from daedalus.core.capabilities import CapabilityManifest, ensure_daemon, probe
from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import DetonationError, ValidationError
from daedalus.core.forge import Forge
from daedalus.core.icarus import ExecOptions, Icarus
from daedalus.core.machina import Machina
from daedalus.core.mint import Mint
from daedalus.core.policy import PolicyEngine, PolicyResult, load_policy_config
from daedalus.core.profiles import ProfileRegistry
from daedalus.core.store import Store
from daedalus.core.talos import Talos
from daedalus.core.volume import Volumes

app = typer.Typer(
    name="daedalus",
    help="DAEDALUS — Detonation, Analysis & Experimentation control plane.",
    no_args_is_help=True,
)

console = Console()

# Lazy-init globals — typed loosely to avoid import-time I/O
_caps: CapabilityManifest | None = None
_backend: CliBackend | None = None
_forge: Forge | None = None
_icarus: Icarus | None = None
_machina: Machina | None = None
_mint: Mint | None = None
_profiles: ProfileRegistry | None = None
_store: Store | None = None
_talos: Talos | None = None
_volumes: Volumes | None = None


def _bootstrap() -> None:
    global _caps, _backend, _forge, _icarus, _machina, _mint
    global _profiles, _store, _talos, _volumes
    if _caps is not None:
        return
    ensure_daemon()
    _caps = probe()
    backend = CliBackend(_caps)
    audit = AuditLog()
    store = Store()
    policy = PolicyEngine(load_policy_config())

    def _policy_audit(operation: str, actor: str, result: PolicyResult) -> None:
        audit.record(
            "policy", actor=actor, actor_kind=ActorKind.HUMAN,
            args={
                "operation": operation,
                "decision": result.decision.value,
                "reason": result.reason,
            },
        )

    policy.config.on_decision = _policy_audit

    _backend = backend
    _store = store
    _forge = Forge(backend, _caps, policy=policy, audit=audit, store=store)
    _icarus = Icarus(
        backend, audit=audit, runtime_binary=_caps.container_binary, store=store,
    )
    _talos = Talos(backend, _caps, policy=policy, audit=audit)
    _mint = Mint(backend, audit=audit)
    _machina = Machina(backend, _caps, policy=policy, audit=audit)
    _profiles = ProfileRegistry()
    _volumes = Volumes(backend, _caps, policy=policy, audit=audit)


def _b() -> CliBackend:
    assert _backend is not None
    return _backend


def _c() -> CapabilityManifest:
    assert _caps is not None
    return _caps


def _f() -> Forge:
    assert _forge is not None
    return _forge


def _i() -> Icarus:
    assert _icarus is not None
    return _icarus


def _m() -> Mint:
    assert _mint is not None
    return _mint


def _mc() -> Machina:
    assert _machina is not None
    return _machina


def _p() -> ProfileRegistry:
    assert _profiles is not None
    return _profiles


def _st() -> Store:
    assert _store is not None
    return _store


def _t() -> Talos:
    assert _talos is not None
    return _talos


def _v() -> Volumes:
    assert _volumes is not None
    return _volumes


# ==================================================================
# M0 — Probe
# ==================================================================


@app.command("probe")
def probe_cmd() -> None:
    """Probe the host and print the capability manifest."""
    m = probe()
    console.print(m.summary())


# ==================================================================
# Lifecycle
# ==================================================================


@app.command()
def create(
    image: str,
    name: Annotated[str | None, typer.Option("--name")] = None,
    profile: Annotated[str, typer.Option("--profile", "-p")] = "detonation",
    kernel: Annotated[str | None, typer.Option("--kernel", "-k")] = None,
    confirm_kernel: Annotated[bool, typer.Option("--confirm-kernel")] = False,
    command: Annotated[str | None, typer.Option("--command", "-c")] = None,
    volume: Annotated[
        list[str] | None,
        typer.Option("--volume", "-v", help="Bind mount host:container"),
    ] = None,
    mount: Annotated[
        list[str] | None,
        typer.Option("--mount", help="Mount spec type=,source=,target="),
    ] = None,
    env: Annotated[list[str] | None, typer.Option("--env", "-e", help="KEY=VALUE")] = None,
    workdir: Annotated[str | None, typer.Option("--workdir", "-w")] = None,
    network: Annotated[str | None, typer.Option("--network", help="Attach to a network")] = None,
    publish: Annotated[
        list[str] | None,
        typer.Option("--publish", help="Publish port spec (repeatable)"),
    ] = None,
    cap_add: Annotated[
        list[str] | None,
        typer.Option("--cap-add", help="Add capability (repeatable)"),
    ] = None,
    cap_drop: Annotated[
        list[str] | None,
        typer.Option("--cap-drop", help="Drop capability (repeatable)"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a container without starting it."""
    _bootstrap()
    p = _p().get(profile)
    kwargs = p.apply()
    if kernel:
        kwargs["kernel"] = kernel
    if volume:
        kwargs["volumes"] = volume
    if mount:
        kwargs["mounts"] = mount
    if env:
        kwargs["env"] = dict(e.split("=", 1) for e in env if "=" in e)
    if workdir:
        kwargs["workdir"] = workdir
    if network:
        kwargs["network"] = network
    if publish:
        kwargs["publish"] = publish
    if cap_add:
        kwargs["cap_add"] = cap_add
    if cap_drop:
        kwargs["cap_drop"] = cap_drop
    cmd_list = command.split() if command else None

    async def _go() -> None:
        lab = await _f().create(
            image, name=name, profile=profile,
            command=cmd_list, confirm_kernel=confirm_kernel, **kwargs,
        )
        if json_output:
            console.print(json.dumps(lab.info.raw, indent=2))
        else:
            console.print(f"[green]✓[/] Created [bold]{lab.id[:12]}[/] (stopped)")
            console.print(f"  Start with: daedalus start {lab.id[:12]}")
    asyncio.run(_go())


@app.command()
def run(
    image: str,
    name: Annotated[str | None, typer.Option("--name")] = None,
    detach: Annotated[bool, typer.Option("--detach", "-d")] = False,
    profile: Annotated[str, typer.Option("--profile", "-p")] = "detonation",
    kernel: Annotated[str | None, typer.Option("--kernel", "-k")] = None,
    confirm_kernel: Annotated[bool, typer.Option("--confirm-kernel")] = False,
    command: Annotated[str | None, typer.Option("--command", "-c")] = None,
    volume: Annotated[
        list[str] | None,
        typer.Option("--volume", "-v", help="Bind mount host:container"),
    ] = None,
    mount: Annotated[
        list[str] | None,
        typer.Option("--mount", help="Mount spec type=,source=,target="),
    ] = None,
    env: Annotated[list[str] | None, typer.Option("--env", "-e", help="KEY=VALUE")] = None,
    workdir: Annotated[str | None, typer.Option("--workdir", "-w")] = None,
    network: Annotated[str | None, typer.Option("--network", help="Attach to a network")] = None,
    publish: Annotated[
        list[str] | None,
        typer.Option("--publish", help="Publish port spec (repeatable)"),
    ] = None,
    cap_add: Annotated[
        list[str] | None,
        typer.Option("--cap-add", help="Add capability (repeatable)"),
    ] = None,
    cap_drop: Annotated[
        list[str] | None,
        typer.Option("--cap-drop", help="Drop capability (repeatable)"),
    ] = None,
    proxy: Annotated[
        str | None,
        typer.Option("--proxy", help="Proxy host:port for Burp/mitmproxy"),
    ] = None,
    cert: Annotated[
        str | None,
        typer.Option("--cert", help="Path to PEM CA cert to inject"),
    ] = None,
    no_proxy: Annotated[
        str | None,
        typer.Option("--no-proxy", help="NO_PROXY exclusion list"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create and start a container."""
    _bootstrap()
    p = _p().get(profile)
    kwargs = p.apply()
    if kernel:
        kwargs["kernel"] = kernel
    if proxy:
        kwargs["proxy"] = proxy
    if cert:
        kwargs["cert_path"] = cert
    if no_proxy:
        kwargs["no_proxy"] = no_proxy
    if volume:
        kwargs["volumes"] = volume
    if mount:
        kwargs["mounts"] = mount
    if env:
        kwargs["env"] = dict(e.split("=", 1) for e in env if "=" in e)
    if workdir:
        kwargs["workdir"] = workdir
    if network:
        kwargs["network"] = network
    if publish:
        kwargs["publish"] = publish
    if cap_add:
        kwargs["cap_add"] = cap_add
    if cap_drop:
        kwargs["cap_drop"] = cap_drop
    cmd_list = command.split() if command else None

    async def _go() -> None:
        lab = await _f().run(
            image, name=name, detach=detach, profile=profile,
            command=cmd_list, confirm_kernel=confirm_kernel, **kwargs,
        )
        if json_output:
            console.print(json.dumps(lab.info.raw, indent=2))
        else:
            console.print(f"[green]✓[/] Created [bold]{lab.id[:12]}[/] from [cyan]{image}[/]")
            if lab.name:
                console.print(f"  Name: {lab.name}")
            console.print(f"  State: {lab.state}")
            ip = ""
            if ip:
                console.print(f"  IP:   {ip}  (host-reachable via vmnet)")
    asyncio.run(_go())


@app.command()
def detonate(
    sample: str,
    image: Annotated[str, typer.Option("--image")] = "alpine:latest",
    profile: Annotated[str, typer.Option("--profile", "-p")] = "detonation",
    name: Annotated[str | None, typer.Option("--name")] = None,
    observe: Annotated[
        float,
        typer.Option("--observe", help="Observation window in seconds"),
    ] = 30.0,
    stats_interval: Annotated[
        float,
        typer.Option("--stats-interval", help="Stats sampling interval in seconds"),
    ] = 2.0,
    command: Annotated[
        list[str] | None,
        typer.Option("--command", help="Override the execution command (repeatable)"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Detonate a sample in a disposable, isolated container.

    The engine runs the sample in a freshly created container on a
    disposable internal network, snapshots the filesystem before/after,
    samples stats while it runs, judges the evidence (Minos), persists a
    report to the store, and destroys the container.
    """
    if not os.path.isfile(sample):
        raise typer.BadParameter(f"sample not found: {sample}")
    _bootstrap()
    p = _p().get(profile)
    kwargs = p.apply()

    async def _go() -> None:
        try:
            result = await _f().detonate(
                sample,
                image=image,
                profile=profile,
                name=name,
                command=command,
                observe_seconds=observe,
                stats_interval=stats_interval,
                profile_kwargs=kwargs,
            )
        except DetonationError as exc:
            console.print(f"[red]✗[/] {exc.message}")
            if exc.partial_evidence:
                console.print("  Partial evidence:")
                for role, path in exc.partial_evidence.items():
                    console.print(f"    {role}: {path}")
            raise typer.Exit(code=1) from None
        if json_output:
            console.print(json.dumps(result, indent=2))
            return
        _print_detonation_report(result)
    asyncio.run(_go())


@app.command()
def ls(all: Annotated[bool, typer.Option("--all", "-a")] = False) -> None:
    """List containers."""
    _bootstrap()
    async def _go() -> None:
        labs = await _f().list(all=all)
        if not labs:
            console.print("[dim]No containers.[/]")
            return
        table = Table(title="Containers")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Image")
        table.add_column("State")
        table.add_column("IP")
        table.add_column("Profile")
        for lab in labs:
            state_style = "green" if lab.is_running else "dim"
            ip = "-"
            table.add_row(
                lab.id[:12], lab.name or "-", lab.image,
                f"[{state_style}]{lab.state}[/]", ip, lab.profile,
            )
        console.print(table)
    asyncio.run(_go())


@app.command()
def inspect(container_id: str) -> None:
    """Inspect a container."""
    _bootstrap()
    async def _go() -> None:
        lab = await _f().inspect(container_id)
        console.print_json(json.dumps(lab.info.raw, indent=2))
    asyncio.run(_go())


@app.command()
def start(
    container_id: str,
    attach: Annotated[bool, typer.Option("--attach", "-a")] = False,
    interactive: Annotated[bool, typer.Option("--interactive", "-i")] = False,
) -> None:
    """Start a stopped container."""
    _bootstrap()
    async def _go() -> None:
        lab = await _f().start(
            container_id, attach=attach, interactive=interactive,
        )
        console.print(f"[green]✓[/] Started [bold]{lab.id[:12]}[/] — {lab.state}")
    asyncio.run(_go())


@app.command()
def stop(
    container_id: str,
    timeout: Annotated[int, typer.Option("-t", "--timeout")] = 10,
    signal: Annotated[str | None, typer.Option("--signal", "-s")] = None,
) -> None:
    """Stop a running container."""
    _bootstrap()
    async def _go() -> None:
        lab = await _f().stop(container_id, timeout=timeout, signal=signal)
        console.print(f"[yellow]■[/] Stopped [bold]{lab.id[:12]}[/] — {lab.state}")
    asyncio.run(_go())


@app.command()
def destroy(
    container_id: str,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
) -> None:
    """Destroy a container (stop + delete). Requires --confirm."""
    _bootstrap()
    async def _go() -> None:
        await _f().destroy(container_id, confirm=confirm)
        console.print(f"[red]✗[/] Destroyed [bold]{container_id[:12]}[/]")
    asyncio.run(_go())


@app.command()
def kill(
    container_id: str,
    signal: Annotated[str, typer.Option("--signal", "-s")] = "KILL",
) -> None:
    """Kill a running container."""
    _bootstrap()
    async def _go() -> None:
        lab = await _f().kill(container_id, signal=signal)
        console.print(f"[red]†[/] Killed [bold]{lab.id[:12]}[/] ({signal}) — {lab.state}")
    asyncio.run(_go())


# ==================================================================
# Interaction
# ==================================================================


@app.command("exec")
def exec_(
    container_id: str,
    command: Annotated[list[str], typer.Argument(help="Command to execute")],
    user: Annotated[str | None, typer.Option("--user", "-u")] = None,
    uid: Annotated[int | None, typer.Option("--uid")] = None,
    gid: Annotated[int | None, typer.Option("--gid")] = None,
    workdir: Annotated[str | None, typer.Option("--workdir", "-w")] = None,
    env_file: Annotated[str | None, typer.Option("--env-file")] = None,
    tty: Annotated[bool, typer.Option("--tty", "-t")] = False,
    interactive: Annotated[bool, typer.Option("--interactive", "-i")] = False,
) -> None:
    """Execute a command inside a running container."""
    _bootstrap()
    async def _go() -> None:
        opts = ExecOptions(
            user=user, uid=uid, gid=gid, workdir=workdir,
            env_file=env_file, tty=tty, interactive=interactive,
        )
        result = await _i().exec(container_id, list(command), options=opts)
        if result.stdout:
            console.print(result.stdout)
        if result.stderr:
            console.print(f"[red]{result.stderr}[/]")
        if result.exit_code != 0:
            raise typer.Exit(code=result.exit_code)
    asyncio.run(_go())


@app.command()
def logs(
    container_id: str,
    boot: Annotated[bool, typer.Option("--boot")] = False,
    tail: Annotated[int | None, typer.Option("--tail", "-n")] = None,
) -> None:
    """Fetch container logs."""
    _bootstrap()
    async def _go() -> None:
        output = await _i().logs(container_id, boot=boot, tail=tail)
        console.print(output)
    asyncio.run(_go())


# ==================================================================
# Operations
# ==================================================================


@app.command()
def stats(
    container_id: Annotated[str | None, typer.Argument()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show resource statistics for a container (or all running)."""
    _bootstrap()
    async def _go() -> None:
        samples = await _f().stats(container_id)
        if json_output:
            console.print(json.dumps([s.to_dict() for s in samples], indent=2))
            return
        if not samples:
            console.print("[dim]No stats available.[/]")
            return
        table = Table(title="Stats")
        table.add_column("ID", style="cyan")
        table.add_column("CPU (µs)")
        table.add_column("MEM used")
        table.add_column("MEM limit")
        table.add_column("NET rx")
        table.add_column("NET tx")
        table.add_column("Block r")
        table.add_column("Block w")
        table.add_column("Procs")
        for s in samples:
            table.add_row(
                s.id[:12],
                f"{s.cpu_usage_usec:,}",
                _fmt_size(s.memory_usage_bytes),
                _fmt_size(s.memory_limit_bytes),
                _fmt_size(s.network_rx_bytes),
                _fmt_size(s.network_tx_bytes),
                _fmt_size(s.block_read_bytes),
                _fmt_size(s.block_write_bytes),
                str(s.num_processes),
            )
        console.print(table)
    asyncio.run(_go())


@app.command()
def cp(source: str, destination: str) -> None:
    """Copy files between a container and the host.

    One side must be <container-id>:<path>; direction is inferred from
    which side carries the ``id:`` prefix.
    """
    _bootstrap()

    async def _go() -> None:
        src_has_prefix = ":" in source
        dst_has_prefix = ":" in destination
        if src_has_prefix == dst_has_prefix:
            raise ValidationError(
                "exactly one of source/destination must be <container-id>:<path>",
            )
        if src_has_prefix:
            cid, cpath = source.split(":", 1)
            out = await _i().copy_from(cid, cpath, destination)
            console.print(f"[green]✓[/] {out or destination}")
        else:
            cid, cpath = destination.split(":", 1)
            out = await _i().copy_to(cid, source, cpath)
            console.print(f"[green]✓[/] {out or f'{cid}:{cpath}'}")
    asyncio.run(_go())


@app.command()
def export(
    container_id: str,
    output: Annotated[str | None, typer.Option("-o", "--output", help="Output tar path")] = None,
) -> None:
    """Export a container's filesystem to a tar archive."""
    _bootstrap()
    async def _go() -> None:
        result = await _i().export_fs(container_id, output)
        console.print(f"[green]✓[/] Exported filesystem → {result['path']}")
        artifact = result.get("artifact")
        if artifact:
            console.print(
                f"  Artifact: [cyan]{artifact.get('name')}[/] — {artifact.get('path')}",
            )
    asyncio.run(_go())


@app.command()
def prune(
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
) -> None:
    """Prune stopped containers. Requires --confirm."""
    _bootstrap()
    async def _go() -> None:
        if not confirm:
            console.print("[yellow]requires --confirm[/]")
            raise typer.Exit(code=1)
        result = await _b().prune()
        if result.message:
            console.print(result.message)
        if not result.ids:
            console.print("[dim]Nothing to prune.[/]")
            return
        for cid in result.ids:
            console.print(f"[red]✗[/] {cid}")
    asyncio.run(_go())


# ==================================================================
# Network
# ==================================================================


network_app = typer.Typer(
    name="network",
    help="Manage container networks.",
    no_args_is_help=True,
)


@network_app.command("create")
def network_create(
    name: str,
    subnet: Annotated[str | None, typer.Option("--subnet", help="IPv4 CIDR")] = None,
    subnet_v6: Annotated[str | None, typer.Option("--subnet-v6", help="IPv6 CIDR")] = None,
    internal: Annotated[bool, typer.Option("--internal")] = False,
    option: Annotated[
        list[str] | None,
        typer.Option("--option", help="k=v network option (repeatable)"),
    ] = None,
    label: Annotated[
        list[str] | None,
        typer.Option("--label", help="k=v label (repeatable)"),
    ] = None,
) -> None:
    """Create a network."""
    _bootstrap()
    async def _go() -> None:
        spec = NetworkSpec(
            name=name,
            subnet=subnet,
            subnet_v6=subnet_v6,
            internal=internal,
            options=dict(i.split("=", 1) for i in option if "=" in i) if option else {},
            labels=dict(i.split("=", 1) for i in label if "=" in i) if label else {},
        )
        info = await _t().network_create(spec)
        console.print(
            f"[green]✓[/] Created network [bold]{info.name or info.id}[/]"
            f" (id {info.id})",
        )
    asyncio.run(_go())


@network_app.command("list")
def network_list(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List networks."""
    _bootstrap()
    async def _go() -> None:
        networks = await _t().network_list()
        if json_output:
            console.print(json.dumps([_network_dict(n) for n in networks], indent=2))
            return
        if not networks:
            console.print("[dim]No networks.[/]")
            return
        table = Table(title="Networks")
        table.add_column("Name", style="cyan")
        table.add_column("Subnet")
        table.add_column("Gateway")
        table.add_column("Plugin")
        table.add_column("Mode")
        for n in networks:
            table.add_row(
                n.name or n.id,
                n.subnet or "-",
                n.gateway or "-",
                n.plugin or "-",
                n.mode or "-",
            )
        console.print(table)
    asyncio.run(_go())


@network_app.command("inspect")
def network_inspect(
    name: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect a network."""
    _bootstrap()
    async def _go() -> None:
        info = await _t().network_inspect(name)
        if json_output:
            console.print(json.dumps(_network_dict(info), indent=2))
            return
        console.print(f"Name:    {info.name or info.id}")
        console.print(f"Plugin:  {info.plugin or '-'}")
        console.print(f"Mode:    {info.mode or '-'}")
        console.print(f"Subnet:  {info.subnet or '-'}")
        console.print(f"Gateway: {info.gateway or '-'}")
    asyncio.run(_go())


@network_app.command("delete")
def network_delete(
    name: str,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
) -> None:
    """Delete a network. Requires --confirm."""
    _bootstrap()
    async def _go() -> None:
        await _t().network_delete(name, confirm=confirm)
        console.print(f"[red]✗[/] Deleted network {name}")
    asyncio.run(_go())


# ==================================================================
# Machines
# ==================================================================


machine_app = typer.Typer(
    name="machine",
    help="Manage container machines.",
    no_args_is_help=True,
)


@machine_app.command("create")
def machine_create(
    image: str,
    name: Annotated[str | None, typer.Option("--name", "-n")] = None,
    cpus: Annotated[int | None, typer.Option("--cpus")] = None,
    memory: Annotated[str | None, typer.Option("--memory")] = None,
    home_mount: Annotated[str | None, typer.Option("--home-mount")] = None,
    virtualization: Annotated[bool, typer.Option("--virtualization")] = False,
    kernel: Annotated[str | None, typer.Option("--kernel")] = None,
    set_default: Annotated[bool, typer.Option("--set-default")] = False,
    no_boot: Annotated[bool, typer.Option("--no-boot")] = False,
) -> None:
    """Create a container machine (does not boot it with --no-boot)."""
    _bootstrap()
    spec = MachineCreateSpec(
        image=image,
        name=name,
        cpus=cpus,
        memory=memory,
        home_mount=home_mount,
        virtualization=virtualization,
        kernel=kernel,
        set_default=set_default,
        no_boot=no_boot,
    )

    async def _go() -> None:
        info = await _mc().create(spec)
        console.print(f"[green]✓[/] Created machine [bold]{info.id}[/]")
        if info.warning:
            console.print(f"[yellow]warning:[/] {info.warning}")
    asyncio.run(_go())


@machine_app.command("list")
def machine_list(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List container machines."""
    _bootstrap()
    async def _go() -> None:
        machines = await _mc().list()
        if json_output:
            console.print(json.dumps([m.to_dict() for m in machines], indent=2))
            return
        if not machines:
            console.print("[dim]No machines.[/]")
            return
        table = Table(title="Machines")
        table.add_column("Name", style="cyan")
        table.add_column("Status")
        table.add_column("CPUs")
        table.add_column("Memory")
        table.add_column("Disk")
        table.add_column("Default")
        for m in machines:
            status_style = "green" if m.status == "running" else "dim"
            table.add_row(
                m.id,
                f"[{status_style}]{m.status}[/]",
                str(m.cpus) if m.cpus else "-",
                _fmt_size(m.memory_bytes) if m.memory_bytes else "-",
                _fmt_size(m.disk_size_bytes) if m.disk_size_bytes else "-",
                "✓" if m.default else "",
            )
        console.print(table)
    asyncio.run(_go())


@machine_app.command("inspect")
def machine_inspect(
    machine_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect a container machine."""
    _bootstrap()
    async def _go() -> None:
        info = await _mc().inspect(machine_id)
        if json_output:
            console.print(json.dumps(info.to_dict(), indent=2))
            return
        console.print(f"Name:    {info.id}")
        console.print(f"Status:  {info.status or '-'}")
        console.print(f"Image:   {info.image_ref or '-'}")
        console.print(f"CPUs:    {info.cpus if info.cpus else '-'}")
        memory = _fmt_size(info.memory_bytes) if info.memory_bytes else "-"
        console.print(f"Memory:  {memory}")
        disk = _fmt_size(info.disk_size_bytes) if info.disk_size_bytes else "-"
        console.print(f"Disk:    {disk}")
        console.print(f"Home:    {info.home_mount or '-'}")
        arch_os = f"{info.platform_arch}/{info.platform_os}"
        console.print(f"Arch/OS: {arch_os if info.platform_arch else '-'}")
        user = info.user_username or "-"
        console.print(f"User:    {user} (uid {info.user_uid}, gid {info.user_gid})")
        console.print(f"Created: {info.created_date or '-'}")
        console.print(f"Default: {'✓' if info.default else '-'}")
    asyncio.run(_go())


@machine_app.command("set")
def machine_set(
    machine_id: str,
    settings: Annotated[
        list[str],
        typer.Argument(help="key=value settings (repeatable)"),
    ],
) -> None:
    """Update machine settings (cpus=, memory=, home-mount=, virtualization=, kernel=)."""
    _bootstrap()
    parsed = _parse_machine_settings(settings)

    async def _go() -> None:
        await _mc().set(machine_id, parsed)
        console.print(f"[green]✓[/] Updated machine [bold]{machine_id}[/]")
    asyncio.run(_go())


@machine_app.command("set-default")
def machine_set_default(machine_id: str) -> None:
    """Make a machine the default (used when no id is given)."""
    _bootstrap()
    async def _go() -> None:
        await _mc().set_default(machine_id)
        console.print(f"[green]✓[/] [bold]{machine_id}[/] is now the default machine")
    asyncio.run(_go())


@machine_app.command("stop")
def machine_stop(machine_id: str) -> None:
    """Stop a container machine."""
    _bootstrap()
    async def _go() -> None:
        await _mc().stop(machine_id)
        console.print(f"[green]✓[/] Stopped machine [bold]{machine_id}[/]")
    asyncio.run(_go())


@machine_app.command("delete")
def machine_delete(
    machine_id: str,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
) -> None:
    """Delete a container machine. Requires --confirm."""
    _bootstrap()
    async def _go() -> None:
        if not confirm:
            console.print("[yellow]requires --confirm[/]")
            raise typer.Exit(code=1)
        await _mc().delete(machine_id, confirm=True)
        console.print(f"[red]✗[/] Deleted machine [bold]{machine_id}[/]")
    asyncio.run(_go())


@machine_app.command("run")
def machine_run(
    machine_id: str,
    command: Annotated[list[str], typer.Argument(help="Command to run inside the machine")],
) -> None:
    """Boot a machine if necessary and run a command inside it."""
    _bootstrap()
    async def _go() -> None:
        result = await _mc().run(machine_id, list(command), timeout=60.0)
        if result.stdout:
            console.print(result.stdout)
        if result.stderr:
            console.print(f"[red]{result.stderr}[/]")
        if result.exit_code != 0:
            explanation = await _mc().explain_failure(machine_id)
            if explanation:
                console.print(f"[yellow]{explanation}[/]")
            raise typer.Exit(code=result.exit_code)
    asyncio.run(_go())


@machine_app.command("logs")
def machine_logs(machine_id: str) -> None:
    """Dump a machine's boot/stdout logs."""
    _bootstrap()
    async def _go() -> None:
        console.print(await _mc().logs(machine_id))
    asyncio.run(_go())


# ==================================================================
# Volumes
# ==================================================================


volume_app = typer.Typer(
    name="volume",
    help="Manage container volumes.",
    no_args_is_help=True,
)


@volume_app.command("create")
def volume_create(
    name: str,
    size: Annotated[str | None, typer.Option("-s", "--size", help="Size e.g. 100M")] = None,
    label: Annotated[
        list[str] | None,
        typer.Option("--label", help="k=v label (repeatable)"),
    ] = None,
    option: Annotated[
        list[str] | None,
        typer.Option("--opt", help="k=v option (repeatable)"),
    ] = None,
) -> None:
    """Create a named volume."""
    _bootstrap()

    async def _go() -> None:
        spec = VolumeSpec(
            name=name,
            size=size,
            labels=dict(i.split("=", 1) for i in label if "=" in i) if label else {},
            options=dict(i.split("=", 1) for i in option if "=" in i) if option else {},
        )
        info = await _v().create(spec)
        console.print(f"[green]✓[/] Created volume [bold]{info.name or info.id}[/]")
    asyncio.run(_go())


@volume_app.command("list")
def volume_list(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List volumes."""
    _bootstrap()

    async def _go() -> None:
        volumes = await _v().list()
        if json_output:
            console.print(json.dumps([v.to_dict() for v in volumes], indent=2))
            return
        if not volumes:
            console.print("[dim]No volumes.[/]")
            return
        table = Table(title="Volumes")
        table.add_column("Name", style="cyan")
        table.add_column("Driver")
        table.add_column("Format")
        table.add_column("Size")
        table.add_column("Source")
        for v in volumes:
            size = v.size_spec or (_fmt_size(v.size_bytes) if v.size_bytes else "-")
            table.add_row(
                v.name or v.id,
                v.driver or "-",
                v.format or "-",
                size,
                v.source or "-",
            )
        console.print(table)
    asyncio.run(_go())


@volume_app.command("inspect")
def volume_inspect(
    name: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect a volume."""
    _bootstrap()

    async def _go() -> None:
        info = await _v().inspect(name)
        if json_output:
            console.print(json.dumps(info.to_dict(), indent=2))
            return
        size = info.size_spec or (_fmt_size(info.size_bytes) if info.size_bytes else "-")
        console.print(f"Name:    {info.name or info.id}")
        console.print(f"Driver:  {info.driver or '-'}")
        console.print(f"Format:  {info.format or '-'}")
        console.print(f"Size:    {size}")
        console.print(f"Source:  {info.source or '-'}")
        console.print(f"Created: {info.created_date or '-'}")
        if info.labels:
            console.print(f"Labels:  {info.labels}")
    asyncio.run(_go())


@volume_app.command("delete")
def volume_delete(
    name: str,
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
) -> None:
    """Delete a volume. Requires --confirm."""
    _bootstrap()

    async def _go() -> None:
        if not confirm:
            console.print("[yellow]requires --confirm[/]")
            raise typer.Exit(code=1)
        await _v().delete(name, confirm=True)
        console.print(f"[red]✗[/] Deleted volume {name}")
    asyncio.run(_go())


@volume_app.command("prune")
def volume_prune(
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
) -> None:
    """Prune unused volumes. Requires --confirm."""
    _bootstrap()

    async def _go() -> None:
        if not confirm:
            console.print("[yellow]requires --confirm[/]")
            raise typer.Exit(code=1)
        removed = await _v().prune(confirm=True)
        if not removed:
            console.print("[dim]Nothing to prune.[/]")
            return
        for name in removed:
            console.print(f"[red]✗[/] {name}")
    asyncio.run(_go())


# ==================================================================
# Kernel
# ==================================================================


kernel_app = typer.Typer(
    name="kernel",
    help="Manage the default container kernel.",
    no_args_is_help=True,
)


@kernel_app.command("set")
def kernel_set(
    recommended: Annotated[bool, typer.Option("--recommended")] = False,
    binary: Annotated[str | None, typer.Option("--binary", help="Path to a kernel binary")] = None,
    tar: Annotated[str | None, typer.Option("--tar", help="Path to a kernel tar archive")] = None,
    arch: Annotated[str, typer.Option("--arch")] = "arm64",
) -> None:
    """Set the default container kernel (--recommended, --binary, or --tar)."""
    _bootstrap()
    selected = [x for x in (recommended, binary, tar) if x]
    if len(selected) != 1:
        raise typer.BadParameter(
            "exactly one of --recommended, --binary, --tar is required",
        )

    async def _go() -> None:
        await _b().system_kernel_set(
            binary=binary, tar=tar, arch=arch, recommended=recommended,
        )
        console.print("[green]✓[/] Kernel set")
    asyncio.run(_go())


@kernel_app.command("status")
def kernel_status() -> None:
    """Show the kernel-set capability gate."""
    _bootstrap()
    gate = _c().kernel_set
    label = "✓" if gate is True else ("✗" if gate is False else str(gate))
    console.print(f"kernel set: {label}")


# ==================================================================
# Images
# ==================================================================


@app.command("image-pull")
def image_pull(
    image: str,
    platform: Annotated[str | None, typer.Option("--platform")] = None,
    scheme: Annotated[str | None, typer.Option("--scheme")] = None,
) -> None:
    """Pull an image from a registry."""
    _bootstrap()
    async def _go() -> None:
        await _m().pull(image, platform=platform, scheme=scheme)
        console.print(f"[green]✓[/] Pulled {image}")
    asyncio.run(_go())


@app.command("image-push")
def image_push(
    image: str,
    platform: Annotated[str | None, typer.Option("--platform")] = None,
    scheme: Annotated[str | None, typer.Option("--scheme")] = None,
) -> None:
    """Push an image to a registry."""
    _bootstrap()
    async def _go() -> None:
        await _m().push(image, platform=platform, scheme=scheme)
        console.print(f"[green]✓[/] Pushed {image}")
    asyncio.run(_go())


@app.command("image-inspect")
def image_inspect(image: str) -> None:
    """Inspect a local image."""
    _bootstrap()
    async def _go() -> None:
        img = await _m().inspect(image)
        console.print_json(json.dumps({
            "name": img.name, "id": img.id, "digest": img.digest,
            "size": img.size, "tag": img.tag, "raw": img.raw,
        }, indent=2))
    asyncio.run(_go())


@app.command("image-list")
def image_list() -> None:
    """List local images."""
    _bootstrap()
    async def _go() -> None:
        images = await _m().list()
        if not images:
            console.print("[dim]No images.[/]")
            return
        table = Table(title="Images")
        table.add_column("Name")
        table.add_column("Tag")
        table.add_column("Size")
        for img in images:
            table.add_row(img.name, img.tag, _fmt_size(img.size))
        console.print(table)
    asyncio.run(_go())


@app.command("image-delete")
def image_delete(image: str, all: Annotated[bool, typer.Option("--all")] = False) -> None:
    """Delete a local image."""
    _bootstrap()
    async def _go() -> None:
        await _m().delete(image, all=all)
        console.print(f"[red]✗[/] Deleted image {image}")
    asyncio.run(_go())


@app.command("image-build")
def image_build(
    tag: str,
    context: Annotated[str, typer.Option("--context", "-c")] = ".",
    file: Annotated[str | None, typer.Option("--file", "-f")] = None,
    target: Annotated[str | None, typer.Option("--target")] = None,
    arch: Annotated[str | None, typer.Option("--arch")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache")] = False,
) -> None:
    """Build an image from a Containerfile/Dockerfile."""
    _bootstrap()
    from daedalus.core.backend import BuildSpec

    async def _go() -> None:
        spec = BuildSpec(
            context=context, file=file, tag=tag,
            target=target, arch=arch, no_cache=no_cache,
        )
        img = await _m().build(spec)
        console.print(f"[green]✓[/] Built {img.name} ({img.id})")
    asyncio.run(_go())


@app.command("image-load")
def image_load(path: str) -> None:
    """Load an image from an OCI-compatible tar archive."""
    _bootstrap()
    async def _go() -> None:
        img = await _m().load(path)
        console.print(f"[green]✓[/] Loaded {img.name} ({img.id})")
    asyncio.run(_go())


@app.command("image-save")
def image_save(image: str, output: Annotated[str, typer.Option("-o", "--output")]) -> None:
    """Save an image as an OCI-compatible tar archive."""
    _bootstrap()
    async def _go() -> None:
        path = await _m().save(image, output)
        console.print(f"[green]✓[/] Saved {image} → {path}")
    asyncio.run(_go())


@app.command("image-tag")
def image_tag(source: str, target: str) -> None:
    """Tag an image (create alias)."""
    _bootstrap()
    async def _go() -> None:
        await _m().tag(source, target)
        console.print(f"[green]✓[/] Tagged {source} → {target}")
    asyncio.run(_go())


@app.command("image-prune")
def image_prune() -> None:
    """Remove dangling/unreferenced images."""
    _bootstrap()
    async def _go() -> None:
        removed = await _m().prune()
        if not removed:
            console.print("[dim]Nothing to prune.[/]")
            return
        for name in removed:
            console.print(f"[yellow]–[/] {name}")
        console.print(f"[green]✓[/] Pruned {len(removed)} image(s)")
    asyncio.run(_go())


@app.command("registry-login")
def registry_login(
    server: str,
    username: Annotated[str | None, typer.Option("-u", "--username")] = None,
    password: Annotated[str | None, typer.Option("-p", "--password", hide_input=True)] = None,
    scheme: Annotated[str | None, typer.Option("--scheme")] = None,
) -> None:
    """Login to a container registry."""
    _bootstrap()
    async def _go() -> None:
        await _f().backend.registry_login(
            server, username=username, password=password, scheme=scheme,
        )
        console.print(f"[green]✓[/] Logged in to {server}")
    asyncio.run(_go())


@app.command("registry-logout")
def registry_logout(server: str) -> None:
    """Logout from a container registry."""
    _bootstrap()
    async def _go() -> None:
        await _f().backend.registry_logout(server)
        console.print(f"[green]✓[/] Logged out from {server}")
    asyncio.run(_go())


@app.command("registry-default-inspect")
def registry_default_inspect() -> None:
    """Show the configured default registry host."""
    _bootstrap()
    async def _go() -> None:
        host = await _f().backend.registry_default_inspect()
        console.print(host or "[dim](none)[/]")
    asyncio.run(_go())


@app.command("registry-default-set")
def registry_default_set(
    host: str,
    scheme: Annotated[str | None, typer.Option("--scheme")] = None,
) -> None:
    """Set the default registry host."""
    _bootstrap()
    async def _go() -> None:
        await _f().backend.registry_default_set(host, scheme=scheme)
        console.print(f"[green]✓[/] Default registry set to {host}")
    asyncio.run(_go())


@app.command("registry-default-unset")
def registry_default_unset() -> None:
    """Clear the default registry host."""
    _bootstrap()
    async def _go() -> None:
        await _f().backend.registry_default_unset()
        console.print("[green]✓[/] Default registry cleared")
    asyncio.run(_go())


@app.command()
def audit_cmd(
    operation: Annotated[str | None, typer.Option("--operation")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 50,
) -> None:
    """Query the audit log."""
    _bootstrap()
    log = AuditLog()
    entries = log.query(operation=operation, limit=limit)
    if not entries:
        console.print("[dim]No audit entries.[/]")
        return
    table = Table(title="Audit Log")
    table.add_column("Time")
    table.add_column("Operation")
    table.add_column("Actor")
    table.add_column("Kind")
    for e in entries:
        table.add_row(
            e.timestamp[:19], e.operation, e.actor, e.actor_kind.value,
        )
    console.print(table)


# ==================================================================
# System
# ==================================================================


@app.command()
def system_status() -> None:
    """Show system status."""
    _bootstrap()
    async def _go() -> None:
        status = await _f().system_status()
        console.print(f"Container: v{status.container_version}")
        console.print(f"Commit: {status.container_commit}")
        console.print(f"API server: {'✓ running' if status.apiserver_running else '✗ stopped'}")
        console.print(f"Containers: {status.container_count} ({status.running_count} running)")
        if status.disk_usage:
            console.print(f"Disk: {status.disk_usage}")
    asyncio.run(_go())


@app.command("builder-status")
def builder_status() -> None:
    """Show image builder status."""
    _bootstrap()
    async def _go() -> None:
        data = await _f().backend.builder_status()
        console.print_json(json.dumps(data, indent=2))
    asyncio.run(_go())


@app.command("builder-start")
def builder_start(
    cpus: Annotated[int, typer.Option("-c", "--cpus")] = 2,
    memory: Annotated[str, typer.Option("-m", "--memory")] = "2048M",
) -> None:
    """Start the image builder VM."""
    _bootstrap()
    async def _go() -> None:
        await _f().backend.builder_start(cpus=cpus, memory=memory)
        console.print(f"[green]✓[/] Builder started ({cpus} CPUs, {memory})")
    asyncio.run(_go())


@app.command("builder-stop")
def builder_stop() -> None:
    """Stop the image builder VM."""
    _bootstrap()
    async def _go() -> None:
        await _f().backend.builder_stop()
        console.print("[yellow]■[/] Builder stopped")
    asyncio.run(_go())


@app.command("builder-delete")
def builder_delete(
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Delete the image builder VM."""
    _bootstrap()
    async def _go() -> None:
        await _f().backend.builder_delete(force=force)
        console.print("[red]✗[/] Builder deleted")
    asyncio.run(_go())


@app.command("system-start")
def system_start() -> None:
    """Start container system services."""
    _bootstrap()
    async def _go() -> None:
        await _f().backend.system_start()
        console.print("[green]✓[/] Container system started")
    asyncio.run(_go())


@app.command("system-stop")
def system_stop() -> None:
    """Stop all container system services."""
    _bootstrap()
    async def _go() -> None:
        await _f().backend.system_stop()
        console.print("[yellow]■[/] Container system stopped")
    asyncio.run(_go())


@app.command("system-logs")
def system_logs(
    last: Annotated[str, typer.Option("--last", "-n")] = "5m",
) -> None:
    """Show container system logs."""
    _bootstrap()
    async def _go() -> None:
        logs = await _f().backend.system_logs(last=last)
        console.print(logs)
    asyncio.run(_go())


@app.command("system-kernel-set")
def system_kernel_set(
    binary: Annotated[str | None, typer.Option("--binary")] = None,
    tar: Annotated[str | None, typer.Option("--tar")] = None,
    arch: Annotated[str, typer.Option("--arch")] = "arm64",
    recommended: Annotated[bool, typer.Option("--recommended")] = False,
) -> None:
    """Set the default container kernel."""
    _bootstrap()
    async def _go() -> None:
        await _f().backend.system_kernel_set(
            binary=binary, tar=tar, arch=arch, recommended=recommended,
        )
        console.print("[green]✓[/] Kernel set")
    asyncio.run(_go())


@app.command("system-restart")
def system_restart() -> None:
    """Restart the container apiserver."""
    _bootstrap()
    async def _go() -> None:
        await _f().backend.system_restart()
        console.print("[green]✓[/] Container apiserver restarted")
    asyncio.run(_go())


# ==================================================================
# Profiles
# ==================================================================


@app.command()
def profiles() -> None:
    """List available security profiles."""
    _bootstrap()
    for p in _p().list():
        console.print(f"[bold]{p.name}[/] — {p.description}")
        tags = []
        if p.kernel:
            tags.append(f"kernel={p.kernel}")
        if p.no_dns:
            tags.append("no-dns")
        if p.dns:
            tags.append(f"dns={','.join(p.dns)}")
        if tags:
            console.print(f"  [dim]{', '.join(tags)}[/]")


# ==================================================================
# Helpers
# ==================================================================


def _risk_label(score: float) -> str:
    """Map a risk score (0.0–1.0) to a severity label."""
    if score > 0.7:
        return "HIGH"
    if score > 0.3:
        return "MEDIUM"
    if score > 0.0:
        return "LOW"
    return "NONE"


def _print_detonation_report(result: dict[str, Any]) -> None:
    """Render a detonation result dict as a human-readable summary.

    ``result["report"]`` is ``BehavioralReport.to_dict()`` — the engine has
    already consumed the rich report; here we only present the parts an
    operator cares about (risk, evidence counts, findings, artifact paths).
    """
    report = result.get("report") or {}
    fs = report.get("filesystem") or {}
    net = report.get("network") or {}
    resources = report.get("resources") or {}
    risk = float(report.get("risk_score", 0.0))
    run_id = str(result.get("run_id", ""))[:12]
    console.print(
        f"[green]✓[/] Detonation complete — run [bold]{run_id}[/]"
        f" (exit code {result.get('exit_code', '?')})",
    )
    console.print(f"Risk:       {risk:.2f} ({_risk_label(risk)})")
    console.print(f"Image:      {report.get('image', '')}")
    console.print(f"Duration:   {float(resources.get('duration_seconds', 0.0)):.1f}s")
    total = int(fs.get("total_changes", 0))
    console.print(
        f"Filesystem: {total} change{'s' if total != 1 else ''}"
        f" ({fs.get('added_count', 0)} added,"
        f" {fs.get('modified_count', 0)} modified,"
        f" {fs.get('removed_count', 0)} removed)",
    )
    for path in (fs.get("added") or [])[:10]:
        console.print(f"  [green]+[/] {path}")
    for path in (fs.get("modified") or [])[:10]:
        console.print(f"  [yellow]~[/] {path}")
    for path in (fs.get("removed") or [])[:10]:
        console.print(f"  [red]-[/] {path}")
    console.print(
        f"Network:    {len(net.get('connections') or [])} connections,"
        f" {len(net.get('dns_queries') or [])} DNS queries,"
        f" {int(net.get('total_bytes_sent', 0)) + int(net.get('total_bytes_received', 0))}"
        " bytes",
    )
    syscalls = report.get("syscalls") or {}
    console.print(
        f"Syscalls:   {syscalls.get('total_calls', 0)} total,"
        f" {syscalls.get('unique_syscalls', 0)} unique",
    )
    console.print(f"CPU peak:   {float(resources.get('peak_cpu_percent', 0.0)):.1f}%")
    for finding in (report.get("findings") or [])[:10]:
        console.print(f"[dim]  - {finding}[/]")
    for factor in (report.get("risk_factors") or [])[:10]:
        console.print(f"[yellow]  ! {factor}[/]")
    report_path = result.get("report_path")
    if report_path:
        console.print(f"Report:     {report_path}")


def _fmt_size(b: int) -> str:
    if b > 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024 * 1024):.1f} GiB"
    if b > 1024 * 1024:
        return f"{b / (1024 * 1024):.0f} MiB"
    return f"{b} B"


def _parse_machine_settings(settings: list[str]) -> MachineSetSettings:
    """Parse repeatable ``key=value`` strings into a settings object."""
    out = MachineSetSettings()
    for item in settings:
        key, sep, value = item.partition("=")
        if not sep:
            raise typer.BadParameter(
                f"invalid machine setting (expected key=value): {item}",
            )
        if key == "cpus":
            try:
                out.cpus = int(value)
            except ValueError:
                raise typer.BadParameter(
                    f"machine setting cpus must be an integer: {value}",
                ) from None
        elif key == "memory":
            out.memory = value
        elif key == "home-mount":
            out.home_mount = value
        elif key == "virtualization":
            if value == "true":
                out.virtualization = True
            elif value == "false":
                out.virtualization = False
            else:
                raise typer.BadParameter(
                    f"machine setting virtualization must be 'true' or 'false': {value}",
                )
        elif key == "kernel":
            out.kernel = value  # empty string resets the kernel
        else:
            raise typer.BadParameter(f"unknown machine setting: {key}")
    return out


def _network_dict(info: NetworkInfo) -> dict[str, Any]:
    """Serialise a :class:`NetworkInfo` as a flat JSON-able dict."""
    return {
        "id": info.id,
        "name": info.name,
        "plugin": info.plugin,
        "mode": info.mode,
        "subnet": info.subnet,
        "subnet_v6": info.subnet_v6,
        "gateway": info.gateway,
        "raw": info.raw,
    }


app.add_typer(network_app, name="network")
app.add_typer(machine_app, name="machine")
app.add_typer(volume_app, name="volume")
app.add_typer(kernel_app, name="kernel")


if __name__ == "__main__":
    app()
