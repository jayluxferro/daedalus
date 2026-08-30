"""Named security postures — reusable container configuration profiles.

Profiles are named security postures that configure every aspect of a
Labyrinth run that the current container runtime supports.  Capability
controls (``cap_drop``) and the read-only rootfs (``read_only``) are live
in container 1.2.2; the custom init image (``init_image``) and SSH agent
forwarding (``ssh``) are live in container 1.3.1; rosetta remains
aspirational until the CLI supports it.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Profile:
    """A named security posture for container runs.

    Only includes fields that map to current RunSpec parameters.
    """

    name: str
    description: str = ""

    # Filesystem
    tmpfs: list[str] = field(default_factory=list)
    mounts: list[str] = field(default_factory=list)
    read_only: bool = False           # --read-only rootfs (container 1.2.2)

    # Capabilities (container 1.2.2)
    cap_drop: list[str] = field(default_factory=list)  # --cap-drop, repeatable

    # DNS
    no_dns: bool = False
    dns: list[str] = field(default_factory=list)
    dns_domain: list[str] = field(default_factory=list)
    dns_search: list[str] = field(default_factory=list)

    # Instrumentation
    kernel: str | None = None
    init_image: str | None = None     # --init-image <image> (container 1.3.1)
    ssh: bool = False                 # --ssh (forward SSH agent socket; 1.3.1)

    # Resources
    cpus: int | None = None
    memory: str | None = None

    def apply(self, **overrides: Any) -> dict[str, Any]:
        """Return kwargs dict suitable for ``forge.run(**kwargs)``.

        Only includes fields that map to current RunSpec parameters.
        Any keyword argument overrides the profile default.
        """
        result: dict[str, Any] = {
            "tmpfs": list(self.tmpfs),
            "mounts": list(self.mounts),
            "read_only": self.read_only,
            "cap_drop": list(self.cap_drop),
            "no_dns": self.no_dns,
            "dns": list(self.dns),
            "dns_domain": list(self.dns_domain),
            "dns_search": list(self.dns_search),
            "kernel": self.kernel,
            "init_image": self.init_image,
            "ssh": self.ssh,
            "cpus": self.cpus,
            "memory": self.memory,
        }
        result.update(overrides)
        return result


# ==========================================================================
# Built-in profiles
# ==========================================================================

BUILTIN_PROFILES: dict[str, Profile] = {
    "general": Profile(
        name="general",
        description=(
            "General-purpose Linux environment. No security restrictions — "
            "use for development, services, or everyday container work."
        ),
    ),

    "detonation": Profile(
        name="detonation",
        description=(
            "Maximum lockdown for safe malware detonation. "
            "DNS fully blocked."
        ),
        # NOTE: no tmpfs here.  tmpfs on /tmp makes runtime drops invisible
        # to the post-run export (tmpfs writes never reach the disk layer),
        # which blinds the filesystem-delta evidence in behavioral reports —
        # and the VM is destroyed after analysis, so anti-persistence is moot.
        #
        # NOTE: read_only stays False on purpose.  A read-only rootfs would
        # blind the phase-3 FS-diff forensics — with nothing writable there
        # are no runtime writes to observe, and samples that write scratch
        # state (e.g. unpackers) could not run at all.  The privilege
        # reduction comes from capability lockdown instead: drop ALL
        # capabilities so the sample runs with no Linux capabilities at all.
        no_dns=True,
        cap_drop=["ALL"],
    ),

    "bench": Profile(
        name="bench",
        description="Permissive profile for benchmarking and development.",
    ),

    "fuzz": Profile(
        name="fuzz",
        description=(
            "For kernel fuzzing and escape research. "
            "Pass --kernel /path/to/kasan-kernel at runtime."
        ),
    ),

    "isolated": Profile(
        name="isolated",
        description="Full network isolation: no DNS.",
        no_dns=True,
    ),

    "deception": Profile(
        name="deception",
        description=(
            "For network deception labs. Controlled DNS pointing at fake resolver."
        ),
        dns=["10.0.0.53"],
        dns_domain=["lab.local"],
    ),

    "proxy": Profile(
        name="proxy",
        description=(
            "Routes traffic through an intercepting proxy (Burp Suite, mitmproxy). "
            "Set proxy host:port via --proxy flag. Mount CA cert via --cert flag. "
            "Set NO_PROXY exclusions via --no-proxy flag."
        ),
    ),
}

SPECIAL_PROFILES: dict[str, str] = {
    "default": "detonation",
}


class ProfileRegistry:
    """Registry of named profiles.

    Built-in profiles ship with DAEDALUS.  Users can register custom
    profiles at runtime.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, Profile] = dict(BUILTIN_PROFILES)

    def get(self, name: str) -> Profile:
        """Resolve a profile name.  Falls back to detonation (safe)."""
        resolved = SPECIAL_PROFILES.get(name, name)
        if resolved in self._profiles:
            return self._profiles[resolved]
        return self._profiles["detonation"]

    def register(self, profile: Profile) -> None:
        self._profiles[profile.name] = profile

    def list(self) -> builtins.list[Profile]:
        return sorted(self._profiles.values(), key=lambda p: p.name)

    def names(self) -> builtins.list[str]:
        return sorted(self._profiles)
