"""M5 — machina: persistent container machines (deus ex machina).

Machina wraps Apple's ``container machine`` (1.2.2) — persistent, stateful
Linux VMs with home-dir sharing.  This is distinct from forge's disposable
labyrinths: machines are long-lived, config-editable, and boot the image's
own ``/sbin/init`` as PID 1 (SPEC phase 2 §2.2).

The subsystem adds a two-tier init-capability advisor (name heuristic +
authoritative log check, SPEC phase 2 §2.3), policy gates (create disk
check, destructive delete confirm) and a structured audit trail for every
mutation.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import (
    Backend,
    ExecResult,
    MachineCreateSpec,
    MachineInfo,
    MachineSetSettings,
)
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import ValidationError
from daedalus.core.policy import Decision, PolicyEngine

# ==========================================================================
# Init-capability advisor (SPEC phase 2 §2.3)
# ==========================================================================


class MachineImageAdvisor:
    """Two-tier init-capability advisor, ported from Orchard's design.

    Tier 1 is a deny-list name heuristic (:meth:`likely_lacks_init`); tier 2
    is an authoritative check of the machine's boot logs
    (:meth:`logs_indicate_missing_init`).  Both tiers are pure and
    data-driven — no state, no I/O — so every surface can apply them.
    """

    # Well-known images that carry no init system (PID 1 cannot boot).
    INITLESS_NAMES: frozenset[str] = frozenset({
        "alpine", "busybox", "scratch", "hello-world", "ubuntu", "debian",
        "fedora", "centos", "rockylinux", "almalinux", "archlinux", "opensuse",
        "nginx", "httpd", "caddy", "traefik", "haproxy", "redis", "postgres",
        "mysql", "mariadb", "mongo", "memcached", "node", "python", "golang",
        "go", "openjdk", "eclipse-temurin", "ruby", "php", "rust", "elixir",
    })

    @staticmethod
    def likely_lacks_init(image_ref: str) -> bool:
        """Tier 1: does the image reference suggest a missing init system?

        References containing ``init`` or ``systemd`` are treated as
        init-capable.  Otherwise the ref is reduced to its last path
        component (registry/org prefix, ``@digest`` and ``:tag`` stripped)
        before the deny-list membership test.  Empty refs are never
        flagged.
        """
        if not image_ref:
            return False
        if "init" in image_ref or "systemd" in image_ref:
            return False
        name = image_ref.split("@", 1)[0]
        name = name.rsplit("/", 1)[-1]
        name = name.rsplit(":", 1)[0]
        return name in MachineImageAdvisor.INITLESS_NAMES

    @staticmethod
    def logs_indicate_missing_init(log_lines: list[str]) -> bool:
        """Tier 2: do the boot logs show a failed ``/sbin/init`` exec?

        Any line mentioning ``/sbin/init`` or ``/sbin/openrc`` alongside
        ``not found`` or ``no such file`` means the image has no init
        system and the machine could not boot (SPEC phase 2 §2.2).  The
        marker match is case-insensitive — real logs emit e.g. "No such
        file or directory".
        """
        for line in log_lines:
            lower = line.lower()
            if ("/sbin/init" in lower or "/sbin/openrc" in lower) and (
                "not found" in lower or "no such file" in lower
            ):
                return True
        return False


# ==========================================================================
# Machina
# ==========================================================================


class Machina:
    """Persistent container machines — deus ex machina.

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

    async def create(self, spec: MachineCreateSpec) -> MachineInfo:
        """Create a persistent machine (no boot with ``no_boot``).

        Policy: the disk budget is checked before creation (forge
        ``_check_run`` pattern).  The advisor flags initless images as a
        warning on the returned :class:`MachineInfo`.
        """
        disk = await self._backend.system_df()
        r = self._policy.check_disk(disk)
        self._policy.log("check_disk", "machina", r)
        self._policy.enforce(r)

        info = await self._backend.machine_create(spec)
        if MachineImageAdvisor.likely_lacks_init(spec.image):
            info.warning = (
                f"{spec.image} may lack an init system — container machines "
                "boot the image's /sbin/init as PID 1; see SPEC phase 2 §2.2"
            )
        result: dict[str, Any] = {"id": info.id}
        if info.warning:
            result["warning"] = info.warning
        self._audit.record(
            "machine_create", actor="machina", actor_kind=ActorKind.SERVICE,
            args={
                "image": spec.image,
                "name": spec.name,
                "cpus": spec.cpus,
                "memory": spec.memory,
                "home_mount": spec.home_mount,
                "virtualization": spec.virtualization,
                "no_boot": spec.no_boot,
            },
            result=result,
        )
        return info

    async def list(self) -> list[MachineInfo]:
        """List machines (read-only: no policy check or audit)."""
        return await self._backend.machine_list()

    async def inspect(self, machine_id: str) -> MachineInfo:
        """Inspect a single machine (read-only: no policy check or audit)."""
        return await self._backend.machine_inspect(machine_id)

    async def set(self, machine_id: str, settings: MachineSetSettings) -> None:
        """Update machine settings (``cpus=``, ``memory=``, ...)."""
        await self._backend.machine_set(machine_id, settings)
        applied = {k: v for k, v in asdict(settings).items() if v is not None}
        self._audit.record(
            "machine_set", actor="machina", actor_kind=ActorKind.SERVICE,
            args={"machine_id": machine_id, **applied},
        )

    async def set_default(self, machine_id: str) -> None:
        """Make a machine the default (used when no id is given)."""
        await self._backend.machine_set_default(machine_id)
        self._audit.record(
            "machine_set_default", actor="machina", actor_kind=ActorKind.SERVICE,
            args={"machine_id": machine_id},
        )

    async def stop(self, machine_id: str) -> None:
        """Stop a machine."""
        await self._backend.machine_stop(machine_id)
        self._audit.record(
            "machine_stop", actor="machina", actor_kind=ActorKind.SERVICE,
            args={"machine_id": machine_id},
        )

    async def delete(self, machine_id: str, *, confirm: bool = False) -> None:
        """Delete a machine.

        Requires explicit ``confirm=True`` (policy-gated).
        """
        r = self._policy.check_machine_delete(confirm=confirm)
        if r.decision == Decision.CONFIRM:
            raise ValidationError("machine delete requires confirm=True")
        self._policy.log("check_machine_delete", "machina", r)
        self._policy.enforce(r)
        await self._backend.machine_delete(machine_id)
        self._audit.record(
            "machine_delete", actor="machina", actor_kind=ActorKind.SERVICE,
            args={"machine_id": machine_id, "confirm": confirm},
        )

    async def run(
        self,
        machine_id: str,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        gid: int | None = None,
        interactive: bool = False,
        timeout: float = 60.0,
    ) -> ExecResult:
        """Boot a machine if necessary and run a command inside it.

        Read/exec path like forge's ``exec`` — not policy-gated
        (SPEC phase 2 §3.5).
        """
        result = await self._backend.machine_run(
            machine_id, argv, env=env, gid=gid,
            interactive=interactive, timeout=timeout,
        )
        self._audit.record(
            "machine_run", actor="machina", actor_kind=ActorKind.SERVICE,
            args={"machine_id": machine_id, "argv": argv},
        )
        return result

    async def logs(self, machine_id: str) -> str:
        """Dump a machine's boot/stdout logs (read-only: no audit)."""
        return await self._backend.machine_logs(machine_id)

    async def explain_failure(self, machine_id: str) -> str | None:
        """Explain why a machine failed to boot, when known.

        Applies the advisor's authoritative log check to the machine's
        boot logs; returns a human-readable explanation when the image
        has no init system, else ``None`` (surfaces show the raw error).
        """
        logs = await self.logs(machine_id)
        if MachineImageAdvisor.logs_indicate_missing_init(logs.splitlines()):
            return (
                f"machine {machine_id} stopped because its image has no init "
                "system (/sbin/init) — use an init-capable image; see "
                "SPEC phase 2 §2.2"
            )
        return None
