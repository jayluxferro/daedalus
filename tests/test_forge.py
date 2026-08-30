"""Tests for M1 — forge lifecycle/system core."""

from __future__ import annotations

import asyncio
import builtins
from dataclasses import replace

import pytest

from daedalus.core.backend import (
    Backend,
    BuildSpec,
    ContainerInfo,
    ContainerState,
    ExecResult,
    MachineCreateSpec,
    MachineInfo,
    MachineSetSettings,
    NetworkInfo,
    NetworkSpec,
    PruneResult,
    RunSpec,
    StatsSample,
    VolumeInfo,
    VolumeSpec,
)
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import DetonationError, PolicyViolationError, ValidationError
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
        # Flat call-order log — every verb stub appends its short name here
        # so tests can assert the relative order of backend operations.
        self.call_log: list[str] = []
        self.stats_calls: list[str | None] = []
        self.stats_result: list[StatsSample] = [
            StatsSample(
                id="mock-0001",
                cpu_usage_usec=2_219,
                memory_usage_bytes=2_764_800,
                memory_limit_bytes=1_073_741_824,
                network_rx_bytes=28_040,
                network_tx_bytes=602,
                block_read_bytes=2_596_864,
                block_write_bytes=0,
                num_processes=1,
            ),
        ]
        self.networks: dict[str, NetworkInfo] = {}
        self.network_create_calls: list[NetworkSpec] = []
        self.network_delete_calls: list[str] = []
        self.machine_calls: list[tuple[str, tuple]] = []
        self.machine_logs_result: str = ""
        self.volumes: dict[str, VolumeInfo] = {}
        self.volume_calls: list[tuple[str, tuple]] = []

    def _cid(self) -> str:
        cid = f"mock-{self.next_id:04d}"
        self.next_id += 1
        return cid

    def _mid(self) -> str:
        mid = f"m{self.next_id}"
        self.next_id += 1
        return mid

    def _info(self, cid: str, name: str, image: str, state: ContainerState) -> ContainerInfo:
        return ContainerInfo(id=cid, name=name, image=image, state=state, raw={"id": cid})

    # -- lifecycle --
    async def create(self, spec: RunSpec) -> ContainerInfo:
        info = self._info(self._cid(), spec.name or "", spec.image, ContainerState.CREATED)
        self.containers[info.id] = info
        return info

    async def run(self, spec: RunSpec) -> ContainerInfo:
        self.call_log.append("run")
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
        *,
        timeout: int = 10,
        signal: str | None = None,
        all: bool = False,
    ) -> None:
        self.call_log.append("stop")
        if container_id in self.containers:
            self.containers[container_id].state = ContainerState.STOPPED

    async def kill(self, container_id: str, signal: str = "KILL") -> None:
        if container_id in self.containers:
            self.containers[container_id].state = ContainerState.STOPPED

    async def delete(
        self, container_id: str, *, force: bool = False, all: bool = False,
    ) -> None:
        self.call_log.append("delete")
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
        self.call_log.append("logs")
        return "mock logs\n"
    async def exec(self, container_id: str, argv: list[str], **opts: object) -> ExecResult:
        self.call_log.append("exec")
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
    async def system_logs(self, last: str = "5m", follow: bool = False, follow_seconds: float | None = None) -> str:
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

    # -- stats / copy / export / prune / network (no-op canned stubs) --
    async def stats(self, container_id: str | None = None) -> list[StatsSample]:
        self.call_log.append("stats")
        self.stats_calls.append(container_id)
        return list(self.stats_result)
    async def copy(self, source: str, destination: str) -> str:
        self.call_log.append("copy")
        return destination
    async def export(self, container_id: str, output_path: str) -> str:
        self.call_log.append("export")
        return output_path
    async def prune(self) -> PruneResult:
        return PruneResult()
    async def network_create(self, spec: NetworkSpec) -> NetworkInfo:
        self.call_log.append("network_create")
        self.network_create_calls.append(spec)
        info = NetworkInfo(id=spec.name, name=spec.name, raw={"mock": True})
        self.networks[spec.name] = info
        return info
    async def network_list(self) -> list[NetworkInfo]:
        return list(self.networks.values())
    async def network_inspect(self, name: str) -> NetworkInfo:
        return self.networks.get(name, NetworkInfo(id=name, name=name))
    async def network_delete(self, name: str) -> None:
        self.call_log.append("network_delete")
        self.network_delete_calls.append(name)
        self.networks.pop(name, None)

    # -- machines (canned call-recording stubs) --
    async def machine_create(self, spec: MachineCreateSpec) -> MachineInfo:
        self.machine_calls.append(("create", (spec,)))
        return MachineInfo(id=self._mid())
    async def machine_list(self) -> list[MachineInfo]:
        self.machine_calls.append(("list", ()))
        return []
    async def machine_inspect(self, machine_id: str) -> MachineInfo:
        self.machine_calls.append(("inspect", (machine_id,)))
        return MachineInfo(id=machine_id)
    async def machine_delete(self, machine_id: str) -> None:
        self.machine_calls.append(("delete", (machine_id,)))
    async def machine_stop(self, machine_id: str) -> None:
        self.machine_calls.append(("stop", (machine_id,)))
    async def machine_set(
        self, machine_id: str, settings: MachineSetSettings,
    ) -> None:
        self.machine_calls.append(("set", (machine_id, settings)))
    async def machine_set_default(self, machine_id: str) -> None:
        self.machine_calls.append(("set_default", (machine_id,)))
    async def machine_run(
        self,
        machine_id: str,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        gid: int | None = None,
        interactive: bool = False,
        timeout: float = 60.0,
        root: bool = False,
        ulimit: builtins.list[str] | None = None,
        detached: bool = False,
        tty: bool = False,
        user: str | None = None,
        workdir: str | None = None,
    ) -> ExecResult:
        self.machine_calls.append(
            ("run", (machine_id, argv, env, gid, interactive, timeout)),
        )
        return ExecResult(0, "", "")
    async def machine_logs(
        self,
        machine_id: str,
        *,
        boot: bool = False,
        follow: bool = False,
        tail: int | None = None,
    ) -> str:
        self.machine_calls.append(("logs", (machine_id,)))
        return self.machine_logs_result

    # -- volumes (canned call-recording stubs) --
    async def volume_create(self, spec: VolumeSpec) -> VolumeInfo:
        self.volume_calls.append(("create", (spec,)))
        info = VolumeInfo(id=spec.name, name=spec.name, raw={"mock": True})
        self.volumes[spec.name] = info
        return info
    async def volume_list(self) -> list[VolumeInfo]:
        self.volume_calls.append(("list", ()))
        return list(self.volumes.values())
    async def volume_inspect(self, name: str) -> VolumeInfo:
        self.volume_calls.append(("inspect", (name,)))
        return self.volumes.get(name, VolumeInfo(id=name, name=name))
    async def volume_delete(self, name: str) -> None:
        self.volume_calls.append(("delete", (name,)))
        self.volumes.pop(name, None)
    async def volume_prune(self) -> list[str]:
        self.volume_calls.append(("prune", ()))
        removed = list(self.volumes.keys())
        self.volumes.clear()
        return removed

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
        # Represents a patched host for the detonation tests below (the
        # runtime-security gate reads this field; unset would hard-block).
        security_safe=True,
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

    async def test_profile_prefers_label_over_stale_memory(self, forge: Forge) -> None:
        """In-memory profile must not mask store/label updates from another client."""
        lab = await forge.run("alpine:latest", name="stale-profile", profile="detonation")
        lab.info.raw = {
            "configuration": {"labels": {"daedalus.profile": "bench"}},
        }
        forge._store.create(lab.id, image="alpine:latest", profile="bench")
        labs = await forge.list()
        match = [item for item in labs if item.id == lab.id]
        assert len(match) == 1
        assert match[0].profile == "bench"


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


