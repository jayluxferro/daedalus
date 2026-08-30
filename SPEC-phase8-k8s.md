# SPEC Phase 8 — k8s plugin wrapping (clusters)

Status: **Approved** · Branch: main (post-P7)

## 1. Objectives

Wrap the container 1.3.1 `k8s` plugin (local Kubernetes development clusters) across the full stack, porting Orchard's ClusterService grouping model. In scope now by user mandate: every runtime feature wrapped, nothing missed.

## 2. Verified surface (captured live, 1.3.1)

`k8s` subcommands: `create` (--name, --node-image, --max-concurrent-downloads, --scheme, --rm, -c cpus, -m memory), `delete/rm` (--name), `list/ls`, `load-image` (--name, --platform), `start` (--name), `write-config` (--name, --kubeconfig). Cluster node containers carry `owningPlugin: k8s` + `pluginRole: control-plane|worker` in the container list JSON; cluster name = control-plane id; workers named `<cluster>-worker-N` (verified pattern in Orchard's `K8sCluster.group`).

## 3. Architecture decisions

1. **New engine module `daedalus/core/clusters.py`** — class `Clusters(backend, capabilities, *, policy=None, audit=None)`: `create(spec: ClusterCreateSpec) -> dict`, `list() -> list[K8sCluster]`, `delete(name, *, confirm=False) -> None`, `start(name) -> None`, `load_image(name, image, platform=None) -> None`, `write_config(name, kubeconfig: str | None = None) -> str` (returns the written path). Audit every mutation.
2. **Grouping is pure**: `K8sCluster.group(containers: list[ContainerInfo]) -> list[K8sCluster]` — data-driven port of Orchard's group function (owningPlugin label check, control-plane vs `<name>-worker-N` derivation); unit-testable without a backend.
3. **Data types** in `daedalus/core/backend.py`: `ClusterCreateSpec` (name, node_image, cpus, memory, remove, scheme) + `to_cli_args()`; `K8sCluster`/`K8sClusterNode` dataclasses in clusters.py (id, name, role, is_control_plane, is_running, address, raw).
4. **Backend verbs**: `k8s_create(spec) -> str` (cluster name), `k8s_list() -> list[dict]` (raw node containers via `container list --format json` filtered client-side by `owningPlugin == k8s` — the CLI `k8s list` output is table-only per the capture, so parse the container list like Orchard does), `k8s_delete(name)`, `k8s_start(name)`, `k8s_load_image(name, image, platform)`, `k8s_write_config(name, kubeconfig) -> str` (kubeconfig default: `container k8s write-config --name X` prints/merges — run with `--kubeconfig <path>` when given; else capture the printed config text and return it). Gate: `_require("container_k8s")`.
5. **Capability gate**: `k8s` tri-state probed via `_probe_subcommand(binary, "k8s")`; `runtime_features["container_k8s"]`; SUBCOMMANDS + summary line.
6. **Policy**: `check_cluster_delete(*, confirm=False)` mirroring the confirm-gate family.

## 4. Surfaces

- **CLI**: `k8s` sub-app — `create` (name, --node-image, --cpus, --memory, --rm), `list` (grouped table: cluster name, control-plane status, worker count, addresses; --json), `start <name>`, `load-image <name> <image> [--platform]`, `write-config <name> [--kubeconfig PATH]`, `delete <name> --confirm`.
- **API**: `GET /clusters`, `POST /clusters` (create), `POST /clusters/{name}/start`, `POST /clusters/{name}/load-image`, `GET /clusters/{name}/kubeconfig` (returns the config text; `POST` variant when a path is given), `DELETE /clusters/{name}?confirm=true`.
- **MCP**: `daedalus_cluster_list` (readOnly), `daedalus_cluster_create`, `daedalus_cluster_start`, `daedalus_cluster_load_image`, `daedalus_cluster_write_config`, `daedalus_cluster_delete` (destructiveHint + CONFIRM_REQUIRED).
- **UI**: `views/Clusters.tsx` + `api/clusters.ts` — cluster cards/table (status, nodes, addresses), create form, per-cluster start/load-image/kubeconfig-view/delete; nav entry. Same data-driven patterns as Machines/Networks.

## 5. Waves

- **W1 (1 agent)**: capabilities k8s gate + backend dataclasses/verbs + CliBackend impls + parsers + MockBackend stubs + parser/gate unit tests.
- **W2 (1 agent, after W1)**: clusters.py engine (grouping pure fn + CRUD + policy gate) + `tests/test_clusters.py`.
- **W3 (4 agents, parallel, after W2)**: CLI / API / MCP / UI.
- **W4 (1 agent, after W3)**: integration (live: `k8s create` a single-node cluster is HEAVY — mark `slow`, skip-if-unavailable; minimal live test: create → list shows cluster → write-config returns config → load-image with a tiny image → delete; full cleanup) + docs.
- **W5 (orchestrator)**: gates + macos-harness regression + commit + push.

## 6. Success metrics

1. A live `k8s create` cluster appears in `daedalus k8s list` grouped with role/status/address; kubeconfig written; delete cleans up.
2. Grouping pure-function unit tests cover control-plane/worker/multiple-cluster/non-k8s filtering.
3. All surfaces expose the same six verbs; UI shows clusters.
4. Gates green.

## 7. Non-goals

- No in-cluster workload management (kubectl/k9s is the user's tool — same call as Orchard).
- No cluster upgrades/autoscaling.
