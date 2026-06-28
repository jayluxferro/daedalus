"""M1 — forge: lifecycle + system core.

Forge is the engine's central subsystem.  It wraps every lifecycle and system
command, owns the ``RunSpec`` object, and acts as the single source of truth
for container existence and state.

All operations flow through forge → backend, with policy checks and audit
applied *before* any backend call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import Backend, ContainerInfo, RunSpec
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import ValidationError
from daedalus.core.policy import Decision, PolicyEngine
from daedalus.core.store import Store

# ==========================================================================
# Enriched container model
# ==========================================================================


@dataclass
class Labyrinth:
    """A container as forge sees it — a Labyrinth (sandbox VM)."""

    info: ContainerInfo
    profile: str = "default"
    labels: dict[str, str] = field(default_factory=dict)
    created_at: str = ""

    @property
    def id(self) -> str:
        return self.info.id

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def image(self) -> str:
        return self.info.image

    @property
    def state(self) -> str:
        return self.info.state.value

    @property
    def is_running(self) -> bool:
        return self.info.state == self.info.state.RUNNING

    @property
    def is_stopped(self) -> bool:
        return self.info.state in (
            self.info.state.STOPPED, self.info.state.EXITED, self.info.state.CREATED,
        )


# ==========================================================================
# System status
# ==========================================================================


@dataclass
class SystemStatus:
    """Aggregated system view."""

    apiserver_running: bool = False
    container_version: str = ""
    container_commit: str = ""
    container_count: int = 0
    running_count: int = 0
    disk_usage: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)


# ==========================================================================
# Forge
# ==========================================================================


class Forge:
    """Lifecycle and system core of the DAEDALUS control plane.

    Parameters
    ----------
    backend:
        The active ``Backend`` implementation.
    capabilities:
        Host capability manifest.
    policy:
        Optional policy engine for pre-execution guardrails.
    audit:
        Optional audit log for operation recording.
    store:
        Optional run store for experiment manifests.
    """

    def __init__(
        self,
        backend: Backend,
        capabilities: CapabilityManifest,
        *,
        policy: PolicyEngine | None = None,
        audit: AuditLog | None = None,
        store: Store | None = None,
    ) -> None:
        self._backend = backend
        self._capabilities = capabilities
        self._policy = policy or PolicyEngine()
        self._audit = audit or AuditLog()
        self._store = store or Store()
        self._labyrinths: dict[str, Labyrinth] = {}

    @property
    def backend(self) -> Backend:
        return self._backend

    @property
    def capabilities(self) -> CapabilityManifest:
        return self._capabilities

    # ==================================================================
    # Lifecycle
    # ==================================================================

    async def create(
        self,
        image: str,
        *,
        name: str | None = None,
        profile: str = "default",
        **kwargs: Any,
    ) -> Labyrinth:
        """Create a container (does not start it)."""
        await self._check_run(image)
        spec = RunSpec(image=image, name=name, **_filter_run_kwargs(kwargs))
        info = await self._backend.create(spec)
        lab = Labyrinth(info=info, profile=profile, created_at=_now())
        self._labyrinths[lab.id] = lab
        self._audit.record(
            "create", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"image": image, "name": name, "profile": profile},
            result={"id": lab.id},
        )
        return lab

    async def run(
        self,
        image: str,
        *,
        name: str | None = None,
        profile: str = "default",
        detach: bool = False,
        command: list[str] | None = None,
        **kwargs: Any,
    ) -> Labyrinth:
        """Create and start a container."""
        await self._check_run(image)
        spec = RunSpec(
            image=image, name=name, detach=detach, command=command, **_filter_run_kwargs(kwargs),
        )
        info = await self._backend.run(spec)
        lab = Labyrinth(info=info, profile=profile, created_at=_now())
        self._labyrinths[lab.id] = lab
        self._audit.record(
            "run", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"image": image, "name": name, "profile": profile},
            result={"id": lab.id},
        )
        self._store.create(
            lab.id, image=image, image_digest="", profile=profile,
            command=command or [], container_name=lab.name,
        )
        return lab

    async def start(self, container_id: str) -> Labyrinth:
        await self._backend.start(container_id)
        return await self._refresh(container_id)

    async def stop(self, container_id: str, timeout: int = 10) -> Labyrinth:
        await self._backend.stop(container_id, timeout=timeout)
        lab = await self._refresh(container_id)
        self._audit.record(
            "stop", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"container_id": container_id, "timeout": timeout},
        )
        return lab

    async def kill(self, container_id: str, signal: str = "KILL") -> Labyrinth:
        await self._backend.kill(container_id, signal=signal)
        lab = await self._refresh(container_id)
        self._audit.record(
            "kill", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"container_id": container_id, "signal": signal},
        )
        return lab

    async def delete(self, container_id: str, force: bool = False) -> None:
        await self._backend.delete(container_id, force=force)
        self._labyrinths.pop(container_id, None)
        self._audit.record(
            "delete", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"container_id": container_id, "force": force},
        )

    async def destroy(self, container_id: str, *, confirm: bool = False) -> None:
        """Destroy a Labyrinth: stop (if running) then delete.

        Requires explicit ``confirm=True`` (policy-gated).
        """
        r = self._policy.check_destroy(confirm=confirm)
        if r.decision == Decision.CONFIRM:
            raise ValidationError("destroy requires confirm=True")
        self._policy.enforce(r)
        try:
            await self._backend.stop(container_id, timeout=5)
        except Exception:
            pass
        await self._backend.delete(container_id, force=True)
        self._labyrinths.pop(container_id, None)
        self._audit.record(
            "destroy", actor="forge", actor_kind=ActorKind.SERVICE,
            args={"container_id": container_id, "confirm": True},
        )
        self._store.update(container_id, exit_code=0)

    async def list(self, all: bool = False) -> list[Labyrinth]:
        infos = await self._backend.list(all=all)
        labs = []
        for info in infos:
            if info.id in self._labyrinths:
                self._labyrinths[info.id].info = info
                labs.append(self._labyrinths[info.id])
            else:
                lab = Labyrinth(info=info)
                self._labyrinths[info.id] = lab
                labs.append(lab)
        return labs

    async def inspect(self, container_id: str) -> Labyrinth:
        info = await self._backend.inspect(container_id)
        lab = Labyrinth(info=info)
        self._labyrinths[container_id] = lab
        return lab

    async def get(self, container_id: str) -> Labyrinth:
        if container_id in self._labyrinths:
            return self._labyrinths[container_id]
        return await self.inspect(container_id)

    # ==================================================================
    # System
    # ==================================================================

    async def system_status(self) -> SystemStatus:
        disk = await self._backend.system_df()
        try:
            all_containers = await self._backend.list(all=True)
            total = len(all_containers)
            running = sum(
                1 for c in all_containers if c.state == c.state.RUNNING
            )
        except Exception:
            total = 0
            running = 0

        return SystemStatus(
            apiserver_running=bool(self._capabilities.apiserver_running),
            container_version=self._capabilities.container_version,
            container_commit=self._capabilities.container_commit,
            container_count=total,
            running_count=running,
            disk_usage=disk,
            capabilities=self._capabilities.as_dict(),
        )

    # ==================================================================
    # Internals
    # ==================================================================

    async def _check_run(self, image: str) -> None:
        """Run policy checks before creating/starting a container."""
        r = self._policy.check_image(image)
        self._policy.enforce(r)
        running_count = sum(
            1 for lab in self._labyrinths.values() if lab.is_running
        )
        r = self._policy.check_concurrency(running_count)
        self._policy.enforce(r)

    async def _refresh(self, container_id: str) -> Labyrinth:
        info = await self._backend.inspect(container_id)
        if container_id in self._labyrinths:
            self._labyrinths[container_id].info = info
        else:
            self._labyrinths[container_id] = Labyrinth(info=info)
        return self._labyrinths[container_id]


def _now() -> str:
    return datetime.now(UTC).isoformat()


# RunSpec constructor fields — used to filter kwargs
_RUNSPEC_FIELDS = {
    "image", "name", "detach", "remove", "workdir", "env", "env_file",
    "entrypoint", "user", "uid", "gid", "interactive", "tty", "cpus",
    "memory", "mounts", "tmpfs", "volumes", "kernel", "hostname", "labels",
    "cidfile", "os", "arch", "dns", "dns_domain", "dns_search", "no_dns",
    "scheme", "disable_progress_updates", "command",
}


def _filter_run_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter kwargs to only include valid RunSpec fields."""
    return {k: v for k, v in kwargs.items() if k in _RUNSPEC_FIELDS}
