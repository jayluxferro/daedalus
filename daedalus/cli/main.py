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

from daedalus.core.audit import AuditLog
from daedalus.core.capabilities import CapabilityManifest, probe
from daedalus.core.cli_backend import CliBackend
from daedalus.core.forge import Forge
from daedalus.core.icarus import ExecOptions, Icarus
from daedalus.core.mint import Mint
from daedalus.core.policy import PolicyEngine
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
    _forge = Forge(backend, _caps, policy=policy, audit=audit, store=store)
    _icarus = Icarus(backend, audit=audit)
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
    command: Annotated[str | None, typer.Option("--command", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create and start a container."""
    _bootstrap()
    p = _p().get(profile)
    kwargs = p.apply()
    if kernel:
        kwargs["kernel"] = kernel
    cmd_list = command.split() if command else None

    async def _go() -> None:
        lab = await _f().run(
            image, name=name, detach=detach, profile=profile,
            command=cmd_list, **kwargs,
        )
        if json_output:
            console.print(json.dumps(lab.info.raw, indent=2))
        else:
            console.print(f"[green]✓[/] Created [bold]{lab.id[:12]}[/] from [cyan]{image}[/]")
            if lab.name:
                console.print(f"  Name: {lab.name}")
            console.print(f"  State: {lab.state}")
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
        table.add_column("Profile")
        for lab in labs:
            state_style = "green" if lab.is_running else "dim"
            table.add_row(
                lab.id[:12], lab.name or "-", lab.image,
                f"[{state_style}]{lab.state}[/]", lab.profile,
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


@app.command()
def image_pull(image: str) -> None:
    """Pull an image from a registry."""
    _bootstrap()
    async def _go() -> None:
        await _m().pull(image)
        console.print(f"[green]✓[/] Pulled {image}")
    asyncio.run(_go())


@app.command()
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
        console.print(f"API server: {'✓ running' if status.apiserver_running else '✗ stopped'}")
        console.print(f"Containers: {status.container_count} ({status.running_count} running)")
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
