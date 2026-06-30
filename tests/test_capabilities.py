"""Tests for M0 — host capability probe."""

import pytest

from daedalus.core.capabilities import EXPECTED_FLAGS, CapabilityManifest, probe


class TestCapabilityProbe:
    """Integration tests — require ``container`` on the local system."""

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
        assert d["init_image"] is False or d["init_image"] is True

    def test_runtime_features_populated(self):
        m = probe()
        rf = m.runtime_features
        assert "port_forwarding" in rf
        assert "vmnet_host_access" in rf
        assert rf["port_forwarding"] is False
        assert rf["bind_mounts_at_create"] is True


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
