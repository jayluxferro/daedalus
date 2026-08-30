"""M2 — icarus: interaction & extraction.

icarus handles everything that crosses the host—guest boundary:

* ``exec`` — run commands inside a container (no sshd needed)
* ``logs`` — container log retrieval (live + boot)
* ``copy_from`` / ``copy_to`` — file transfer across the boundary
* ``export_fs`` — full filesystem export (tar), indexed as a store artifact
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import Backend, ExecResult
from daedalus.core.store import Artifact, Store


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
    timeout: float = 30.0
    detached: bool = False      # exec -d
    ulimit: list[str] | None = None  # exec --ulimit <spec>, repeatable


class Icarus:
    """Interaction and extraction for DAEDALUS containers.

    Parameters
    ----------
    backend:
        The active ``Backend`` implementation.
    audit:
        Optional audit log for operation recording.
    store:
        Optional run store for artifact indexing.
    """

    def __init__(
        self,
        backend: Backend,
        *,
        audit: AuditLog | None = None,
        store: Store | None = None,
        runtime_binary: str = "container",
    ) -> None:
        self._backend = backend
        self._audit = audit or AuditLog()
        self._store = store or Store()
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
            detached=opts.detached,
            ulimit=opts.ulimit,
            timeout=opts.timeout,
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

    async def copy_from(
        self,
        container_id: str,
        container_path: str,
        host_path: str,
    ) -> str:
        """Copy a file or directory out of a container to the host.

        The ``<container-id>:<path>`` prefix is built here — the id comes
        from code, never from user string-munging.

        Returns the backend's stdout (the destination path on success).
        """
        result = await self._backend.copy(
            source=f"{container_id}:{container_path}",
            destination=host_path,
        )
        self._audit.record(
            "copy_from", actor="icarus", actor_kind=ActorKind.SERVICE,
            args={
                "container_id": container_id,
                "container_path": container_path,
                "host_path": host_path,
            },
        )
        return result

    async def copy_to(
        self,
        container_id: str,
        host_path: str,
        container_path: str,
    ) -> str:
        """Copy a file or directory from the host into a container."""
        result = await self._backend.copy(
            source=host_path,
            destination=f"{container_id}:{container_path}",
        )
        self._audit.record(
            "copy_to", actor="icarus", actor_kind=ActorKind.SERVICE,
            args={
                "container_id": container_id,
                "host_path": host_path,
                "container_path": container_path,
            },
        )
        return result

    async def export_fs(
        self,
        container_id: str,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """Export a container's full filesystem to a tar archive.

        Defaults to ``<store.root>/<container_id>.fs.tar``.  The resulting
        archive is registered as a store artifact (``kind="fs"``).

        Returns a dict with ``path``, ``container_id``, and the registered
        ``artifact``.
        """
        if output_path is None:
            output_path = os.path.join(self._store.root, f"{container_id}.fs.tar")
        await self._backend.export(container_id, output_path)
        artifact = Artifact(
            name="filesystem.tar",
            path=output_path,
            kind="fs",
            description="Full filesystem export (tar)",
        )
        self._store.add_artifact(container_id, artifact)
        self._audit.record(
            "export", actor="icarus", actor_kind=ActorKind.SERVICE,
            args={"container_id": container_id, "output_path": output_path},
        )
        return {
            "path": output_path,
            "container_id": container_id,
            "artifact": asdict(artifact),
        }

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

