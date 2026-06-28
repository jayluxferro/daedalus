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

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

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
    max_disk_bytes: int = 100 * 1024 * 1024 * 1024  # 100 GiB
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

    def check_disk(self, current_disk_used: int) -> PolicyResult:
        if current_disk_used >= self.config.max_disk_bytes:
            return PolicyResult(
                Decision.DENY,
                f"Disk budget exceeded "
                f"({current_disk_used} >= {self.config.max_disk_bytes})",
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
