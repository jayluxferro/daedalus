"""Tests for Phase 1 — structured exceptions."""

import json

import pytest

from daedalus.core.exceptions import (
    AuditError,
    BackendError,
    BackendTimeoutError,
    CapabilityError,
    CommandNotFoundError,
    ContainerNotFoundError,
    DaedalusError,
    DaemonUnreachableError,
    PolicyViolationError,
    ValidationError,
)


class TestDaedalusError:
    def test_base_error_is_json_serializable(self):
        e = DaedalusError("TEST_CODE", "test message", details={"extra": "value"})
        d = e.to_dict()
        assert d["ok"] is False
        assert d["error"] == "TEST_CODE"
        assert d["message"] == "test message"
        assert d["details"] == {"extra": "value"}
        json.dumps(d)  # must not raise

    def test_str_representation(self):
        e = DaedalusError("TEST", "something happened")
        assert str(e) == "[TEST] something happened"

    def test_is_catchable_as_base(self):
        with pytest.raises(DaedalusError):
            raise ContainerNotFoundError("abc123")


class TestBackendErrors:
    def test_container_not_found(self):
        e = ContainerNotFoundError("abc123")
        assert e.code == "CONTAINER_NOT_FOUND"
        assert "abc123" in e.message
        assert e.details["container_id"] == "abc123"

    def test_command_not_found(self):
        e = CommandNotFoundError("prune")
        assert e.code == "COMMAND_NOT_FOUND"
        assert "prune" in e.message

    def test_timeout(self):
        e = BackendTimeoutError("pull", 30.0)
        assert e.code == "TIMEOUT"
        assert e.details["timeout_seconds"] == 30.0

    def test_daemon_unreachable(self):
        e = DaemonUnreachableError()
        assert e.code == "DAEMON_UNREACHABLE"

    def test_generic_backend_error(self):
        e = BackendError(message="something failed")
        assert e.code == "BACKEND_ERROR"


class TestDomainErrors:
    def test_capability_error(self):
        e = CapabilityError(feature="networking", hint="start container system")
        assert e.code == "CAPABILITY_MISSING"
        assert e.details["feature"] == "networking"
        assert e.details["hint"] == "start container system"

    def test_capability_error_not_implemented(self):
        e = CapabilityError(
            code="NOT_IMPLEMENTED_IN_V010",
            message="prune is not available in container v0.1.0",
            feature="prune",
        )
        assert e.code == "NOT_IMPLEMENTED_IN_V010"

    def test_policy_violation(self):
        e = PolicyViolationError("disk budget exceeded")
        assert e.code == "POLICY_VIOLATION"
        assert "disk budget" in str(e)

    def test_validation_error(self):
        e = ValidationError("bad image name", field="image", value="!!!")
        assert e.code == "VALIDATION_ERROR"
        assert e.details["field"] == "image"

    def test_audit_error(self):
        e = AuditError("checksum mismatch")
        assert e.code == "AUDIT_ERROR"
