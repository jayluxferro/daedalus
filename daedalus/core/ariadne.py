"""M6 — ariadne: instrumentation & telemetry.

Ariadne is the thread that traces what happens inside the Labyrinth.

It manages:
* **init-image** variants that load eBPF programs at boot (syscall tracing,
  network capture, file-access auditing) — invisible to the workload
* **kernel** variants (instrumented/vulnerable) via ``-k`` and
  ``system kernel set``
* **telemetry egress** channels: published socket, vsock, or captured volume
* **capability and hardening flags** (capabilities, read-only, tmpfs, etc.)

Named for Ariadne, who gave Theseus the thread to navigate the maze.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Tracer(Enum):
    """eBPF tracer types that can be loaded via init-image."""
    SYSCALLS = "syscalls"
    NETWORK = "network"
    FILE = "file"
    PROCESS = "process"
    MEMORY = "memory"


class TelemetryChannel(Enum):
    """How telemetry leaves the sandbox."""
    VSOCK = "vsock"          # gRPC over vsock (L2 backend)
    PUBLISHED_PORT = "port"  # published TCP port
    CAPTURED_VOLUME = "volume"  # write to a mounted volume
    STDOUT = "stdout"        # multiplex onto stdout (simple but noisy)


@dataclass
class InitImageSpec:
    """Specification for a custom init-image build.

    The init-image is a minimal filesystem that runs as PID 1 before the
    OCI workload.  It can load eBPF programs, configure network capture,
    and start telemetry daemons.
    """
    name: str
    tracers: list[Tracer] = field(default_factory=list)
    telemetry_channel: TelemetryChannel = TelemetryChannel.VSOCK
    extra_packages: list[str] = field(default_factory=list)
    build_args: dict[str, str] = field(default_factory=dict)


@dataclass
class KernelVariant:
    """A kernel variant available for use with ``-k``."""
    name: str           # e.g. "kasan", "kcov", "syzkaller"
    path: str           # absolute path to kernel image
    description: str = ""
    tags: list[str] = field(default_factory=list)  # e.g. ["instrumented", "vulnerable"]


class Ariadne:
    """Instrumentation and telemetry manager.

    Parameters
    ----------
    backend:
        The active ``Backend`` implementation.
    capabilities:
        Host capability manifest.
    """

    def __init__(
        self,
        backend: Any,
        capabilities: Any,
    ) -> None:
        self._backend = backend
        self._caps = capabilities
        self._init_images: dict[str, InitImageSpec] = {}
        self._kernels: dict[str, KernelVariant] = {}

    # ==================================================================
    # Init-image management
    # ==================================================================

    def register_init_image(self, spec: InitImageSpec) -> None:
        """Register an init-image spec.

        The actual build happens via ``mint.build()`` with a special
        Containerfile that produces a minimal init image.
        """
        self._init_images[spec.name] = spec

    def get_init_image(self, name: str) -> InitImageSpec | None:
        return self._init_images.get(name)

    def list_init_images(self) -> list[InitImageSpec]:
        return list(self._init_images.values())

    @property
    def has_init_image_support(self) -> bool:
        return self._caps.init_image is True

    # ==================================================================
    # Kernel variant management
    # ==================================================================

    def register_kernel(self, kv: KernelVariant) -> None:
        """Register a known kernel variant."""
        self._kernels[kv.name] = kv

    def get_kernel(self, name: str) -> KernelVariant | None:
        return self._kernels.get(name)

    def list_kernels(self) -> list[KernelVariant]:
        return list(self._kernels.values())

    async def list_system_kernels(self) -> list[dict[str, Any]]:
        """List kernels available on the host (via ``system kernel``)."""
        if self._caps.kernel_set:
            result: list[dict[str, Any]] = await self._backend.system_kernel_list()
            return result
        return []

    async def set_default_kernel(self, kernel: str) -> None:
        """Set the default kernel for future runs."""
        if self._caps.kernel_set:
            await self._backend.system_kernel_set(kernel)

    # ==================================================================
    # Pre-built init-image recipes
    # ==================================================================

    @staticmethod
    def default_tracer_specs() -> list[InitImageSpec]:
        """Return the standard set of init-image recipes."""
        return [
            InitImageSpec(
                name="trace-syscalls",
                tracers=[Tracer.SYSCALLS],
                telemetry_channel=TelemetryChannel.VSOCK,
            ),
            InitImageSpec(
                name="trace-network",
                tracers=[Tracer.NETWORK],
                telemetry_channel=TelemetryChannel.VSOCK,
            ),
            InitImageSpec(
                name="trace-full",
                tracers=[Tracer.SYSCALLS, Tracer.NETWORK, Tracer.FILE, Tracer.PROCESS],
                telemetry_channel=TelemetryChannel.VSOCK,
            ),
            InitImageSpec(
                name="trace-stdout",
                tracers=[Tracer.SYSCALLS],
                telemetry_channel=TelemetryChannel.STDOUT,
            ),
        ]

    # ==================================================================
    # RunSpec helpers
    # ==================================================================

    def instrumentation_args(
        self,
        *,
        init_image: str | None = None,
        kernel: str | None = None,
        cap_add: list[str] | None = None,
        cap_drop: list[str] | None = None,
        read_only: bool = False,
    ) -> dict[str, Any]:
        """Build the instrumentation kwargs dict for a ``RunSpec``.

        This is the primary entry point for "instrument this run" —
        returns kwargs that can be passed to ``forge.run(**kwargs)``.
        """
        kwargs: dict[str, Any] = {}

        if init_image and self.has_init_image_support:
            kwargs["init_image"] = init_image
        if kernel and self._caps.kernel_set:
            kwargs["kernel"] = kernel
        if cap_add:
            kwargs["cap_add"] = cap_add
        if cap_drop:
            kwargs["cap_drop"] = cap_drop
        if read_only:
            kwargs["read_only"] = True

        return kwargs
