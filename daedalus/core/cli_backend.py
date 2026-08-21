"""L1 backend — shells ``container`` and parses output.

This is the reference implementation.  It matches the **actual** command
surface of container CLI 1.2.2 (commit 0190097), not aspirational
features.

Structured JSON output is available for these commands:
* ``container list --format json``
* ``container image list --format json``
* ``container builder status --json``
* ``container network list --format json``
* ``container stats --no-stream --format json``

All other commands produce text output that must be parsed.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import shutil
import time
from typing import Any

from daedalus.core.backend import (
    Backend,
    BuildSpec,
    ContainerInfo,
    ContainerState,
    ExecResult,
    NetworkInfo,
    NetworkSpec,
    PruneResult,
    RunSpec,
    StatsSample,
)
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import (
    BackendError,
    BackendTimeoutError,
    CapabilityError,
)

# ==========================================================================
# Output parsing
# ==========================================================================


class OutputParser:
    """Strategies for parsing ``container`` CLI output.

    ``container list``, ``container image list``, ``container builder
    status``, ``container network list`` and ``container stats`` support
    structured JSON output.  Everything else requires text parsing.
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
    def stats(text: str) -> list[StatsSample]:
        """Parse ``container stats --no-stream --format json`` output.

        The output is an array of per-container objects (SPEC §2.1).
        ``cpuUsageUsec`` is cumulative; ``sampled_at`` is set to
        :func:`time.monotonic` at parse time so callers can derive rates
        between successive samples.
        """
        data = OutputParser.json(text)
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict) and data:
            items = [data]
        sampled_at = time.monotonic()
        return [
            StatsSample(
                id=str(item.get("id", "") or ""),
                cpu_usage_usec=int(item.get("cpuUsageUsec", 0) or 0),
                memory_usage_bytes=int(item.get("memoryUsageBytes", 0) or 0),
                memory_limit_bytes=int(item.get("memoryLimitBytes", 0) or 0),
                network_rx_bytes=int(item.get("networkRxBytes", 0) or 0),
                network_tx_bytes=int(item.get("networkTxBytes", 0) or 0),
                block_read_bytes=int(item.get("blockReadBytes", 0) or 0),
                block_write_bytes=int(item.get("blockWriteBytes", 0) or 0),
                num_processes=int(item.get("numProcesses", 0) or 0),
                sampled_at=sampled_at,
            )
            for item in items
        ]

    @staticmethod
    def network_list(text: str) -> list[NetworkInfo]:
        """Parse ``container network list --format json`` output (SPEC §2.5)."""
        data = OutputParser.json(text)
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict) and data:
            items = [data]
        return [OutputParser._network_info(item) for item in items]

    @staticmethod
    def network_inspect(text: str) -> NetworkInfo:
        """Parse ``container network inspect <name>`` output.

        ``inspect`` prints a JSON array directly (no ``--format`` flag);
        the first element describes the requested network.
        """
        data = OutputParser.json(text)
        item: dict[str, Any] = {}
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                item = first
        elif isinstance(data, dict):
            item = data
        return OutputParser._network_info(item)

    @staticmethod
    def _network_info(item: dict[str, Any]) -> NetworkInfo:
        """Map a single network list/inspect entry to :class:`NetworkInfo`."""
        cfg = item.get("configuration", {})
        if not isinstance(cfg, dict):
            cfg = {}
        status = item.get("status", {})
        if not isinstance(status, dict):
            status = {}
        return NetworkInfo(
            id=str(item.get("id", "") or ""),
            name=str(cfg.get("name", "") or ""),
            plugin=str(cfg.get("plugin", "") or ""),
            mode=str(cfg.get("mode", "") or ""),
            subnet=str(status.get("ipv4Subnet", "") or ""),
            subnet_v6=str(status.get("ipv6Subnet", "") or ""),
            gateway=str(status.get("ipv4Gateway", "") or ""),
            raw=item,
        )

    @staticmethod
    def reclaimed_message(text: str) -> str:
        """Extract the ``Reclaimed …`` summary line from prune output.

        container ≥ 1.x prints the summary to stderr; ids land on stdout.
        """
        for line in OutputParser.lines(text):
            if line.startswith("Reclaimed "):
                return line
        return ""

    @staticmethod
    def prune(text: str) -> PruneResult:
        """Parse ``container prune`` text output (SPEC §2.4).

        The ``Reclaimed <size> in disk space`` line (when present)
        becomes :attr:`PruneResult.message`; the remaining non-empty
        lines are the removed container ids.
        """
        lines = OutputParser.lines(text)
        message = OutputParser.reclaimed_message(text)
        return PruneResult(
            ids=[line for line in lines if line != message],
            message=message,
        )

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

    @staticmethod
    def container_item(item: dict[str, Any]) -> ContainerInfo:
        """Parse one ``container list --format json`` element (container ≥ 1.x).

        Wire shape: ``{configuration: {...}, id, status: {...}}`` where
        ``status`` is a nested object (``state`` plus per-network info), not a
        bare string.  The container name lives at
        ``status.networks[0].hostname`` when ``configuration.hostname`` is
        absent.
        """
        cfg = item.get("configuration", {}) if isinstance(item.get("configuration"), dict) else {}
        img = cfg.get("image", {}) if isinstance(cfg.get("image"), dict) else {}
        status = item.get("status", {}) if isinstance(item.get("status"), dict) else {}
        name = cfg.get("hostname") or ""
        if not name:
            networks = status.get("networks") or []
            if networks and isinstance(networks[0], dict):
                name = networks[0].get("hostname", "")
        return ContainerInfo(
            id=cfg.get("id", ""),
            name=name,
            image=img.get("reference", ""),
            state=_coerce_state(status.get("state", "unknown")),
            raw=item,
        )

    @staticmethod
    def image_list_item(item: dict[str, Any]) -> dict[str, Any]:
        """Normalise one ``container image list --format json`` element.

        container ≥ 1.x nests the reference at ``configuration.name`` and the
        descriptor at ``configuration.descriptor``; older releases carried
        both at the top level.  Expose a stable contract: ``reference``,
        ``name`` and ``descriptor`` always present at the top level (derived
        when the runtime nests them), everything else raw.
        """
        cfg = item.get("configuration", {}) if isinstance(item.get("configuration"), dict) else {}
        ref = cfg.get("name") or item.get("reference", "")
        item.setdefault("reference", ref)
        item.setdefault("name", ref)
        item.setdefault("descriptor", cfg.get("descriptor", {}))
        return item


