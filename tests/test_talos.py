"""Tests for M4 — talos: network CRUD + topology materialization."""

from __future__ import annotations

import pytest

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import NetworkInfo, NetworkSpec
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import PolicyViolationError, ValidationError
from daedalus.core.policy import Decision, PolicyConfig, PolicyEngine, PolicyResult
from daedalus.core.talos import Talos, Topology, TopologyTemplate
from tests.test_forge import MockBackend

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def caps() -> CapabilityManifest:
    return CapabilityManifest(
        host_arch="arm64",
        macos_version="26.0",
        macos_version_tuple=(26, 0, 0),
        container_binary="/usr/local/bin/container",
        container_version="0.1.0",
        container_commit="test",
        apiserver_running=True,
        networking=True,
        kernel_set=True,
        builder=False,
        system_dns=True,
    )


@pytest.fixture
def strict_policy() -> PolicyEngine:
    return PolicyEngine(PolicyConfig(max_concurrent_vms=2))


@pytest.fixture
def talos(caps: CapabilityManifest) -> Talos:
    return Talos(MockBackend(), caps, audit=AuditLog())


# ------------------------------------------------------------------
# Network create
# ------------------------------------------------------------------


class TestNetworkCreate:
    async def test_delegates_and_audits(self, talos: Talos):
        backend = talos._backend
        assert isinstance(backend, MockBackend)
        spec = NetworkSpec(
            name="victim-net", subnet="192.168.99.0/24", internal=True,
        )
        info = await talos.network_create(spec)
        assert info.id == "victim-net"
        assert backend.network_create_calls == [spec]
        assert backend.networks["victim-net"] is info
        entry = talos._audit.query(operation="network_create")[-1]
        assert entry.actor == "talos"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {
            "name": "victim-net",
            "subnet": "192.168.99.0/24",
            "subnet_v6": None,
            "internal": True,
        }

    async def test_internal_skips_expose_policy(
        self, caps: CapabilityManifest, strict_policy: PolicyEngine
    ):
        t = Talos(MockBackend(), caps, policy=strict_policy, audit=AuditLog())
        spec = NetworkSpec(name="internal-net", internal=True)
        info = await t.network_create(spec)
        assert info.id == "internal-net"

    async def test_routable_requires_confirm(
        self, caps: CapabilityManifest, strict_policy: PolicyEngine
    ):
        t = Talos(MockBackend(), caps, policy=strict_policy, audit=AuditLog())
        spec = NetworkSpec(name="routable-net")
        with pytest.raises(ValidationError, match="confirm"):
            await t.network_create(spec)


# ------------------------------------------------------------------
# Network list / inspect
# ------------------------------------------------------------------


class TestNetworkReadOnly:
    async def test_list_delegates_no_audit(self, talos: Talos):
        backend = talos._backend
        assert isinstance(backend, MockBackend)
        backend.networks["net-a"] = NetworkInfo(id="net-a", name="net-a")
        assert [n.id for n in await talos.network_list()] == ["net-a"]
        assert talos._audit.count() == 0

    async def test_inspect_delegates_no_audit(self, talos: Talos):
        backend = talos._backend
        assert isinstance(backend, MockBackend)
        info = await talos.network_inspect("net-a")
        assert info.id == "net-a"
        assert talos._audit.count() == 0


# ------------------------------------------------------------------
# Network delete
# ------------------------------------------------------------------


class TestNetworkDelete:
    async def test_requires_confirm(self, talos: Talos):
        backend = talos._backend
        assert isinstance(backend, MockBackend)
        with pytest.raises(ValidationError, match="confirm"):
            await talos.network_delete("victim-net")
        assert backend.network_delete_calls == []

    async def test_with_confirm_delegates_and_audits(self, talos: Talos):
        backend = talos._backend
        assert isinstance(backend, MockBackend)
        backend.networks["victim-net"] = NetworkInfo(id="victim-net", name="victim-net")
        await talos.network_delete("victim-net", confirm=True)
        assert backend.network_delete_calls == ["victim-net"]
        assert "victim-net" not in backend.networks
        entry = talos._audit.query(operation="network_delete")[-1]
        assert entry.actor == "talos"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {"name": "victim-net", "confirm": True}

    async def test_policy_deny_despite_confirm(self, caps: CapabilityManifest):
        class DenyNetworkDeletePolicy(PolicyEngine):
            def check_network_delete(self, *, confirm: bool = False) -> PolicyResult:
                return PolicyResult(Decision.DENY, "network delete forbidden by policy")

        t = Talos(MockBackend(), caps, policy=DenyNetworkDeletePolicy(), audit=AuditLog())
        with pytest.raises(PolicyViolationError, match="forbidden"):
            await t.network_delete("victim-net", confirm=True)


# ------------------------------------------------------------------
# Topology materialization
# ------------------------------------------------------------------


class TestApplyTopology:
    async def test_creates_missing_skips_existing(self, talos: Talos):
        backend = talos._backend
        assert isinstance(backend, MockBackend)
        template = TopologyTemplate(
            name="lab",
            networks=[
                NetworkSpec(name="existing-net", internal=True),
                NetworkSpec(name="fresh-net", subnet="10.42.0.0/24", internal=True),
            ],
        )
        backend.networks["existing-net"] = NetworkInfo(id="existing-1", name="existing-net")
        topology = await talos.apply_topology(template)
        assert isinstance(topology, Topology)
        assert topology.template is template
        assert topology.network_ids == {
            "existing-net": "existing-1",
            "fresh-net": "fresh-net",
        }
        assert [c.name for c in backend.network_create_calls] == ["fresh-net"]

    async def test_audits(self, talos: Talos):
        backend = talos._backend
        assert isinstance(backend, MockBackend)
        template = TopologyTemplate(
            name="lab", networks=[NetworkSpec(name="n1", internal=True)],
        )
        await talos.apply_topology(template)
        entry = talos._audit.query(operation="topology_apply")[-1]
        assert entry.actor == "talos"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {"name": "lab", "networks": ["n1"]}
