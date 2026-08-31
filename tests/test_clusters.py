"""Tests for the k8s cluster engine (SPEC phase 8, W2).

Grouping tests exercise the pure ``K8sCluster.group`` function against
literal wire shapes (the same shape W1's ``OutputParser.k8s_nodes``
produces); lifecycle tests exercise ``Clusters`` against the shared
``MockBackend`` (call-recording k8s stubs) with a settable-return
subclass for ``k8s_list``.
"""

from __future__ import annotations

from typing import Any

import pytest

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import ClusterCreateSpec
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.clusters import Clusters, K8sCluster
from daedalus.core.exceptions import PolicyViolationError, ValidationError
from daedalus.core.policy import Decision, PolicyEngine, PolicyResult
from tests.test_forge import MockBackend

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _node(
    *,
    node_id: str,
    name: str,
    role: str | None = None,
    state: str = "running",
    plugin: bool = True,
    address: str | None = None,
) -> dict[str, Any]:
    """Build one literal ``container list --format json`` item in the
    k8s-node wire shape W1 documents (top-level ``id``/``status``/
    ``pluginRole`` + ``configuration.labels`` plugin marker)."""
    item: dict[str, Any] = {
        "id": node_id,
        "configuration": {
            "id": node_id,
            "name": name,
            "labels": (
                {"com.apple.container.plugin": "k8s"} if plugin else {}
            ),
        },
        "status": {"state": state},
    }
    if role is not None:
        item["pluginRole"] = role
    if address is not None:
        item["address"] = address
    return item


@pytest.fixture
def caps() -> CapabilityManifest:
    return CapabilityManifest(
        host_arch="arm64",
        macos_version="26.0",
        macos_version_tuple=(26, 0, 0),
        container_binary="/usr/local/bin/container",
        container_version="0.1.0",
        container_commit="test",
        apiserver_running=True,
        networking=True,
        kernel_set=True,
        builder=False,
        system_dns=True,
        k8s=True,
    )


@pytest.fixture
def clusters(caps: CapabilityManifest) -> Clusters:
    return Clusters(MockBackend(), caps, audit=AuditLog())