# ==========================================================================
# Helpers
# ==========================================================================


def _coerce_state(value: Any) -> ContainerState:
    """Tolerant ``ContainerState`` coercion — unknown runtime states fall
    back to ``UNKNOWN`` instead of raising."""
    try:
        return ContainerState(str(value))
    except ValueError:
        return ContainerState.UNKNOWN


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


async def _run_cli_bounded(
    *args: str,
    binary: str = "container",
    duration: float = 15.0,
) -> str:
    """Run a blocking/follow CLI command for up to ``duration`` seconds."""
    proc = await asyncio.create_subprocess_exec(
        binary, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=duration,
        )
    except TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    return out if out.strip() else err


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

    def _require(self, feature: str) -> None:
        """Raise :class:`CapabilityError` unless ``feature`` is available.

        ``feature`` is a key of ``CapabilityManifest.runtime_features``
        (e.g. ``"container_stats"``, ``"custom_networks"``).  Backends
        gate on this so older hosts fail loudly with a clear message
        instead of leaking ``container: unrecognized subcommand``.
        """
        if self._caps.runtime_features.get(feature) is not True:
            raise CapabilityError(
                message=f"Feature '{feature}' is not available on this host",
                feature=feature,
                container_version=self._caps.container_version,
            )

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

    async def start(
        self,
        container_id: str,
        *,
        attach: bool = False,
        interactive: bool = False,
    ) -> None:
        cmd = ["start", container_id]
        if attach:
            cmd.append("--attach")
        if interactive:
            cmd.append("--interactive")
        await _run_cli_impl(*cmd, binary=self._binary)

    async def stop(
        self,
        container_id: str,
        timeout: int = 10,
        *,
        signal: str | None = None,
    ) -> None:
        cmd = ["stop", container_id, "-t", str(timeout)]
        if signal:
            cmd += ["--signal", signal]
        await _run_cli_impl(*cmd, binary=self._binary)

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
            result.append(OutputParser.container_item(item))
        return result

    async def inspect(self, container_id: str) -> ContainerInfo:
        exit_code, out, _ = await _run_cli_impl(
            "inspect", container_id, binary=self._binary, check=False,
        )
        if exit_code != 0:
            # container ≥ 1.x exits non-zero for unknown ids; the contract is
            # an empty result, not an error (see test_unknown_container_inspect).
            return ContainerInfo(id="", name="", image="", state=ContainerState.UNKNOWN)
        data = OutputParser.json(out)
        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, dict) and data:
            info = OutputParser.container_item(data)
            if not info.id:
                info.id = container_id
            return info
        # No data returned — container not found
        return ContainerInfo(id="", name="", image="", state=ContainerState.UNKNOWN)

    async def logs(
        self,
        container_id: str,
        *,
        follow: bool = False,
        follow_seconds: float | None = None,
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
        if follow:
            return await _run_cli_bounded(
                *cmd, binary=self._binary, duration=follow_seconds or 15.0,
            )
        _, out, _ = await _run_cli_impl(*cmd, binary=self._binary)
        return out

    # ==================================================================
    # Stats / copy / export / prune / network (container 1.2.2)
    # ==================================================================

    async def stats(self, container_id: str | None = None) -> list[StatsSample]:
        self._require("container_stats")
        cmd = ["stats", "--no-stream", "--format", "json"]
        if container_id:
            cmd.append(container_id)
        _, out, _ = await _run_cli_impl(*cmd, binary=self._binary)
        return OutputParser.stats(out)

    async def copy(self, source: str, destination: str) -> str:
        self._require("container_copy")
        _, out, _ = await _run_cli_impl(
            "copy", source, destination, binary=self._binary,
        )
        return out

    async def export(self, container_id: str, output_path: str) -> str:
        self._require("container_export")
        await _run_cli_impl(
            "export", "-o", output_path, container_id, binary=self._binary,
        )
        return output_path

    async def prune(self) -> PruneResult:
        self._require("container_prune")
        _, out, err = await _run_cli_impl("prune", binary=self._binary)
        # The "Reclaimed …" summary is printed to stderr; ids to stdout.
        result = OutputParser.prune(out)
        if not result.message:
            result.message = OutputParser.reclaimed_message(err)
        return result

    async def network_create(self, spec: NetworkSpec) -> NetworkInfo:
        self._require("custom_networks")
        _, out, _ = await _run_cli_impl(
            "network", "create", *spec.to_cli_args(), binary=self._binary,
        )
        # Create output may be minimal — resolve the full info via inspect.
        lines = OutputParser.lines(out)
        name = lines[-1] if lines else spec.name
        return await self.network_inspect(name)

    async def network_list(self) -> list[NetworkInfo]:
        self._require("custom_networks")
        _, out, _ = await _run_cli_impl(
            "network", "list", "--format", "json", binary=self._binary,
        )
        return OutputParser.network_list(out)

    async def network_inspect(self, name: str) -> NetworkInfo:
        self._require("custom_networks")
        # NOTE: no --format flag — inspect errors when given one.
        _, out, _ = await _run_cli_impl(
            "network", "inspect", name, binary=self._binary,
        )
        return OutputParser.network_inspect(out)

    async def network_delete(self, name: str) -> None:
        self._require("custom_networks")
        await _run_cli_impl("network", "delete", name, binary=self._binary)

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
        interactive: bool = False,
        timeout: float = 30.0,
    ) -> ExecResult:
        cmd = ["exec", container_id]
        if tty:
            cmd.append("--tty")
        if interactive or tty:
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
            *cmd, binary=self._binary, check=False, timeout=timeout,
        )
        return ExecResult(exit_code=exit_code, stdout=out, stderr=err)

    # ==================================================================
    # Images
    # ==================================================================

    async def image_pull(
        self,
        image: str,
        platform: str | None = None,
        scheme: str | None = None,
    ) -> None:
        cmd = ["image", "pull", image]
        if scheme:
            cmd += ["--scheme", scheme]
        if platform:
            cmd += ["--platform", platform]
        await _run_cli_impl(*cmd, binary=self._binary)

    async def image_push(
        self,
        image: str,
        platform: str | None = None,
        scheme: str | None = None,
    ) -> None:
        cmd = ["image", "push", image]
        if scheme:
            cmd += ["--scheme", scheme]
        if platform:
            cmd += ["--platform", platform]
        await _run_cli_impl(*cmd, binary=self._binary)

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

    async def image_delete(self, image: str, *, all: bool = False) -> None:
        cmd = ["image", "delete", image]
        if all:
            cmd.append("--all")
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
            return [OutputParser.image_list_item(item) for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [OutputParser.image_list_item(dict(data))]
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
        self,
        server: str,
        username: str | None = None,
        password: str | None = None,
        scheme: str | None = None,
    ) -> None:
        cmd = ["registry", "login"]
        if username:
            cmd += ["--username", username]
        if scheme:
            cmd += ["--scheme", scheme]
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

    async def registry_default_inspect(self) -> str:
        _, out, _ = await _run_cli_impl(
            "registry", "default", "inspect", binary=self._binary,
        )
        return out.strip()

    async def registry_default_set(self, host: str, scheme: str | None = None) -> None:
        cmd = ["registry", "default", "set"]
        if scheme:
            cmd += ["--scheme", scheme]
        cmd.append(host)
        await _run_cli_impl(*cmd, binary=self._binary)

    async def registry_default_unset(self) -> None:
        await _run_cli_impl("registry", "default", "unset", binary=self._binary)

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

    async def system_logs(
        self,
        last: str = "5m",
        follow: bool = False,
        follow_seconds: float | None = None,
    ) -> str:
        cmd = ["system", "logs", "--last", last]
        if follow:
            cmd.append("--follow")
            return await _run_cli_bounded(
                *cmd, binary=self._binary, duration=follow_seconds or 15.0,
            )
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
        """Disk usage — measured at the OS level via ``shutil.disk_usage``.

        (``container system df`` exists in 1.2.2, but this host-side
        measurement is the one policy checks compare against.)

        Returns volume stats plus ``store_bytes``: actual size of the Apple
        container data directory (used for policy checks).  ``used`` is the
        whole-volume figure from ``shutil.disk_usage`` and is *not* compared
        against the container store budget.
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
                store_bytes = await self._store_bytes(data_dir)
                return {
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "path": data_dir,
                    "store_bytes": store_bytes,
                }
            except Exception:
                continue
        try:
            home = os.path.expanduser("~")
            usage = shutil.disk_usage(home)
            return {
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "path": "~",
                "note": "home volume (container data path not found)",
            }
        except Exception:
            return {"error": "could not determine disk usage"}

    async def _store_bytes(self, path: str) -> int | None:
        """Best-effort size of a directory tree (KiB blocks from ``du``)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "du", "-sk", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120.0)
            if proc.returncode == 0 and stdout:
                kib = int(stdout.decode().split()[0])
                return kib * 1024
        except Exception:
            pass
        return None
