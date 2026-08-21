"""M4 — talos: network guardian.

Talos builds named topologies declaratively, controls DNS, and enforces
traffic policy.

Talos owns the full ``container network`` surface: network CRUD
(create/list/inspect/delete) and topology materialization.  The
``container network`` subcommand is a top-level command in container
1.2.2 (default plugin ``container-network-vmnet``).  DNS control via
``--dns`` flags on ``container run`` and system DNS management via
``container system dns`` remain part of Talos's remit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import Backend, NetworkInfo, NetworkSpec
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import ValidationError
from daedalus.core.policy import Decision, PolicyEngine

# ==========================================================================
# Topology model
# ==========================================================================


@dataclass
class TopologyAttachment:
    """How one container attaches to one network."""

    container: str
    network: str
    mac: str | None = None
    mtu: int | None = None
    ip: str | None = None


@dataclass
class DNSEntry:
    """A lying-DNS entry — map a domain to a controlled IP."""

    domain: str
    target: str


@dataclass
class TopologyTemplate:
    """A named topology of networks, attachments, and DNS overrides."""

    name: str
    description: str = ""
    networks: list[NetworkSpec] = field(default_factory=list)
    attachments: list[TopologyAttachment] = field(default_factory=list)
    dns_entries: list[DNSEntry] = field(default_factory=list)
    internal: bool = True


@dataclass
class Topology:
    """A materialised topology — networks have been created."""

    template: TopologyTemplate
    network_ids: dict[str, str] = field(default_factory=dict)


# ==========================================================================
# Talos
# ==========================================================================


class Talos:
    """Network guardian for DAEDALUS.

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
        self._caps = capabilities
        self._policy = policy or PolicyEngine()
        self._audit = audit or AuditLog()
        self._topologies: dict[str, Topology] = {}

    # ==================================================================
    # DNS control
    # ==================================================================

    @staticmethod
    def dns_args(
        *,
        servers: list[str] | None = None,
        domains: list[str] | None = None,
        search: list[str] | None = None,
        disable: bool = False,
    ) -> list[str]:
        """Build ``--dns`` / ``--dns-domain`` / etc arguments for
        ``container run/create``.
        """
        args: list[str] = []
        if disable:
            args.append("--no-dns")
        for s in servers or []:
            args += ["--dns", s]
        for d in domains or []:
            args += ["--dns-domain", d]
        for s in search or []:
            args += ["--dns-search", s]
        return args

    # ==================================================================
    # System DNS (local resolvable domains)
    # ==================================================================

    async def system_dns_create(self, domain: str) -> None:
        """Create a local DNS domain (requires administrator)."""
        await self._backend.system_dns_create(domain)
        self._audit.record(
            "system_dns_create", actor="talos", actor_kind=ActorKind.SERVICE,
            args={"domain": domain},
        )

    async def system_dns_delete(self, domain: str) -> None:
        """Delete a local DNS domain (requires administrator)."""
        await self._backend.system_dns_delete(domain)
        self._audit.record(
            "system_dns_delete", actor="talos", actor_kind=ActorKind.SERVICE,
            args={"domain": domain},
        )

    async def system_dns_list(self) -> list[str]:
        """List local DNS domains."""
        return await self._backend.system_dns_list()

    # ==================================================================
    # Topology templates (YAML → live networks)
    # ==================================================================

    @staticmethod
    def parse_topology(data: dict[str, Any]) -> TopologyTemplate:
        """Parse a topology template from a parsed YAML/JSON dict."""
        networks = [
            NetworkSpec(
                name=n["name"],
                subnet=n.get("subnet"),
                subnet_v6=n.get("subnet_v6"),
                internal=n.get("internal", True),
                plugin=n.get("plugin", "container-network-vmnet"),
                options=n.get("options", {}),
            )
            for n in data.get("networks", [])
        ]

        attachments = [
            TopologyAttachment(
                container=a["container"],
                network=a["network"],
                mac=a.get("mac"),
                mtu=a.get("mtu"),
                ip=a.get("ip"),
            )
            for a in data.get("attachments", [])
        ]

        dns_entries = [
            DNSEntry(domain=d["domain"], target=d["target"])
            for d in data.get("dns_entries", [])
        ]

        return TopologyTemplate(
            name=data["name"],
            description=data.get("description", ""),
            networks=networks,
            attachments=attachments,
            dns_entries=dns_entries,
            internal=data.get("internal", True),
        )

    @staticmethod
    def load_topology(path: str) -> TopologyTemplate:
        """Parse a topology template from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("topology file must contain a mapping")
        return Talos.parse_topology(data)

    # ==================================================================
    # Network CRUD (container 1.2.2)
    # ==================================================================

    async def network_create(self, spec: NetworkSpec) -> NetworkInfo:
        """Create a network.

        Host-routable networks (``internal=False``) are policy-gated via
        ``check_network_expose`` — creating one without an explicit
        confirm is rejected.  Internal networks skip the check.
        """
        if not spec.internal:
            r = self._policy.check_network_expose()
            if r.decision == Decision.CONFIRM:
                raise ValidationError("network create requires confirm=True")
            self._policy.log("check_network_expose", "talos", r)
            self._policy.enforce(r)
        info = await self._backend.network_create(spec)
        self._audit.record(
            "network_create", actor="talos", actor_kind=ActorKind.SERVICE,
            args={
                "name": spec.name,
                "subnet": spec.subnet,
                "subnet_v6": spec.subnet_v6,
                "internal": spec.internal,
            },
        )
        return info

    async def network_list(self) -> list[NetworkInfo]:
        """List networks (read-only, no policy or audit)."""
        return await self._backend.network_list()

    async def network_inspect(self, name: str) -> NetworkInfo:
        """Inspect a single network (read-only, no policy or audit)."""
        return await self._backend.network_inspect(name)

    async def network_delete(self, name: str, *, confirm: bool = False) -> None:
        """Delete a network.

        Requires explicit ``confirm=True`` (policy-gated).
        """
        r = self._policy.check_network_delete(confirm=confirm)
        if r.decision == Decision.CONFIRM:
            raise ValidationError("network delete requires confirm=True")
        self._policy.log("check_network_delete", "talos", r)
        self._policy.enforce(r)
        await self._backend.network_delete(name)
        self._audit.record(
            "network_delete", actor="talos", actor_kind=ActorKind.SERVICE,
            args={"name": name, "confirm": confirm},
        )

    # ==================================================================
    # Topology materialization
    # ==================================================================

    async def apply_topology(self, template: TopologyTemplate) -> Topology:
        """Materialise a topology template into live networks.

        Each network in ``template.networks`` is created unless a network
        with the same name already exists (data-driven dedupe); the
        resulting ``network_ids`` maps every template network name to its
        live network id (the existing id for skipped names).

        Attachments are NOT applied here — the caller attaches containers
        via ``Forge.run(..., network=...)`` (``RunSpec.network``).
        """
        existing = {n.name: n for n in await self.network_list()}
        network_ids: dict[str, str] = {}
        for spec in template.networks:
            if spec.name in network_ids:
                continue
            if spec.name in existing:
                network_ids[spec.name] = existing[spec.name].id
            else:
                info = await self.network_create(spec)
                network_ids[spec.name] = info.id
        topology = Topology(template=template, network_ids=network_ids)
        self._topologies[template.name] = topology
        self._audit.record(
            "topology_apply", actor="talos", actor_kind=ActorKind.SERVICE,
            args={"name": template.name, "networks": [n.name for n in template.networks]},
        )
        return topology
