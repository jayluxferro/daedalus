"""PTY-backed interactive container shell sessions."""

from __future__ import annotations

import asyncio
import os
import pty
from dataclasses import dataclass


@dataclass
class TtySession:
    """Host PTY bridged to ``container exec -it``."""

    process: asyncio.subprocess.Process
    master_fd: int
    container_id: str


async def open_container_shell(
    binary: str,
    container_id: str,
    argv: tuple[str, ...] = ("sh",),
) -> TtySession:
    """Spawn an interactive shell inside a container with a host PTY."""
    master_fd, slave_fd = pty.openpty()
    proc = await asyncio.create_subprocess_exec(
        binary, "exec", "-i", "-t", container_id, *argv,
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)
    return TtySession(process=proc, master_fd=master_fd, container_id=container_id)


async def close_tty_session(session: TtySession) -> None:
    """Terminate the container exec process and close the PTY."""
    if session.process.returncode is None:
        session.process.terminate()
        try:
            await asyncio.wait_for(session.process.wait(), timeout=2)
        except TimeoutError:
            session.process.kill()
    try:
        os.close(session.master_fd)
    except OSError:
        pass
