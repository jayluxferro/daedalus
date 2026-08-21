"""Integration tests — real container lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import tarfile
from pathlib import Path

import pytest

from daedalus.core.backend import NetworkSpec, PruneResult, RunSpec
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import PolicyViolationError
from daedalus.core.forge import Forge
from daedalus.core.icarus import Icarus
from daedalus.core.store import Store
from daedalus.core.talos import Talos

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


async def _run_detached_alpine(forge: Forge, command: list[str] | None = None) -> str:
    """Run a detached ``alpine:latest`` container; return its id.

    Skips when host policy blocks container creation.
    """
    try:
        lab = await forge.run(TEST_IMAGE, detach=True, command=command or ["sleep", "300"])
    except Exception as e:
        _skip_on_policy(e)
        raise
    return lab.id


def _artifact_field(artifact: object, name: str) -> str:
    """Read a store artifact field — dict when rehydrated from disk JSON,
    dataclass when read in-memory."""
    if isinstance(artifact, dict):
        return str(artifact.get(name, ""))
    return str(getattr(artifact, name, ""))


@pytest.mark.integration
class TestRealSystemOps:
    """End-to-end system-op tests — stats, copy, export, prune, network.

    These exercise the container 1.2.2 verb families (stats / copy /
    export / prune / network) through the real forge / icarus / talos
    engines against the live daemon.
    """

    async def test_stats_live(self, forge: Forge) -> None:
        """One-shot stats report sane numbers for a running container."""
        cid = await _run_detached_alpine(forge)
        try:
            await asyncio.sleep(4)  # let the VM boot and report metrics
            samples = await forge.stats(cid)
            assert samples, "stats returned no samples for a running container"
            sample = next((s for s in samples if s.id == cid), None)
            assert sample is not None, f"no sample for {cid} in {[s.id for s in samples]}"
            assert sample.memory_usage_bytes > 0
            assert sample.num_processes >= 1
            assert sample.sampled_at > 0.0
            # stats() with no id covers every running container
            all_samples = await forge.stats()
            assert any(s.id == cid for s in all_samples), "stats() omitted the running container"
        finally:
            with contextlib.suppress(Exception):
                await forge.destroy(cid, confirm=True)
            Store().delete(cid)

    async def test_copy_roundtrip_byte_identical(
        self, forge: Forge, icarus: Icarus, tmp_path: Path,
    ) -> None:
        """A file copied into a container and back out is byte-identical."""
        content = b"DAEDALUS-IT-COPY\x00" + bytes(range(48))  # 64 deterministic bytes
        src = tmp_path / "probe-in.bin"
        src.write_bytes(content)
        cid = await _run_detached_alpine(forge)
        container_path = "/tmp/daedalus-it-copy-probe.bin"
        out = tmp_path / "probe-out.bin"
        try:
            await icarus.copy_to(cid, str(src), container_path)
            await icarus.copy_from(cid, container_path, str(out))
            assert out.read_bytes() == content
        finally:
            with contextlib.suppress(Exception):
                await forge.destroy(cid, confirm=True)
            Store().delete(cid)

    async def test_export_fs_roundtrip(self, forge: Forge, icarus: Icarus, tmp_path: Path) -> None:
        """Export a running container's FS to a tar; the artifact is store-indexed."""
        cid = await _run_detached_alpine(forge)
        tar_path = tmp_path / "alpine-fs.tar"
        try:
            result = await icarus.export_fs(cid, output_path=str(tar_path))
            assert result["container_id"] == cid
            assert result["path"] == str(tar_path)
            assert result["artifact"]["kind"] == "fs"
            assert tar_path.is_file()
            assert tarfile.is_tarfile(str(tar_path))
            # forge.run created the run manifest; export added the fs artifact
            manifest = Store().get(cid)
            assert manifest is not None, "run manifest missing for exported container"
            fs_artifacts = [a for a in manifest.artifacts if _artifact_field(a, "kind") == "fs"]
            assert fs_artifacts, "store manifest has no fs artifact"
            assert _artifact_field(fs_artifacts[0], "name") == "filesystem.tar"
            assert _artifact_field(fs_artifacts[0], "path") == str(tar_path)
        finally:
            with contextlib.suppress(Exception):
                await forge.destroy(cid, confirm=True)
            Store().delete(cid)

    async def test_prune_removes_stopped(self, forge: Forge, backend: CliBackend) -> None:
        """``container prune`` reclaims a stopped container."""
        cid = await _run_detached_alpine(forge)
        try:
            await forge.stop(cid, timeout=5)
            result = await backend.prune()
            assert isinstance(result, PruneResult)
            assert isinstance(result.ids, list)
            # The "Reclaimed …" line goes to stderr on this runtime, so
            # message may be empty — the removed ids are the contract here.
            assert cid in result.ids, f"pruned ids {result.ids} do not include {cid}"
        finally:
            with contextlib.suppress(Exception):
                await forge.destroy(cid, confirm=True)
            Store().delete(cid)

    async def test_network_roundtrip(self, backend: CliBackend, caps: CapabilityManifest) -> None:
        """Network create → list → inspect → delete round-trip on the live runtime."""
        if caps.networking is not True:
            pytest.skip("networking capability not available on this host")
        talos = Talos(backend, caps)
        name = "daedalus-it-net"
        # Clear any leftovers from an aborted previous run.
        with contextlib.suppress(Exception):
            await talos.network_delete(name, confirm=True)
        try:
            info = await talos.network_create(
                NetworkSpec(name=name, internal=True, subnet="172.31.0.0/24"),
            )
            assert info.name == name
            listed = {n.name for n in await talos.network_list()}
            assert name in listed, f"created network missing from list: {sorted(listed)}"
            inspected = await talos.network_inspect(name)
            assert inspected.name == name
            assert inspected.id == info.id
        finally:
            with contextlib.suppress(Exception):
                await talos.network_delete(name, confirm=True)
        names_after = {n.name for n in await talos.network_list()}
        assert name not in names_after, "deleted network still listed"
