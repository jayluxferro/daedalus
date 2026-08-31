"""K8s clusters — the container 1.3.1 ``k8s`` plugin, grouped per Orchard.

Local Kubernetes development clusters are thin wrappers over the ``k8s``
plugin: a cluster is a control-plane node container plus zero or more
worker node containers.  The plugin marks every node container with the
label ``com.apple.container.plugin == "k8s"`` (the spec's
``owningPlugin: k8s``), which is the data-driven membership filter the
whole module is built on (SPEC phase 8 §2).

Grouping follows Orchard's ``K8sCluster.group`` naming rule exactly
(verified against the 1.3.1 surface):

* the cluster name **is the control-plane node container's id**;
* workers are named ``<cluster>-worker-N`` and group under the
  ``<cluster>`` prefix (the ``-worker-N`` suffix is stripped; the
  pattern is ``^(.*)-worker-\\d+$`` — greedy prefix, so a cluster whose
  own name contains ``-worker-`` still groups correctly);
* a node that matches neither the control-plane id rule nor the worker
  name pattern falls back to its own id as the cluster name.

Every mutation is audited; reads are not (same contract as Volumes and
Talos).

.. note::
   The node role wire field was not captured live (no k8s containers on
   the capture host), so ``K8sClusterNode.from_container`` reads the
   role from several tolerated sources, in priority order: a top-level
   ``pluginRole`` key (the shape W1's parser fixture documents), a
   top-level ``role`` key, a ``pluginRole`` inside ``configuration``,
   and finally any ``configuration.labels`` entry whose key contains
   ``"role"`` (e.g. ``com.apple.container.plugin.role``).  Nodes
   without any role key are still grouped (name-pattern fallback) with
   ``role=None`` — see ``test_group_missing_role_tolerated``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from daedalus.core.audit import ActorKind, AuditLog
from daedalus.core.backend import Backend, ClusterCreateSpec
from daedalus.core.capabilities import CapabilityManifest
from daedalus.core.exceptions import ValidationError
from daedalus.core.policy import Decision, PolicyEngine

# Data-driven membership filter — mirrors OutputParser.k8s_nodes.
_PLUGIN_LABEL = "com.apple.container.plugin"
_PLUGIN_VALUE = "k8s"

# Orchard worker naming: ``<cluster>-worker-N``.  The greedy ``.+``
# prefix keeps a cluster whose own name contains ``-worker-`` intact.
_WORKER_NAME_RE = re.compile(r"^(?P<cluster>.+)-worker-\d+$")


@dataclass
class K8sClusterNode:
    """One node container of a k8s cluster."""

    id: str
    cluster_name: str
    role: str | None
    is_control_plane: bool
    is_running: bool
    address: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_container(c: dict[str, Any]) -> K8sClusterNode | None:
        """Map one raw container-list dict to a node.

        Returns ``None`` for containers that are not k8s-owned — the
        data-driven filter (label ``com.apple.container.plugin == "k8s"``,
        the same check W1's ``OutputParser.k8s_nodes`` applies).
        """
        if not _is_k8s_node(c):
            return None
        node_id = _read_id(c)
        role = _read_role(c)
        cluster_name = _derive_cluster_name(c, node_id, role)
        return K8sClusterNode(
            id=node_id,
            cluster_name=cluster_name,
            role=role,
            is_control_plane=_is_control_plane(role, cluster_name, node_id),
            is_running=_read_running(c),
            address=_read_address(c),
            raw=c,
        )


@dataclass
class K8sCluster:
    """A grouped k8s cluster: its control-plane plus its workers."""

    name: str
    nodes: list[K8sClusterNode] = field(default_factory=list)

    @property
    def id(self) -> str:
        # Cluster name == control-plane container id (Orchard's rule).
        return self.name

    @property
    def control_plane(self) -> K8sClusterNode | None:
        """The control-plane node, or ``None`` if not identified."""
        for node in self.nodes:
            if node.is_control_plane:
                return node
        return None

    @property
    def workers(self) -> list[K8sClusterNode]:
        """Worker nodes (everything that is not the control plane)."""
        return [node for node in self.nodes if not node.is_control_plane]

    @property
    def is_running(self) -> bool:
        """True when every node container is running."""
        return bool(self.nodes) and all(node.is_running for node in self.nodes)

    @property
    def status(self) -> str:
        """``running`` | ``stopped`` | ``partial`` | ``unknown``."""
        if not self.nodes:
            return "unknown"
        running = sum(1 for node in self.nodes if node.is_running)
        if running == len(self.nodes):
            return "running"
        if running == 0:
            return "stopped"
        return "partial"

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable shape for the API/CLI/UI surfaces."""
        cp = self.control_plane
        return {
            "name": self.name,
            "id": self.id,
            "is_running": self.is_running,
            "status": self.status,
            "control_plane": _node_to_dict(cp) if cp is not None else None,
            "workers": [_node_to_dict(node) for node in self.workers],
        }

    @staticmethod
    def group(containers: list[dict[str, Any]]) -> list[K8sCluster]:
        """Group raw node containers into clusters (pure, backend-free).

        Data-driven: non-k8s containers are filtered out by
        :meth:`K8sClusterNode.from_container`; the remainder are grouped
        by their Orchard-derived cluster name.  Clusters sort by name;
        nodes sort control-plane first, then workers by id.
        """
        nodes = [
            node
            for c in containers
            if (node := K8sClusterNode.from_container(c)) is not None
        ]
        by_name: dict[str, list[K8sClusterNode]] = {}
        for node in nodes:
            by_name.setdefault(node.cluster_name, []).append(node)
        clusters = [
            K8sCluster(
                name=name,
                nodes=sorted(
                    ns, key=lambda n: (not n.is_control_plane, n.id),
                ),
            )
            for name, ns in by_name.items()
        ]
        clusters.sort(key=lambda c: c.name)
        return clusters


class Clusters:
    """The k8s cluster engine — CRUD over the container ``k8s`` plugin.

    Parameters
    ----------
    backend:
        The active ``Backend`` implementation.
    capabilities:
        Host capability manifest.
    policy:
        Optional policy engine for pre-execution guardrails.
    audit:
        Optional audit log for operation recording.
    """

    def __init__(
        self,
        backend: Backend,
        capabilities: CapabilityManifest,
        *,
        policy: PolicyEngine | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._backend = backend
        self._capabilities = capabilities
        self._policy = policy or PolicyEngine()
        self._audit = audit or AuditLog()

    @property
    def backend(self) -> Backend:
        return self._backend

    @property
    def capabilities(self) -> CapabilityManifest:
        return self._capabilities

    # ==================================================================
    # Lifecycle
    # ==================================================================

    async def create(self, spec: ClusterCreateSpec) -> str:
        """Create and start a local Kubernetes cluster; returns its name."""
        name = await self._backend.k8s_create(spec)
        self._audit.record(
            "cluster_create", actor="clusters", actor_kind=ActorKind.SERVICE,
            args={
                "name": spec.name,
                "node_image": spec.node_image,
                "cpus": spec.cpus,
                "memory": spec.memory,
            },
            result={"name": name},
        )
        return name

    async def list(self) -> list[K8sCluster]:
        """List clusters, grouped per Orchard (read-only: no audit).

        The backend returns the raw node containers pre-filtered to the
        k8s plugin; grouping happens here in the pure function so the
        engine stays unit-testable without a backend.
        """
        raw = await self._backend.k8s_list()
        return K8sCluster.group(raw)

    async def delete(self, name: str, *, confirm: bool = False) -> None:
        """Delete a Kubernetes cluster.

        Destructive — requires explicit ``confirm=True`` (policy-gated,
        mirroring network/volume delete).
        """
        r = self._policy.check_cluster_delete(confirm=confirm)
        if r.decision == Decision.CONFIRM:
            raise ValidationError("cluster delete requires confirm=True")
        self._policy.log("check_cluster_delete", "clusters", r)
        self._policy.enforce(r)
        await self._backend.k8s_delete(name)
        self._audit.record(
            "cluster_delete", actor="clusters", actor_kind=ActorKind.SERVICE,
            args={"name": name, "confirm": confirm},
        )

    async def start(self, name: str) -> None:
        """Start a stopped Kubernetes cluster."""
        await self._backend.k8s_start(name)
        self._audit.record(
            "cluster_start", actor="clusters", actor_kind=ActorKind.SERVICE,
            args={"name": name},
        )

    async def load_image(
        self, name: str, image: str, *, platform: str | None = None,
    ) -> None:
        """Load a container image into a cluster's containerd."""
        await self._backend.k8s_load_image(name, image, platform=platform)
        self._audit.record(
            "cluster_load_image", actor="clusters", actor_kind=ActorKind.SERVICE,
            args={"name": name, "image": image, "platform": platform},
        )

    async def write_config(
        self, name: str, *, kubeconfig: str | None = None,
    ) -> str:
        """Write the cluster context to a kubeconfig; returns the path
        (or the printed config text when no path was given)."""
        path = await self._backend.k8s_write_config(name, kubeconfig=kubeconfig)
        self._audit.record(
            "cluster_write_config", actor="clusters", actor_kind=ActorKind.SERVICE,
            args={"name": name, "kubeconfig": kubeconfig},
        )
        return path


# ==================================================================
# Wire-shape helpers (tolerant, data-driven)
# ==================================================================


def _is_k8s_node(c: dict[str, Any]) -> bool:
    """Data-driven membership filter: the k8s plugin label."""
    cfg = c.get("configuration")
    if not isinstance(cfg, dict):
        return False
    labels = cfg.get("labels")
    return isinstance(labels, dict) and labels.get(_PLUGIN_LABEL) == _PLUGIN_VALUE


def _read_id(c: dict[str, Any]) -> str:
    """Node id: top-level ``id`` (the list shape), else ``configuration.id``."""
    top = c.get("id")
    if isinstance(top, str) and top:
        return top
    cfg = c.get("configuration")
    if isinstance(cfg, dict):
        cfg_id = cfg.get("id")
        if isinstance(cfg_id, str) and cfg_id:
            return cfg_id
    return ""


def _read_role(c: dict[str, Any]) -> str | None:
    """Read the node role from the tolerated wire shapes (see module doc).

    Priority: top-level ``pluginRole`` (W1's documented parser shape),
    top-level ``role``, ``configuration.pluginRole``, then any
    ``configuration.labels`` key containing ``"role"``.
    """
    for candidate in (c.get("pluginRole"), c.get("role")):
        if isinstance(candidate, str) and candidate:
            return candidate
    cfg = c.get("configuration")
    if isinstance(cfg, dict):
        cfg_role = cfg.get("pluginRole")
        if isinstance(cfg_role, str) and cfg_role:
            return cfg_role
        labels = cfg.get("labels")
        if isinstance(labels, dict):
            for key, value in labels.items():
                if isinstance(key, str) and "role" in key and isinstance(value, str) and value:
                    return value
    return None


def _read_name(c: dict[str, Any]) -> str:
    """Container name: ``configuration.name``, else top-level ``name``."""
    cfg = c.get("configuration")
    if isinstance(cfg, dict):
        cfg_name = cfg.get("name")
        if isinstance(cfg_name, str) and cfg_name:
            return cfg_name
    top = c.get("name")
    return top if isinstance(top, str) else ""


def _derive_cluster_name(
    c: dict[str, Any], node_id: str, role: str | None,
) -> str:
    """Orchard's grouping rule (SPEC phase 8 §2):

    * control-plane role → the node's own id is the cluster name;
    * worker role (or role-less node matching the worker name pattern)
      → strip the ``-worker-N`` suffix from the container name;
    * anything else → fall back to the node's own id.
    """
    if (role or "").lower() == "control-plane":
        return node_id
    name = _read_name(c)
    match = _WORKER_NAME_RE.match(name)
    if match:
        return match.group("cluster")
    return node_id


def _is_control_plane(role: str | None, cluster_name: str, node_id: str) -> bool:
    """Control-plane when the role says so, or when a role-less node was
    grouped as its own cluster (the name-pattern fallback)."""
    if role is not None:
        return role.lower() == "control-plane"
    return cluster_name == node_id


def _read_running(c: dict[str, Any]) -> bool:
    """Running state from ``status.state`` (tolerant coercion)."""
    status = c.get("status")
    if not isinstance(status, dict):
        return False
    state = status.get("state")
    return isinstance(state, str) and state.lower() == "running"


def _read_address(c: dict[str, Any]) -> str | None:
    """Node address — heuristic, pending a live capture of the k8s plugin
    wire shape (no k8s containers existed on the capture host): reads the
    first string from top-level ``address``, ``status.address`` or
    ``configuration.address``."""
    status = c.get("status")
    if not isinstance(status, dict):
        status = {}
    cfg = c.get("configuration")
    if not isinstance(cfg, dict):
        cfg = {}
    for source in (c, status, cfg):
        value = source.get("address")
        if isinstance(value, str) and value:
            return value
    return None


def _node_to_dict(node: K8sClusterNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "cluster_name": node.cluster_name,
        "role": node.role,
        "is_control_plane": node.is_control_plane,
        "is_running": node.is_running,
        "address": node.address,
    }
