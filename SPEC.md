# SPEC: Phase 1 — Ungate DAEDALUS against container 1.2.2

Status: **Approved for implementation** · Branch: `feature/ungate-container-1.2.2`

## 1. Objectives

The host now runs `container` CLI **1.2.2** (commit `0190097`) — the same release Orchard pins. Every capability DAEDALUS's docs mark "not yet in container v0.1.0" now exists. This phase:

1. **Re-probe and ungate** — extend `CapabilityManifest` with gates for the new verb families and make `CliBackend` fail loudly (`CapabilityError`) when a gated verb is called on a host that lacks it. No more silent reliance on stale v0.1.0 assumptions.
2. **Implement the missing L1 verbs** — `stats`, `copy`/`cp`, `export`, `prune`, and full `network` CRUD — in `Backend`/`CliBackend`.
3. **Wire the engine layer** — Forge (stats), Icarus (copy/export), Talos (network CRUD + topology materialization), plus the RunSpec fields 1.2.2 unlocks (`--network`, `--publish`, `--cap-add/--cap-drop`).
4. **Surface everywhere** — CLI, HTTP API, MCP — same engine façade, per house convention.
5. **Test and document** — unit + integration round-trips, stale comments removed, README/ARCHITECTURE status tables updated.

Out of scope (later phases): container `machine` (phase 2), `volume` (phase 2), `k8s` plugin (phase 3), stats *rate* derivation + UI sparklines (needs UI work; this phase delivers one-shot raw samples + the sampler hook), build/registry enhancements.

## 2. Verified environment (wire contract, captured live 2026-08-21)

These shapes were captured on this host with container 1.2.2 and are the parsing contract. Agents must parse against THESE shapes.

### 2.1 `container stats --no-stream --format json [ids...]`

```json
[{
  "blockReadBytes": 2596864, "blockWriteBytes": 0,
  "cpuUsageUsec": 2219, "id": "f634ad0e-...",
  "memoryLimitBytes": 1073741824, "memoryUsageBytes": 2764800,
  "networkRxBytes": 28040, "networkTxBytes": 602,
  "numProcesses": 1
}]
```

Array of per-container objects. `cpuUsageUsec` is **cumulative** — rates require two samples (hook for later; this phase returns raw + `sampled_at`). No ids argument → all running containers. Empty set → `[]`.

### 2.2 `container copy <source> <destination>`

- Container side is `<container-id>:<path>`. The literal `container:` prefix is **invalid** (`Error: invalid path given`).
- Copy-out (`<id>:<path>` → local) prints the destination path to stdout. Copy-in prints nothing on success.
- Works on running containers. Direction = whichever side carries the `id:` prefix.

### 2.3 `container export -o <path> <id>`

- Works on a **running** container in 1.2.2 (verified; the v0.1.0 "must be stopped" constraint is gone).
- Silent on success; writes a full-FS tar (alpine ≈ 9 MB).

### 2.4 `container prune`

- No `--format json`. Text output: `Reclaimed 282.1 MB in disk space` followed by the removed container IDs (one per line).

### 2.5 `container network`

- `create` flags (verified): `--internal`, `--label <k=v>`, `--option <k=v>`, `--plugin` (default `container-network-vmnet`), `--subnet <cidr>`, `--subnet-v6 <cidr>`, positional `<name>`. **`NetworkSpec.to_cli_args()` in `backend.py:269` already matches — reuse unchanged.**
- `list --format json` shape:

```json
[{"configuration": {"creationDate": "2026-08-20T00:56:04Z",
   "labels": {"com.apple.container.resource.role": "builtin"},
   "mode": "nat", "name": "default", "options": {}, "plugin": "container-network-vmnet"},
  "id": "default",
  "status": {"ipv4Gateway": "192.168.64.1", "ipv4Subnet": "192.168.64.0/24",
             "ipv6Subnet": "fd8b:1877:bb8f:91bb::/64"}}]
```

