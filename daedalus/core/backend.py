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

    This matches the flags confirmed present in container v0.1.0 via
    ``container run --help`` / ``container create --help``.  Flags that
    don't exist yet (rosetta, virtualization, shm-size, ulimit, publish,
    publish-socket, network, cap-add/cap-drop, init-image)
    are intentionally absent — they will be added when the CLI supports
    them.
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
    scheme: str | None = None       # --scheme  (http, https, auto)
    disable_progress_updates: bool = False  # --disable-progress-updates

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
        ``container network`` is not wired as a top-level subcommand in
        v0.1.0.  The plugin binary exists but the command tree does not
        expose it yet.  This dataclass is retained for forward
        compatibility.
    """

    name: str
    subnet: str | None = None       # IPv4 CIDR
    subnet_v6: str | None = None    # IPv6 CIDR
    internal: bool = False          # host-only, no host route
    plugin: str = "container-network-vmnet"
    options: dict[str, str] = field(default_factory=dict)

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


# ==========================================================================
# Abstract backend
# ==========================================================================


class Backend(ABC):
    """Abstract interface to the container runtime.

    Methods for commands that exist in container v0.1.0 are concrete
    abstract methods.  Methods for future features raise
    ``CapabilityError("NOT_IMPLEMENTED_IN_V010")`` by default so that
    L2/L3 backends can override them when container supports them.
    """

    # -- lifecycle (all present in v0.1.0) ---------------------------------

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
        """List containers.  Supports ``--format json`` in v0.1.0."""
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
    ) -> ExecResult:
        """Execute a command inside a running container."""
        ...

    # -- images (all present in v0.1.0) ------------------------------------

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
        """List images.  Supports ``--format json`` in v0.1.0."""
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
        """Print builder status.  Supports ``--json`` in v0.1.0."""
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

    # -- system (subset present in v0.1.0) ---------------------------------

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

        NOTE: ``container system df`` does not exist yet in v0.1.0.
        Default implementation falls back to OS-level disk check.
        """
        return {}

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
