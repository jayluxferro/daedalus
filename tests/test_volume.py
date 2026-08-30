"""Tests for V — volumes: the forensic/lab storage plane."""

from __future__ import annotations

import pytest

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import VolumeInfo, VolumeSpec
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import PolicyViolationError, ValidationError
from daedalus.core.policy import Decision, PolicyEngine, PolicyResult
from daedalus.core.volume import Volumes
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
def volumes(caps: CapabilityManifest) -> Volumes:
    return Volumes(MockBackend(), caps, audit=AuditLog())


# ------------------------------------------------------------------
# Create
# ------------------------------------------------------------------


class TestCreate:
    async def test_create_delegates_and_audits(self, volumes: Volumes):
        backend = volumes._backend
        assert isinstance(backend, MockBackend)
        spec = VolumeSpec(
            name="probe", size="100M",
            labels={"tier": "lab"}, options={"format": "ext4"},
        )
        info = await volumes.create(spec)
        assert isinstance(info, VolumeInfo)
        assert backend.volume_calls == [("create", (spec,))]
        entry = volumes._audit.query(operation="volume_create")[-1]
        assert entry.actor == "volumes"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {"name": "probe", "size": "100M"}
        assert entry.result == {"id": info.id}


# ------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------


class TestDelete:
    async def test_requires_confirm(self, volumes: Volumes):
        backend = volumes._backend
        assert isinstance(backend, MockBackend)
        with pytest.raises(ValidationError, match="confirm"):
            await volumes.delete("probe")
        assert backend.volume_calls == []

    async def test_with_confirm_delegates_and_audits(self, volumes: Volumes):
        backend = volumes._backend
        assert isinstance(backend, MockBackend)
        await volumes.delete("probe", confirm=True)
        assert backend.volume_calls == [("delete", ("probe",))]
        entry = volumes._audit.query(operation="volume_delete")[-1]
        assert entry.actor == "volumes"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {"name": "probe", "confirm": True}

    async def test_policy_deny(self, caps: CapabilityManifest):
        class DenyVolumeDeletePolicy(PolicyEngine):
            def check_volume_delete(self, *, confirm: bool = False) -> PolicyResult:
                return PolicyResult(Decision.DENY, "volume delete forbidden by policy")

        v = Volumes(
            MockBackend(), caps, policy=DenyVolumeDeletePolicy(), audit=AuditLog(),
        )
        with pytest.raises(PolicyViolationError, match="forbidden"):
            await v.delete("probe", confirm=True)


# ------------------------------------------------------------------
# Prune
# ------------------------------------------------------------------


class TestPrune:
    async def test_requires_confirm(self, volumes: Volumes):
        backend = volumes._backend
        assert isinstance(backend, MockBackend)
        with pytest.raises(ValidationError, match="confirm"):
            await volumes.prune()
        assert backend.volume_calls == []

    async def test_with_confirm_delegates_and_audits(self, volumes: Volumes):
        backend = volumes._backend
        assert isinstance(backend, MockBackend)
        pruned = await volumes.prune(confirm=True)
        assert pruned == []
        assert backend.volume_calls == [("prune", ())]
        entry = volumes._audit.query(operation="volume_prune")[-1]
        assert entry.actor == "volumes"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.result == {"count": 0}


# ------------------------------------------------------------------
# Reads (no audit)
# ------------------------------------------------------------------


class TestReads:
    async def test_list_and_inspect_no_audit(self, volumes: Volumes):
        backend = volumes._backend
        assert isinstance(backend, MockBackend)
        listed = await volumes.list()
        inspected = await volumes.inspect("probe")
        assert listed == []
        assert isinstance(inspected, VolumeInfo)
        assert backend.volume_calls == [("list", ()), ("inspect", ("probe",))]
        # Reads are not policy-gated and leave no audit trail.
        assert volumes._audit.count() == 0
