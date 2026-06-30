"""Tests for M1 — forge lifecycle/system core."""

from __future__ import annotations

import pytest

from daedalus.core.backend import (
    Backend,
    BuildSpec,
    ContainerInfo,
    ContainerState,
    ExecResult,
    RunSpec,
)
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import PolicyViolationError, ValidationError
from daedalus.core.forge import DEFAULT_DETACHED_COMMAND, Forge, Labyrinth, SystemStatus
from daedalus.core.policy import PolicyConfig, PolicyEngine
from daedalus.core.store import Store

# ------------------------------------------------------------------
# Mock backend
# ------------------------------------------------------------------


class MockBackend(Backend):
    """In-memory backend for unit tests."""

    def __init__(self) -> None:
        self.containers: dict[str, ContainerInfo] = {}
        self.next_id = 1
        self.last_spec: RunSpec | None = None

    def _cid(self) -> str:
        cid = f"mock-{self.next_id:04d}"
        self.next_id += 1
        return cid

    def _info(self, cid: str, name: str, image: str, state: ContainerState) -> ContainerInfo:
        return ContainerInfo(id=cid, name=name, image=image, state=state, raw={"id": cid})

    # -- lifecycle --
    async def create(self, spec: RunSpec) -> ContainerInfo:
        info = self._info(self._cid(), spec.name or "", spec.image, ContainerState.CREATED)
        self.containers[info.id] = info
        return info

    async def run(self, spec: RunSpec) -> ContainerInfo:
        self.last_spec = spec
        info = self._info(self._cid(), spec.name or "", spec.image, ContainerState.RUNNING)
        self.containers[info.id] = info
        return info

    async def start(
        self,
        container_id: str,
        *,
        attach: bool = False,
        interactive: bool = False,
    ) -> None:
        if container_id in self.containers:
            self.containers[container_id].state = ContainerState.RUNNING

    async def stop(
        self,
        container_id: str,
        timeout: int = 10,
        *,
        signal: str | None = None,
    ) -> None:
        if container_id in self.containers:
            self.containers[container_id].state = ContainerState.STOPPED

    async def kill(self, container_id: str, signal: str = "KILL") -> None:
        if container_id in self.containers:
            self.containers[container_id].state = ContainerState.STOPPED

    async def delete(self, container_id: str, force: bool = False) -> None:
        self.containers.pop(container_id, None)

    async def list(self, all: bool = False) -> list[ContainerInfo]:
        if all:
            return list(self.containers.values())
        return [c for c in self.containers.values() if c.state == ContainerState.RUNNING]

    async def inspect(self, container_id: str) -> ContainerInfo:
        if container_id in self.containers:
            return self.containers[container_id]
        raise RuntimeError(f"container {container_id} not found")

    async def logs(self, container_id: str, **kwargs: object) -> str:
        return "mock logs\n"

    # -- interaction --
    async def exec(self, container_id: str, argv: list[str], **opts: object) -> ExecResult:
        return ExecResult(0, f"mock: {' '.join(argv)}", "")

    # -- images --
    async def image_pull(
        self,
        image: str,
        platform: str | None = None,
        scheme: str | None = None,
    ) -> None:
        pass
    async def image_push(
        self,
        image: str,
        platform: str | None = None,
        scheme: str | None = None,
    ) -> None:
        pass
    async def image_save(self, image: str, output: str) -> str:
        return output
    async def image_load(self, input_path: str) -> str:
        return "loaded-image:latest"
    async def image_tag(self, source: str, target: str) -> None:
        pass
    async def image_delete(self, image: str, *, all: bool = False) -> None:
        pass
    async def image_inspect(self, image: str) -> dict:
        return {"id": image}
    async def image_list(self, quiet: bool = False) -> list[dict]:
        return []
    async def image_prune(self) -> list[str]:
        return []
    async def build(self, spec: BuildSpec) -> str:
        return spec.tag or "built-image:latest"

    # -- registry --
    async def registry_login(self, server: str, **kwargs: object) -> None:
        pass
    async def registry_logout(self, server: str) -> None:
        pass
    async def registry_default_inspect(self) -> str:
        return ""
    async def registry_default_set(self, host: str, scheme: str | None = None) -> None:
        pass
    async def registry_default_unset(self) -> None:
        pass

    # -- builder --
    async def builder_status(self) -> dict:
        return {"running": False}
    async def builder_start(self, cpus: int = 2, memory: str = "2048M") -> None:
        pass
    async def builder_stop(self) -> None:
        pass
    async def builder_delete(self, force: bool = False) -> None:
        pass

    # -- system --
    async def system_start(self) -> None:
        pass
    async def system_stop(self) -> None:
        pass
    async def system_restart(self) -> None:
        pass
    async def system_logs(self, last: str = "5m", follow: bool = False) -> str:
        return ""
    async def system_kernel_set(self, **kwargs: object) -> None:
        pass
    async def system_dns_create(self, domain: str) -> None:
        pass
    async def system_dns_delete(self, domain: str) -> None:
        pass
    async def system_dns_list(self) -> list[str]:
        return []
    async def system_df(self) -> dict:
        return {"total": 100_000_000_000, "used": 5_000_000_000, "free": 95_000_000_000}

    async def _run_cli(self, *args: str, **kwargs: object) -> tuple[int, str, str]:
        return (0, "", "")


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
def forge(caps: CapabilityManifest, tmp_path) -> Forge:
    return Forge(MockBackend(), caps, store=Store(root=str(tmp_path / "store")))


