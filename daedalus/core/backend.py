"""Abstract backend interface.

Every subsystem calls an abstract ``Backend`` with verbs.  Three
implementations exist behind this interface:

* **CliBackend (L1)** — shells ``container …``, parses output.
  Stable; the reference implementation.
* **VsockBackend (L2)** — talks ``vminitd`` gRPC over vsock.
* **NativeBackend (L3)** — links the Containerization Swift package / XPC
  directly.

This module also defines the shared data types used across all subsystems.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ==========================================================================
# Shared data types
# ==========================================================================


class ContainerState(str, Enum):
    """Known container states."""
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    EXITED = "exited"
    UNKNOWN = "unknown"


@dataclass
class RunSpec:
    """The full set of flags for ``container create`` / ``container run``.

    This matches the flags confirmed present in container 1.2.2 via
    ``container run --help`` / ``container create --help``, plus
    ``--init-image`` and ``--ssh`` (live in container 1.3.1).  Flags not
    yet covered by this dataclass (rosetta, shm-size, ulimit,
    publish-socket, virtualization) are intentionally absent — they will
    be added when the CLI supports them.
    """

    image: str
    name: str | None = None
    detach: bool = False
    remove: bool = False  # --rm (boolean flag; omit when False)

    # Execution environment
    workdir: str | None = None      # -w / --cwd / --workdir
    env: dict[str, str] = field(default_factory=dict)  # -e / --env
    env_file: str | None = None     # --env-file
    entrypoint: str | None = None   # --entrypoint
    user: str | None = None         # -u / --user
    uid: int | None = None          # --uid
    gid: int | None = None          # --gid

    # TTY / interactive
    interactive: bool = False       # -i
    tty: bool = False               # -t

    # Resources
    cpus: int | None = None         # -c / --cpus
    memory: str | None = None       # -m / --memory

    # Filesystem
    mounts: list[str] = field(default_factory=list)   # --mount  type=<>,source=<>,target=<>,readonly
    tmpfs: list[str] = field(default_factory=list)    # --tmpfs
    volumes: list[str] = field(default_factory=list)  # -v / --volume
    read_only: bool = False         # --read-only (rootfs read-only)

    # Kernel
    kernel: str | None = None       # -k / --kernel

    # Identity
    labels: dict[str, str] = field(default_factory=dict)  # -l / --label
    cidfile: str | None = None      # --cidfile

    # Platform
    os: str | None = None           # --os
    arch: str | None = None         # -a / --arch

    # DNS
    dns: list[str] = field(default_factory=list)          # --dns
    dns_domain: list[str] = field(default_factory=list)   # --dns-domain
    dns_search: list[str] = field(default_factory=list)   # --dns-search
    dns_option: list[str] = field(default_factory=list)   # --dns-option
    no_dns: bool = False            # --no-dns

    # Registry
    scheme: str | None = None       # --scheme  (http, https — auto removed in 1.3.0)
    disable_progress_updates: bool = False  # --disable-progress-updates

    # Network / capabilities (container 1.2.2)
    network: str | None = None        # --network <name>  (attach to a network)
    publish: list[str] = field(default_factory=list)   # -p <spec>, repeatable
    cap_add: list[str] = field(default_factory=list)   # --cap-add <cap>, repeatable
    cap_drop: list[str] = field(default_factory=list)  # --cap-drop <cap>, repeatable

    # Init image / SSH agent (container 1.3.1)
    init_image: str | None = None     # --init-image <image> (custom PID-1 / eBPF boot)
    ssh: bool = False                 # --ssh (forward SSH agent socket into container)

    # Analysis / instrumentation
    proxy: str | None = None          # HTTP_PROXY/HTTPS_PROXY host:port
    cert_path: str | None = None      # PEM cert to volume-mount into container
    no_proxy: str | None = None       # NO_PROXY exclusion list

    # Command to run (after image name)
    command: list[str] | None = None

    def to_cli_args(self, *, for_create: bool = False) -> list[str]:
        """Serialize this spec into ``container create/run`` CLI arguments.

        Container CLI syntax: container run [<options>] <image> [<arguments> ...]
        Options must come BEFORE the image.
        """
        flags: list[str] = []
        tail: list[str] = []

        # --- name ---
        if self.name:
            flags += ["--name", self.name]

        # --- detach ---
        if self.detach:
            flags.append("-d")

        # --- rm (boolean flag; no value) ---
        if self.remove:
            flags.append("--rm")

        # --- workdir ---
        if self.workdir:
            flags += ["--workdir", self.workdir]

        # --- env ---
        for k, v in self.env.items():
            flags += ["--env", f"{k}={v}"]
        if self.env_file:
            flags += ["--env-file", self.env_file]

        # --- entrypoint ---
        if self.entrypoint:
            flags += ["--entrypoint", self.entrypoint]

        # --- user / uid / gid ---
        if self.user:
            flags += ["--user", self.user]
        if self.uid is not None:
            flags += ["--uid", str(self.uid)]
        if self.gid is not None:
            flags += ["--gid", str(self.gid)]

        # --- interactive / tty ---
        if self.interactive:
            flags.append("--interactive")
        if self.tty:
            flags.append("--tty")

        # --- resources ---
        if self.cpus is not None:
            flags += ["--cpus", str(self.cpus)]
        if self.memory:
            flags += ["--memory", self.memory]

        # --- filesystem ---
        for m in self.mounts:
            flags += ["--mount", m]
        for t in self.tmpfs:
            flags += ["--tmpfs", t]
        for v in self.volumes:
            flags += ["--volume", v]
        if self.read_only:
            flags.append("--read-only")

        # --- kernel ---
        if self.kernel:
            flags += ["--kernel", self.kernel]

        # --- identity ---
        for k, v in self.labels.items():
            flags += ["--label", f"{k}={v}"]
        if self.cidfile:
            flags += ["--cidfile", self.cidfile]

        # --- platform ---
        if self.os:
            flags += ["--os", self.os]
        if self.arch:
            flags += ["--arch", self.arch]

        # --- DNS ---
        for d in self.dns:
            flags += ["--dns", d]
        for d in self.dns_domain:
            flags += ["--dns-domain", d]
        for d in self.dns_search:
            flags += ["--dns-search", d]
        for d in self.dns_option:
            flags += ["--dns-option", d]
        if self.no_dns:
            flags.append("--no-dns")

        # --- registry ---
        if self.scheme:
            flags += ["--scheme", self.scheme]
        if self.disable_progress_updates:
            flags.append("--disable-progress-updates")

        # --- proxy (Burp Suite / MITM analysis) ---
        if self.proxy:
            flags += ["--env", f"HTTP_PROXY=http://{self.proxy}"]
            flags += ["--env", f"HTTPS_PROXY=http://{self.proxy}"]
            # BusyBox / Alpine wget uses lowercase variants
            flags += ["--env", f"http_proxy=http://{self.proxy}"]
            flags += ["--env", f"https_proxy=http://{self.proxy}"]
        if self.no_proxy:
            flags += ["--env", f"NO_PROXY={self.no_proxy}"]
            flags += ["--env", f"no_proxy={self.no_proxy}"]

        # --- cert injection (via forge after container start) ---
        # container volume mounts only support directories, not
        # individual files.  Cert injection happens at the Forge level
        # after the container is running: the cert file content is piped
        # via exec into /usr/local/share/ca-certificates/.

        # --- network / capabilities (container 1.2.2) ---
        if self.network:
            flags += ["--network", self.network]
        for p in self.publish:
            flags += ["-p", p]
        for cap in self.cap_add:
            flags += ["--cap-add", cap]
        for cap in self.cap_drop:
            flags += ["--cap-drop", cap]

        # --- init image / ssh agent (container 1.3.1) ---
        if self.init_image:
            flags += ["--init-image", self.init_image]
        if self.ssh:
            flags.append("--ssh")

        # Image comes after all flags
        tail.append(self.image)

        # Command comes after image
        if self.command:
            tail += self.command

        return flags + tail


@dataclass
class ContainerInfo:
    """Parsed ``container list`` / ``container inspect`` result."""

    id: str
    name: str
    image: str
    state: ContainerState = ContainerState.UNKNOWN
    created_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecResult:
    """Result of ``container exec``."""

    exit_code: int
    stdout: str
    stderr: str


@dataclass
class NetworkSpec:
    """Spec for ``container network create``.

    .. note::
        The ``container network`` subcommand tree is present in container
        1.2.2; ``to_cli_args()`` matches its verified ``create`` flags.
    """

    name: str
    subnet: str | None = None       # IPv4 CIDR
    subnet_v6: str | None = None    # IPv6 CIDR
    internal: bool = False          # host-only, no host route
    plugin: str = "container-network-vmnet"
    options: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)  # --label k=v

    def to_cli_args(self) -> list[str]:
        args = [self.name]
        if self.subnet:
            args += ["--subnet", self.subnet]
        if self.subnet_v6:
            args += ["--subnet-v6", self.subnet_v6]
        if self.internal:
            args.append("--internal")
        if self.plugin:
            args += ["--plugin", self.plugin]
        for k, v in self.labels.items():
            args += ["--label", f"{k}={v}"]
        for k, v in self.options.items():
            args += ["--option", f"{k}={v}"]
        return args


@dataclass
class BuildSpec:
    """Specification for ``container build``."""

    context: str = "."
    file: str | None = None          # -f / --file (Containerfile/Dockerfile)
    tag: str | None = None           # -t / --tag
    target: str | None = None        # --target (multi-stage)
    arch: str | None = None          # --arch
    os: str | None = None            # --os
    build_arg: dict[str, str] = field(default_factory=dict)  # --build-arg
    label: dict[str, str] = field(default_factory=dict)      # -l / --label
    no_cache: bool = False           # --no-cache
    output: str | None = None        # -o / --output (default: type=oci)
    progress: str | None = None      # --progress auto|plain|tty
    vsock_port: int | None = None    # --vsock-port
    quiet: bool = False              # -q / --quiet
    cpus: int | None = None          # -c / --cpus
    memory: str | None = None        # -m / --memory

    def to_cli_args(self) -> list[str]:
        args: list[str] = []
        if self.file:
            args += ["-f", self.file]
        if self.tag:
            args += ["-t", self.tag]
        if self.target:
            args += ["--target", self.target]
        if self.arch:
            args += ["--arch", self.arch]
        if self.os:
            args += ["--os", self.os]
        for k, v in self.build_arg.items():
            args += ["--build-arg", f"{k}={v}"]
        for k, v in self.label.items():
            args += ["--label", f"{k}={v}"]
        if self.no_cache:
            args.append("--no-cache")
        if self.output:
            args += ["-o", self.output]
        if self.progress:
            args += ["--progress", self.progress]
        if self.vsock_port is not None:
            args += ["--vsock-port", str(self.vsock_port)]
        if self.quiet:
            args.append("-q")
        if self.cpus is not None:
            args += ["-c", str(self.cpus)]
        if self.memory:
            args += ["-m", self.memory]
        args.append(self.context)
        return args


@dataclass
class StatsSample:
    """One-shot resource statistics for a container (``container stats``).

    ``cpu_usage_usec`` is **cumulative** CPU time — rates require two
    samples and are derived via :meth:`cpu_percent`.  ``sampled_at`` is
    a :func:`time.monotonic` timestamp captured when the output was
    parsed.
    """

    id: str
    cpu_usage_usec: int = 0        # cumulative
    memory_usage_bytes: int = 0
    memory_limit_bytes: int = 0
    network_rx_bytes: int = 0
    network_tx_bytes: int = 0
    block_read_bytes: int = 0
    block_write_bytes: int = 0
    num_processes: int = 0
    sampled_at: float = 0.0        # time.monotonic()

    def cpu_percent(self, prev: StatsSample) -> float:
        """CPU utilisation of this sample relative to ``prev``, 0..100.

        percent = (delta_cpu_usec / 1e6) / delta_time_sec * 100 —
        ``cpu_usage_usec`` is cumulative MICROseconds, so the delta is
        converted to core-seconds before dividing by wall time.  Returns
        0.0 when the time delta is zero or negative (guards division by
        zero) or when CPU time did not advance.
        """
        delta_usec = self.cpu_usage_usec - prev.cpu_usage_usec
        delta_time = self.sampled_at - prev.sampled_at
        if delta_time <= 0 or delta_usec <= 0:
            return 0.0
        percent = ((delta_usec / 1e6) / delta_time) * 100.0
        return max(0.0, min(100.0, percent))

    def to_dict(self) -> dict[str, Any]:
        """Serialise the sample as a flat dict."""
        return {
            "id": self.id,
            "cpu_usage_usec": self.cpu_usage_usec,
            "memory_usage_bytes": self.memory_usage_bytes,
            "memory_limit_bytes": self.memory_limit_bytes,
            "network_rx_bytes": self.network_rx_bytes,
            "network_tx_bytes": self.network_tx_bytes,
            "block_read_bytes": self.block_read_bytes,
            "block_write_bytes": self.block_write_bytes,
            "num_processes": self.num_processes,
            "sampled_at": self.sampled_at,
        }


@dataclass
class NetworkInfo:
    """Parsed ``container network list`` / ``network inspect`` result."""

    id: str
    name: str
    plugin: str = ""                 # e.g. "container-network-vmnet"
    mode: str = ""                   # "nat" | "internal" | ...
    subnet: str = ""                 # from status.ipv4Subnet
    subnet_v6: str = ""              # from status.ipv6Subnet
    gateway: str = ""                # from status.ipv4Gateway
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PruneResult:
    """Result of ``container prune``."""

    ids: list[str] = field(default_factory=list)
    message: str = ""                # "Reclaimed 282.1 MB in disk space"


@dataclass
class MachineInfo:
    """Parsed ``machine list`` / ``machine inspect`` result (SPEC §2.1).

    ``memory_bytes`` and ``disk_size_bytes`` are **bytes** (the wire
    emits raw byte counts).  ``warning`` is engine-set (the machina
    init-capability advisor), never read off the wire.
    """

    id: str
    status: str = ""                 # stopped | running | ...
    cpus: int = 0
    memory_bytes: int = 0
    disk_size_bytes: int = 0
    default: bool = False
    created_date: str = ""
    home_mount: str = ""             # inspect-only
    image_ref: str = ""              # inspect-only
    platform_arch: str = ""
    platform_os: str = ""
    user_username: str = ""
    user_uid: int = 0
    user_gid: int = 0
    warning: str = ""                # advisor note (engine-set, not from wire)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the machine as a flat dict."""
        return {
            "id": self.id,
            "status": self.status,
            "cpus": self.cpus,
            "memory_bytes": self.memory_bytes,
            "disk_size_bytes": self.disk_size_bytes,
            "default": self.default,
            "created_date": self.created_date,
            "home_mount": self.home_mount,
            "image_ref": self.image_ref,
            "platform_arch": self.platform_arch,
            "platform_os": self.platform_os,
            "user_username": self.user_username,
            "user_uid": self.user_uid,
            "user_gid": self.user_gid,
            "warning": self.warning,
        }


