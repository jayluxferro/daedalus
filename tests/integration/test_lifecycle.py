"""Integration tests — real container lifecycle."""

from __future__ import annotations

import pytest

from daedalus.core.backend import RunSpec
from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import PolicyViolationError

TEST_IMAGE = "alpine:latest"


def _skip_on_policy(exc: BaseException) -> None:
    if isinstance(exc, PolicyViolationError) or "POLICY" in str(exc):
        pytest.skip("disk policy blocked container create on this host")


@pytest.mark.integration
class TestRealLifecycle:
    """End-to-end lifecycle tests using the real container CLI."""

    async def test_list_json(self, backend: CliBackend) -> None:
        """``container list --format json`` returns valid JSON."""
        result = await backend.list(all=True)
        assert isinstance(result, list)

    async def test_create_and_inspect(self, backend: CliBackend) -> None:
        """Create a container and inspect it."""
        spec = RunSpec(image=TEST_IMAGE, command=["echo", "hello"])
        try:
            info = await backend.create(spec)
        except Exception as e:
            _skip_on_policy(e)
            raise
        assert info.id
        assert info.image == TEST_IMAGE
        # Inspect should find it
        inspected = await backend.inspect(info.id)
        assert inspected.id == info.id
        # Cleanup
        await backend.delete(info.id, force=True)

    async def test_run_and_stop(self, backend: CliBackend) -> None:
        """Run a container, verify it appears, stop it."""
        spec = RunSpec(image=TEST_IMAGE, detach=True, command=["sleep", "30"])
        try:
            info = await backend.run(spec)
        except Exception as e:
            _skip_on_policy(e)
            raise
        assert info.id
        await backend.stop(info.id, timeout=5)
        await backend.delete(info.id, force=True)

    async def test_exec_inside_container(self, icarus) -> None:  # type: ignore[no-untyped-def]
        """Execute a command inside a running container."""
        import contextlib
        spec = RunSpec(image=TEST_IMAGE, detach=True, command=["sleep", "300"])
        try:
            info = await icarus._backend.run(spec)
        except Exception as e:
            _skip_on_policy(e)
            raise
        try:
            result = await icarus.exec(info.id, ["echo", "hello", "world"])
            assert result.exit_code == 0
            assert "hello" in result.stdout
        finally:
            with contextlib.suppress(Exception):
                await icarus._backend.kill(info.id)
            with contextlib.suppress(Exception):
                await icarus._backend.delete(info.id, force=True)

    async def test_logs(self, backend: CliBackend) -> None:
        """Retrieve logs from a container."""
        spec = RunSpec(image=TEST_IMAGE, detach=True, command=["sh", "-c", "echo log-test-42 && sleep 2"])
        try:
            info = await backend.run(spec)
        except Exception as e:
            _skip_on_policy(e)
            raise
        import asyncio
        await asyncio.sleep(3)
        logs = await backend.logs(info.id)
        assert "log-test-42" in logs
        await backend.delete(info.id, force=True)

    async def test_invalid_image(self, backend: CliBackend) -> None:
        """Running a nonexistent image should raise an error."""
        from daedalus.core.exceptions import BackendError
        spec = RunSpec(image="nonexistent-image-xyz-99999:latest")
        with pytest.raises(BackendError):
            await backend.run(spec)

    async def test_unknown_container_inspect(self, backend: CliBackend) -> None:
        """Inspecting a nonexistent container returns empty result."""
        result = await backend.inspect("nonexistent-container-id-12345")
        assert result.id == ""  # empty result, not an error
