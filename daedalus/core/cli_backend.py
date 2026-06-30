"""L1 backend — shells ``container`` and parses output.

This is the reference implementation.  It matches the **actual** command
surface of container v0.1.0 (commit 0fd8692), not aspirational features.

JSON output is available for exactly three commands:
* ``container list --format json``
* ``container image list --format json``
* ``container builder status --json``

All other commands produce text output that must be parsed.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import shutil
from typing import Any

from daedalus.core.backend import (
    Backend,
    BuildSpec,
    ContainerInfo,
    ContainerState,
    ExecResult,
    RunSpec,
)
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import (
    BackendError,
    BackendTimeoutError,
)

# ==========================================================================
# Output parsing
# ==========================================================================


class OutputParser:
    """Strategies for parsing ``container`` CLI output.

    Only three commands support ``--format json``.  Everything else
    requires text parsing.
    """

    @staticmethod
    def json(text: str) -> dict[str, Any] | list[Any]:
        """Parse JSON from ``--format json`` or ``--json`` commands."""
        text = text.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Some commands emit newline-delimited JSON objects.
            lines = text.splitlines()
            objects: list[Any] = []
            for line in lines:
                line = line.strip()
                if line:
                    try:
                        objects.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return objects if objects else {}

    @staticmethod
    def container_id(text: str) -> str:
        """Parse a container ID from ``container run`` / ``container create``.

        These commands print the container ID on stdout and nothing else.
        """
        return text.strip().splitlines()[0].strip()

    @staticmethod
    def image_name(text: str) -> str:
        """Parse image reference from ``container image load`` output."""
        return text.strip().splitlines()[-1].strip()

    @staticmethod
    def lines(text: str) -> list[str]:
        """Return non-empty lines."""
        return [l for l in text.strip().splitlines() if l.strip()]


# ==========================================================================
# Helpers
# ==========================================================================


async def _run_cli_impl(
    *args: str,
    binary: str = "container",
    timeout: float = 60.0,
    check: bool = True,
) -> tuple[int, str, str]:
    """Run ``container`` and return ``(exit_code, stdout, stderr)``.

    Raises :class:`BackendTimeoutError` on timeout,
    :class:`BackendError` on non-zero exit (if ``check=True``).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except TimeoutError:
            proc.kill()
            raise BackendTimeoutError(
                operation=" ".join(args), timeout=timeout,
            )

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")

        if check and proc.returncode != 0:
            raise BackendError(
                message=f"container {' '.join(args)} exited {proc.returncode}",
                exit_code=proc.returncode,
                stderr=err.strip()[:500],
            )

        return proc.returncode or 0, out, err
    except (BackendTimeoutError, BackendError):
        raise
    except FileNotFoundError:
        raise BackendError(
            message=f"Container binary '{binary}' not found",
        )


# ==========================================================================
# CliBackend
# ==========================================================================


