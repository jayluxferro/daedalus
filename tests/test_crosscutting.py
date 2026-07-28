"""Tests for M5 — profiles, policy, audit, store."""

import json
import os
import tempfile

import pytest

from daedalus.core.audit import ActorKind, AuditEntry, AuditLog
from daedalus.core.exceptions import PolicyViolationError
from daedalus.core.policy import Decision, PolicyConfig, PolicyEngine, PolicyResult
from daedalus.core.profiles import (
    BUILTIN_PROFILES,
    Profile,
    ProfileRegistry,
)
from daedalus.core.store import Artifact, RunManifest, Store

# ==========================================================================
# Profiles
# ==========================================================================


class TestProfiles:
    def test_builtin_profiles_exist(self):
        assert "detonation" in BUILTIN_PROFILES
        assert "bench" in BUILTIN_PROFILES
        assert "fuzz" in BUILTIN_PROFILES
        assert "isolated" in BUILTIN_PROFILES
        assert "deception" in BUILTIN_PROFILES

    def test_detonation_is_safe(self):
        p = BUILTIN_PROFILES["detonation"]
        assert p.no_dns is True
        assert p.dns == []
        assert p.tmpfs == ["/tmp", "/var/tmp"]

    def test_bench_is_permissive(self):
        p = BUILTIN_PROFILES["bench"]
        assert p.kernel is None
        assert p.cpus is None

    def test_apply_returns_dict(self):
        p = BUILTIN_PROFILES["detonation"]
        kwargs = p.apply()
        assert kwargs["no_dns"] is True
        assert kwargs["dns"] == []
        assert kwargs["tmpfs"] == ["/tmp", "/var/tmp"]

    def test_apply_accepts_overrides(self):
        p = BUILTIN_PROFILES["detonation"]
        kwargs = p.apply(no_dns=False, kernel="custom")
        assert kwargs["no_dns"] is False
        assert kwargs["kernel"] == "custom"

    def test_registry_default_is_detonation(self):
        reg = ProfileRegistry()
        p = reg.get("default")
        assert p.name == "detonation"

    def test_registry_unknown_falls_back_to_detonation(self):
        reg = ProfileRegistry()
        p = reg.get("nonexistent")
        assert p.name == "detonation"

    def test_registry_register_custom(self):
        reg = ProfileRegistry()
        custom = Profile(name="minimal", no_dns=True, kernel="custom")
        reg.register(custom)
        assert reg.get("minimal") is custom

    def test_registry_list(self):
        reg = ProfileRegistry()
        profiles = reg.list()
        assert len(profiles) >= 5  # builtins


# ==========================================================================
# Policy
# ==========================================================================


class TestPolicy:
    def test_concurrency_allow(self):
        engine = PolicyEngine()
        r = engine.check_concurrency(5)
        assert r.decision == Decision.ALLOW

    def test_concurrency_deny(self):
        engine = PolicyEngine(PolicyConfig(max_concurrent_vms=4))
        r = engine.check_concurrency(4)
        assert r.decision == Decision.DENY

    def test_disk_allow(self):
        engine = PolicyEngine()
        r = engine.check_disk({"store_bytes": 1024, "free": 10 * 1024**3})
        assert r.decision == Decision.ALLOW

    def test_disk_deny_store(self):
        engine = PolicyEngine(PolicyConfig(max_disk_bytes=1000))
        r = engine.check_disk({"store_bytes": 1000, "free": 10 * 1024**3})
        assert r.decision == Decision.DENY

    def test_disk_deny_low_free(self):
        engine = PolicyEngine(PolicyConfig(min_free_bytes=1000))
        r = engine.check_disk({"store_bytes": 100, "free": 500})
        assert r.decision == Decision.DENY

    def test_disk_ignores_volume_used(self):
        """Whole-volume used bytes must not trip the store budget."""
        engine = PolicyEngine(PolicyConfig(max_disk_bytes=100 * 1024**3))
        r = engine.check_disk({
            "used": 500 * 1024**3,
            "store_bytes": 1024,
            "free": 50 * 1024**3,
        })
        assert r.decision == Decision.ALLOW

    def test_image_blocklist(self):
        engine = PolicyEngine(PolicyConfig(image_blocklist=["malicious"]))
        r = engine.check_image("malicious:latest")
        assert r.decision == Decision.DENY

    def test_image_allowlist_deny(self):
        engine = PolicyEngine(PolicyConfig(image_allowlist=["alpine"]))
        r = engine.check_image("ubuntu:latest")
        assert r.decision == Decision.DENY

    def test_image_allowlist_allow(self):
        engine = PolicyEngine(PolicyConfig(image_allowlist=["alpine"]))
        r = engine.check_image("alpine:latest")
        assert r.decision == Decision.ALLOW

    def test_destroy_requires_confirm(self):
        engine = PolicyEngine()
        r = engine.check_destroy()
        assert r.decision == Decision.CONFIRM

    def test_destroy_with_confirm(self):
        engine = PolicyEngine()
        r = engine.check_destroy(confirm=True)
        assert r.decision == Decision.ALLOW

    def test_enforce_raises_on_deny(self):
        engine = PolicyEngine()
        with pytest.raises(PolicyViolationError):
            engine.enforce(PolicyResult(Decision.DENY, "blocked"))

    def test_enforce_passes_on_allow(self):
        engine = PolicyEngine()
        engine.enforce(PolicyResult(Decision.ALLOW))  # no exception

    def test_custom_on_decision_callback(self):
        calls = []
        config = PolicyConfig(on_decision=lambda op, actor, r: calls.append((op, actor, r.decision)))
        engine = PolicyEngine(config)
        r = engine.check_concurrency(1)
        engine.log("create", "agent", r)
        assert len(calls) == 1
        assert calls[0] == ("create", "agent", Decision.ALLOW)


