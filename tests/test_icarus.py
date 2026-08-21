"""Tests for M2 — icarus interaction & extraction."""

import pytest

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import ExecResult, RunSpec
from daedalus.core.icarus import ExecOptions, Icarus
from daedalus.core.store import Store
from tests.test_forge import MockBackend


class RecordingBackend(MockBackend):
    """MockBackend that records ``copy`` / ``export`` call arguments."""

    def __init__(self) -> None:
        super().__init__()
        self.copy_calls: list[tuple[str, str]] = []
        self.export_calls: list[tuple[str, str]] = []

    async def copy(self, source: str, destination: str) -> str:
        self.copy_calls.append((source, destination))
        return destination

    async def export(self, container_id: str, output_path: str) -> str:
        self.export_calls.append((container_id, output_path))
        return output_path


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend()


@pytest.fixture
def audit(tmp_path) -> AuditLog:
    return AuditLog(path=str(tmp_path / "audit.jsonl"))


@pytest.fixture
def icarus(backend: MockBackend, audit: AuditLog) -> Icarus:
    return Icarus(backend, audit=audit)


class TestExec:
    async def test_exec_basic(self, icarus: Icarus, backend: MockBackend):
        info = await backend.run(RunSpec(image="alpine:latest"))
        result = await icarus.exec(info.id, ["echo", "hello"])
        assert isinstance(result, ExecResult)
        assert result.exit_code == 0
        assert "hello" in result.stdout

    async def test_exec_with_options(self, icarus: Icarus, backend: MockBackend):
        info = await backend.run(RunSpec(image="alpine:latest"))
        opts = ExecOptions(user="nobody", workdir="/tmp", tty=True)
        result = await icarus.exec(info.id, ["ls"], options=opts)
        assert result.exit_code == 0

    async def test_shell(self, icarus: Icarus, backend: MockBackend):
        info = await backend.run(RunSpec(image="alpine:latest"))
        result = await icarus.shell(info.id, "echo hello")
        assert result.exit_code == 0


class TestLogs:
    async def test_logs_default(self, icarus: Icarus, backend: MockBackend):
        info = await backend.run(RunSpec(image="alpine:latest"))
        logs = await icarus.logs(info.id)
        assert isinstance(logs, str)

    async def test_logs_boot(self, icarus: Icarus, backend: MockBackend):
        info = await backend.run(RunSpec(image="alpine:latest"))
        logs = await icarus.logs(info.id, boot=True)
        assert isinstance(logs, str)

    async def test_logs_tail(self, icarus: Icarus, backend: MockBackend):
        info = await backend.run(RunSpec(image="alpine:latest"))
        logs = await icarus.logs(info.id, tail=50)
        assert isinstance(logs, str)


class TestCopy:
    async def test_copy_from_builds_id_prefix(
        self, tmp_path, audit: AuditLog,
    ):
        backend = RecordingBackend()
        icarus = Icarus(backend, audit=audit)
        host_path = str(tmp_path / "out.txt")
        result = await icarus.copy_from("mock-0001", "/etc/hostname", host_path)
        assert backend.copy_calls == [("mock-0001:/etc/hostname", host_path)]
        assert result == host_path

        entries = audit.query(operation="copy_from")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.actor == "icarus"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {
            "container_id": "mock-0001",
            "container_path": "/etc/hostname",
            "host_path": host_path,
        }

    async def test_copy_to_builds_id_prefix(self, tmp_path, audit: AuditLog):
        backend = RecordingBackend()
        icarus = Icarus(backend, audit=audit)
        host_path = str(tmp_path / "in.txt")
        result = await icarus.copy_to("mock-0001", host_path, "/tmp/in.txt")
        assert backend.copy_calls == [(host_path, "mock-0001:/tmp/in.txt")]
        assert result == "mock-0001:/tmp/in.txt"

        entries = audit.query(operation="copy_to")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.actor == "icarus"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {
            "container_id": "mock-0001",
            "host_path": host_path,
            "container_path": "/tmp/in.txt",
        }


class TestExportFs:
    async def test_export_fs_default_path_and_artifact(
        self, tmp_path, audit: AuditLog,
    ):
        backend = RecordingBackend()
        store = Store(root=str(tmp_path / "store"))
        store.create("mock-0001", image="alpine:latest")
        icarus = Icarus(backend, store=store, audit=audit)

        result = await icarus.export_fs("mock-0001")
        expected = str(tmp_path / "store" / "mock-0001.fs.tar")
        assert backend.export_calls == [("mock-0001", expected)]

        assert result["path"] == expected
        assert result["container_id"] == "mock-0001"
        assert result["artifact"]["name"] == "filesystem.tar"
        assert result["artifact"]["path"] == expected
        assert result["artifact"]["kind"] == "fs"

        manifest = store.get("mock-0001")
        assert manifest is not None
        assert len(manifest.artifacts) == 1
        assert manifest.artifacts[0].name == "filesystem.tar"
        assert manifest.artifacts[0].path == expected
        assert manifest.artifacts[0].kind == "fs"

        entries = audit.query(operation="export")
        assert len(entries) == 1
        assert entries[0].args == {
            "container_id": "mock-0001",
            "output_path": expected,
        }

    async def test_export_fs_explicit_path(self, tmp_path, audit: AuditLog):
        backend = RecordingBackend()
        store = Store(root=str(tmp_path / "store"))
        icarus = Icarus(backend, store=store, audit=audit)

        out = str(tmp_path / "custom-export.tar")
        result = await icarus.export_fs("mock-0002", output_path=out)
        assert backend.export_calls == [("mock-0002", out)]
        assert result["path"] == out
        assert result["artifact"]["path"] == out
        assert result["artifact"]["kind"] == "fs"

        entries = audit.query(operation="export")
        assert len(entries) == 1
        assert entries[0].args == {
            "container_id": "mock-0002",
            "output_path": out,
        }
