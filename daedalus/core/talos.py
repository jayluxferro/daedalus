"""M4 — talos: network guardian.

Talos builds named topologies declaratively, controls DNS, and enforces
traffic policy.

.. note::
    ``container network`` is not wired as a top-level subcommand in
    container v0.1.0.  The plugin binary
    (``container-network-vmnet``) exists but the CLI command tree does
    not expose it yet.  Talos focuses on what *is* available today:
    DNS control via ``--dns`` flags on ``container run``, and system DNS
    management via ``container system dns``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import Backend, NetworkSpec
from daedalus.core.capabilities import CapabilityManifest

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
    audit:
        Optional audit log for operation recording.
    """

    def __init__(
        self,
        backend: Backend,
        capabilities: CapabilityManifest,
        *,
        audit: AuditLog | None = None,
    ) -> None:
        self._backend = backend
        self._caps = capabilities
        self._audit = audit or AuditLog()
        self._topologies: dict[str, Topology] = {}

    # ==================================================================
    # DNS control (available in v0.1.0)
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
    # System DNS (local resolvable domains) — available in v0.1.0
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
    def load_topology(path: str) -> TopologyTemplate:
        """Parse a topology template from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

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