- `inspect <name>` prints a JSON array directly — **no `--format` flag** (it errors). Same shape as list entries.
- `delete <names...>` takes names, plus `--all`.
- `create` output: parse the last non-empty stdout line as the id/name (exact text unverified; integration test asserts round-trip via `list`, not the create output text).

### 2.6 Run/create flags newly verified present (1.2.2)

`--network <network>`, `-p/--publish <spec>`, `--publish-socket <spec>`, `--cap-add <cap>`, `--cap-drop <cap>`, `--virtualization`. RunSpec extension in §5.2 covers `network`, `publish`, `cap_add`, `cap_drop` only.

## 3. Architecture decisions

1. **Gating is defensive, not aspirational.** All new verbs exist in 1.2.2 and the probe will confirm them; the gates exist so older/other hosts fail loudly instead of emitting `container: unrecognized subcommand` mid-operation. `CliBackend` gains `self._require(feature: str)` reading `capabilities.runtime_features`; raising `daedalus.core.exceptions.CapabilityError` (verify its constructor signature in `exceptions.py`; extend it minimally if it cannot carry `(feature, container_version)`).
2. **Export → store path + artifact index** (user decision). `icarus.export_fs(container_id, output_path=None)`: default path `<store.root>/<id>.fs.tar`; after a successful backend export, `store.add_artifact(id, Artifact(name="filesystem.tar", path=..., kind="fs", ...))`. Surfaces return `{path, artifact}`. No binary payloads through API/MCP.
3. **Stats are one-shot samples.** `StatsSample` carries cumulative raw values + `sampled_at` (monotonic). Rate derivation (`cpu_percent(prev)`) is a pure helper on the dataclass, ready for the later sampling engine; nothing in this phase stores history.
4. **Copy direction inference lives in Icarus, raw passthrough in Backend.** `Backend.copy(source, destination)` is the raw `container copy` verb (either side may be `id:path`). `Icarus.copy_from(container_id, container_path, host_path)` / `copy_to(container_id, host_path, container_path)` build the `id:` prefix — the id comes from code, never from user string-munging.
5. **Network delete is policy-gated like destroy.** Talos gains `network_delete(name, confirm=True)` using a new `PolicyEngine.check_network_delete(confirm=...)` mirroring `check_destroy` (`policy.py:141`). Network *create* with `internal=False` (host-routable) goes through the existing `check_network_expose` (`policy.py:149`) — the implementing agent must read its exact signature and wire it per the `check_image` log+enforce pattern (`forge.py:402-404`).
6. **Data-driven, no ad-hoc checks.** New CLI/API/MCP surfaces map verb→handler via the existing registration patterns; no `if surface == ...` logic. RunSpec field serialization stays in the single `to_cli_args()`.

## 4. Component breakdown

### 4.1 Capability probe — `daedalus/core/capabilities.py`

- New tri-state gates on `CapabilityManifest`: `stats`, `copy`, `export`, `prune`, `network_subcommand` (default `"untested"`).
- Extend `SUBCOMMANDS` reference set with: `"stats", "copy", "export", "prune", "network", "machine", "volume"`.
- Generic helper `_probe_subcommand(binary, *subcmd) -> bool | str` (runs `<binary> <subcmd> --help`, `returncode == 0` → True); wire five probes through it. `_probe_network_subcommand` already exists — keep its richer "create" check and store it into `network_subcommand`.
- `runtime_features` gains: `"container_stats"`, `"container_copy"`, `"container_export"`, `"container_prune"`, `"custom_networks"` (from `network_subcommand`).
- `summary()` prints the new gates.
- Docs header comments: drop the v0.1.0 framing.

### 4.2 Backend contract — `daedalus/core/backend.py`

New dataclasses (module-level, next to existing ones):