# ------------------------------------------------------------------
# Stats tests
# ------------------------------------------------------------------


class TestStats:
    async def test_stats_delegates(self, forge: Forge):
        backend = forge.backend
        assert isinstance(backend, MockBackend)
        samples = await forge.stats("mock-0001")
        assert samples == backend.stats_result
        assert backend.stats_calls == ["mock-0001"]

    async def test_stats_all(self, forge: Forge):
        backend = forge.backend
        assert isinstance(backend, MockBackend)
        samples = await forge.stats()
        assert samples == backend.stats_result
        assert backend.stats_calls == [None]


# ------------------------------------------------------------------
# Detonation loop tests (phase 3, W2)
# ------------------------------------------------------------------


class SlowExecMockBackend(MockBackend):
    """MockBackend whose exec takes ~150ms so the stats sampler can fire."""

    async def exec(self, container_id: str, argv: list[str], **opts: object) -> ExecResult:
        self.call_log.append("exec")
        await asyncio.sleep(0.15)
        return ExecResult(0, f"mock: {' '.join(argv)}", "")


class TestDetonate:
    @staticmethod
    def _sample_file(tmp_path) -> str:
        sample = tmp_path / "sample.sh"
        sample.write_bytes(b"#!/bin/sh\nexit 0\n")
        return str(sample)

    @staticmethod
    def _assert_relative_order(call_log: list[str], verbs: list[str]) -> None:
        """Assert verbs appear in call_log in the given relative order.

        Duplicate verbs resolve to successive occurrences (e.g. both
        exports), and unrelated calls (stop, stats) may interleave.
        """
        pos = -1
        for verb in verbs:
            pos = next(i for i, c in enumerate(call_log) if c == verb and i > pos)

    async def test_detonate_orders_the_loop(
        self, caps: CapabilityManifest, tmp_path,
    ):
        backend = SlowExecMockBackend()
        forge = Forge(
            backend, caps,
            store=Store(root=str(tmp_path / "store")),
        )
        result = await forge.detonate(
            self._sample_file(tmp_path),
            observe_seconds=5.0,
            stats_interval=0.05,  # sampler fires while exec is slow
        )
        assert result["run_id"]
        self._assert_relative_order(backend.call_log, [
            "network_create", "run", "export", "copy", "exec",
            "logs", "export", "delete", "network_delete",
        ])
        # Stats sampled while the sample exec runs: present, between exec
        # and the detonation logs (not positionally asserted beyond that).
        assert "stats" in backend.call_log
        exec_idx = backend.call_log.index("exec")
        first_stats = backend.call_log.index("stats")
        first_logs = backend.call_log.index("logs")
        assert exec_idx < first_stats < first_logs
        assert backend.call_log[-1] == "network_delete"

    async def test_detonate_returns_report_with_fs_changes(
        self, caps: CapabilityManifest, tmp_path,
    ):
        store = Store(root=str(tmp_path / "store"))
        backend = MockBackend()
        forge = Forge(backend, caps, store=store)
        sample = self._sample_file(tmp_path)
        result = await forge.detonate(sample, observe_seconds=5.0)
        # Mock export writes no real tars → pre/post paths don't exist →
        # Minos sees an empty diff; orchestration-level, real diff belongs
        # to test_minos.
        assert result["run_id"]
        assert result["report"]["run_id"] == result["run_id"]
        assert result["report"]["filesystem"]["total_changes"] == 0
        assert "risk_score" in result
        assert result["report_path"].endswith(f"{result['run_id']}.report.json")
        assert result["report_path"] in result["artifacts"]
        assert result["exit_code"] == 0

        manifest = store.get(result["run_id"])
        assert manifest is not None
        # One artifact entry per evidence path (Store.add_artifact dedupes
        # by path — icarus's generic export registration and detonate's
        # pre/post registrations point at the same files).
        paths = {a.path for a in manifest.artifacts}
        assert result["report_path"] in paths
        assert any(p.endswith(".pre.tar") for p in paths)
        assert any(p.endswith(".post.tar") for p in paths)

        entries = forge._audit.query(operation="detonate")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.actor == "forge"
        assert entry.args["sample"] == sample
        assert entry.args["profile"] == "detonation"
        assert entry.result["run_id"] == result["run_id"]
        assert entry.result["risk_score"] == result["risk_score"]
        assert entry.result["fs_changes"] == 0

    async def test_detonate_failure_destroys_and_cleans(
        self, caps: CapabilityManifest, tmp_path, monkeypatch: pytest.MonkeyPatch,
    ):
        store = Store(root=str(tmp_path / "store"))
        backend = MockBackend()
        forge = Forge(backend, caps, store=store)

        async def _boom(container_id: str, argv: list[str], **opts: object) -> None:
            backend.call_log.append("exec")
            raise RuntimeError("sample crashed")

        monkeypatch.setattr(backend, "exec", _boom)
        with pytest.raises(DetonationError) as ei:
            await forge.detonate(self._sample_file(tmp_path))
        exc = ei.value
        assert exc.code == "DETONATION_ERROR"
        assert isinstance(exc.__cause__, RuntimeError)
        # Partial evidence: the pre-export landed, post-export/report did not.
        assert "pre_tar" in exc.partial_evidence
        assert "post_tar" not in exc.partial_evidence
        assert "report_path" not in exc.partial_evidence
        # Cleanup: destroy then disposable network delete, no network leak.
        assert backend.call_log[-2:] == ["delete", "network_delete"]
        assert backend.network_delete_calls

        manifest = store.list()[0]
        names = {a.name for a in manifest.artifacts}
        # Only what was actually written before the failure (plus the
        # best-effort destroy's own report artifact).
        assert "report.json" not in names
        assert "filesystem.post.tar" not in names

    async def test_detonate_reuses_existing_network(
        self, caps: CapabilityManifest, tmp_path,
    ):
        backend = MockBackend()
        forge = Forge(
            backend, caps,
            store=Store(root=str(tmp_path / "store")),
        )
        await forge.detonate(
            self._sample_file(tmp_path), network_name="preexisting",
        )
        # Provided network is reused: no create, delete still attempted.
        assert "network_create" not in backend.call_log
        assert backend.call_log[-1] == "network_delete"
        assert backend.network_delete_calls == ["preexisting"]

    @pytest.mark.parametrize("security_safe", [False, "untested"])
    async def test_detonate_gated_on_unpatched_runtime(
        self, caps: CapabilityManifest, tmp_path, security_safe: bool | str,
    ):
        """Below the 1.3.1 advisory floor (or unknown) → hard block, no backend calls."""
        unsafe = replace(caps, security_safe=security_safe)
        backend = MockBackend()
        forge = Forge(
            backend, unsafe,
            store=Store(root=str(tmp_path / "store")),
        )
        with pytest.raises(ValidationError, match="1.3.1"):
            await forge.detonate(self._sample_file(tmp_path))
        # The gate fires before any backend call — not even the network.
        assert backend.call_log == []

    async def test_detonate_proceeds_on_patched_runtime(
        self, caps: CapabilityManifest, tmp_path,
    ):
        backend = MockBackend()
        forge = Forge(
            backend, caps,  # caps fixture carries security_safe=True
            store=Store(root=str(tmp_path / "store")),
        )
        result = await forge.detonate(
            self._sample_file(tmp_path), observe_seconds=5.0,
        )
        assert result["run_id"]
        assert backend.call_log  # the loop ran past the security gate