@pytest.fixture
def strict_policy() -> PolicyEngine:
    return PolicyEngine(PolicyConfig(max_concurrent_vms=2))


# ------------------------------------------------------------------
# Lifecycle tests
# ------------------------------------------------------------------


class TestLifecycle:
    async def test_create(self, forge: Forge):
        lab = await forge.create("alpine:latest", name="test-create")
        assert isinstance(lab, Labyrinth)
        assert lab.name == "test-create"
        assert lab.state == "created"
        assert not lab.is_running

    async def test_run(self, forge: Forge):
        lab = await forge.run("alpine:latest", name="test-run")
        assert lab.state == "running"
        assert lab.is_running

    async def test_run_with_command(self, forge: Forge):
        lab = await forge.run("alpine:latest", command=["echo", "hello"])
        assert lab.state == "running"

    async def test_run_detached_keepalive(self, forge: Forge):
        backend = forge.backend
        assert isinstance(backend, MockBackend)
        await forge.run("alpine:latest", detach=True)
        assert backend.last_spec is not None
        assert backend.last_spec.command == DEFAULT_DETACHED_COMMAND

    async def test_stop(self, forge: Forge):
        lab = await forge.run("alpine:latest")
        stopped = await forge.stop(lab.id)
        assert stopped.state == "stopped"
        assert stopped.is_stopped

    async def test_kill(self, forge: Forge):
        lab = await forge.run("alpine:latest")
        killed = await forge.kill(lab.id, signal="TERM")
        assert killed.state == "stopped"

    async def test_delete(self, forge: Forge):
        lab = await forge.run("alpine:latest")
        await forge.delete(lab.id)
        with pytest.raises(RuntimeError):
            await forge.inspect(lab.id)

    async def test_destroy_requires_confirm(self, forge: Forge):
        lab = await forge.run("alpine:latest")
        with pytest.raises(ValidationError, match="confirm"):
            await forge.destroy(lab.id)

    async def test_destroy(self, forge: Forge):
        lab = await forge.run("alpine:latest")
        await forge.destroy(lab.id, confirm=True)
        with pytest.raises(RuntimeError):
            await forge.inspect(lab.id)

    async def test_list(self, forge: Forge):
        await forge.run("alpine:latest")
        await forge.run("busybox:latest")
        labs = await forge.list()
        assert len(labs) == 2
        assert all(lab.is_running for lab in labs)

    async def test_list_all(self, forge: Forge):
        lab = await forge.run("alpine:latest")
        await forge.stop(lab.id)
        labs = await forge.list(all=True)
        assert len(labs) == 1
        assert labs[0].is_stopped

    async def test_inspect(self, forge: Forge):
        lab = await forge.run("alpine:latest")
        inspected = await forge.inspect(lab.id)
        assert inspected.id == lab.id
        assert inspected.state == "running"

    async def test_get_cached(self, forge: Forge):
        lab = await forge.run("alpine:latest")
        cached = await forge.get(lab.id)
        assert cached is lab

    async def test_get_uncached(self, forge: Forge):
        info = await forge._backend.run(RunSpec(image="alpine:latest"))
        lab = await forge.get(info.id)
        assert lab.id == info.id

    async def test_profile_tracking(self, forge: Forge):
        lab = await forge.run("alpine:latest", name="profiled", profile="detonation")
        assert lab.profile == "detonation"

    async def test_run_injects_profile_label(self, forge: Forge):
        await forge.run("alpine:latest", profile="bench")
        spec = forge._backend.last_spec
        assert spec is not None
        assert spec.labels.get("daedalus.profile") == "bench"

    async def test_external_container_profile(self, forge: Forge):
        info = await forge._backend.run(RunSpec(image="alpine:latest"))
        labs = await forge.list()
        match = [lab for lab in labs if lab.id == info.id]
        assert len(match) == 1
        assert match[0].profile == "external"


class TestPolicyIntegration:
    async def test_concurrency_limit(self, caps: CapabilityManifest):
        f = Forge(MockBackend(), caps, policy=PolicyEngine(PolicyConfig(max_concurrent_vms=1)))
        await f.run("alpine:latest")
        with pytest.raises(PolicyViolationError):
            await f.run("alpine:latest")

    async def test_image_blocklist(self, caps: CapabilityManifest):
        f = Forge(MockBackend(), caps, policy=PolicyEngine(
            PolicyConfig(image_blocklist=["bad-image"]),
        ))
        with pytest.raises(PolicyViolationError):
            await f.run("bad-image:latest")


# ------------------------------------------------------------------
# System tests
# ------------------------------------------------------------------


class TestSystem:
    async def test_system_status(self, forge: Forge):
        status = await forge.system_status()
        assert isinstance(status, SystemStatus)
        assert status.container_version == "0.1.0"
        assert "total" in status.disk_usage

    async def test_system_status_counts_containers(self, forge: Forge):
        await forge.run("alpine:latest")
        await forge.run("busybox:latest")
        status = await forge.system_status()
        assert status.container_count == 2
        assert status.running_count == 2
