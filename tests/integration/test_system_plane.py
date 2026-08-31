"""Integration tests — SPEC phase 9 W4: the system plane, live.

Live round-trips against the ``container`` daemon (1.3.1) for the
system-plane completion workstream (SPEC phase 9 §4 W4):

* **system version** — ``system version --format json`` keyed by
  ``appName`` (``container``, ``container-apiserver`` on 1.3.1);
* **system properties** — ``system property list`` (TOML flattened to
  dotted keys).  Verified live on 1.3.1: ``container system property
  --help`` exposes **only** ``list, ls`` — ``get``/``set`` answer
  ``Error: N unexpected arguments`` (exit 64), so both daedalus verbs
  fail loudly with the honest list-only CapabilityError.  When a future
  runtime adds real get/set subcommands, flip those two assertions.
* **system df** — structured ``system df --format json`` (per-category
  store accounting) merged with host-measured total/free;
* **registry** — ``registry list`` (logged-in servers) and the fail-loud
  guard for the missing ``registry default`` tree (1.3.1 exposes only
  ``registry login/logout/list``).

The skip-when-no-container gate comes from the session ``caps`` fixture
(``tests/integration/conftest.py`` → ``_skip_if_no_container``).  All
verbs here are read-only apart from the two error-path sets.
"""

from __future__ import annotations

import pytest

from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.cli_backend import CliBackend
from daedalus.core.exceptions import CapabilityError
from daedalus.core.forge import Forge


@pytest.mark.integration
class TestSystemPlane:
    """System-plane verbs against the real container daemon (1.3.1)."""

    async def test_system_version_live(
        self, forge: Forge, caps: CapabilityManifest,
    ) -> None:
        """``system version --format json`` keyed by appName.

        The dict must carry the ``container`` service with the probed
        runtime version and the ``container-apiserver`` service (both
        present on 1.3.1).
        """
        version = await forge.system_version()
        assert isinstance(version, dict)
        assert "container" in version
        assert "container-apiserver" in version
        info = version["container"]
        assert isinstance(info, dict)
        assert str(info.get("version", "")) == caps.container_version
        # Every entry mirrors the wire: appName/version/buildType/commit.
        for entry in version.values():
            assert isinstance(entry, dict)
            assert entry.get("appName")

    async def test_system_properties_live(
        self, forge: Forge, caps: CapabilityManifest,
    ) -> None:
        """``system property list`` flattening + the list-only 1.3.1 reality.

        The list is a flat dotted-key dict with ≥ 10 entries including
        ``build.cpus``.  Live 1.3.1 has **no** property get/set
        subcommand (verified: ``container system property --help`` lists
        only ``list, ls``), so both daedalus verbs must raise the honest
        CapabilityError naming ``'system property list'`` — never leak
        the runtime's usage text as if it were a property value.
        """
        props = await forge.system_property_list()
        assert isinstance(props, dict)
        assert len(props) >= 10
        assert "build.cpus" in props
        assert isinstance(props["build.cpus"], str) and props["build.cpus"]

        with pytest.raises(CapabilityError) as exc_get:
            await forge.system_property_get("build.cpus")
        assert "only 'system property list'" in exc_get.value.message

        with pytest.raises(CapabilityError) as exc_set:
            await forge.system_property_set("daedalus.test", "1")
        assert "only 'system property list'" in exc_set.value.message

    async def test_system_df_structured(self, backend: CliBackend) -> None:
        """``system df --format json`` — structured store accounting.

        Live 1.3.1 emits per-category objects (``containers``,
        ``images``, ``volumes``) with ``active``/``total``/``sizeInBytes``;
        the backend merges host-measured ``total``/``free`` (the df JSON
        reports no capacity figure) and tags the result with the source
        command so consumers can tell the two df paths apart.
        """
        df = await backend.system_df()
        assert isinstance(df, dict)
        assert df.get("source") == "container system df --format json"
        for category in ("containers", "images", "volumes"):
            assert category in df
            assert isinstance(df[category], dict)
            assert "sizeInBytes" in df[category]
        for key in ("total", "used", "free"):
            assert isinstance(df.get(key), (int, float)), f"df.{key} numeric"
        assert df["used"] >= 0

    async def test_registry_list_and_default_guard(
        self, backend: CliBackend,
    ) -> None:
        """Registry surface on 1.3.1: list works, default fails loudly.

        ``registry list --format json`` returns the logged-in servers
        (empty list is fine — the parse is shape-tolerant).  The
        ``registry default`` tree does **not** exist on 1.3.1 (the
        runtime exposes only ``registry login/logout/list`` — verified
        live: the subcommand answers ``Error: N unexpected arguments``),
        so the backend must convert that into the honest CapabilityError
        instead of returning the runtime's usage text as data.
        """
        servers = await backend.registry_list()
        assert isinstance(servers, list)
        assert all(isinstance(s, str) for s in servers)

        with pytest.raises(CapabilityError) as exc:
            await backend.registry_default_inspect()
        assert "registry login/logout/list" in exc.value.message
