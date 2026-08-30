"""M0 — Host capability probe.

Detects what the local Apple ``container`` runtime supports and emits a
structured capability manifest.  Every other subsystem gates optional
features on this manifest — we never assume a flag is available.

The probe shells out to ``container`` subcommands and parses ``--help``
output to build a flag inventory.  It checks feature gates by actually
invoking the relevant commands (or confirming they fail gracefully) so
the manifest reflects what *this* host + *this* container version
actually support.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

# ==========================================================================
# Capability manifest
# ==========================================================================


@dataclass
class CapabilityManifest:
    """The complete host capability fingerprint.

    Every field is populated by :func:`probe`.  Tri-state booleans use
    ``True`` / ``False`` / ``"untested"`` so callers can distinguish
    "confirmed absent" from "could not check".
    """

    # -- host identity ---------------------------------------------------
    host_arch: str = ""
    macos_version: str = ""
    macos_version_tuple: tuple[int, int, int] = (0, 0, 0)

    # -- container binary ------------------------------------------------
    container_binary: str = ""
    container_version: str = ""
    container_version_tuple: tuple[int, int, int] = (0, 0, 0)
    container_commit: str = ""

    # -- daemon ----------------------------------------------------------
    apiserver_running: bool | str = "untested"

    # -- feature gates (tri-state) ---------------------------------------
    networking: bool | str = "untested"
    kernel_set: bool | str = "untested"
    init_image: bool | str = "untested"
    rosetta: bool | str = "untested"
    virtualization_nested: bool | str = "untested"
    builder: bool | str = "untested"
    system_dns: bool | str = "untested"
    gpu: bool | str = "untested"
    stats: bool | str = "untested"
    copy: bool | str = "untested"
    export: bool | str = "untested"
    prune: bool | str = "untested"
    network_subcommand: bool | str = "untested"
    machine: bool | str = "untested"
    volume: bool | str = "untested"
    security_safe: bool | str = "untested"

    # -- flag inventory (from --help parsing) ----------------------------
    known_flags: set[str] = field(default_factory=set)
    extra_flags: set[str] = field(default_factory=set)

    # -- timestamp -------------------------------------------------------
    probe_time: str = ""

    # -- runtime feature matrix (derived from probes + flag inventory) ---
    runtime_features: dict[str, bool | str] = field(default_factory=dict)

    # -- derived properties ----------------------------------------------

    @property
    def is_macos_26_plus(self) -> bool:
        """Networking commands require macOS ≥ 26."""
        return self.macos_version_tuple >= (26, 0, 0)

    @property
    def container_found(self) -> bool:
        return bool(self.container_binary and self.container_version)

    # -- serialisation ---------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["known_flags"] = sorted(self.known_flags)
        d["extra_flags"] = sorted(self.extra_flags)
        return d

    def summary(self) -> str:
        lines = [
            "DAEDALUS host capability manifest",
            "─────────────────────────────────",
            f"Host:     {self.host_arch}  macOS {self.macos_version}",
            f"Binary:   {self.container_binary}  v{self.container_version}",
            f"Commit:   {self.container_commit}",
            f"Daemon:   {self._fmt(self.apiserver_running)}",
            "─────────────────────────────────",
            f"macOS ≥ 26:   {'✓' if self.is_macos_26_plus else '✗ (networking requires 26+)'}",
            f"networking:   {self._fmt(self.networking)}",
            f"kernel-set:   {self._fmt(self.kernel_set)}",
            f"init-image:   {self._fmt(self.init_image)}",
            f"rosetta:      {self._fmt(self.rosetta)}",
            f"nested-virt:  {self._fmt(self.virtualization_nested)}",
            f"builder:      {self._fmt(self.builder)}",
            f"system-dns:   {self._fmt(self.system_dns)}",
            f"gpu:          {self._fmt(self.gpu)}",
            f"stats:        {self._fmt(self.stats)}",
            f"copy:         {self._fmt(self.copy)}",
            f"export:       {self._fmt(self.export)}",
            f"prune:        {self._fmt(self.prune)}",
            f"net-subcmd:   {self._fmt(self.network_subcommand)}",
            f"machine:      {self._fmt(self.machine)}",
            f"volume:       {self._fmt(self.volume)}",
            "security:     "
            + (
                "✓ ≥ 1.3.1 advisories fixed"
                if self.security_safe is True
                else "✗ < 1.3.1 vulnerable" if self.security_safe is False else "? unknown"
            ),
        ]
        if self.extra_flags:
            lines.append("─────────────────────────────────")
            lines.append(
                f"Extra flags: {', '.join(sorted(self.extra_flags))}"
            )
        return "\n".join(lines)

    @staticmethod
    def _fmt(v: bool | str) -> str:
        if v is True:
            return "✓ yes"
        if v is False:
            return "✗ no"
        return f"? {v}"


# ==========================================================================
# Known flag catalogue
# ==========================================================================
# Flags we expect container to expose (from --help outputs, not subcommand
# names).  Populated by inspecting actual ``container * --help`` output.
# This is a *reference set* — the probe diffs discovered flags against it.

# Security floor for the detonation workflow: container 1.3.1 fixed the six
# GHSA advisories (bundle file-deletion via unchecked ids, OCI descriptor
# path traversal, symlink reads on image load, registry realm validation,
# crafted-layer crashes) that the detonation isolation promise depends on.
SECURITY_MIN_VERSION: str = "1.3.1"
SECURITY_MIN_VERSION_TUPLE: tuple[int, int, int] = (1, 3, 1)

EXPECTED_FLAGS: set[str] = {
    # Run / create flags (from container run --help)
    "cwd", "workdir", "env", "env-file", "uid", "gid",
    "interactive", "tty", "user", "cpus", "memory",
    "detach", "entrypoint", "mount", "tmpfs", "name",
    "remove", "rm", "os", "arch", "volume", "kernel",
    "cidfile", "no-dns", "dns", "dns-domain", "dns-search",
    "dns-option", "label", "disable-progress-updates", "scheme",
    "init-image", "ssh", "debug", "version", "help",
}

# Subcommands (not flags) — tracked separately for backend routing.
SUBCOMMANDS: set[str] = {
    "create", "delete", "exec", "inspect", "kill", "list", "logs",
    "run", "start", "stop", "build", "images", "image", "registry",
    "builder", "system", "stats", "copy", "export", "prune", "network",
    "machine", "volume",
}


# ==========================================================================
# Public entry point
# ==========================================================================


def probe(*, container_path: str | None = None) -> CapabilityManifest:
    """Run the full capability probe and return a manifest.

    Parameters
    ----------
    container_path:
        Optional explicit path.  Auto-detected from ``PATH`` / standard
        locations when not given.
    """
    ensure_daemon()
    m = CapabilityManifest()

    # -- host identity ---------------------------------------------------
    m.host_arch = platform.machine()
    m.macos_version = platform.mac_ver()[0] or "unknown"
    try:
        parts = m.macos_version.split(".")
        m.macos_version_tuple = (
            int(parts[0]) if len(parts) > 0 else 0,
            int(parts[1]) if len(parts) > 1 else 0,
            int(parts[2]) if len(parts) > 2 else 0,
        )
    except (ValueError, IndexError):
        pass

    # -- locate container binary -----------------------------------------
    m.container_binary = _find_container(container_path)
    if not m.container_binary:
        m.probe_time = _now()
        return m

    # -- version ---------------------------------------------------------
    m.container_version, m.container_commit = _container_version(m.container_binary)
    m.container_version_tuple = _version_tuple(m.container_version)
    m.security_safe = _security_status(m.container_version_tuple)

    # -- daemon ----------------------------------------------------------
    m.apiserver_running = _probe_daemon(m)

    # -- flag inventory --------------------------------------------------
    m.known_flags, m.extra_flags = _flag_inventory(m.container_binary)

    # -- feature probes --------------------------------------------------
    m.networking = _probe_networking(m)
    m.kernel_set = _probe_kernel_set(m)
    m.init_image = _probe_init_image(m)
    m.rosetta = _probe_rosetta(m)
    m.virtualization_nested = _probe_virtualization_nested(m)
    m.builder = _probe_builder(m)
    m.system_dns = _probe_system_dns(m)
    m.gpu = _probe_gpu(m)
    m.stats = _probe_subcommand(m.container_binary, "stats")
    m.copy = _probe_subcommand(m.container_binary, "copy")
    m.export = _probe_subcommand(m.container_binary, "export")
    m.prune = _probe_subcommand(m.container_binary, "prune")
    m.network_subcommand = _probe_network_subcommand(m)
    m.machine = _probe_subcommand(m.container_binary, "machine")
    m.volume = _probe_subcommand(m.container_binary, "volume")
    m.runtime_features = _runtime_features(m)

    m.probe_time = _now()
    return m


# ==========================================================================
# Internal helpers
# ==========================================================================


def _find_container(explicit: str | None) -> str:
    if explicit:
        return explicit if os.path.isfile(explicit) else ""
    return shutil.which("container") or ""


def _container_version(binary: str) -> tuple[str, str]:
    """Extract (version, commit) from ``container --version``.

    Output format::

        container CLI version 1.2.2 (build: release, commit: 0190097)
    """
    try:
        r = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10
        )
        text = r.stdout + r.stderr
        # "container CLI version X.Y.Z"
        ver_match = re.search(r"version\s+(\S+)", text)
        ver = ver_match.group(1) if ver_match else ""
        # "commit: hex"
        commit_match = re.search(r"commit:\s*(\S+)", text)
        commit = commit_match.group(1).rstrip(")") if commit_match else ""
        return (ver or "unknown", commit or "unknown")
    except Exception:
        return ("unknown", "unknown")


def _version_tuple(version: str) -> tuple[int, int, int]:
    """Parse the leading ``X.Y.Z`` of a container version string.

    Returns ``(0, 0, 0)`` when the version is unparseable (e.g. a
    ``dev-...`` build string) — the sentinel drives ``"untested"`` in
    :func:`_security_status`.
    """
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", version or "")
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _security_status(version_tuple: tuple[int, int, int]) -> bool | str:
    """Tri-state security floor check for the detonation workflow.

    True when the version is >= :data:`SECURITY_MIN_VERSION_TUPLE` (the
    1.3.1 GHSA fixes are in), False below it, and ``"untested"`` when
    the version could not be parsed at all (``(0, 0, 0)`` sentinel) —
    an unknown version must not silently pass the gate.
    """
    if version_tuple == (0, 0, 0):
        return "untested"
    return version_tuple >= SECURITY_MIN_VERSION_TUPLE


def _probe_daemon(m: CapabilityManifest) -> bool | str:
    """Check if ``container-apiserver`` is running."""
    try:
        r = subprocess.run(
            ["pgrep", "-f", "container-apiserver"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return "untested"


def ensure_daemon() -> bool:
    """Ensure the container daemon is running. Start it if not.

    Returns True if the daemon was already running or was started
    successfully.  Called by the API and MCP server lifespans so
    the daemon is always available when DAEDALUS is running.
    """
    # Check if already running
    try:
        r = subprocess.run(
            ["pgrep", "-f", "container-apiserver"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass

    # Daemon not running — start it
    try:
        binary = shutil.which("container") or "container"
        subprocess.run(
            [binary, "system", "start", "--disable-kernel-install"],
            capture_output=True, text=True, timeout=30,
        )
        # Verify it started
        r = subprocess.run(
            ["pgrep", "-f", "container-apiserver"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _flag_inventory(binary: str) -> tuple[set[str], set[str]]:
    """Parse ``container * --help`` outputs to build a flag inventory.

    Returns ``(known_flags, extra_flags)`` where *known* are flags in
    :data:`EXPECTED_FLAGS` and *extra* are flags we saw but didn't expect.
    """
    found: set[str] = set()
    help_targets = [
        [],
        ["create", "--help"],
        ["run", "--help"],
        ["exec", "--help"],
        ["image", "--help"],
        ["system", "--help"],
        ["builder", "--help"],
    ]

    for target in help_targets:
        try:
            r = subprocess.run(
                [binary] + target, capture_output=True, text=True, timeout=10
            )
            text = r.stdout + r.stderr
            for token in re.findall(r"--[a-z][-a-z0-9]*", text):
                name = token[2:]  # strip leading --
                if name and not name.startswith("<"):
                    found.add(name)
        except Exception:
            pass

    known = found & EXPECTED_FLAGS
    extra = found - EXPECTED_FLAGS
    return known, extra


# ------------------------------------------------------------------
# Individual feature probes
# ------------------------------------------------------------------


def _probe_networking(m: CapabilityManifest) -> bool | str:
    """Check if the network plugin is installed and usable.

    Networking is provided by the ``container-network-vmnet`` plugin at
    ``/usr/local/libexec/container/plugins/``.  Check for the plugin
    binary first, falling back to the ``container network`` subcommand.
    """
    if not m.is_macos_26_plus:
        return False
    plugin_path = (
        "/usr/local/libexec/container/plugins/"
        "container-network-vmnet/bin/container-network-vmnet"
    )
    if os.path.exists(plugin_path):
        return True
    # Fall back to the network subcommand
    try:
        r = subprocess.run(
            [m.container_binary, "network", "ls"],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return "untested"


def _probe_kernel_set(m: CapabilityManifest) -> bool | str:
    """Check if ``container system kernel`` is available."""
    try:
        r = subprocess.run(
            [m.container_binary, "system", "kernel", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _probe_init_image(m: CapabilityManifest) -> bool | str:
    """Check if ``--init-image`` is recognised, via the flag inventory.

    The inventory (:func:`_flag_inventory`) parses ``container run --help``,
    so the flag's presence there proves support.  An earlier version of
    this probe additionally ran a live ``create`` with a nonexistent init
    image — but that step tested *image fetch*, not flag support (any
    pull failure read as "untested"), so it was removed.
    """
    return ("init-image" in m.known_flags) or ("init-image" in m.extra_flags)


def _probe_rosetta(m: CapabilityManifest) -> bool | str:
    """Check if ``--rosetta`` flag is recognised."""
    if m.host_arch != "arm64":
        return False
    try:
        r = subprocess.run(
            [m.container_binary, "run", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        return "--rosetta" in (r.stdout + r.stderr)
    except Exception:
        return "untested"


def _probe_virtualization_nested(m: CapabilityManifest) -> bool | str:
    """Check if ``--virtualization`` flag is recognised."""
    try:
        r = subprocess.run(
            [m.container_binary, "run", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        return "--virtualization" in (r.stdout + r.stderr)
    except Exception:
        return "untested"


def _probe_builder(m: CapabilityManifest) -> bool | str:
    """Check if ``container builder`` is available."""
    try:
        r = subprocess.run(
            [m.container_binary, "builder", "status"],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def _probe_system_dns(m: CapabilityManifest) -> bool | str:
    """Check if ``container system dns`` is available."""
    try:
        r = subprocess.run(
            [m.container_binary, "system", "dns", "list"],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def _probe_gpu(m: CapabilityManifest) -> bool | str:
    """Check if ``--gpu`` flag is recognised."""
    try:
        r = subprocess.run(
            [m.container_binary, "run", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        return "--gpu" in (r.stdout + r.stderr)
    except Exception:
        return "untested"


def _runtime_features(m: CapabilityManifest) -> dict[str, bool | str]:
    """Summarise what the container CLI supports for DAEDALUS consumers."""
    flags = m.known_flags
    has_mount = "mount" in flags and "volume" in flags
    has_publish = "publish" in flags or "p" in flags
    return {
        "port_forwarding": bool(has_publish),
        "vmnet_host_access": m.networking is True,
        "bind_mounts_at_create": bool(has_mount),
        "volume_hot_attach": False,
        "oci_image_load": True,
        "iso_raw_disk_images": False,
        "custom_networks": m.network_subcommand is True,
        "container_stats": m.stats is True,
        "container_copy": m.copy is True,
        "container_export": m.export is True,
        "container_prune": m.prune is True,
        "container_machines": m.machine is True,
        "container_volumes": m.volume is True,
        "shared_default_network": m.networking is True,
        "security_advisories_fixed": m.security_safe is True,
    }


def _probe_subcommand(binary: str, *subcmd: str) -> bool | str:
    """Check whether ``container <subcmd> --help`` exits successfully.

    Returns True when the subcommand is available (``--help`` exits 0),
    False when it is not, and ``"untested"`` when the probe itself failed.
    """
    try:
        r = subprocess.run(
            [binary, *subcmd, "--help"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return "untested"


def _probe_network_subcommand(m: CapabilityManifest) -> bool | str:
    """Check the ``network`` subcommand and confirm it offers ``create``."""
    try:
        r = subprocess.run(
            [m.container_binary, "network", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and "create" in (r.stdout + r.stderr).lower()
    except Exception:
        return False


def _now() -> str:
    return datetime.now(UTC).isoformat()
