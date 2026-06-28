"""Integration test fixtures — use the real ``container`` CLI."""

from __future__ import annotations

import pytest

from daedalus.core.audit import AuditLog
from daedalus.core.capabilities import CapabilityManifest, probe
from daedalus.core.cli_backend import CliBackend
from daedalus.core.forge import Forge
from daedalus.core.icarus import Icarus
from daedalus.core.mint import Mint
from daedalus.core.policy import PolicyEngine
from daedalus.core.store import Store


def _skip_if_no_container(caps: CapabilityManifest) -> None:
    if not caps.container_found:
        pytest.skip("container binary not found")
    if not caps.apiserver_running:
        pytest.skip("container API server not running — start with: container system start")


@pytest.fixture(scope="session")
def caps() -> CapabilityManifest:
    m = probe()
    _skip_if_no_container(m)
    return m


@pytest.fixture(scope="session")
def backend(caps: CapabilityManifest) -> CliBackend:
    return CliBackend(caps)


@pytest.fixture(scope="session")
def forge(caps: CapabilityManifest, backend: CliBackend) -> Forge:
    return Forge(backend, caps, policy=PolicyEngine(), audit=AuditLog(), store=Store())


@pytest.fixture(scope="session")
def icarus(backend: CliBackend) -> Icarus:
    return Icarus(backend)


@pytest.fixture(scope="session")
def mint(backend: CliBackend) -> Mint:
    return Mint(backend)
