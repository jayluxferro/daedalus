"""Named security postures — reusable container configuration profiles.

Profiles are named security postures that configure every aspect of a
Labyrinth run that container v0.1.0 supports.  Aspirational fields
(capabilities, read-only rootfs, init-image, rosetta, networking) are
reserved for when the CLI supports them.
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

    # DNS
    no_dns: bool = False
    dns: list[str] = field(default_factory=list)
    dns_domain: list[str] = field(default_factory=list)
    dns_search: list[str] = field(default_factory=list)

    # Instrumentation
    kernel: str | None = None

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
            "no_dns": self.no_dns,
            "dns": list(self.dns),
            "dns_domain": list(self.dns_domain),
            "dns_search": list(self.dns_search),
            "kernel": self.kernel,
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
            "DNS fully blocked, tmpfs for /tmp and /var/tmp."
        ),
        tmpfs=["/tmp", "/var/tmp"],
        no_dns=True,
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
