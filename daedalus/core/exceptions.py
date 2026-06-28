"""Structured exception hierarchy for DAEDALUS.

Every exception carries a machine-readable ``code``, a human-readable
``message``, and optional ``details`` for structured error reporting.
All exceptions inherit from :class:`DaedalusError` so callers can catch
a single base type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DaedalusError(Exception):
    """Base exception for all DAEDALUS errors.

    Every error is serialisable to JSON for MCP ``isError=True`` responses.
    """

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self.code,
            "message": self.message,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Backend errors
# ---------------------------------------------------------------------------


class BackendError(DaedalusError):
    """Raised when the container backend fails.

    Codes:
        BACKEND_ERROR — generic backend failure
        CONTAINER_NOT_FOUND — container ID/name not known to the runtime
        COMMAND_NOT_FOUND — subcommand doesn't exist in this version
        TIMEOUT — backend operation exceeded its time limit
        DAEMON_UNREACHABLE — container API server is not running
    """

    def __init__(
        self,
        code: str = "BACKEND_ERROR",
        message: str = "Backend operation failed",
        **details: Any,
    ) -> None:
        super().__init__(code=code, message=message, details=details)


class ContainerNotFoundError(BackendError):
    """Raised when a container ID or name is not found."""

    def __init__(self, container_id: str) -> None:
        super().__init__(
            code="CONTAINER_NOT_FOUND",
            message=f"Container '{container_id}' not found",
            container_id=container_id,
        )


class CommandNotFoundError(BackendError):
    """Raised when a container subcommand does not exist in this version."""

    def __init__(self, command: str) -> None:
        super().__init__(
            code="COMMAND_NOT_FOUND",
            message=f"Command '{command}' is not available in this version of container",
            command=command,
        )


class BackendTimeoutError(BackendError):
    """Raised when a backend operation times out."""

    def __init__(self, operation: str, timeout: float) -> None:
        super().__init__(
            code="TIMEOUT",
            message=f"Operation '{operation}' timed out after {timeout}s",
            operation=operation,
            timeout_seconds=timeout,
        )


class DaemonUnreachableError(BackendError):
    """Raised when the container API server is not running."""

    def __init__(self) -> None:
        super().__init__(
            code="DAEMON_UNREACHABLE",
            message="The container API server is not running. "
                    "Start it with: container system start",
        )


# ---------------------------------------------------------------------------
# Domain errors
# ---------------------------------------------------------------------------


class CapabilityError(DaedalusError):
    """Raised when a requested feature is not available on this host.

    Codes:
        CAPABILITY_MISSING — feature is absent
        NOT_IMPLEMENTED_IN_V010 — feature expected in a future container version
    """

    def __init__(
        self,
        code: str = "CAPABILITY_MISSING",
        message: str = "Required capability is not available",
        feature: str = "",
        hint: str = "",
    ) -> None:
        details: dict[str, Any] = {"feature": feature}
        if hint:
            details["hint"] = hint
        super().__init__(code=code, message=message, details=details)


class PolicyViolationError(DaedalusError):
    """Raised when a policy check denies an operation.

    Replaces the old ``PolicyViolation(RuntimeError)``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            code="POLICY_VIOLATION",
            message=reason,
        )


class ValidationError(DaedalusError):
    """Raised when input validation fails (bad RunSpec, bad profile, etc.)."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            details=details,
        )


class AuditError(DaedalusError):
    """Raised when an audit operation fails (e.g., checksum mismatch)."""

    def __init__(self, message: str) -> None:
        super().__init__(
            code="AUDIT_ERROR",
            message=message,
        )
