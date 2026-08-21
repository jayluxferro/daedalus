"""Tests for M0 — host capability probe."""

import shutil

import pytest

from daedalus.core.capabilities import (
    EXPECTED_FLAGS,
    SUBCOMMANDS,
    CapabilityManifest,
    _probe_subcommand,
    _runtime_features,
    probe,
)


class TestCapabilityProbe:
    """Integration tests — require ``container`` on the local system."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_container(self) -> None:
        if shutil.which("container") is None:
            pytest.skip("container binary not found")

    def test_probe_returns_manifest(self):
        m = probe()
        assert isinstance(m, CapabilityManifest)
        assert m.probe_time

    def test_probe_finds_binary(self):
        m = probe()
        assert m.container_binary, "container binary not found on PATH"
        assert m.container_version
        assert m.container_version != "unknown"

    def test_probe_host_identity(self):
        m = probe()
        assert m.host_arch in ("arm64", "x86_64")
        assert m.macos_version

    def test_probe_macos_26_plus(self):
        m = probe()
        assert m.is_macos_26_plus, (
            f"Expected macOS >= 26, got {m.macos_version} — "
            "networking commands require macOS 26+"
        )

    def test_probe_finds_expected_flags(self):
        m = probe()
        assert m.known_flags, "expected some known flags to be detected"
        # Core flags that should absolutely be present
        core = {"detach", "name", "env", "mount", "volume", "kernel"}
        found = m.known_flags & core
        assert found, f"Expected at least some core flags, got none from {core}"

    def test_probe_networking_checked(self):
        m = probe()
        # networking should be True or False, not "untested"
        assert m.networking != "untested", (
            "networking probe should return True/False, not 'untested'"
        )

    def test_probe_daemon_checked(self):
        m = probe()
        assert m.apiserver_running != "untested", (
            "daemon probe should return True/False, not 'untested'"
        )

    def test_probe_builder_checked(self):
        m = probe()
        assert m.builder != "untested", (
            "builder probe should return True/False, not 'untested'"
        )

    def test_summary_string(self):
        m = probe()
        summary = m.summary()
        assert "DAEDALUS host capability manifest" in summary
        assert m.container_binary in summary

    def test_as_dict(self):
        m = probe()
        d = m.as_dict()
        assert d["host_arch"] == m.host_arch
        assert d["container_binary"] == m.container_binary
        assert d["networking"] == m.networking
        assert "runtime_features" in d
        # Tri-state: on 1.2.2 the init-image create probe gets a registry
        # 401 (not a clear yes/no), so "untested" is a legitimate result.
        assert d["init_image"] in (True, False, "untested")

    def test_runtime_features_populated(self):
        m = probe()
        rf = m.runtime_features
        assert "port_forwarding" in rf
        assert "vmnet_host_access" in rf
        # --publish exists on 1.2.2 but lands in extra_flags (not in
        # EXPECTED_FLAGS), so known_flags-derived port_forwarding stays False.
        assert rf["port_forwarding"] is False
        assert rf["bind_mounts_at_create"] is True

    def test_probe_new_verb_gates(self):
        m = probe()
        assert m.stats is True
        assert m.copy is True
        assert m.export is True
        assert m.prune is True
        assert m.network_subcommand is True

    def test_probe_new_runtime_features(self):
        m = probe()
        rf = m.runtime_features
        assert rf["container_stats"] is True
        assert rf["container_copy"] is True
        assert rf["container_export"] is True
        assert rf["container_prune"] is True
        assert rf["custom_networks"] is True

    def test_subcommands_reference_has_new_verbs(self):
        new_verbs = {
            "stats", "copy", "export", "prune", "network", "machine", "volume",
        }
        assert new_verbs <= SUBCOMMANDS


class TestCapabilityManifestUnit:
    """Unit tests — no system dependencies."""

    def test_empty_manifest(self):
        m = CapabilityManifest()
        assert not m.container_found
        assert not m.is_macos_26_plus

    def test_macos_26_plus_detection(self):
        m = CapabilityManifest(
            macos_version="26.0", macos_version_tuple=(26, 0, 0)
        )
        assert m.is_macos_26_plus

        m = CapabilityManifest(
            macos_version="25.5", macos_version_tuple=(25, 5, 0)
        )
        assert not m.is_macos_26_plus

    def test_container_found(self):
        m = CapabilityManifest(
            container_binary="/usr/local/bin/container",
            container_version="0.1.0",
        )
        assert m.container_found

    def test_fmt_helper(self):
        assert "✓" in CapabilityManifest._fmt(True)
        assert "✗" in CapabilityManifest._fmt(False)
        assert "?" in CapabilityManifest._fmt("untested")

    def test_expected_flags_are_lowercase(self):
        """All expected flags should be lowercase kebab strings."""
        for flag in EXPECTED_FLAGS:
            assert flag == flag.lower(), f"'{flag}' should be lowercase"
            assert " " not in flag, f"'{flag}' should not contain spaces"
            assert flag.startswith("--") is False, (
                f"'{flag}' should not include leading --"
            )

    @pytest.mark.parametrize("flag", [
        "detach", "name", "env", "mount", "dns", "kernel",
        "volume", "tty", "user", "uid", "gid",
    ])
    def test_core_flags_in_expected_set(self, flag):
        assert flag in EXPECTED_FLAGS, (
            f"Core flag '{flag}' should be in EXPECTED_FLAGS"
        )

    @pytest.mark.parametrize("gate", [
        "stats", "copy", "export", "prune", "network_subcommand",
    ])
    def test_new_verb_gates_default_untested(self, gate):
        m = CapabilityManifest()
        assert getattr(m, gate) == "untested"

    def test_runtime_features_maps_gates(self):
        m = CapabilityManifest(
            stats=True,
            copy=True,
            export=True,
            prune=True,
            network_subcommand=True,
        )
        rf = _runtime_features(m)
        assert rf["container_stats"] is True
        assert rf["container_copy"] is True
        assert rf["container_export"] is True
        assert rf["container_prune"] is True
        assert rf["custom_networks"] is True

        m = CapabilityManifest()
        rf = _runtime_features(m)
        assert rf["container_stats"] is False
        assert rf["container_copy"] is False
        assert rf["container_export"] is False
        assert rf["container_prune"] is False
        assert rf["custom_networks"] is False

    def test_as_dict_includes_new_fields(self):
        m = CapabilityManifest(
            stats=True,
            copy=False,
            export="untested",
            prune=True,
            network_subcommand=True,
        )
        d = m.as_dict()
        assert d["stats"] is True
        assert d["copy"] is False
        assert d["export"] == "untested"
        assert d["prune"] is True
        assert d["network_subcommand"] is True

    def test_summary_includes_new_gates(self):
        m = CapabilityManifest(
            stats=True,
            copy=False,
            export="untested",
            prune=True,
            network_subcommand=True,
        )
        summary = m.summary()
        assert "stats:        ✓ yes" in summary
        assert "copy:         ✗ no" in summary
        assert "export:       ? untested" in summary
        assert "prune:        ✓ yes" in summary
        assert "net-subcmd:   ✓ yes" in summary

    def test_probe_subcommand_helper(self):
        assert _probe_subcommand("/usr/bin/true") is True
        assert _probe_subcommand("/usr/bin/false", "nonexistent") is False
        assert _probe_subcommand("/nonexistent/binary") == "untested"