```python
@dataclass
class StatsSample:
    id: str
    cpu_usage_usec: int = 0        # cumulative
    memory_usage_bytes: int = 0
    memory_limit_bytes: int = 0
    network_rx_bytes: int = 0
    network_tx_bytes: int = 0
    block_read_bytes: int = 0
    block_write_bytes: int = 0
    num_processes: int = 0
    sampled_at: float = 0.0        # time.monotonic()

    def cpu_percent(self, prev: "StatsSample") -> float: ...   # 0..100, guard div-by-zero
    def to_dict(self) -> dict[str, Any]: ...

@dataclass
class NetworkInfo:
    id: str
    name: str
    plugin: str = ""
    mode: str = ""                 # "nat" | "internal" | ...
    subnet: str = ""               # from status.ipv4Subnet
    subnet_v6: str = ""
    gateway: str = ""              # from status.ipv4Gateway
    raw: dict[str, Any] = field(default_factory=dict)

@dataclass
class PruneResult:
    ids: list[str] = field(default_factory=list)
    message: str = ""              # "Reclaimed 282.1 MB in disk space"
```

New abstract verbs on `Backend` (all `@abstractmethod`; drop the `NOT_IMPLEMENTED_IN_V010` default-raise docstring convention for these — they ARE implemented):

```python
async def stats(self, container_id: str | None = None) -> list[StatsSample]
async def copy(self, source: str, destination: str) -> str        # raw passthrough, returns stdout
async def export(self, container_id: str, output_path: str) -> str  # returns output_path
async def prune(self) -> PruneResult
async def network_create(self, spec: NetworkSpec) -> NetworkInfo
async def network_list(self) -> list[NetworkInfo]
async def network_inspect(self, name: str) -> NetworkInfo
async def network_delete(self, name: str) -> None
```

RunSpec extension (same file — file ownership: W2 owns backend.py):

```python
network: str | None = None        # --network <name>  (attach)
publish: list[str] = field(default_factory=list)   # -p spec, repeatable
cap_add: list[str] = field(default_factory=list)   # --cap-add
cap_drop: list[str] = field(default_factory=list)  # --cap-drop
```

`to_cli_args()` emits them in this order: network → publish (repeatable `-p <spec>`) → cap_add/cap_drop (repeatable). Update the docstring (no more "flags that don't exist yet" list).

### 4.3 L1 implementation — `daedalus/core/cli_backend.py`

- `_require(feature: str)` helper as per §3.1; call at the top of each new verb.
- `stats`: `stats --no-stream --format json [id]` → parse array into `StatsSample`s (`OutputParser.stats(text) -> list[StatsSample]` — pure static, unit-testable; use `time.monotonic()` for `sampled_at`).
- `copy`: `copy <source> <destination>` → return stdout.
- `export`: `export -o <output_path> <id>` → return output_path.
- `prune`: `prune` → `OutputParser.prune(text) -> PruneResult` (ids = lines that look like container ids; message = the `Reclaimed ...` line if present).
- `network_create`: `network create <spec.to_cli_args()>` → parse id from last stdout line → `network_inspect(name)` for the full `NetworkInfo` (create output may be minimal).
- `network_list`: `network list --format json` → `OutputParser.network_list(text) -> list[NetworkInfo]` (pure).
- `network_inspect`: `network inspect <name>` (no `--format`) → parse JSON array → first element.
- `network_delete`: `network delete <name>`.
- Update the module docstring ("JSON output is available for exactly three commands" is stale — enumerate the real list: list, image list, builder status, network list, stats).

### 4.4 Engine — forge / icarus / talos / policy

**`daedalus/core/forge.py`** — add:

```python
async def stats(self, container_id: str | None = None) -> list[StatsSample]:
    # no policy checks (read-only, high-frequency); no audit entry
```

**`daedalus/core/icarus.py`** — constructor gains optional `store: Store | None = None` (mirrors Forge; default `Store()`). Add:

