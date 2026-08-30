"""V — volumes: the storage plane for forensic artifacts and lab data.

Volumes are named, persistent disk-backed stores (ext4 images) that
survive container deletion.  Detonations deposit their evidence here and
lab experiments keep state across runs, so the volume verbs are a
first-class surface: create, list, inspect, delete (destructive,
confirm-gated) and prune.

Every mutation is audited; reads are not.
"""

from __future__ import annotations

import builtins

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import Backend, VolumeInfo, VolumeSpec
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import ValidationError
from daedalus.core.policy import Decision, PolicyEngine


class Volumes:
    """Named volumes — the persistent storage plane.

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
    """

    def __init__(
        self,
        backend: Backend,
        capabilities: CapabilityManifest,
        *,
        policy: PolicyEngine | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._backend = backend
        self._capabilities = capabilities
        self._policy = policy or PolicyEngine()
        self._audit = audit or AuditLog()

    @property
    def backend(self) -> Backend:
        return self._backend

    @property
    def capabilities(self) -> CapabilityManifest:
        return self._capabilities

    # ==================================================================
    # Lifecycle
    # ==================================================================

    async def create(self, spec: VolumeSpec) -> VolumeInfo:
        """Create a named volume (persists across container deletions)."""
        info = await self._backend.volume_create(spec)
        self._audit.record(
            "volume_create", actor="volumes", actor_kind=ActorKind.SERVICE,
            args={"name": spec.name, "size": spec.size},
            result={"id": info.id},
        )
        return info

    async def list(self) -> builtins.list[VolumeInfo]:
        """List volumes (read-only: no policy check or audit)."""
        return await self._backend.volume_list()

    async def inspect(self, name: str) -> VolumeInfo:
        """Inspect a single volume (read-only: no policy check or audit)."""
        return await self._backend.volume_inspect(name)

    async def delete(self, name: str, *, confirm: bool = False) -> None:
        """Delete a volume.

        Volumes hold forensic artifacts — deletion is destructive and
        requires explicit ``confirm=True`` (policy-gated).
        """
        r = self._policy.check_volume_delete(confirm=confirm)
        if r.decision == Decision.CONFIRM:
            raise ValidationError("volume delete requires confirm=True")
        self._policy.log("check_volume_delete", "volumes", r)
        self._policy.enforce(r)
        await self._backend.volume_delete(name)
        self._audit.record(
            "volume_delete", actor="volumes", actor_kind=ActorKind.SERVICE,
            args={"name": name, "confirm": confirm},
        )

    async def prune(self, *, confirm: bool = False) -> builtins.list[str]:
        """Reclaim unused volumes (destructive — same confirm gate as delete)."""
        r = self._policy.check_volume_delete(confirm=confirm)
        if r.decision == Decision.CONFIRM:
            raise ValidationError("volume delete requires confirm=True")
        self._policy.log("check_volume_delete", "volumes", r)
        self._policy.enforce(r)
        pruned = await self._backend.volume_prune()
        self._audit.record(
            "volume_prune", actor="volumes", actor_kind=ActorKind.SERVICE,
            args={"confirm": confirm},
            result={"count": len(pruned)},
        )
        return pruned
