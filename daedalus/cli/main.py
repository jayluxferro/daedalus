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
from daedalus.core.network import network_names, primary_ip
from daedalus.core.policy import PolicyEngine, PolicyResult
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
    policy = PolicyEngine()

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
            ip = primary_ip(lab.info.raw)
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
            ip = primary_ip(lab.info.raw) or "-"
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
def start(container_id: str) -> None:
    """Start a stopped container."""
    _bootstrap()
    async def _go() -> None:
        lab = await _f().start(container_id)
        console.print(f"[green]✓[/] Started [bold]{lab.id[:12]}[/] — {lab.state}")
    asyncio.run(_go())


@app.command()
def stop(container_id: str) -> None:
    """Stop a running container."""
    _bootstrap()
    async def _go() -> None:
        lab = await _f().stop(container_id)
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


# ==================================================================
# Interaction
# ==================================================================


@app.command()
def exec_(
    container_id: str,
    command: Annotated[list[str], typer.Argument(help="Command to execute")],
    user: Annotated[str | None, typer.Option("--user", "-u")] = None,
    workdir: Annotated[str | None, typer.Option("--workdir", "-w")] = None,
    tty: Annotated[bool, typer.Option("--tty", "-t")] = False,
) -> None:
    """Execute a command inside a running container."""
    _bootstrap()
    async def _go() -> None:
        opts = ExecOptions(user=user, workdir=workdir, tty=tty)
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
def image_pull(image: str) -> None:
    """Pull an image from a registry."""
    _bootstrap()
    async def _go() -> None:
        await _m().pull(image)
        console.print(f"[green]✓[/] Pulled {image}")
    asyncio.run(_go())


@app.command("image-push")
def image_push(image: str) -> None:
    """Push an image to a registry."""
    _bootstrap()
    async def _go() -> None:
        await _m().push(image)
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
def image_delete(image: str, force: Annotated[bool, typer.Option("--force")] = False) -> None:
    """Delete a local image."""
    _bootstrap()
    async def _go() -> None:
        await _m().delete(image, force=force)
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
        return f"{b / 1024 * 1024 * 1024:.1f} GiB"
    if b > 1024 * 1024:
        return f"{b / 1024 * 1024:.0f} MiB"
    return f"{b} B"


if __name__ == "__main__":
    app()
