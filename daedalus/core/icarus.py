"""M2 — icarus: interaction & extraction.

icarus handles everything that crosses the host—guest boundary:

* ``exec`` — run commands inside a container (no sshd needed)
* ``logs`` — container log retrieval (live + boot)

.. note::
    ``cp``, ``export``, and ``stats`` do not exist as container subcommands
    in v0.1.0.  They will be added when the CLI supports them.
"""

from __future__ import annotations

from dataclasses import dataclass

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import Backend, ExecResult
from daedalus.core.tty_session import TtySession, close_tty_session, open_container_shell


@dataclass
class ExecOptions:
    """Options for ``icarus.exec``."""

    user: str | None = None
    uid: int | None = None
    gid: int | None = None
    tty: bool = False
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
        boot: bool = False,
        tail: int | None = None,
    ) -> str:
        """Retrieve container logs.

        Parameters
        ----------
        boot:
            If True, retrieve boot-time logs rather than OCI process output.
        """
        return await self._backend.logs(
            container_id, follow=follow, boot=boot, tail=tail,
        )

    async def spawn_shell(
        self,
        container_id: str,
        *,
        argv: tuple[str, ...] = ("sh",),
        actor: str = "icarus",
        actor_kind: ActorKind = ActorKind.SERVICE,
    ) -> TtySession:
        """Open an interactive PTY shell inside a running container."""
        session = await open_container_shell(
            self._runtime_binary, container_id, argv=argv,
        )
        self._audit.record(
            "shell_attach", actor=actor, actor_kind=actor_kind,
            args={"container_id": container_id, "argv": list(argv)},
        )
        return session

    async def close_shell(
        self,
        session: TtySession,
        *,
        actor: str = "icarus",
        actor_kind: ActorKind = ActorKind.SERVICE,
    ) -> None:
        """Close an interactive shell session."""
        await close_tty_session(session)
        self._audit.record(
            "shell_detach", actor=actor, actor_kind=actor_kind,
            args={"container_id": session.container_id},
        )