```python
async def copy_from(self, container_id, container_path, host_path) -> str   # audit "copy_from"
async def copy_to(self, container_id, host_path, container_path) -> str     # audit "copy_to"
async def export_fs(self, container_id, output_path: str | None = None) -> dict[str, Any]
    # default path: <store.root>/<container_id>.fs.tar
    # backend.export → store.add_artifact(kind="fs") → audit "export"
    # returns {"path": ..., "container_id": ..., "artifact": {...}}
```

Drop the module docstring's "cp, export, and stats do not exist in v0.1.0" note.

**`daedalus/core/talos.py`** — add network CRUD + materialization:

```python
async def network_create(self, spec: NetworkSpec) -> NetworkInfo
    # policy: check_network_expose when spec.internal is False (verify signature first)
async def network_list(self) -> list[NetworkInfo]
async def network_inspect(self, name: str) -> NetworkInfo
async def network_delete(self, name: str, *, confirm: bool = False) -> None
    # policy.check_network_delete(confirm) — CONFIRM branch raises ValidationError
async def apply_topology(self, template: TopologyTemplate) -> Topology
    # create each network (dedupe: skip names already in network_list), record network_ids
    # attachments are returned on the Topology for the caller (forge run with RunSpec.network)
```

Each records an audit entry (`actor="talos"`, `ActorKind.SERVICE`). Drop the module docstring's v0.1.0 note.

**`daedalus/core/policy.py`** — add `check_network_delete(self, confirm: bool = False) -> Decision` mirroring `check_destroy` (CONFIRM unless confirm=True).

### 4.5 Surfaces

**CLI — `daedalus/cli/main.py`** (single file; Typer):

- `stats [container_id]` — table (Rich) of the sample fields; `--json` for raw.
- `cp <source> <destination>` — accepts `<id>:/path` on either side exactly like `container copy`; infers direction and calls `icarus.copy_from`/`copy_to`; prints destination.
- `export <container_id> [-o PATH]` — calls `icarus.export_fs`; prints path (+ artifact note).
- `prune` — `--confirm` required (destructive); prints reclaimed message + ids.
- `network` Typer sub-app (`app.add_typer(network_app, name="network")`): `create` (name, --subnet, --subnet-v6, --internal, --option, --label), `list` (table + `--json`), `inspect <name>`, `delete <name> --confirm`.
- Wire `store` into `_bootstrap()` (`Store()` like other surfaces) and pass to `Icarus(...)`; add `talos` to CLI globals.
- Each command follows the `async def _go()` + `asyncio.run(_go())` pattern.

**API — `daedalus/api/server.py`**:

| Method | Path | Notes |
|---|---|---|
| GET | `/containers/{id}/stats` | one-shot sample for one container |
| GET | `/stats` | one-shot samples for all running |
| POST | `/containers/{id}/copy` | body `{"source", "destination", "direction": "from"\|"to"}` |
| POST | `/containers/{id}/export` | body `{"output_path"?}`; returns `{path, container_id, artifact}` |
| POST | `/containers/prune` | body `{"confirm"}`; 400 without confirm |
| GET | `/networks` | list |
| POST | `/networks` | create (pydantic model mirroring NetworkSpec fields) |
| GET | `/networks/{name}` | inspect |
| DELETE | `/networks/{name}` | `?confirm=true` required; 400 otherwise |

Follow `_get_state()` + `s["..."]` access, `_container_dict`-style serializers, `DaedalusError` global handler, `HTTPException(400)` confirm guards. State gains `talos` and `store` (store already in MCP; wire into API state if absent).

**MCP — `daedalus/mcp/server.py`**:

Tools (all via `@mcp.tool(annotations=ToolAnnotations(...))`, JSON-string returns via `_ok`/`_err`):

| Tool | Destructive | Notes |
|---|---|---|
| `daedalus_stats` | no | `container_id?` → list |
| `daedalus_copy` | no | `container_id, source, destination, direction` |
| `daedalus_export` | no | `container_id, output_path?` → path+artifact |
| `daedalus_prune` | **yes** | `confirm` required; early `CONFIRM_REQUIRED` guard like `daedalus_dns_delete` |
| `daedalus_network_list` | no | |
| `daedalus_network_create` | no* | *policy-gated internally when routable |
| `daedalus_network_inspect` | no | |
| `daedalus_network_delete` | **yes** | `confirm` required |