class K8sMockBackend(MockBackend):
    """MockBackend with settable ``k8s_list`` results (extends nothing)."""

    def __init__(self, nodes: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self._k8s_nodes = nodes or []

    async def k8s_list(self) -> list[dict]:
        self.k8s_calls.append(("list", ()))
        return self._k8s_nodes


# ------------------------------------------------------------------
# Grouping (pure function — no backend)
# ------------------------------------------------------------------


class TestGroup:
    def test_group_filters_non_k8s(self) -> None:
        cp = _node(node_id="cp-abc", name="my-cluster", role="control-plane")
        plain = _node(node_id="plain-xyz", name="plain-xyz", plugin=False)
        clusters = K8sCluster.group([plain, cp])
        assert [c.name for c in clusters] == ["cp-abc"]
        assert [n.id for n in clusters[0].nodes] == ["cp-abc"]

    def test_group_single_control_plane(self) -> None:
        cp = _node(node_id="cp-abc", name="my-cluster", role="control-plane")
        clusters = K8sCluster.group([cp])
        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster.name == "cp-abc"
        assert cluster.id == "cp-abc"
        assert [n.id for n in cluster.nodes] == ["cp-abc"]
        node = cluster.nodes[0]
        assert node.is_control_plane is True
        assert node.cluster_name == "cp-abc"
        assert node.role == "control-plane"
        assert node.is_running is True
        assert cluster.workers == []
        assert cluster.is_running is True
        assert cluster.status == "running"

    def test_group_workers_derived(self) -> None:
        cp = _node(node_id="cp-abc", name="my-cluster", role="control-plane")
        w1 = _node(node_id="w-001", name="cp-abc-worker-1", role="worker")
        w2 = _node(node_id="w-002", name="cp-abc-worker-2", role="worker")
        clusters = K8sCluster.group([w2, cp, w1])
        assert [c.name for c in clusters] == ["cp-abc"]
        cluster = clusters[0]
        # Nodes sort control-plane first, then workers by id.
        assert [n.id for n in cluster.nodes] == ["cp-abc", "w-001", "w-002"]
        assert [n.cluster_name for n in cluster.nodes] == [
            "cp-abc", "cp-abc", "cp-abc",
        ]
        assert [n.role for n in cluster.nodes] == [
            "control-plane", "worker", "worker",
        ]
        assert [n.id for n in cluster.workers] == ["w-001", "w-002"]
        assert cluster.control_plane is not None
        assert cluster.control_plane.id == "cp-abc"
        assert cluster.status == "running"

    def test_group_multiple_clusters(self) -> None:
        a_cp = _node(node_id="a-cp", name="a", role="control-plane")
        a_w = _node(node_id="a-w1", name="a-cp-worker-1", role="worker")
        b_cp = _node(node_id="b-cp", name="b", role="control-plane")
        b_w = _node(node_id="b-w1", name="b-cp-worker-1", role="worker")
        clusters = K8sCluster.group([b_cp, a_cp, b_w, a_w])
        # Clusters sort by name.
        assert [c.name for c in clusters] == ["a-cp", "b-cp"]
        assert [n.id for n in clusters[0].nodes] == ["a-cp", "a-w1"]
        assert [n.id for n in clusters[1].nodes] == ["b-cp", "b-w1"]

    def test_group_missing_role_tolerated(self) -> None:
        # No role key at all: still grouped, role stays None, and the
        # node is treated as its own cluster's control plane.
        cp = _node(node_id="cp-abc", name="my-cluster")
        clusters = K8sCluster.group([cp])
        assert len(clusters) == 1
        node = clusters[0].nodes[0]
        assert node.role is None
        assert node.cluster_name == "cp-abc"
        assert node.is_control_plane is True

    def test_group_roleless_worker_name_still_groups(self) -> None:
        # A role-less node whose *name* matches the Orchard worker
        # pattern groups under the stripped prefix (name-pattern fallback).
        w1 = _node(node_id="w-001", name="cp-abc-worker-1")
        clusters = K8sCluster.group([w1])
        assert len(clusters) == 1
        node = clusters[0].nodes[0]
        assert node.role is None
        assert node.cluster_name == "cp-abc"
        assert node.is_control_plane is False

    def test_group_non_running_and_address(self) -> None:
        cp = _node(
            node_id="cp-abc", name="my-cluster", role="control-plane",
            state="stopped", address="10.0.2.2",
        )
        w = _node(node_id="w-001", name="cp-abc-worker-1", role="worker")
        cluster = K8sCluster.group([cp, w])[0]
        assert cluster.is_running is False
        assert cluster.status == "partial"
        assert cluster.nodes[0].is_running is False
        assert cluster.nodes[0].address == "10.0.2.2"
        assert cluster.workers[0].is_running is True

    def test_to_dict(self) -> None:
        cp = _node(node_id="cp-abc", name="my-cluster", role="control-plane")
        w = _node(node_id="w-001", name="cp-abc-worker-1", role="worker")
        cluster = K8sCluster.group([cp, w])[0]
        d = cluster.to_dict()
        assert d["name"] == "cp-abc"
        assert d["id"] == "cp-abc"
        assert d["status"] == "running"
        assert d["is_running"] is True
        assert d["control_plane"] is not None
        assert d["control_plane"]["id"] == "cp-abc"
        assert [n["id"] for n in d["workers"]] == ["w-001"]

    def test_group_empty(self) -> None:
        assert K8sCluster.group([]) == []


# ------------------------------------------------------------------
# Policy gate
# ------------------------------------------------------------------


class TestCheckClusterDelete:
    def test_requires_confirm(self) -> None:
        engine = PolicyEngine()
        r = engine.check_cluster_delete()
        assert r.decision == Decision.CONFIRM
        assert "confirm" in r.reason

    def test_with_confirm(self) -> None:
        engine = PolicyEngine()
        assert engine.check_cluster_delete(confirm=True).decision == Decision.ALLOW


# ------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------


class TestCreate:
    async def test_create_delegates_and_audits(self, clusters: Clusters) -> None:
        backend = clusters._backend
        assert isinstance(backend, MockBackend)
        spec = ClusterCreateSpec(
            name="lab", node_image="docker.io/kindest/node:v1.35.5",
            cpus=2, memory="2G",
        )
        name = await clusters.create(spec)
        assert name == "lab"
        assert backend.k8s_calls == [("create", (spec,))]
        entry = clusters._audit.query(operation="cluster_create")[-1]
        assert entry.actor == "clusters"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {
            "name": "lab",
            "node_image": "docker.io/kindest/node:v1.35.5",
            "cpus": 2,
            "memory": "2G",
        }
        assert entry.result == {"name": "lab"}


class TestList:
    async def test_list_groups_and_no_audit(self) -> None:
        cp = _node(node_id="cp-abc", name="my-cluster", role="control-plane")
        w = _node(node_id="w-001", name="cp-abc-worker-1", role="worker")
        plain = _node(node_id="plain-xyz", name="plain-xyz", plugin=False)
        cl = Clusters(K8sMockBackend([plain, w, cp]), CapabilityManifest(), audit=AuditLog())
        result = await cl.list()
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], K8sCluster)
        assert result[0].name == "cp-abc"
        assert [n.id for n in result[0].nodes] == ["cp-abc", "w-001"]
        assert cl._audit.count() == 0


