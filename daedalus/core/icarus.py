"""M2 — icarus: interaction & extraction.

icarus handles everything that crosses the host—guest boundary:

* ``exec`` — run commands inside a container (no sshd needed)
* ``logs`` — container log retrieval (live + boot)

.. note::
    ``cp``, ``export``, and ``stats`` do not exist as container subcommands
    in v0.1.0.  They will be added when the CLI supports them.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import Backend, ExecResult



@dataclass
class ExecOptions:
    """Options for ``icarus.exec``."""

    user: str | None = None
    uid: int | None = None
    gid: int | None = None
    tty: bool = False
    interactive: bool = False
    workdir: str | None = None
    env: dict[str, str] | None = None
    env_file: str | None = None


class Icarus:
    """Interaction and extraction for DAEDALUS containers.

    Parameters
    ----------
    backend:
        The active ``Backend`` implementation.
    audit:
        Optional audit log for operation recording.
    """

    def __init__(
        self,
        backend: Backend,
        *,
        audit: AuditLog | None = None,
        runtime_binary: str = "container",
    ) -> None:
        self._backend = backend
        self._audit = audit or AuditLog()
        self._runtime_binary = runtime_binary

    async def exec(
        self,
        container_id: str,
        argv: list[str],
        *,
        options: ExecOptions | None = None,
    ) -> ExecResult:
        """Execute a command inside a running container.

        This is the primary "get inside the sandbox" primitive — no sshd
        required.  The ``container`` runtime tunnels the command through
        vminitd's gRPC API over vsock.

        Parameters
        ----------
        container_id:
            Target container.
        argv:
            Command + arguments to execute.
        options:
            Optional environment, user, working directory, tty settings.
        """
        opts = options or ExecOptions()
        result = await self._backend.exec(
            container_id,
            argv,
            env=opts.env,
            user=opts.user,
            uid=opts.uid,
            gid=opts.gid,
            tty=opts.tty,
            interactive=opts.interactive,
            workdir=opts.workdir,
            env_file=opts.env_file,
        )
        self._audit.record(
            "exec", actor="icarus", actor_kind=ActorKind.SERVICE,
            args={"container_id": container_id, "argv": argv},
        )
        return result

    async def shell(
        self,
        container_id: str,
        command: str,
        *,
        options: ExecOptions | None = None,
    ) -> ExecResult:
        """Execute a shell command (wraps in ``sh -c …``)."""
        return await self.exec(container_id, ["sh", "-c", command], options=options)

    async def logs(
        self,
        container_id: str,
        *,
        follow: bool = False,
        follow_seconds: float | None = None,
        boot: bool = False,
        tail: int | None = None,
    ) -> str:
        """Retrieve container logs.

        Parameters
        ----------
        boot:
            If True, retrieve boot-time logs rather than OCI process output.
        follow:
            Stream logs for up to ``follow_seconds`` (default 15).
        """
        return await self._backend.logs(
            container_id,
            follow=follow,
            follow_seconds=follow_seconds,
            boot=boot,
            tail=tail,
        )

    async def logs_all(
        self,
        *,
        all_containers: bool = True,
        boot: bool = False,
        tail: int | None = None,
        include_system: bool = True,
        system_last: str = "5m",
        follow: bool = False,
        follow_seconds: float = 15.0,
    ) -> dict[str, Any]:
        """Fetch logs for every container plus optional system daemon logs."""
        containers = await self._backend.list(all=all_containers)
        entries: list[dict[str, str]] = []

        for info in containers:
            logs = await self._backend.logs(
                info.id,
                follow=False,
                boot=boot,
                tail=tail,
            )
            entries.append({
                "id": info.id,
                "name": info.name,
                "image": info.image,
                "state": info.state.value,
                "logs": logs,
            })

        if follow:
            deadline = time.monotonic() + follow_seconds
            seen: dict[str, set[str]] = {e["id"]: set() for e in entries}
            for entry in entries:
                for line in entry["logs"].splitlines():
                    if line.strip():
                        seen[entry["id"]].add(line)
            while time.monotonic() < deadline:
                for entry in entries:
                    chunk = await self._backend.logs(
                        entry["id"], follow=False, boot=boot, tail=30,
                    )
                    new_lines: list[str] = []
                    for line in chunk.splitlines():
                        if line.strip() and line not in seen[entry["id"]]:
                            seen[entry["id"]].add(line)
                            new_lines.append(line)
                    if new_lines:
                        entry["logs"] = (
                            entry["logs"].rstrip("\n") + "\n" + "\n".join(new_lines) + "\n"
                        )
                await asyncio.sleep(1)

        system_logs = ""
        if include_system:
            system_logs = await self._backend.system_logs(
                last=system_last,
                follow=follow,
                follow_seconds=follow_seconds if follow else None,
            )

        return {
            "containers": entries,
            "system": system_logs,
            "count": len(entries),
        }

