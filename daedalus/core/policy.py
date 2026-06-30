"""Policy guardrails — enforced *before* any backend call.

Policy gates every operation that could cause harm:

* Maximum concurrent VMs
* Total disk budget (checked via ``system df``)
* Egress allow/deny
* Mandatory ``confirm`` on destructive operations
* Image allow-lists

The policy engine is the safety boundary between an autonomous agent and
the host — every agent action flows through policy checks before reaching
the backend.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from daedalus.core.exceptions import PolicyViolationError


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"  # requires explicit confirm=True


@dataclass
class PolicyResult:
    decision: Decision
    reason: str = ""


@dataclass
class PolicyConfig:
    """User-configurable policy parameters."""

    max_concurrent_vms: int = 16
    # Cap on Apple container *store* size (not whole-volume used bytes).
    max_disk_bytes: int = 100 * 1024 * 1024 * 1024  # 100 GiB; 0 = disabled
    min_free_bytes: int = 5 * 1024 * 1024 * 1024  # require 5 GiB free on volume
    egress_default: Decision = Decision.DENY
    image_allowlist: list[str] = field(default_factory=list)
    image_blocklist: list[str] = field(default_factory=list)
    require_confirm_destroy: bool = True
    require_confirm_network_expose: bool = True
    require_confirm_kernel_change: bool = True

    # Audit hook — called with every policy decision
    on_decision: Callable[[str, str, PolicyResult], None] | None = None


class PolicyEngine:
    """Pre-execution policy enforcement.

    Every operation that could affect host security is checked here before
    it reaches the backend.
    """

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()

    # ==================================================================
    # Checks
    # ==================================================================

    def check_concurrency(self, current_vm_count: int) -> PolicyResult:
        if current_vm_count >= self.config.max_concurrent_vms:
            return PolicyResult(
                Decision.DENY,
                f"Max concurrent VMs ({self.config.max_concurrent_vms}) reached "
                f"(currently {current_vm_count})",
            )
        return PolicyResult(Decision.ALLOW)

    def check_disk(self, disk: int | dict[str, Any]) -> PolicyResult:
        """Check disk policy against container store size and volume free space."""
        if isinstance(disk, int):
            store_bytes: int | None = disk
            free_bytes: int | None = None
        else:
            store_bytes = disk.get("store_bytes")
            if not isinstance(store_bytes, int):
                store_bytes = None
            free_bytes = disk.get("free")
            if not isinstance(free_bytes, int):
                free_bytes = None

        if free_bytes is not None and free_bytes < self.config.min_free_bytes:
            return PolicyResult(
                Decision.DENY,
                f"Insufficient free disk space "
                f"({free_bytes} < {self.config.min_free_bytes})",
            )

        cap = self.config.max_disk_bytes
        if cap > 0 and store_bytes is not None and store_bytes >= cap:
            return PolicyResult(
                Decision.DENY,
                f"Container store budget exceeded "
                f"({store_bytes} >= {cap})",
            )
        return PolicyResult(Decision.ALLOW)

    def check_egress(self, network_spec: str) -> PolicyResult:
        """Check whether this network config allows egress."""
        if self.config.egress_default == Decision.DENY:
            # "host" or "bridge" networks allow egress
            if network_spec in ("host", "bridge"):
                return PolicyResult(
                    Decision.CONFIRM,
                    f"Network '{network_spec}' allows host egress — requires confirm",
                )
        return PolicyResult(Decision.ALLOW)

    def check_image(self, image: str) -> PolicyResult:
        """Check image against allow-list / block-list."""
        # Block-list takes priority
        for blocked in self.config.image_blocklist:
            if blocked in image:
                return PolicyResult(
                    Decision.DENY,
                    f"Image '{image}' matches block-list entry '{blocked}'",
                )
        # If allow-list is set, image must match
        if self.config.image_allowlist:
            for allowed in self.config.image_allowlist:
                if allowed in image:
                    return PolicyResult(Decision.ALLOW)
            return PolicyResult(
                Decision.DENY,
                f"Image '{image}' not in allow-list",
            )
        return PolicyResult(Decision.ALLOW)

    def check_destroy(self, *, confirm: bool = False) -> PolicyResult:
        if self.config.require_confirm_destroy and not confirm:
            return PolicyResult(
                Decision.CONFIRM,
                "Destroy requires confirm=True",
            )
        return PolicyResult(Decision.ALLOW)

    def check_network_expose(self, *, confirm: bool = False) -> PolicyResult:
        if self.config.require_confirm_network_expose and not confirm:
            return PolicyResult(
                Decision.CONFIRM,
                "Network-exposing operation requires confirm=True",
            )
        return PolicyResult(Decision.ALLOW)

    def check_kernel_change(self, *, confirm: bool = False) -> PolicyResult:
        if self.config.require_confirm_kernel_change and not confirm:
            return PolicyResult(
                Decision.CONFIRM,
                "Kernel change requires confirm=True",
            )
        return PolicyResult(Decision.ALLOW)

    # ==================================================================
    # Enforcement
    # ==================================================================

    def enforce(self, result: PolicyResult) -> None:
        """Raise if the decision is DENY.  CONFIRM passes (caller handles)."""
        if result.decision == Decision.DENY:
            raise PolicyViolationError(result.reason)

    def log(self, operation: str, actor: str, result: PolicyResult) -> None:
        if self.config.on_decision:
            self.config.on_decision(operation, actor, result)


def load_policy_config() -> PolicyConfig:
    """Build policy config from environment overrides."""
    cfg = PolicyConfig()
    if raw := os.environ.get("DAEDALUS_MAX_DISK_GIB"):
        gib = int(raw)
        cfg.max_disk_bytes = 0 if gib <= 0 else gib * 1024**3
    if raw := os.environ.get("DAEDALUS_MIN_FREE_GIB"):
        cfg.min_free_bytes = int(raw) * 1024**3
    if raw := os.environ.get("DAEDALUS_MAX_CONCURRENT_VMS"):
        cfg.max_concurrent_vms = int(raw)
    return cfg
