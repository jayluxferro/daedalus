"""API surface tests — FastAPI route smoke tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from daedalus.api.server import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "container_version" in data


@pytest.mark.asyncio
async def test_profiles(client: AsyncClient) -> None:
    r = await client.get("/profiles")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()]
    assert "detonation" in names


@pytest.mark.asyncio
async def test_containers_list(client: AsyncClient) -> None:
    r = await client.get("/containers?all=true")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_system_status(client: AsyncClient) -> None:
    r = await client.get("/system/status")
    assert r.status_code == 200
    data = r.json()
    assert "container_count" in data


@pytest.mark.asyncio
async def test_experiments_list(client: AsyncClient) -> None:
    r = await client.get("/experiments")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_images_list(client: AsyncClient) -> None:
    r = await client.get("/images")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_system_dns_list(client: AsyncClient) -> None:
    r = await client.get("/system/dns")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_audit(client: AsyncClient) -> None:
    r = await client.get("/system/audit?limit=10")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_root_redirect(client: AsyncClient) -> None:
    r = await client.get("/", follow_redirects=False)
    assert r.status_code in (307, 308)
    assert r.headers["location"] == "/ui/"


@pytest.mark.asyncio
async def test_topology_validate(client: AsyncClient) -> None:
    yaml_content = """
name: lab-net
description: victim + attacker
networks:
  - name: internal
    subnet: 10.89.0.0/24
    internal: true
attachments:
  - container: victim
    network: internal
dns_entries:
  - domain: c2.evil
    target: 10.89.0.2
"""
    r = await client.post("/topology/validate", json={"content": yaml_content})
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is True
    assert data["name"] == "lab-net"
    assert data["networks"] == 1
    assert data["attachments"] == 1
    assert data["dns_entries"] == 1


@pytest.mark.asyncio
async def test_image_load_missing_file(client: AsyncClient) -> None:
    r = await client.post("/images/load?path=/nonexistent/daedalus-test.tar")
    assert r.status_code == 500
