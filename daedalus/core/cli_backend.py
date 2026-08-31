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
* ``container system version --format json``
* ``container system df --format json``

All other commands produce text output that must be parsed
(``container system property list`` emits TOML).
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import shutil
import time
import tomllib
from typing import Any

from daedalus.core.backend import (
    Backend,
    BuildSpec,
    ClusterCreateSpec,
    ContainerInfo,
    ContainerState,
    ExecResult,
    MachineCreateSpec,
    MachineInfo,
    MachineSetSettings,
    NetworkInfo,
    NetworkSpec,
    PruneResult,
    RunSpec,
    StatsSample,
    VolumeInfo,
    VolumeSpec,
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
    def registry_list(text: str) -> list[str]:
        """Parse ``container registry list --format json`` (1.3.x).

        Element shape is ``[{...}]`` (empty when no logins); the server name
        is read from ``server``/``registry``/``host`` keys, tolerant to
        unknown shapes.
        """
        data = OutputParser.json(text)
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [i for i in data if isinstance(i, dict)]
        elif isinstance(data, dict) and data:
            items = [data]
        servers: list[str] = []
        for item in items:
            for key in ("server", "registry", "host", "name"):
                val = item.get(key)
                if val and str(val).strip():
                    servers.append(str(val))
                    break
        return servers

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

    # -- machines (container 1.2.2) --------------------------------------

    @staticmethod
    def machine_id(text: str) -> str:
        """Parse the echoed machine id from ``machine create`` output.

        The CLI prints the machine id on stdout (and nothing else).
        """
        lines = OutputParser.lines(text)
        return lines[0] if lines else ""

    @staticmethod
    def machine_list(text: str) -> list[MachineInfo]:
        """Parse ``machine list --format json`` output (SPEC §2.1).

        ``memory`` and ``diskSize`` are **bytes** on the wire.
        """
        data = OutputParser.json(text)
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict) and data:
            items = [data]
        return [OutputParser._machine_info_from(item) for item in items]

    @staticmethod
    def _machine_info_from(item: dict[str, Any]) -> MachineInfo:
        """Map the common fields of one machine list/inspect element."""
        return MachineInfo(
            id=str(item.get("id", "") or ""),
            status=str(item.get("status", "") or ""),
            cpus=int(item.get("cpus", 0) or 0),
            memory_bytes=int(item.get("memory", 0) or 0),
            disk_size_bytes=int(item.get("diskSize", 0) or 0),
            default=bool(item.get("default", False)),
            created_date=str(item.get("createdDate", "") or ""),
            raw=item,
        )

    @staticmethod
    def machine_inspect(text: str) -> MachineInfo:
        """Parse ``machine inspect <id>`` output (SPEC §2.1).

        Inspect prints a JSON array directly (no ``--format`` flag); the
        first element describes the requested machine.  The richer
        inspect-only fields (homeMount, image.reference, platform.*,
        userSetup.*) are merged over the shared list fields.
        """
        data = OutputParser.json(text)
        item: dict[str, Any] = {}
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                item = first
        elif isinstance(data, dict):
            item = data
        info = OutputParser._machine_info_from(item)
        image = item.get("image", {})
        if not isinstance(image, dict):
            image = {}
        platform = item.get("platform", {})
        if not isinstance(platform, dict):
            platform = {}
        user = item.get("userSetup", {})
        if not isinstance(user, dict):
            user = {}
        info.home_mount = str(item.get("homeMount", "") or "")
        info.image_ref = str(image.get("reference", "") or "")
        info.platform_arch = str(platform.get("architecture", "") or "")
        info.platform_os = str(platform.get("os", "") or "")
        info.user_username = str(user.get("username", "") or "")
        info.user_uid = int(user.get("uid", 0) or 0)
        info.user_gid = int(user.get("gid", 0) or 0)
        return info

    # -- volumes (container 1.2.2) ----------------------------------------

    @staticmethod
    def volume_id(text: str) -> str:
        """Parse the echoed volume name from ``volume create`` output.

        The CLI prints the volume name on stdout (and nothing else).
        """
        lines = OutputParser.lines(text)
        return lines[0] if lines else ""

    @staticmethod
    def volume_list(text: str) -> list[VolumeInfo]:
        """Parse ``volume list --format json`` output (SPEC §2).

        The top-level ``id`` and ``configuration.name`` are both the
        volume name; ``options.size`` carries the human size spec
        (e.g. ``"100M"``) alongside the byte count ``sizeInBytes``.
        """
        data = OutputParser.json(text)
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict) and data:
            items = [data]
        return [OutputParser._volume_info(item) for item in items]

    @staticmethod
    def volume_inspect(text: str) -> VolumeInfo:
        """Parse ``volume inspect <name>`` output (SPEC §2).

        Inspect prints a JSON array directly (no ``--format`` flag); the
        first element describes the requested volume.
        """
        data = OutputParser.json(text)
        item: dict[str, Any] = {}
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                item = first
        elif isinstance(data, dict):
            item = data
        return OutputParser._volume_info(item)

    @staticmethod
    def _volume_info(item: dict[str, Any]) -> VolumeInfo:
        """Map a single volume list/inspect entry to :class:`VolumeInfo`."""
        cfg = item.get("configuration", {})
        if not isinstance(cfg, dict):
            cfg = {}
        options = cfg.get("options", {})
        if not isinstance(options, dict):
            options = {}
        labels = cfg.get("labels", {})
        if not isinstance(labels, dict):
            labels = {}
        return VolumeInfo(
            id=str(item.get("id", "") or ""),
            name=str(cfg.get("name", "") or ""),
            driver=str(cfg.get("driver", "") or ""),
            format=str(cfg.get("format", "") or ""),
            size_bytes=int(cfg.get("sizeInBytes", 0) or 0),
            size_spec=str(options.get("size", "") or ""),
            source=str(cfg.get("source", "") or ""),
            created_date=str(cfg.get("creationDate", "") or ""),
            labels={str(k): str(v) for k, v in labels.items()},
            options={str(k): str(v) for k, v in options.items()},
            raw=item,
        )

    # -- k8s plugin (container 1.3.1) --------------------------------------

    @staticmethod
    def k8s_nodes(text: str) -> list[dict[str, Any]]:
        """Parse ``container list --format json`` and keep k8s node containers.

        ``container k8s list`` renders a human table only (no JSON form),
        so the backend reads the raw container list and filters it
        client-side — the same source Orchard's ClusterService groups
        from (SPEC phase 8 §2).  Cluster node containers are marked with
        the plugin label ``com.apple.container.plugin == "k8s"`` (the
        spec's ``owningPlugin: k8s``); their ``pluginRole``
        (control-plane|worker) rides along in the raw dicts for the
        engine to group on.
        """
        data = OutputParser.json(text)
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict) and data:
            items = [data]
        nodes: list[dict[str, Any]] = []
        for item in items:
            cfg = item.get("configuration", {})
            if not isinstance(cfg, dict):
                cfg = {}
            labels = cfg.get("labels", {})
            if isinstance(labels, dict) and labels.get("com.apple.container.plugin") == "k8s":
                nodes.append(item)
        return nodes

    # -- system (SPEC phase 9 §2 — captured live on container 1.3.1) ----

    @staticmethod
    def system_properties(text: str) -> dict[str, str]:
        """Parse ``container system property list`` output into a flat dict.

        The live 1.3.1 output is TOML (the ``--format`` default): sectioned
        tables like ``[kernel]`` with ``binaryPath = "…"`` entries.  Tables
        are flattened with dot notation (``build.cpus`` → ``"2"``) and
        every value is stringified (booleans → ``"true"``/``"false"``), so
        the result is a display-ready ``dict[str, str]``.  Empty tables
        (``[dns]``, ``[network]``) contribute no keys.  Unparseable input
        (e.g. a host that never emits TOML) yields ``{}``.
        """
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {}
        flat: dict[str, str] = {}

        def walk(node: Any, prefix: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    child = f"{prefix}.{key}" if prefix else str(key)
                    walk(value, child)
            elif isinstance(node, bool):
                # Match the TOML source spelling (``true``/``false``) —
                # Python's str() would render ``True``/``False``.
                flat[prefix] = "true" if node else "false"
            else:
                flat[prefix] = str(node)

        walk(data, "")
        return flat

    @staticmethod
    def system_version(text: str) -> dict[str, Any]:
        """Parse ``container system version --format json`` output.

        The wire is a JSON array of ``{appName, buildType, commit,
        version}`` objects (one per service — ``container`` and
        ``container-apiserver`` on 1.3.1).  The result is keyed by
        ``appName`` so consumers can look up either service.
        """
        data = OutputParser.json(text)
        result: dict[str, Any] = {}
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = [data]
        for item in items:
            if isinstance(item, dict) and item.get("appName"):
                result[str(item["appName"])] = item
        return result

    @staticmethod
    def system_df_json(text: str) -> dict[str, Any]:
        """Parse ``container system df --format json`` output (SPEC phase 9 §2).

        Live 1.3.1 shape — per-category objects carrying the runtime's own
        store accounting::

            {"containers": {"active": 0, "reclaimable": …, "sizeInBytes": …, "total": 1},
             "images":     {"active": 1, "reclaimable": …, "sizeInBytes": …, "total": 14},
             "volumes":    {"active": 0, "reclaimable": …, "sizeInBytes": …, "total": 0}}

        ``used`` is the sum of the per-category ``sizeInBytes`` (the store
        footprint).  The df JSON reports **no free-space or capacity
        figure**, so ``total`` mirrors ``used`` here and ``free`` is
        omitted — the backend merges host-volume measurements for those
        keys (policy's min-free check needs real free space).  Non-JSON or
        unexpected output yields ``{}``.
        """
        data = OutputParser.json(text)
        if not isinstance(data, dict):
            return {}
        categories: dict[str, dict[str, Any]] = {}
        for name in ("containers", "images", "volumes"):
            cat = data.get(name)
            if isinstance(cat, dict):
                categories[name] = {str(k): v for k, v in cat.items()}
        used = sum(
            int(cat.get("sizeInBytes", 0) or 0) for cat in categories.values()
        )
        if not categories:
            return {}
        return {
            "containers": categories.get("containers", {}),
            "images": categories.get("images", {}),
            "volumes": categories.get("volumes", {}),
            "used": used,
            "total": used,
        }


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
    input_text: str | None = None,
) -> tuple[int, str, str]:
    """Run ``container`` and return ``(exit_code, stdout, stderr)``.

    When ``input_text`` is given, it is fed to the process's stdin
    (stdin is piped only then — existing call sites keep the exact same
    subprocess shape).  Used for ``registry login --password-stdin``.

    Raises :class:`BackendTimeoutError` on timeout,
    :class:`BackendError` on non-zero exit (if ``check=True``).
    """
    try:
        if input_text is None:
            proc = await asyncio.create_subprocess_exec(
                binary, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdin: bytes | None = None
        else:
            proc = await asyncio.create_subprocess_exec(
                binary, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdin = input_text.encode()
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin), timeout=timeout,
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
        *,
        timeout: int = 10,
        signal: str | None = None,
        all: bool = False,
    ) -> None:
        if all:
            # stop -a — container_id is ignored/empty in this mode.
            await _run_cli_impl("stop", "-a", binary=self._binary)
            return
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

    async def delete(
        self, container_id: str, *, force: bool = False, all: bool = False,
    ) -> None:
        if all:
            # delete -a — container_id is ignored/empty in this mode.
            await _run_cli_impl("delete", "-a", binary=self._binary)
            return
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
    # Machines (container 1.2.2)
    # ==================================================================

    async def machine_create(self, spec: MachineCreateSpec) -> MachineInfo:
        self._require("container_machines")
        _, out, _ = await _run_cli_impl(
            "machine", "create", *spec.to_cli_args(), binary=self._binary,
        )
        mid = OutputParser.machine_id(out)
        if not mid:
            return MachineInfo(id="")
        # Create echoes only the id — resolve the full info via inspect.
        return await self.machine_inspect(mid)

    async def machine_list(self) -> list[MachineInfo]:
        self._require("container_machines")
        _, out, _ = await _run_cli_impl(
            "machine", "list", "--format", "json", binary=self._binary,
        )
        return OutputParser.machine_list(out)

    async def machine_inspect(self, machine_id: str) -> MachineInfo:
        self._require("container_machines")
        # NOTE: no --format flag — machine inspect emits a JSON array.
        _, out, _ = await _run_cli_impl(
            "machine", "inspect", machine_id, binary=self._binary,
        )
        return OutputParser.machine_inspect(out)

    async def machine_delete(self, machine_id: str) -> None:
        self._require("container_machines")
        await _run_cli_impl("machine", "delete", machine_id, binary=self._binary)

    async def machine_stop(self, machine_id: str) -> None:
        self._require("container_machines")
        await _run_cli_impl("machine", "stop", machine_id, binary=self._binary)

    async def machine_set(
        self, machine_id: str, settings: MachineSetSettings,
    ) -> None:
        self._require("container_machines")
        await _run_cli_impl(
            "machine", "set", "-n", machine_id, *settings.to_set_args(),
            binary=self._binary,
        )

    async def machine_set_default(self, machine_id: str) -> None:
        self._require("container_machines")
        await _run_cli_impl(
            "machine", "set-default", machine_id, binary=self._binary,
        )

    async def machine_run(
        self,
        machine_id: str,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        gid: int | None = None,
        interactive: bool = False,
        timeout: float = 60.0,
        root: bool = False,
        ulimit: builtins.list[str] | None = None,
        detached: bool = False,
        tty: bool = False,
        user: str | None = None,
        workdir: str | None = None,
    ) -> ExecResult:
        self._require("container_machines")
        cmd = ["machine", "run", "-n", machine_id]
        if env:
            for k, v in env.items():
                cmd += ["-e", f"{k}={v}"]
        if gid is not None:
            cmd += ["--gid", str(gid)]
        if interactive:
            cmd.append("-i")
        if root:
            cmd.append("--root")
        if ulimit is not None:
            for u in ulimit:
                cmd += ["--ulimit", u]
        if detached:
            cmd.append("-d")
        if tty:
            cmd.append("-t")
        if user:
            cmd += ["-u", user]
        if workdir:
            cmd += ["-w", workdir]
        cmd += argv
        # check=False — run captures the command's exit code, not the CLI's.
        exit_code, out, err = await _run_cli_impl(
            *cmd, binary=self._binary, check=False, timeout=timeout,
        )
        return ExecResult(exit_code=exit_code, stdout=out, stderr=err)

    async def machine_logs(
        self,
        machine_id: str,
        *,
        boot: bool = False,
        follow: bool = False,
        tail: int | None = None,
    ) -> str:
        self._require("container_machines")
        cmd = ["machine", "logs", machine_id]
        if boot:
            cmd.append("--boot")
        if follow:
            cmd.append("-f")
        if tail is not None:
            cmd += ["-n", str(tail)]
        if follow:
            return await _run_cli_bounded(
                *cmd, binary=self._binary, duration=15.0,
            )
        _, out, _ = await _run_cli_impl(*cmd, binary=self._binary)
        return out

    # ==================================================================
    # Volumes (container 1.2.2)
    # ==================================================================

    async def volume_create(self, spec: VolumeSpec) -> VolumeInfo:
        self._require("container_volumes")
        _, out, _ = await _run_cli_impl(
            "volume", "create", *spec.to_cli_args(), binary=self._binary,
        )
        name = OutputParser.volume_id(out)
        if not name:
            return VolumeInfo(id="")
        # Create echoes only the name — resolve the full info via inspect.
        return await self.volume_inspect(name)

    async def volume_list(self) -> list[VolumeInfo]:
        self._require("container_volumes")
        _, out, _ = await _run_cli_impl(
            "volume", "list", "--format", "json", binary=self._binary,
        )
        return OutputParser.volume_list(out)

    async def volume_inspect(self, name: str) -> VolumeInfo:
        self._require("container_volumes")
        # NOTE: no --format flag — volume inspect emits a JSON array.
        _, out, _ = await _run_cli_impl(
            "volume", "inspect", name, binary=self._binary,
        )
        return OutputParser.volume_inspect(out)

    async def volume_delete(self, name: str) -> None:
        self._require("container_volumes")
        await _run_cli_impl("volume", "delete", name, binary=self._binary)

    async def volume_prune(self) -> list[str]:
        self._require("container_volumes")
        # check=False — an empty prune output (nothing to remove) is a
        # successful run, not an error; the pruned names land on stdout.
        _, out, _ = await _run_cli_impl(
            "volume", "prune", binary=self._binary, check=False,
        )
        return OutputParser.lines(out)

    # ==================================================================
    # k8s plugin (container 1.3.1)
    # ==================================================================

    async def k8s_create(self, spec: ClusterCreateSpec) -> str:
        self._require("container_k8s")
        _, out, _ = await _run_cli_impl(
            "k8s", "create", *spec.to_cli_args(), binary=self._binary,
        )
        # The CLI echoes the cluster name on stdout when it does;
        # otherwise the requested name is authoritative (verified: the
        # name is set via --name — there is no positional argument).
        lines = OutputParser.lines(out)
        return lines[0] if lines else spec.name

    async def k8s_list(self) -> list[dict[str, Any]]:
        self._require("container_k8s")
        _, out, _ = await _run_cli_impl(
            "list", "--format", "json", binary=self._binary,
        )
        return OutputParser.k8s_nodes(out)

    async def k8s_delete(self, name: str) -> None:
        self._require("container_k8s")
        await _run_cli_impl("k8s", "delete", "--name", name, binary=self._binary)

    async def k8s_start(self, name: str) -> None:
        self._require("container_k8s")
        await _run_cli_impl("k8s", "start", "--name", name, binary=self._binary)

    async def k8s_load_image(
        self, name: str, image: str, *, platform: str | None = None,
    ) -> None:
        self._require("container_k8s")
        cmd = ["k8s", "load-image", "--name", name, image]
        if platform:
            cmd += ["--platform", platform]
        await _run_cli_impl(*cmd, binary=self._binary)

    async def k8s_write_config(
        self, name: str, *, kubeconfig: str | None = None,
    ) -> str:
        self._require("container_k8s")
        cmd = ["k8s", "write-config", "--name", name]
        if kubeconfig:
            # Explicit path: the CLI writes/merges the file — return it.
            cmd += ["--kubeconfig", kubeconfig]
            await _run_cli_impl(*cmd, binary=self._binary)
            return kubeconfig
        # No path: the CLI prints the config text — return it as-is.
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
        interactive: bool = False,
        detached: bool = False,
        ulimit: builtins.list[str] | None = None,
        workdir: str | None = None,
        env_file: str | None = None,
        timeout: float = 30.0,
    ) -> ExecResult:
        # Option flags must PRECEDE the container id: container ≥ 1.x treats
        # every argument after <id> as the executable + its arguments (there
        # is no `--` separator).  Verified live: `container exec -t <id> ...`
        # works, `<id> -t ...` fails with "target executable" errors.
        cmd = ["exec"]
        if tty:
            cmd.append("--tty")
        if interactive or tty:
            cmd.append("--interactive")
        if detached:
            cmd.append("-d")
        if user:
            cmd += ["--user", user]
        if uid is not None:
            cmd += ["--uid", str(uid)]
        if gid is not None:
            cmd += ["--gid", str(gid)]
        if ulimit is not None:
            for u in ulimit:
                cmd += ["--ulimit", u]
        if workdir:
            cmd += ["--workdir", workdir]
        if env_file:
            cmd += ["--env-file", env_file]
        if env:
            for k, v in env.items():
                cmd += ["--env", f"{k}={v}"]
        cmd.append(container_id)
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
        password_stdin: str | None = None,
        scheme: str | None = None,
    ) -> None:
        cmd = ["registry", "login"]
        if username:
            cmd += ["-u", username]
        if scheme:
            cmd += ["--scheme", scheme]
        if password is not None or password_stdin is not None:
            cmd.append("--password-stdin")
        cmd.append(server)
        if password is not None:
            # Existing password path: feed --password-stdin directly via a
            # dedicated subprocess (kept identical to pre-phase-7 behavior).
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
        if password_stdin is not None:
            await _run_cli_impl(*cmd, binary=self._binary, input_text=password_stdin)
            return
        await _run_cli_impl(*cmd, binary=self._binary)

    async def registry_logout(self, server: str) -> None:
        await _run_cli_impl("registry", "logout", server, binary=self._binary)

    async def registry_list(self) -> list[str]:
        """Logged-in registry servers (``container registry list``)."""
        _, out, _ = await _run_cli_impl(
            "registry", "list", "--format", "json", binary=self._binary,
        )
        return OutputParser.registry_list(out)

    async def registry_default_inspect(self) -> str:
        _, out, err = await _run_cli_impl(
            "registry", "default", "inspect", binary=self._binary,
            check=False,
        )
        self._registry_default_guard(out + err)
        return out.strip()

    async def registry_default_set(self, host: str, scheme: str | None = None) -> None:
        cmd = ["registry", "default", "set"]
        if scheme:
            cmd += ["--scheme", scheme]
        cmd.append(host)
        _, out, err = await _run_cli_impl(*cmd, binary=self._binary, check=False)
        self._registry_default_guard(out + err)

    async def registry_default_unset(self) -> None:
        _, out, err = await _run_cli_impl(
            "registry", "default", "unset", binary=self._binary, check=False,
        )
        self._registry_default_guard(out + err)

    def _registry_default_guard(self, output: str) -> None:
        """Fail loudly when the runtime lacks the ``registry default`` tree.

        container 1.3.1 exposes only ``registry login/logout/list`` — the
        default-registry subcommand errors with "unexpected arguments".
        """
        if "unexpected arguments" in output.lower() or "unexpected argument" in output.lower():
            raise CapabilityError(
                message=(
                    "registry default is not available on this host "
                    "(container exposes only registry login/logout/list)"
                ),
                feature="registry_default",
                container_version=self._caps.container_version,
            )

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

    async def system_property_list(self) -> dict[str, str]:
        self._require("system_properties")
        _, out, _ = await _run_cli_impl(
            "system", "property", "list", binary=self._binary,
        )
        # The default --format is toml (verified live on 1.3.1; the JSON
        # form exists too, but the default is the widest-compatible wire).
        return OutputParser.system_properties(out)

    async def system_property_get(self, key: str) -> str:
        self._require("system_properties")
        exit_code, out, _ = await _run_cli_impl(
            "system", "property", "get", key,
            binary=self._binary, check=False,
        )
        # Verified live on 1.3.1: the CLI has NO property get — it answers
        # with a usage error on stderr, exit code 64.  Future hosts with a
        # real get exit 0; on this host we fail loudly instead of leaking
        # the usage text as if it were the property value.
        if exit_code != 0:
            raise CapabilityError(
                message=(
                    f"system property get is not available on this host "
                    f"(container {self._caps.container_version} exposes only "
                    f"'system property list')"
                ),
                feature="system_property_get",
                container_version=self._caps.container_version,
            )
        return out.strip()

    async def system_property_set(self, key: str, value: str) -> None:
        self._require("system_properties")
        exit_code, _, _ = await _run_cli_impl(
            "system", "property", "set", key, value,
            binary=self._binary, check=False,
        )
        # Same 1.3.1 finding as get: no property set exists (usage error,
        # exit 64).  See ``system_property_get`` for the rationale.
        if exit_code != 0:
            raise CapabilityError(
                message=(
                    f"system property set is not available on this host "
                    f"(container {self._caps.container_version} exposes only "
                    f"'system property list')"
                ),
                feature="system_property_set",
                container_version=self._caps.container_version,
            )

    async def system_version(self) -> dict[str, Any]:
        _, out, _ = await _run_cli_impl(
            "system", "version", "--format", "json", binary=self._binary,
        )
        return OutputParser.system_version(out)

    async def system_df(self) -> dict[str, Any]:
        """Disk usage — runtime store usage when JSON is available.

        Preferred path: ``container system df --format json`` (verified
        live on 1.3.1).  ``used``/``store_bytes`` come from the runtime's
        own accounting (sum of per-category ``sizeInBytes``) and the raw
        per-category breakdown is preserved.  The df JSON reports **no
        free-space or capacity figure**, so ``total``/``free`` still come
        from the host-volume measurement — policy's min-free check needs
        real free space, which only the OS can report.  The fully
        OS-level measurement (``shutil.disk_usage``) is kept as the
        fallback for hosts whose ``system df`` does not emit JSON.
        """
        data: dict[str, Any] = {}
        try:
            exit_code, out, _ = await _run_cli_impl(
                "system", "df", "--format", "json",
                binary=self._binary, check=False,
            )
            if exit_code == 0:
                data = OutputParser.system_df_json(out)
        except Exception:
            data = {}
        if data:
            host = await self._os_disk_usage()
            return {
                "total": host.get("total", data["total"]),
                "used": data["used"],
                "free": host.get("free", 0),
                "path": host.get("path", ""),
                "store_bytes": data["used"],
                "containers": data["containers"],
                "images": data["images"],
                "volumes": data["volumes"],
                "source": "container system df --format json",
            }
        return await self._os_disk_usage()

    async def _os_disk_usage(self) -> dict[str, Any]:
        """OS-level disk measurement — the fallback df source.

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
