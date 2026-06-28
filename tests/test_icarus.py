"""Tests for M2 — icarus interaction & extraction."""

import pytest

from daedalus.core.backend import ExecResult, RunSpec
from daedalus.core.icarus import ExecOptions, Icarus
from tests.test_forge import MockBackend


@pytest.fixture
def backend() -> MockBackend:
    return MockBackend()


@pytest.fixture
def icarus(backend: MockBackend) -> Icarus:
    return Icarus(backend)


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