@dataclass
class MachineCreateSpec:
    """Spec for ``container machine create`` (container 1.2.2).

    The image is emitted **last** — the CLI syntax is
    ``machine create [opts] <image>``.
    """

    image: str
    name: str | None = None
    cpus: int | None = None
    memory: str | None = None        # "2G" | "8G" — CLI string form
    home_mount: str | None = None    # ro | rw | none
    virtualization: bool = False
    kernel: str | None = None
    set_default: bool = False
    no_boot: bool = False
    arch: str | None = None
    os: str | None = None
    platform: str | None = None
    scheme: str | None = None

    def to_cli_args(self) -> list[str]:
        """Serialize into ``container machine create`` CLI arguments."""
        flags: list[str] = []
        if self.name:
            flags += ["--name", self.name]
        if self.set_default:
            flags.append("--set-default")
        if self.no_boot:
            flags.append("--no-boot")
        if self.cpus is not None:
            flags += ["--cpus", str(self.cpus)]
        if self.memory:
            flags += ["--memory", self.memory]
        if self.home_mount:
            flags += ["--home-mount", self.home_mount]
        if self.virtualization:
            flags.append("--virtualization")
        if self.kernel:
            flags += ["--kernel", self.kernel]
        if self.arch:
            flags += ["--arch", self.arch]
        if self.os:
            flags += ["--os", self.os]
        if self.platform:
            flags += ["--platform", self.platform]
        if self.scheme:
            flags += ["--scheme", self.scheme]
        flags.append(self.image)
        return flags


