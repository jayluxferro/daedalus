"""Tests for M5 — machina: persistent container machines."""

from __future__ import annotations

import pytest

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import (
    ExecResult,
    MachineCreateSpec,
    MachineInfo,
    MachineSetSettings,
)
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import PolicyViolationError, ValidationError
from daedalus.core.machina import Machina, MachineImageAdvisor
from daedalus.core.policy import Decision, PolicyEngine, PolicyResult
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
def machina(caps: CapabilityManifest) -> Machina:
    return Machina(MockBackend(), caps, audit=AuditLog())


# ------------------------------------------------------------------
# Advisor (SPEC phase 2 §2.3)
# ------------------------------------------------------------------


class TestAdvisor:
    def test_likely_lacks_init(self):
        assert MachineImageAdvisor.likely_lacks_init("alpine") is True
        assert MachineImageAdvisor.likely_lacks_init("debian") is True
        assert MachineImageAdvisor.likely_lacks_init("nginx") is True
        assert MachineImageAdvisor.likely_lacks_init("my/init-image") is False
        assert MachineImageAdvisor.likely_lacks_init("jrei/systemd-ubuntu") is False
        # @digest stripped before the membership test
        assert MachineImageAdvisor.likely_lacks_init("alpine@sha256:abc123") is True
        # registry/org prefix + :tag stripped before the membership test
        assert (
            MachineImageAdvisor.likely_lacks_init("docker.io/library/alpine:3.22")
            is True
        )
        assert MachineImageAdvisor.likely_lacks_init("") is False

    def test_logs_indicate_missing_init(self):
        assert MachineImageAdvisor.logs_indicate_missing_init(
            ["exec: /sbin/init: not found"],
        ) is True
        assert MachineImageAdvisor.logs_indicate_missing_init(
            ["can't run '/sbin/openrc': No such file or directory"],
        ) is True
        # normal systemd boot log — no missing-init markers
        assert MachineImageAdvisor.logs_indicate_missing_init(
            [
                "Booting Linux Kernel ...",
                "systemd[1]: Reached target Multi-User System.",
                "Ubuntu 24.04 ... ttyS0",
            ],
        ) is False
        assert MachineImageAdvisor.logs_indicate_missing_init([]) is False


# ------------------------------------------------------------------
# Create
# ------------------------------------------------------------------


class TestCreate:
    async def test_create_delegates_and_audits(self, machina: Machina):
        backend = machina._backend
        assert isinstance(backend, MockBackend)
        spec = MachineCreateSpec(
            image="alpine:latest", name="probe", cpus=2, memory="2G",
            home_mount="rw", virtualization=True, no_boot=True,
        )
        info = await machina.create(spec)
        assert isinstance(info, MachineInfo)
        assert info.id == "m1"
        assert backend.machine_calls == [("create", (spec,))]
        entry = machina._audit.query(operation="machine_create")[-1]
        assert entry.actor == "machina"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {
            "image": "alpine:latest",
            "name": "probe",
            "cpus": 2,
            "memory": "2G",
            "home_mount": "rw",
            "virtualization": True,
            "no_boot": True,
        }
        assert entry.result == {"id": "m1", "warning": info.warning}

    async def test_create_sets_advisor_warning(self, machina: Machina):
        info = await machina.create(MachineCreateSpec(image="alpine"))
        assert info.warning
        assert "init" in info.warning

    async def test_create_without_advisor_warning(self, machina: Machina):
        info = await machina.create(MachineCreateSpec(image="my-init-image"))
        assert info.warning == ""


# ------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------


class TestDelete:
    async def test_requires_confirm(self, machina: Machina):
        backend = machina._backend
        assert isinstance(backend, MockBackend)
        with pytest.raises(ValidationError, match="confirm"):
            await machina.delete("m1")
        assert backend.machine_calls == []

    async def test_with_confirm_delegates(self, machina: Machina):
        backend = machina._backend
        assert isinstance(backend, MockBackend)
        await machina.delete("m1", confirm=True)
        assert backend.machine_calls == [("delete", ("m1",))]
        entry = machina._audit.query(operation="machine_delete")[-1]
        assert entry.actor == "machina"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {"machine_id": "m1", "confirm": True}

    async def test_policy_deny(self, caps: CapabilityManifest):
        class DenyMachineDeletePolicy(PolicyEngine):
            def check_machine_delete(self, *, confirm: bool = False) -> PolicyResult:
                return PolicyResult(Decision.DENY, "machine delete forbidden by policy")

        m = Machina(
            MockBackend(), caps, policy=DenyMachineDeletePolicy(), audit=AuditLog(),
        )
        with pytest.raises(PolicyViolationError, match="forbidden"):
            await m.delete("m1", confirm=True)


# ------------------------------------------------------------------
# Run
# ------------------------------------------------------------------


class TestRun:
    async def test_run_returns_exec_result(self, machina: Machina):
        backend = machina._backend
        assert isinstance(backend, MockBackend)
        result = await machina.run("m1", ["uname", "-a"], gid=20, timeout=5.0)
        assert isinstance(result, ExecResult)
        assert result.exit_code == 0
        assert backend.machine_calls == [
            ("run", ("m1", ["uname", "-a"], None, 20, False, 5.0)),
        ]
        entry = machina._audit.query(operation="machine_run")[-1]
        assert entry.actor == "machina"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {"machine_id": "m1", "argv": ["uname", "-a"]}


# ------------------------------------------------------------------
# Explain failure
# ------------------------------------------------------------------


class TestExplainFailure:
    async def test_missing_init_logs_explained(self, machina: Machina):
        backend = machina._backend
        assert isinstance(backend, MockBackend)
        backend.machine_logs_result = "exec: /sbin/init: not found\n"
        explanation = await machina.explain_failure("m1")
        assert explanation is not None
        assert "init" in explanation

    async def test_normal_logs_no_explanation(self, machina: Machina):
        backend = machina._backend
        assert isinstance(backend, MockBackend)
        backend.machine_logs_result = "Linux 6.6.0 boot complete\n"
        assert await machina.explain_failure("m1") is None


# ------------------------------------------------------------------
# Set
# ------------------------------------------------------------------


class TestSet:
    async def test_set_to_set_args_delegation(self, machina: Machina):
        backend = machina._backend
        assert isinstance(backend, MockBackend)
        settings = MachineSetSettings(cpus=2, kernel="")
        await machina.set("m1", settings)
        assert len(backend.machine_calls) == 1
        name, (mid, recorded) = backend.machine_calls[0]
        assert name == "set"
        assert mid == "m1"
        assert recorded is settings
        assert recorded.to_set_args() == ["cpus=2", "kernel="]
        entry = machina._audit.query(operation="machine_set")[-1]
        assert entry.actor == "machina"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {"machine_id": "m1", "cpus": 2, "kernel": ""}