class TestDelete:
    async def test_requires_confirm(self, clusters: Clusters) -> None:
        backend = clusters._backend
        assert isinstance(backend, MockBackend)
        with pytest.raises(ValidationError, match="confirm"):
            await clusters.delete("victim-cluster")
        assert backend.k8s_calls == []

    async def test_with_confirm_delegates_and_audits(self, clusters: Clusters) -> None:
        backend = clusters._backend
        assert isinstance(backend, MockBackend)
        await clusters.delete("victim-cluster", confirm=True)
        assert backend.k8s_calls == [("delete", ("victim-cluster",))]
        entry = clusters._audit.query(operation="cluster_delete")[-1]
        assert entry.actor == "clusters"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {"name": "victim-cluster", "confirm": True}

    async def test_delete_policy_deny(self, caps: CapabilityManifest) -> None:
        # Mirrors TestNetworkDelete.test_policy_deny_despite_confirm.
        class DenyClusterDeletePolicy(PolicyEngine):
            def check_cluster_delete(self, *, confirm: bool = False) -> PolicyResult:
                return PolicyResult(Decision.DENY, "cluster delete forbidden by policy")

        cl = Clusters(
            MockBackend(), caps, policy=DenyClusterDeletePolicy(), audit=AuditLog(),
        )
        with pytest.raises(PolicyViolationError, match="forbidden"):
            await cl.delete("victim-cluster", confirm=True)


class TestStartLoadImageWriteConfig:
    async def test_start_delegates_and_audits(self, clusters: Clusters) -> None:
        backend = clusters._backend
        assert isinstance(backend, MockBackend)
        await clusters.start("lab")
        assert backend.k8s_calls == [("start", ("lab",))]
        entry = clusters._audit.query(operation="cluster_start")[-1]
        assert entry.actor == "clusters"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {"name": "lab"}

    async def test_load_image_delegates_and_audits(self, clusters: Clusters) -> None:
        backend = clusters._backend
        assert isinstance(backend, MockBackend)
        await clusters.load_image("lab", "docker.io/library/alpine:latest", platform="linux/arm64")
        assert backend.k8s_calls == [
            ("load_image", ("lab", "docker.io/library/alpine:latest", "linux/arm64")),
        ]
        entry = clusters._audit.query(operation="cluster_load_image")[-1]
        assert entry.args == {
            "name": "lab",
            "image": "docker.io/library/alpine:latest",
            "platform": "linux/arm64",
        }

    async def test_load_image_default_platform(self, clusters: Clusters) -> None:
        backend = clusters._backend
        assert isinstance(backend, MockBackend)
        await clusters.load_image("lab", "alpine:latest")
        assert backend.k8s_calls == [("load_image", ("lab", "alpine:latest", None))]

    async def test_write_config_delegates_and_audits(self, clusters: Clusters) -> None:
        backend = clusters._backend
        assert isinstance(backend, MockBackend)
        path = await clusters.write_config("lab")
        assert path.startswith("apiVersion")
        assert backend.k8s_calls == [("write_config", ("lab", None))]
        entry = clusters._audit.query(operation="cluster_write_config")[-1]
        assert entry.actor == "clusters"
        assert entry.actor_kind == ActorKind.SERVICE
        assert entry.args == {"name": "lab", "kubeconfig": None}

    async def test_write_config_with_path(self, clusters: Clusters) -> None:
        backend = clusters._backend
        assert isinstance(backend, MockBackend)
        path = await clusters.write_config("lab", kubeconfig="/tmp/lab.yaml")
        assert path == "/tmp/lab.yaml"
        assert backend.k8s_calls == [("write_config", ("lab", "/tmp/lab.yaml"))]
