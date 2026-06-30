"""Comprehensive API endpoint smoke tests — hits every route."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from daedalus.api.server import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", timeout=120.0,
        ) as ac:
            yield ac


@pytest.fixture
async def container_id(client: AsyncClient) -> str:
    r = await client.get("/containers?all=true")
    assert r.status_code == 200
    items = r.json()
    if not items:
        pytest.skip("no containers on host")
    return items[0]["id"]


@pytest.fixture
async def image_ref(client: AsyncClient) -> str:
    r = await client.get("/images")
    assert r.status_code == 200
    items = r.json()
    if not items:
        pytest.skip("no images on host")
    for preferred in ("docker.io/library/alpine:latest", "alpine:latest"):
        if any(i.get("name") == preferred for i in items):
            return preferred
    # Avoid chained test-tag names from prior runs
    for item in items:
        name = item.get("name", "")
        if "daedalus-test" not in name:
            return name
    return items[0]["name"]


class TestReadEndpoints:
    @pytest.mark.asyncio
    async def test_health(self, client: AsyncClient) -> None:
        r = await client.get("/health")
        assert r.status_code == 200
        assert "runtime_features" in r.json()

    @pytest.mark.asyncio
    async def test_profiles(self, client: AsyncClient) -> None:
        r = await client.get("/profiles")
        assert r.status_code == 200
        assert "detonation" in [p["name"] for p in r.json()]

    @pytest.mark.asyncio
    async def test_containers_list(self, client: AsyncClient) -> None:
        r = await client.get("/containers?all=true")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        if data:
            assert "ip" in data[0]
            assert "networks" in data[0]

    @pytest.mark.asyncio
    async def test_container_inspect(self, client: AsyncClient, container_id: str) -> None:
        r = await client.get(f"/containers/{container_id}")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)

    @pytest.mark.asyncio
    async def test_container_logs(self, client: AsyncClient, container_id: str) -> None:
        r = await client.get(f"/containers/{container_id}/logs")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_container_report_missing(self, client: AsyncClient) -> None:
        r = await client.get("/containers/nonexistent-id/report")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_images_list(self, client: AsyncClient) -> None:
        r = await client.get("/images")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_image_inspect(self, client: AsyncClient, image_ref: str) -> None:
        r = await client.get(f"/images/{image_ref}")
        assert r.status_code == 200
        assert r.json()["name"]

    @pytest.mark.asyncio
    async def test_experiments_list(self, client: AsyncClient) -> None:
        r = await client.get("/experiments")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_experiment_not_found(self, client: AsyncClient) -> None:
        r = await client.get("/experiments/does-not-exist")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_system_status(self, client: AsyncClient) -> None:
        r = await client.get("/system/status")
        assert r.status_code == 200
        data = r.json()
        assert "disk_usage" in data
        assert "capabilities" in data

    @pytest.mark.asyncio
    async def test_system_audit(self, client: AsyncClient) -> None:
        r = await client.get("/system/audit?limit=5")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_system_dns_list(self, client: AsyncClient) -> None:
        r = await client.get("/system/dns")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_builder_status(self, client: AsyncClient) -> None:
        r = await client.get("/builder/status")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)


class TestWriteEndpoints:
    @pytest.mark.asyncio
    async def test_topology_validate(self, client: AsyncClient) -> None:
        content = "name: test\nnetworks:\n  - name: n1\n    internal: true\n"
        r = await client.post("/topology/validate", json={"content": content})
        assert r.status_code == 200
        assert r.json()["valid"] is True

    @pytest.mark.asyncio
    async def test_topology_invalid(self, client: AsyncClient) -> None:
        r = await client.post("/topology/validate", json={"content": "not: yaml: ["})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_destroy_requires_confirm(self, client: AsyncClient, container_id: str) -> None:
        r = await client.delete(f"/containers/{container_id}")
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_image_prune(self, client: AsyncClient) -> None:
        r = await client.post("/images/prune")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "pruned"
        assert "count" in data

    @pytest.mark.asyncio
    async def test_image_tag(self, client: AsyncClient) -> None:
        source = "docker.io/library/alpine:latest"
        target = "docker.io/library/daedalus-api-test-tag:latest"
        r = await client.post("/images/tag", json={"source": source, "target": target})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "tagged"
        await client.delete(f"/images/{target}", params={"force": True})

    @pytest.mark.asyncio
    async def test_image_load_missing(self, client: AsyncClient) -> None:
        r = await client.post("/images/load?path=/nonexistent/daedalus-test.tar")
        assert r.status_code == 500

    @pytest.mark.asyncio
    async def test_image_save(self, client: AsyncClient, image_ref: str, tmp_path) -> None:
        out = str(tmp_path / "test-save.tar")
        r = await client.post(
            "/images/save",
            params={"image": image_ref, "output": out},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "saved"

    @pytest.mark.asyncio
    async def test_registry_logout_fake(self, client: AsyncClient) -> None:
        r = await client.post("/registry/logout?server=registry.example.invalid")
        # May 500 if server unknown — endpoint must be reachable
        assert r.status_code in (200, 500)

    @pytest.mark.asyncio
    async def test_container_exec(self, client: AsyncClient, container_id: str) -> None:
        # Only works on running containers
        inspect = await client.get(f"/containers/{container_id}")
        if inspect.json().get("status") != "running":
            cfg = inspect.json().get("configuration", inspect.json())
            status = inspect.json().get("status", "")
            if status != "running":
                pytest.skip("container not running")
        r = await client.post(
            f"/containers/{container_id}/exec",
            json={"command": ["echo", "daedalus-test"]},
        )
        assert r.status_code == 200
        assert r.json()["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_container_kill_stopped(self, client: AsyncClient, container_id: str) -> None:
        inspect = await client.get(f"/containers/{container_id}")
        raw = inspect.json()
        if raw.get("status") == "running":
            pytest.skip("use stopped container for kill smoke")
        r = await client.post(f"/containers/{container_id}/kill")
        # kill on stopped may error — endpoint exists
        assert r.status_code in (200, 500)


class TestContainerLifecycle:
    @pytest.mark.asyncio
    async def test_run_and_destroy(self, client: AsyncClient) -> None:
        """Create ephemeral container, verify fields, destroy."""
        r = await client.post("/containers", json={
            "image": "alpine:latest",
            "profile": "bench",
            "detach": True,
            "command": ["sleep", "5"],
            "hostname": "daedalus-test",
            "env": {"DAEDALUS_TEST": "1"},
        })
        if r.status_code == 400 and "POLICY" in r.text:
            pytest.skip("disk policy blocked container create on this host")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["id"]
        assert data["profile"] == "bench"
        cid = data["id"]

        r2 = await client.post(f"/containers/{cid}/stop")
        assert r2.status_code == 200

        r3 = await client.delete(f"/containers/{cid}", params={"confirm": True})
        assert r3.status_code == 200
        assert r3.json()["status"] == "destroyed"


    @pytest.mark.asyncio
    async def test_container_logs_stream(self, client: AsyncClient, container_id: str) -> None:
        import anyio
        with anyio.move_on_after(8, shield=False):
            async with client.stream(
                "GET", f"/containers/{container_id}/logs/stream", timeout=10.0,
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
                chunk = await resp.aread(256)
                assert chunk.startswith(b":")

    @pytest.mark.asyncio
    async def test_container_report(self, client: AsyncClient, container_id: str) -> None:
        r = await client.get(f"/containers/{container_id}/report")
        # report exists only for experiment-tracked runs; 404 is valid
        assert r.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_experiment_by_id(self, client: AsyncClient) -> None:
        r = await client.get("/experiments")
        items = r.json()
        if not items:
            pytest.skip("no experiments")
        run_id = items[0]["run_id"]
        r2 = await client.get(f"/experiments/{run_id}")
        assert r2.status_code == 200
        assert r2.json()["run_id"] == run_id

    @pytest.mark.asyncio
    async def test_registry_login_fake(self, client: AsyncClient) -> None:
        r = await client.post("/registry/login", json={
            "server": "registry.example.invalid",
            "username": "test",
            "password": "test",
        })
        assert r.status_code in (200, 500)

    @pytest.mark.asyncio
    async def test_images_build_missing(self, client: AsyncClient) -> None:
        r = await client.post("/images/build", json={
            "context": "/nonexistent/daedalus-build-ctx",
            "tag": "daedalus-test:build",
        })
        assert r.status_code == 500

    @pytest.mark.asyncio
    async def test_images_pull_reachable(self, client: AsyncClient) -> None:
        """Endpoint must accept request; use bogus image for fast failure."""
        r = await client.post("/images/pull", params={"image": "nonexistent-xyz-99999:latest"})
        assert r.status_code in (200, 500)

    @pytest.mark.asyncio
    async def test_images_push_reachable(self, client: AsyncClient, image_ref: str) -> None:
        r = await client.post("/images/push", params={"image": image_ref})
        assert r.status_code in (200, 500)


class TestExistingContainerOps:
    @pytest.mark.asyncio
    async def test_start_stop(self, client: AsyncClient) -> None:
        """Start a stopped container then stop it again."""
        r = await client.get("/containers?all=true")
        stopped = [c for c in r.json() if c.get("state") != "running"]
        if not stopped:
            pytest.skip("no stopped containers")
        cid = stopped[0]["id"]
        r1 = await client.post(f"/containers/{cid}/start")
        assert r1.status_code == 200
        r2 = await client.post(f"/containers/{cid}/stop")
        assert r2.status_code == 200

    @pytest.mark.asyncio
    async def test_builder_start_stop(self, client: AsyncClient) -> None:
        r1 = await client.post("/builder/start", params={"cpus": 1, "memory": "1024M"})
        assert r1.status_code in (200, 500)
        if r1.status_code == 200:
            r2 = await client.post("/builder/stop")
            assert r2.status_code == 200

    @pytest.mark.asyncio
    async def test_system_events_sse(self, client: AsyncClient) -> None:
        import anyio
        with anyio.move_on_after(8, shield=False):
            async with client.stream("GET", "/system/events", timeout=10.0) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
                chunk = await resp.aread(512)
                assert b"data:" in chunk
