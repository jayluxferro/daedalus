"""M8a — CLI: human-facing verb-first interface.

Every command maps to the Core Engine.  No business logic lives here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.capabilities import CapabilityManifest, probe
from daedalus.core.cli_backend import CliBackend
from daedalus.core.forge import Forge
from daedalus.core.icarus import ExecOptions, Icarus
from daedalus.core.mint import Mint
from daedalus.core.policy import PolicyEngine, PolicyResult, load_policy_config
from daedalus.core.profiles import ProfileRegistry
from daedalus.core.store import Store

app = typer.Typer(
    name="daedalus",
    help="DAEDALUS — Detonation, Analysis & Experimentation control plane.",
    no_args_is_help=True,
)

console = Console()

# Lazy-init globals — typed loosely to avoid import-time I/O
_caps: CapabilityManifest | None = None
_forge: Forge | None = None
_icarus: Icarus | None = None
_mint: Mint | None = None
_profiles: ProfileRegistry | None = None


def _bootstrap() -> None:
    global _caps, _forge, _icarus, _mint, _profiles
    if _caps is not None:
        return
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

    _forge = Forge(backend, _caps, policy=policy, audit=audit, store=store)
    _icarus = Icarus(backend, audit=audit, runtime_binary=_caps.container_binary)
    _mint = Mint(backend, audit=audit)
    _profiles = ProfileRegistry()


def _f() -> Forge:
    assert _forge is not None
    return _forge


def _i() -> Icarus:
    assert _icarus is not None
    return _icarus


def _m() -> Mint:
    assert _mint is not None
    return _mint


def _p() -> ProfileRegistry:
    assert _profiles is not None
    return _profiles


# ==================================================================
# M0 — Probe
# ==================================================================


@app.command()
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
    volume: Annotated[list[str], typer.Option("--volume", "-v", help="Bind mount host:container")] = [],
    mount: Annotated[list[str], typer.Option("--mount", help="Mount spec type=,source=,target=")] = [],
    env: Annotated[list[str], typer.Option("--env", "-e", help="KEY=VALUE")] = [],
    workdir: Annotated[str | None, typer.Option("--workdir", "-w")] = None,
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
    volume: Annotated[list[str], typer.Option("--volume", "-v", help="Bind mount host:container")] = [],
    mount: Annotated[list[str], typer.Option("--mount", help="Mount spec type=,source=,target=")] = [],
    env: Annotated[list[str], typer.Option("--env", "-e", help="KEY=VALUE")] = [],
    workdir: Annotated[str | None, typer.Option("--workdir", "-w")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create and start a container."""
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
            ip = "" or "-"
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


@app.command()
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


def _fmt_size(b: int) -> str:
    if b > 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024 * 1024):.1f} GiB"
    if b > 1024 * 1024:
        return f"{b / (1024 * 1024):.0f} MiB"
    return f"{b} B"


if __name__ == "__main__":
    app()