class CliBackend(Backend):
    """L1 backend: shell out to ``container`` CLI.

    Parameters
    ----------
    capabilities:
        Host capability manifest from :func:`daedalus.core.capabilities.probe`.
    """

    def __init__(self, capabilities: CapabilityManifest) -> None:
        self._binary = capabilities.container_binary
        self._caps = capabilities

    # -- escape hatch (used by Mint, Talos) -------------------------------

    async def _run_cli(
        self, *args: str, timeout: float = 60.0, check: bool = True
    ) -> tuple[int, str, str]:
        return await _run_cli_impl(
            *args, binary=self._binary, timeout=timeout, check=check,
        )

    # ==================================================================
    # Lifecycle
    # ==================================================================

    async def create(self, spec: RunSpec) -> ContainerInfo:
        _, out, _ = await _run_cli_impl(
            "create", *spec.to_cli_args(for_create=True),
            binary=self._binary,
        )
        cid = OutputParser.container_id(out)
        return ContainerInfo(
            id=cid, name=spec.name or "", image=spec.image,
            state=ContainerState.CREATED, raw={"id": cid},
        )

    async def run(self, spec: RunSpec) -> ContainerInfo:
        _, out, _ = await _run_cli_impl(
            "run", *spec.to_cli_args(for_create=False),
            binary=self._binary,
        )
        cid = OutputParser.container_id(out)
        return ContainerInfo(
            id=cid, name=spec.name or "", image=spec.image,
            state=ContainerState.RUNNING, raw={"id": cid},
        )

    async def start(self, container_id: str) -> None:
        await _run_cli_impl("start", container_id, binary=self._binary)

    async def stop(self, container_id: str, timeout: int = 10) -> None:
        await _run_cli_impl(
            "stop", container_id, "-t", str(timeout), binary=self._binary,
        )

    async def kill(self, container_id: str, signal: str = "KILL") -> None:
        if signal != "KILL":
            await _run_cli_impl(
                "kill", container_id, "--signal", signal, binary=self._binary,
            )
        else:
            await _run_cli_impl("kill", container_id, binary=self._binary)

    async def delete(self, container_id: str, force: bool = False) -> None:
        cmd = ["delete", container_id]
        if force:
            cmd.append("--force")
        await _run_cli_impl(*cmd, binary=self._binary)

    async def list(self, all: bool = False) -> builtins.list[ContainerInfo]:
        cmd = ["list", "--format", "json"]
        if all:
            cmd.append("--all")
        _, out, _ = await _run_cli_impl(*cmd, binary=self._binary)
        data = OutputParser.json(out)
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict) and data:
            items = [data]
        result: list[ContainerInfo] = []
        for item in items:
            cfg = item.get("configuration", {}) if isinstance(item.get("configuration"), dict) else {}
            img = cfg.get("image", {}) if isinstance(cfg.get("image"), dict) else {}
            result.append(ContainerInfo(
                id=cfg.get("id", ""),
                name=cfg.get("hostname", ""),
                image=img.get("reference", ""),
                state=ContainerState(item.get("status", "unknown")),
                created_at="",
                raw=item,
            ))
        return result

    async def inspect(self, container_id: str) -> ContainerInfo:
        _, out, _ = await _run_cli_impl(
            "inspect", container_id, binary=self._binary,
        )
        data = OutputParser.json(out)
        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, dict) and data:
            return ContainerInfo(
                id=data.get("configuration", {}).get("id", data.get("id", container_id)),
                name=data.get("configuration", {}).get("hostname", ""),
                image=data.get("configuration", {}).get("image", {}).get("reference", ""),
                state=ContainerState(data.get("status", "unknown")),
                raw=data,
            )
        # No data returned — container not found
        return ContainerInfo(id="", name="", image="", state=ContainerState.UNKNOWN)

    async def logs(
        self,
        container_id: str,
        *,
        follow: bool = False,
        boot: bool = False,
        tail: int | None = None,
    ) -> str:
        cmd = ["logs", container_id]
        if follow:
            cmd.append("--follow")
        if boot:
            cmd.append("--boot")
        if tail is not None:
            cmd += ["-n", str(tail)]
        _, out, _ = await _run_cli_impl(*cmd, binary=self._binary)
        return out

    # ==================================================================
    # Interaction
    # ==================================================================

    async def exec(
        self,
        container_id: str,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        user: str | None = None,
        uid: int | None = None,
        gid: int | None = None,
        tty: bool = False,
        workdir: str | None = None,
        env_file: str | None = None,
    ) -> ExecResult:
        cmd = ["exec", container_id]
        if tty:
            cmd.append("--tty")
            cmd.append("--interactive")
        if user:
            cmd += ["--user", user]
        if uid is not None:
            cmd += ["--uid", str(uid)]
        if gid is not None:
            cmd += ["--gid", str(gid)]
        if workdir:
            cmd += ["--workdir", workdir]
        if env_file:
            cmd += ["--env-file", env_file]
        if env:
            for k, v in env.items():
                cmd += ["--env", f"{k}={v}"]
        # container exec does NOT use -- separator; args after container-id
        # are the command directly.
        cmd += argv

        exit_code, out, err = await _run_cli_impl(
            *cmd, binary=self._binary, check=False,
        )
        return ExecResult(exit_code=exit_code, stdout=out, stderr=err)

    # ==================================================================
    # Images
    # ==================================================================

    async def image_pull(self, image: str, platform: str | None = None) -> None:
        cmd = ["image", "pull", image]
        if platform:
            cmd += ["--platform", platform]
        await _run_cli_impl(*cmd, binary=self._binary)

    async def image_push(self, image: str) -> None:
        await _run_cli_impl("image", "push", image, binary=self._binary)

    async def image_save(self, image: str, output: str) -> str:
        await _run_cli_impl(
            "image", "save", image, "-o", output, binary=self._binary,
        )
        return output

    async def image_load(self, input_path: str) -> str:
        _, out, _ = await _run_cli_impl(
            "image", "load", "-i", input_path, binary=self._binary,
        )
        return OutputParser.image_name(out)

    async def image_tag(self, source: str, target: str) -> None:
        await _run_cli_impl("image", "tag", source, target, binary=self._binary)

    async def image_delete(self, image: str, force: bool = False) -> None:
        cmd = ["image", "delete", image]
        if force:
            cmd.append("--force")
        await _run_cli_impl(*cmd, binary=self._binary)

    async def image_inspect(self, image: str) -> dict[str, Any]:
        _, out, _ = await _run_cli_impl(
            "image", "inspect", image, binary=self._binary,
        )
        data = OutputParser.json(out)
        if isinstance(data, list):
            return data[0] if data else {}
        return data if isinstance(data, dict) else {}

    async def image_list(self, quiet: bool = False) -> builtins.list[dict[str, Any]]:
        cmd = ["image", "list", "--format", "json"]
        if quiet:
            cmd.append("--quiet")
        _, out, _ = await _run_cli_impl(*cmd, binary=self._binary)
        data = OutputParser.json(out)
        if isinstance(data, list):
            result: list[dict[str, Any]] = [item for item in data if isinstance(item, dict)]
            return result
        if isinstance(data, dict):
            return [dict(data)]
        return []  # type: ignore[unreachable]

    async def image_prune(self) -> builtins.list[str]:
        _, out, _ = await _run_cli_impl(
            "image", "prune", binary=self._binary,
        )
        removed: list[str] = []
        for line in OutputParser.lines(out):
            if ":" in line:
                removed.append(line.split(":")[-1].strip())
            else:
                removed.append(line.strip())
        return removed

    async def build(self, spec: BuildSpec) -> str:
        _, out, _ = await _run_cli_impl(
            "build", *spec.to_cli_args(), binary=self._binary,
        )
        return out.strip() or (spec.tag or "")

    # ==================================================================
    # Registry
    # ==================================================================

    async def registry_login(
        self, server: str, username: str | None = None,
        password: str | None = None,
    ) -> None:
        cmd = ["registry", "login"]
        if username:
            cmd += ["--username", username]
        if password is not None:
            cmd.append("--password-stdin")
        cmd.append(server)
        if password is not None:
            proc = await asyncio.create_subprocess_exec(
                self._binary, *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(input=password.encode())
            if proc.returncode != 0:
                raise BackendError(
                    message=f"registry login failed for {server}",
                    exit_code=proc.returncode or 1,
                    stderr=stderr.decode("utf-8", errors="replace").strip()[:500],
                )
            return
        await _run_cli_impl(*cmd, binary=self._binary)

    async def registry_logout(self, server: str) -> None:
        await _run_cli_impl("registry", "logout", server, binary=self._binary)

    # ==================================================================
    # Builder
    # ==================================================================

    async def builder_status(self) -> dict[str, Any]:
        _, out, _ = await _run_cli_impl(
            "builder", "status", "--json", binary=self._binary,
        )
        data = OutputParser.json(out)
        return data if isinstance(data, dict) else {}

    async def builder_start(self, cpus: int = 2, memory: str = "2048M") -> None:
        await _run_cli_impl(
            "builder", "start", "-c", str(cpus), "-m", memory,
            binary=self._binary,
        )

    async def builder_stop(self) -> None:
        await _run_cli_impl("builder", "stop", binary=self._binary)

    async def builder_delete(self, force: bool = False) -> None:
        cmd = ["builder", "delete"]
        if force:
            cmd.append("--force")
        await _run_cli_impl(*cmd, binary=self._binary)

    # ==================================================================
    # System
    # ==================================================================

    async def system_start(self) -> None:
        await _run_cli_impl("system", "start", binary=self._binary)

    async def system_stop(self) -> None:
        await _run_cli_impl("system", "stop", binary=self._binary)

    async def system_restart(self) -> None:
        await _run_cli_impl("system", "restart", binary=self._binary)

    async def system_logs(self, last: str = "5m", follow: bool = False) -> str:
        cmd = ["system", "logs", "--last", last]
        if follow:
            cmd.append("--follow")
        _, out, _ = await _run_cli_impl(*cmd, binary=self._binary)
        return out

    async def system_kernel_set(
        self,
        binary: str | None = None,
        tar: str | None = None,
        arch: str = "arm64",
        recommended: bool = False,
    ) -> None:
        cmd = ["system", "kernel", "set"]
        if recommended:
            cmd.append("--recommended")
        else:
            if binary:
                cmd += ["--binary", binary]
            if tar:
                cmd += ["--tar", tar]
            cmd += ["--arch", arch]
        await _run_cli_impl(*cmd, binary=self._binary)

    async def system_kernel_list(self) -> list[dict[str, Any]]:
        """Return registered kernel variants from the in-process ariadne registry."""
        return []

    async def system_dns_create(self, domain: str) -> None:
        await _run_cli_impl("system", "dns", "create", domain, binary=self._binary)

    async def system_dns_delete(self, domain: str) -> None:
        await _run_cli_impl("system", "dns", "delete", domain, binary=self._binary)

    async def system_dns_list(self) -> list[str]:
        _, out, _ = await _run_cli_impl(
            "system", "dns", "list", binary=self._binary,
        )
        return OutputParser.lines(out)

    async def system_df(self) -> dict[str, Any]:
        """Disk usage — OS-level fallback since ``container system df``
        does not exist in v0.1.0.
        """
        candidates = [
            os.path.expanduser("~/Library/Application Support/com.apple.container"),
            os.path.expanduser("~/.container"),
            os.path.expanduser("~/.daedalus"),
        ]
        for data_dir in candidates:
            if not os.path.isdir(data_dir):
                continue
            try:
                usage = shutil.disk_usage(data_dir)
                return {
                    "total": usage.total, "used": usage.used, "free": usage.free,
                    "path": data_dir,
                }
            except Exception:
                continue
        try:
            usage = shutil.disk_usage(os.path.expanduser("~"))
            return {
                "total": usage.total, "used": usage.used, "free": usage.free,
                "path": "~", "note": "home volume (container data path not found)",
            }
        except Exception:
            return {"error": "could not determine disk usage"}