# ==========================================================================
# Audit
# ==========================================================================


class TestAudit:
    def test_record_entry(self):
        log = AuditLog(path=os.path.join(tempfile.mkdtemp(), "audit.jsonl"))
        entry = log.record("create", actor="test", actor_kind=ActorKind.AGENT)
        assert entry.operation == "create"
        assert entry.entry_id
        assert entry.checksum
        assert log.count() == 1

    def test_query_by_operation(self):
        log = AuditLog(path=os.path.join(tempfile.mkdtemp(), "audit.jsonl"))
        log.record("create")
        log.record("destroy")
        log.record("create")
        results = log.query(operation="create")
        assert len(results) == 2

    def test_query_by_actor(self):
        log = AuditLog(path=os.path.join(tempfile.mkdtemp(), "audit.jsonl"))
        log.record("create", actor="human")
        log.record("create", actor="agent")
        results = log.query(actor="agent")
        assert len(results) == 1

    def test_tail(self):
        log = AuditLog(path=os.path.join(tempfile.mkdtemp(), "audit.jsonl"))
        for i in range(30):
            log.record(f"op-{i}")
        tail = log.tail(5)
        assert len(tail) == 5
        assert tail[-1].operation == "op-29"

    def test_verify(self):
        log = AuditLog(path=os.path.join(tempfile.mkdtemp(), "audit.jsonl"))
        log.record("create")
        assert log.verify() is True

    def test_to_json(self):
        entry = AuditEntry(
            operation="create",
            actor="test",
            actor_kind=ActorKind.HUMAN,
        )
        entry.finalize()
        data = json.loads(entry.to_json())
        assert data["operation"] == "create"
        assert data["entry_id"]


# ==========================================================================
# Store
# ==========================================================================


class TestStore:
    def test_create_and_get(self):
        s = Store(root=tempfile.mkdtemp())
        m = s.create("run-001", image="alpine:latest", profile="detonation")
        assert m.run_id == "run-001"
        assert m.image == "alpine:latest"

        retrieved = s.get("run-001")
        assert retrieved is not None
        assert retrieved.image == "alpine:latest"

    def test_list(self):
        s = Store(root=tempfile.mkdtemp())
        s.create("run-001", image="alpine:latest")
        s.create("run-002", image="ubuntu:latest")
        manifests = s.list()
        assert len(manifests) == 2

    def test_update(self):
        s = Store(root=tempfile.mkdtemp())
        s.create("run-001", image="alpine:latest")
        s.update("run-001", exit_code=0)
        m = s.get("run-001")
        assert m is not None
        assert m.exit_code == 0

    def test_add_artifact(self):
        s = Store(root=tempfile.mkdtemp())
        s.create("run-001", image="alpine:latest")
        s.add_artifact("run-001", Artifact(
            name="report.json",
            path="/tmp/report.json",
            kind="report",
            size_bytes=1024,
        ))
        m = s.get("run-001")
        assert m is not None
        assert len(m.artifacts) == 1
        assert m.artifacts[0].name == "report.json"

    def test_get_nonexistent(self):
        s = Store(root=tempfile.mkdtemp())
        assert s.get("nonexistent") is None

    def test_manifest_to_json(self):
        m = RunManifest(
            run_id="run-001",
            image="alpine:latest",
            profile="detonation",
        )
        data = json.loads(m.to_json())
        assert data["run_id"] == "run-001"
        assert data["profile"] == "detonation"