Update the tool table in README.

## 5. Agent work breakdown

Waves run sequentially; agents within a wave run in parallel. Every agent: reads this SPEC, implements exactly its files, runs `mypy --strict daedalus/`, `ruff check daedalus/ tests/`, and its targeted `pytest -m unit` subset, and reports results. No agent touches another agent's files.

**Wave 1 (2 agents, parallel):**

- **W1 — capabilities** (`daedalus/core/capabilities.py`, `tests/test_capabilities.py`): §4.1. Real-probe tests skip when binary absent (existing pattern); manifest-unit tests cover new fields' defaults + `runtime_features` mapping.
- **W2 — backend contract + L1** (`daedalus/core/backend.py`, `daedalus/core/cli_backend.py`, `daedalus/core/exceptions.py` if `CapabilityError` needs extension, new `tests/test_cli_backend_parsers.py`): §4.2 + §4.3. Parser unit tests run against the literal JSON from §2 (no daemon needed). Also: `tests/test_forge.py::MockBackend` must gain no-op implementations of the new abstract verbs (MockBackend lives in test_forge.py — W2 edits ONLY the MockBackend class body there, coordinating note: W3a/W3b extend tests in test_forge/test_icarus but not concurrently — W2 finishes first).

**Wave 2 (3 agents, parallel, after W1+W2):**

- **W3a — forge** (`daedalus/core/forge.py`, `tests/test_forge.py` additions): `Forge.stats` + unit test with MockBackend.
- **W3b — icarus** (`daedalus/core/icarus.py`, `tests/test_icarus.py` additions): §4.4 icarus items + store integration + unit tests.
- **W4 — talos + policy** (`daedalus/core/talos.py`, `daedalus/core/policy.py`, new `tests/test_talos.py`): §4.4 talos/policy items + unit tests (MockBackend network verbs, policy CONFIRM branches).

**Wave 3 (3 agents, parallel, after W3a+W3b+W4):**

- **W5a — CLI** (`daedalus/cli/main.py`): §4.5 CLI.
- **W5b — API** (`daedalus/api/server.py`): §4.5 API.
- **W5c — MCP** (`daedalus/mcp/server.py`): §4.5 MCP.

**Wave 4 (1 agent, after Wave 3):**

- **W6 — integration tests + docs** (`tests/integration/test_lifecycle.py`, `README.md`, `ARCHITECTURE.md`): live round-trips — stats one-shot on a running `alpine:latest`, copy-out → byte-identical copy-in, export tar opens with `tarfile` and artifact indexed, prune reclaims a stopped container, network create/list/inspect/delete round-trip (cleanup in finally, skip via `_skip_if_no_container` pattern). Docs: README feature tables + ARCHITECTURE §9 status matrix updated to 1.2.2 reality; delete all stale "not in v0.1.0" comments found in `grep -rn "v0.1.0" daedalus/`.

**Wave 5 (1 agent, after Wave 4): UI surfacing — `ui/src/api/*.ts`, `ui/src/views/*.tsx`, `ui/src/App.tsx`:**

Expose the new capabilities in the Labyrinth Control Center following existing UI patterns (typed client functions in `ui/src/api/`, views in `ui/src/views/`, `GlassBox`/`Box` shared components, 3s polling where live data is shown):