@dataclass
class MachineSetSettings:
    """Settings for ``container machine set`` (SPEC §2.1).

    ``kernel=""`` is meaningful — an empty string resets the kernel
    (emitted as a bare ``kernel=`` arg).
    """

    cpus: int | None = None
    memory: str | None = None
    home_mount: str | None = None
    virtualization: bool | None = None
    kernel: str | None = None

    def to_set_args(self) -> list[str]:
        """Serialize only non-None fields as ``key=value`` args."""
        args: list[str] = []
        if self.cpus is not None:
            args.append(f"cpus={self.cpus}")
        if self.memory is not None:
            args.append(f"memory={self.memory}")
        if self.home_mount is not None:
            args.append(f"home-mount={self.home_mount}")
        if self.virtualization is not None:
            args.append(f"virtualization={'true' if self.virtualization else 'false'}")
        if self.kernel is not None:
            args.append(f"kernel={self.kernel}")
        return args


@dataclass
class VolumeInfo:
    """Parsed ``container volume list`` / ``volume inspect`` result (SPEC §2).

    ``size_bytes`` is the byte count from ``sizeInBytes``; ``size_spec``
    is the human CLI form from ``options.size`` (e.g. ``"100M"``).
    """

    id: str
    name: str = ""
    driver: str = ""                 # e.g. "local"
    format: str = ""                 # e.g. "ext4"
    size_bytes: int = 0
    size_spec: str = ""              # e.g. "100M" (options.size)
    source: str = ""                 # path to the volume.img backing file
    created_date: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    options: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the volume as a flat dict."""
        return {
            "id": self.id,
            "name": self.name,
            "driver": self.driver,
            "format": self.format,
            "size_bytes": self.size_bytes,
            "size_spec": self.size_spec,
            "source": self.source,
            "created_date": self.created_date,
            "labels": self.labels,
            "options": self.options,
        }


@dataclass
class VolumeSpec:
    """Spec for ``container volume create`` (container 1.2.2).

    The name is emitted **last** — the CLI syntax is
    ``volume create [opts] <name>``.
    """

    name: str
    size: str | None = None          # -s <size> e.g. "100M"
    labels: dict[str, str] = field(default_factory=dict)   # --label k=v
    options: dict[str, str] = field(default_factory=dict)  # --opt k=v

    def to_cli_args(self) -> list[str]:
        """Serialize into ``container volume create`` CLI arguments."""
        args: list[str] = []
        for k, v in self.labels.items():
            args += ["--label", f"{k}={v}"]
        for k, v in self.options.items():
            args += ["--opt", f"{k}={v}"]
        if self.size:
            args += ["-s", self.size]
        args.append(self.name)
        return args


# ==========================================================================
# Abstract backend
# ==========================================================================


class Backend(ABC):
    """Abstract interface to the container runtime.

    Every verb is an abstract method; each backend implements the verbs
    it supports.  ``CliBackend`` (L1) covers the container CLI 1.2.2
    surface; L2/L3 backends override the subset they provide.
    """

    # -- lifecycle -----------------------------------------------------------

    @abstractmethod
    async def create(self, spec: RunSpec) -> ContainerInfo:
        """Create a container (does not start it)."""
        ...

    @abstractmethod
    async def run(self, spec: RunSpec) -> ContainerInfo:
        """Create and start a container."""
        ...

    @abstractmethod
    async def start(
        self,
        container_id: str,
        *,
        attach: bool = False,
        interactive: bool = False,
    ) -> None:
        """Start a stopped container."""
        ...

    @abstractmethod
    async def stop(
        self,
        container_id: str,
        timeout: int = 10,
        *,
        signal: str | None = None,
    ) -> None:
        """Stop a running container with ``SIGTERM`` + timeout."""
        ...

    @abstractmethod
    async def kill(self, container_id: str, signal: str = "KILL") -> None:
        """Force-kill a container with a signal."""
        ...

    @abstractmethod
    async def delete(self, container_id: str, force: bool = False) -> None:
        """Delete one or more containers."""
        ...

    @abstractmethod
    async def list(self, all: bool = False) -> builtins.list[ContainerInfo]:
        """List containers.  Supports ``--format json``."""
        ...

    @abstractmethod
    async def inspect(self, container_id: str) -> ContainerInfo:
        """Detailed container inspection."""
        ...

    @abstractmethod
    async def logs(
        self,
        container_id: str,
        *,
        follow: bool = False,
        follow_seconds: float | None = None,
        boot: bool = False,
        tail: int | None = None,
    ) -> str:
        """Retrieve container stdout or boot logs."""
        ...

    # -- interaction -------------------------------------------------------

    @abstractmethod
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
        workdir: str | None = None,
        env_file: str | None = None,
        timeout: float = 30.0,
    ) -> ExecResult:
        """Execute a command inside a running container."""
        ...

    # -- images --------------------------------------------------------------

    @abstractmethod
    async def image_pull(
        self,
        image: str,
        platform: str | None = None,
        scheme: str | None = None,
    ) -> None:
        """Pull an image from a registry."""
        ...

    @abstractmethod
    async def image_push(
        self,
        image: str,
        platform: str | None = None,
        scheme: str | None = None,
    ) -> None:
        """Push an image to a registry."""
        ...

    @abstractmethod
    async def image_save(self, image: str, output: str) -> str:
        """Save an image as an OCI tar archive."""
        ...

    @abstractmethod
    async def image_load(self, input_path: str) -> str:
        """Load images from an OCI tar archive."""
        ...

    @abstractmethod
    async def image_tag(self, source: str, target: str) -> None:
        """Tag an image."""
        ...

    @abstractmethod
    async def image_delete(self, image: str, *, all: bool = False) -> None:
        """Remove one or more images."""
        ...

    @abstractmethod
    async def image_inspect(self, image: str) -> dict[str, Any]:
        """Display information about one or more images."""
        ...

    @abstractmethod
    async def image_list(self, quiet: bool = False) -> builtins.list[dict[str, Any]]:
        """List images.  Supports ``--format json``."""
        ...

    @abstractmethod
    async def image_prune(self) -> builtins.list[str]:
        """Remove unreferenced and dangling images."""
        ...

    @abstractmethod
    async def build(self, spec: BuildSpec) -> str:
        """Build an image from a Dockerfile/Containerfile.

        Returns the built image ID or tag.
        """
        ...

    # -- registry ----------------------------------------------------------

    @abstractmethod
    async def registry_login(
        self,
        server: str,
        username: str | None = None,
        password: str | None = None,
        scheme: str | None = None,
    ) -> None:
        """Login to a registry."""
        ...

    @abstractmethod
    async def registry_logout(self, server: str) -> None:
        """Log out from a registry."""
        ...

    @abstractmethod
    async def registry_default_inspect(self) -> str:
        """Return the configured default registry host."""
        ...

    @abstractmethod
    async def registry_default_set(self, host: str, scheme: str | None = None) -> None:
        """Set the default registry host."""
        ...

    @abstractmethod
    async def registry_default_unset(self) -> None:
        """Clear the default registry host."""
        ...

    # -- builder -----------------------------------------------------------

    @abstractmethod
    async def builder_status(self) -> dict[str, Any]:
        """Print builder status.  Supports ``--json``."""
        ...

    @abstractmethod
    async def builder_start(self, cpus: int = 2, memory: str = "2048M") -> None:
        """Start the image builder."""
        ...

    @abstractmethod
    async def builder_stop(self) -> None:
        """Stop the image builder."""
        ...

    @abstractmethod
    async def builder_delete(self, force: bool = False) -> None:
        """Delete the image builder."""
        ...

    # -- system --------------------------------------------------------------

    @abstractmethod
    async def system_start(self) -> None:
        """Start container services."""
        ...

    @abstractmethod
    async def system_stop(self) -> None:
        """Stop all container services."""
        ...

    @abstractmethod
    async def system_restart(self) -> None:
        """Restart the API server."""
        ...

    @abstractmethod
    async def system_logs(
        self,
        last: str = "5m",
        follow: bool = False,
        follow_seconds: float | None = None,
    ) -> str:
        """Fetch system logs."""
        ...

    @abstractmethod
    async def system_kernel_set(
        self,
        binary: str | None = None,
        tar: str | None = None,
        arch: str = "arm64",
        recommended: bool = False,
    ) -> None:
        """Set the default kernel."""
        ...

    async def system_kernel_list(self) -> list[dict[str, Any]]:
        """List kernels available on the host.  Subclasses may override."""
        return []

    @abstractmethod
    async def system_dns_create(self, domain: str) -> None:
        """Create a local DNS domain (requires sudo)."""
        ...

    @abstractmethod
    async def system_dns_delete(self, domain: str) -> None:
        """Delete a local DNS domain (requires sudo)."""
        ...

    @abstractmethod
    async def system_dns_list(self) -> list[str]:
        """List local DNS domains."""
        ...

    @abstractmethod
    async def system_df(self) -> dict[str, Any]:
        """Disk usage info.

        NOTE: ``container system df`` is available in container 1.2.2;
        the default implementation here still measures disk at the OS
        level (the CliBackend's host-side check feeds policy budgets).
        """
        return {}

    # -- stats / copy / export / prune / network (container 1.2.2) ----------

    @abstractmethod
    async def stats(self, container_id: str | None = None) -> list[StatsSample]:
        """One-shot resource statistics for a container (or all running)."""
        ...

    @abstractmethod
    async def copy(self, source: str, destination: str) -> str:
        """Copy files to/from a container.  Raw passthrough; returns stdout."""
        ...

    @abstractmethod
    async def export(self, container_id: str, output_path: str) -> str:
        """Export a container's filesystem to a tar archive.  Returns ``output_path``."""
        ...

    @abstractmethod
    async def prune(self) -> PruneResult:
        """Remove stopped containers and reclaim disk space."""
        ...

    @abstractmethod
    async def network_create(self, spec: NetworkSpec) -> NetworkInfo:
        """Create a network and return its full info."""
        ...

    @abstractmethod
    async def network_list(self) -> list[NetworkInfo]:
        """List networks."""
        ...

    @abstractmethod
    async def network_inspect(self, name: str) -> NetworkInfo:
        """Inspect a single network."""
        ...

    @abstractmethod
    async def network_delete(self, name: str) -> None:
        """Delete a network."""
        ...

    # -- machines (container 1.2.2) ----------------------------------------

    @abstractmethod
    async def machine_create(self, spec: MachineCreateSpec) -> MachineInfo:
        """Create a persistent machine (does not boot it when ``no_boot``)."""
        ...

    @abstractmethod
    async def machine_list(self) -> list[MachineInfo]:
        """List machines.  Supports ``--format json``."""
        ...

    @abstractmethod
    async def machine_inspect(self, machine_id: str) -> MachineInfo:
        """Inspect a single machine (JSON array on the wire)."""
        ...

    @abstractmethod
    async def machine_delete(self, machine_id: str) -> None:
        """Delete a machine (destructive)."""
        ...

    @abstractmethod
    async def machine_stop(self, machine_id: str) -> None:
        """Stop a machine."""
        ...

    @abstractmethod
    async def machine_set(
        self, machine_id: str, settings: MachineSetSettings,
    ) -> None:
        """Update machine settings (``cpus=``, ``memory=``, ...)."""
        ...

    @abstractmethod
    async def machine_set_default(self, machine_id: str) -> None:
        """Make a machine the default (used when no id is given)."""
        ...

    @abstractmethod
    async def machine_run(
        self,
        machine_id: str,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        gid: int | None = None,
        interactive: bool = False,
        timeout: float = 60.0,
    ) -> ExecResult:
        """Boot a machine if necessary and run a command inside it."""
        ...

    @abstractmethod
    async def machine_logs(self, machine_id: str) -> str:
        """Dump a machine's boot/stdout logs as plain text."""
        ...

    # -- volumes (container 1.2.2) ----------------------------------------

    @abstractmethod
    async def volume_create(self, spec: VolumeSpec) -> VolumeInfo:
        """Create a named volume (ext4 image) and return its full info."""
        ...

    @abstractmethod
    async def volume_list(self) -> list[VolumeInfo]:
        """List volumes.  Supports ``--format json``."""
        ...

    @abstractmethod
    async def volume_inspect(self, name: str) -> VolumeInfo:
        """Inspect a single volume (JSON array on the wire)."""
        ...

    @abstractmethod
    async def volume_delete(self, name: str) -> None:
        """Delete a volume (destructive)."""
        ...

    @abstractmethod
    async def volume_prune(self) -> list[str]:
        """Remove unused volumes; returns the pruned volume names."""
        ...

    # -- escape hatch ------------------------------------------------------

    @abstractmethod
    async def _run_cli(
        self, *args: str, timeout: float = 60.0, check: bool = True
    ) -> tuple[int, str, str]:
        """Run a raw ``container`` command and return ``(exit_code, stdout, stderr)``.

        Internal escape hatch for subsystems (Mint, Talos) that need to
        call commands not yet covered by dedicated Backend methods.
        Not part of the stable public API.
        """
        ...