- **API client** — add to the matching `ui/src/api/*.ts` module: `getStats(id?)`, `copyInto/From` (`POST /containers/{id}/copy`), `exportFs` (`POST /containers/{id}/export`), `pruneContainers` (`POST /containers/prune`), network CRUD (`GET/POST /networks`, `GET/DELETE /networks/{name}`).
- **Networks view** (new `ui/src/views/Networks.tsx`, routed from App sidebar): network table (name, subnet, gateway, plugin, mode), create form (name, optional subnet, internal checkbox), delete with confirm; `?confirm=true` on the DELETE call.
- **Containers view**: add a stats column group (CPU µs→% via two polls of `/stats`, memory used/limit humanized) and an **Export** action button per row (calls export, surfaces the returned store path + artifact); keep it data-driven — no per-id `if` logic.
- **Dashboard**: aggregate running-container stats from `GET /stats` (total mem used, sum CPU) as tiles next to the existing system tiles.
- **Prune**: a button on Containers (confirm dialog → `POST /containers/prune`).
- **File copy**: a small copy form (container path ↔ host path, direction selector) on the Terminal view or container detail — one clear location, not both.
- `npm run build` must be clean (existing TS checks).

**Wave 6 (orchestrator + macos-harness, after Wave 5): end-to-end UI verification (user-mandated).**

Use the **macos-harness** skill (Browser Harness CDP; background capture only — never foreground the target app; telemetry stays disabled) to drive the real UI and prove every single page and flow works:

1. Build UI (`npm run build`) and start the API server (`python -m daedalus.api.server`), open `http://127.0.0.1:8420/ui` in a browser via CDP.
2. **Walk every page**: Dashboard, Containers, Terminal, Images, Logs, Audit, Profiles, Experiments, Dns, Topology, Networks — screenshot each, assert no render errors, console errors collected.
3. **Execute real flows through the UI**: create a container (form → appears in list) → stats populate on Containers + Dashboard → open Terminal and run `uname -a` → copy a file out and back (byte-identical via hash) → Export → artifact path surfaces and appears under Experiments → create a network via the form, verify in Networks list, delete it → prune a stopped container → destroy the created container.
4. Verify audit entries appear in the Audit page for the UI-driven actions.
5. Report a pass/fail table per page + flow with screenshots; any failure gets a root-cause note and is re-tested after fix.

## 6. Testing strategy

- **Unit** (no daemon): parser purity tests (literal JSON from §2), MockBackend engine tests (forge stats, icarus copy/export paths + artifact indexing, talos network CRUD + policy branches), capability manifest tests.
- **Integration** (live daemon, alpine:latest): the W6 round-trips above. All create/fixture containers are cleaned up (`finally: delete --force`); network fixtures deleted in finally with confirm.
- **UI end-to-end** (Wave 6): macos-harness drive-through of every page and flow in the real browser UI, with screenshots and a per-flow verdict.
- **Quality gates** (reviewer, after W6): `mypy --strict daedalus/` clean; `ruff check daedalus/ tests/` clean of new findings; `pytest -m unit` all pass; `pytest -m integration` all pass live; live smoke of API endpoints (`curl /stats`, `/networks`) and MCP handshake; `npm run build` clean; Wave 6 UI verdict all-pass.

## 7. Success metrics

1. `daedalus stats <id>` returns the same figures as `container stats --no-stream --format json <id>`.
2. Copy round-trip is byte-identical (hash both ends).
3. Export tar opens and the artifact appears in the store index.
4. `daedalus network create/list/inspect/delete` drives real vmnet networks; topology template materializes an internal victim network.
5. On a host with a feature gated off (simulated in unit tests), the verb raises `CapabilityError` with a clear message — no raw `unrecognized subcommand` leakage.
6. Zero stale `v0.1.0` comments remain in `daedalus/`.
7. All quality gates green.
8. Every UI page renders and every UI-driven flow (create, stats, terminal, copy, export, networks, prune, destroy) passes the Wave 6 macos-harness drive-through with screenshots — no console errors, no 4xx/5xx API failures.

## 8. Non-goals (explicit)

- No `machine`/`volume`/`k8s` wrapping (phases 2–3).
- No stats history/persistence or rate-based UI (later sampling engine).
- No Docker Hub search, fuzzy palette, or other Orchard-UX ports (separate UI initiative).
- No L2/L3 backend work.
