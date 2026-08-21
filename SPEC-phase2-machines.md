# SPEC Phase 2 — Container machines (persistent stateful VMs)

Status: **Approved for implementation** · Branch: `feature/ungate-container-1.2.2` (phase 1 committed as `7ef9311`)

## 1. Objectives

Wrap `container machine` (1.2.2) — Apple's persistent, stateful Linux VMs with home-dir sharing — across the full daedalus stack, matching phase-1 conventions:

1. Capability gate (`machine`) + fail-loud `_require`.
2. New `Backend`/`CliBackend` verbs: create/list/inspect/set/set-default/stop/delete/run/logs.
3. New engine subsystem **`machina`** (deus ex machina) with an **init-capability advisor** ported from Orchard's two-tier design (name heuristic + authoritative log check).
4. Policy: delete confirm gate; create disk check; advisor warnings surfaced on every surface.
5. Surfaces: CLI `machine` sub-app, API `/machines*`, MCP `daedalus_machine_*`, UI Machines view.
6. Tests (unit + live integration incl. a real systemd boot), docs, and the macos-harness Safari UI drive-through.

Out of scope (later phases): live machine resource stats via backing containers (sampling engine), interactive machine terminal in UI (API/MCP get non-interactive `run` only), volumes/k8s, GUI-app integration.

## 2. Verified environment (captured live 2026-08-21, container 1.2.2)

### 2.1 Wire contract

`machine create <image>` flags (verified via --help): `-a/--arch`, `--os`, `--platform`, `--scheme`, `--progress`, `--max-concurrent-downloads`, `-n/--name`, `--set-default`, `--no-boot`, `--cpus <n>`, `--memory <size>` (e.g. 2G; default half host RAM), `--home-mount <ro|rw|none>` (default rw), `--virtualization` (M3+ + KVM kernel), `--kernel <path>`. Stdout: the machine id.

`machine list --format json` (verified):
```json
[{"createdDate": "2026-08-21T20:31:29Z", "status": "stopped",
  "diskSize": 1169178624, "default": true, "id": "daedalus-probe-m",
  "cpus": 8, "memory": 25769803776}]
```

`machine inspect <id>` (verified — JSON array, no `--format` flag):
```json
[{"cpus": 8, "createdDate": "...", "diskSize": 1169178624, "homeMount": "rw",
  "id": "...", "image": {"descriptor": {...}, "reference": "docker.io/library/alpine:3.22"},
  "memory": 25769803776, "platform": {"architecture": "arm64", "os": "linux"},
  "status": "stopped", "userSetup": {"gid": 20, "uid": 501, "username": "jay"}}]
```

`machine set -n <id> <setting>...` — positional `key=value` settings: `cpus=`, `memory=`, `home-mount=`, `virtualization=true|false`, `kernel=` (empty resets). Echoes id. Changes apply after restart.

`machine set-default <id>` / `stop <id>` / `delete <id>` — positional id (default machine when omitted; daedalus always passes explicitly), echo id.

`machine run -n <id> [executable] [args...]` — boots the machine if necessary; `-e/--env`, `--env-file`, `--gid`, `-i/--interactive`; no command → interactive login shell. Runs as the host-mapped user (not root).

`machine logs <id>` — plain text boot/stdio dump, no flags. `machine list -q/--quiet`. `m` is an alias.

### 2.2 Runtime behaviors (verified live)

- **Backing containers are invisible**: `container list --all` shows ZERO containers while a machine runs — the CLI hides machine backing containers client-side. **No backing-filter needed in daedalus L1.**
- **`container prune` does not touch machines** (verified: prune with a stopped machine reclaimed nothing and the machine survived).
- **Init requirement** ([apple/container docs](https://github.com/apple/container/blob/main/docs/container-machine.md), upstream issue [apple/container#1669](https://github.com/apple/container/issues/1669)): the machine boots the image's own `/sbin/init` as PID 1. `alpine:latest` boots then self-terminates (`can't run '/sbin/openrc'` → shutdown); `debian:latest` fails immediately (`exec: /sbin/init: not found`); `run` against a failed boot reports `Error: The operation couldn't be completed. Operation not supported by device`.
- **Official systemd image** (from the docs, use verbatim for the slow boot test — save at `tests/fixtures/ubuntu-machine.Dockerfile`):
```dockerfile
FROM ubuntu:24.04

ENV container container

RUN apt-get update && \
    apt-get install -y \
    dbus systemd openssh-server net-tools iproute2 iputils-ping curl wget vim-tiny man sudo && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    yes | unminimize

RUN >/etc/machine-id
RUN >/var/lib/dbus/machine-id

RUN systemctl set-default multi-user.target
RUN systemctl mask \
      dev-hugepages.mount \
      sys-fs-fuse-connections.mount \
      systemd-update-utmp.service \
      systemd-tmpfiles-setup.service \
      console-getty.service
RUN systemctl disable \
      networkd-dispatcher.service

RUN sed -i -e 's/^AcceptEnv LANG LC_\*$/#AcceptEnv LANG LC_*/' /etc/ssh/sshd_config
```
Build with `--dns 8.8.8.8` (builds have no DNS by default; apt needs it). Custom setup script hook: `/etc/machine/create-user.sh` (runs once as root with CONTAINER_UID/GID/USER/HOME/MACHINE_ID env).

### 2.3 Init-capability advisor (ported from Orchard `MachineImageAdvisor.swift`)

Two tiers, pure and data-driven:
1. **Name heuristic** `likely_lacks_init(ref)`: deny-list Set of well-known initless image names (`alpine, busybox, scratch, hello-world, ubuntu, debian, fedora, centos, rockylinux, almalinux, archlinux, opensuse, nginx, httpd, caddy, traefik, haproxy, redis, postgres, mysql, mariadb, mongo, memcached, node, python, golang, go, openjdk, eclipse-temurin, ruby, php, rust, elixir`); references containing `init` or `systemd` are treated as fine; reduce ref to last path component (strip @digest, :tag, registry/org prefix).
2. **Authoritative log check** `logs_indicate_missing_init(lines)`: any line mentioning `/sbin/init` or `/sbin/openrc` AND (`not found` OR `no such file`).

## 3. Architecture decisions

1. **New subsystem `machina`** (`daedalus/core/machina.py`) — machines are persistent, config-editable, user-mapped VMs; distinct lifecycle from forge's disposable labyrinths. Constructed `Machina(backend, capabilities, *, policy=None, audit=None)` mirroring Forge/Talos.
2. **Data-driven settings**: `MachineSetSettings` with Optional fields + single `to_set_args()` (only non-None → `key=value` args); `MachineCreateSpec.to_cli_args()` mirrors RunSpec's serializer pattern. No scattered `if` chains.
3. **Advisor is pure static + surface-visible**: `machina.create` runs `likely_lacks_init`; when positive, audit-record the warning and include `"warning": "<ref> may lack an init system..."` in the return payload (MachineInfo-level field or create-result dict — surfaces pass it through; UI shows an amber note). `logs_indicate_missing_init` is applied after stop/run failures to explain them (`explain_failure(logs)` helper).
4. **Destructive + resource policy**: `PolicyEngine.check_machine_delete(confirm)` mirroring check_destroy/check_network_delete (CONFIRM unless confirm=True); `machina.create` runs the existing `check_disk` gate. `machine run` is NOT policy-gated (read/exec path like exec).
5. **Run = non-interactive exec contract**: `machina.run` returns `ExecResult` (exit_code/stdout/stderr) reusing the existing dataclass; interactive shell + UI terminal for machines deferred.
6. **No backing-container filtering** — verified unnecessary on L1; add a regression integration assertion (`container list` shows no machine backings while a machine runs) with a comment explaining why.

## 4. Component breakdown

### 4.1 Capabilities (`daedalus/core/capabilities.py`)
- New tri-state gate `machine` (default `"untested"`) probed via `_probe_subcommand(binary, "machine")`.
- `runtime_features["container_machines"] = m.machine is True`. Add `"machine"` to SUBCOMMANDS set. `summary()` line.

### 4.2 Backend contract (`daedalus/core/backend.py`)

```python
@dataclass
class MachineInfo:
    id: str
    status: str = ""                 # stopped | running | ...
    cpus: int = 0
    memory_bytes: int = 0
    disk_size_bytes: int = 0
    default: bool = False
    created_date: str = ""
    home_mount: str = ""             # inspect-only
    image_ref: str = ""              # inspect-only
    platform_arch: str = ""
    platform_os: str = ""
    user_username: str = ""
    user_uid: int = 0
    user_gid: int = 0
    warning: str = ""                # advisor note (engine-set, not from wire)
    raw: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: ...

@dataclass
class MachineCreateSpec:
    image: str
    name: str | None = None
    cpus: int | None = None
    memory: str | None = None        # "2G" | "8G" — CLI string form
    home_mount: str | None = None    # ro | rw | none
    virtualization: bool = False
    kernel: str | None = None
    set_default: bool = False
    no_boot: bool = False
    arch: str | None = None
    os: str | None = None
    platform: str | None = None
    scheme: str | None = None
    def to_cli_args(self) -> list[str]: ...   # image LAST (CLI: machine create [opts] <image>)

@dataclass
class MachineSetSettings:
    cpus: int | None = None
    memory: str | None = None
    home_mount: str | None = None
    virtualization: bool | None = None
    kernel: str | None = None
    def to_set_args(self) -> list[str]: ...   # only non-None -> "key=value"
```

New abstract verbs (all `@abstractmethod`): `machine_create(spec) -> MachineInfo`, `machine_list() -> list[MachineInfo]`, `machine_inspect(machine_id) -> MachineInfo`, `machine_delete(machine_id) -> None`, `machine_stop(machine_id) -> None`, `machine_set(machine_id, settings) -> None`, `machine_set_default(machine_id) -> None`, `machine_run(machine_id, argv, *, env=None, gid=None, interactive=False, timeout=60.0) -> ExecResult`, `machine_logs(machine_id) -> str`.

### 4.3 L1 implementation (`daedalus/core/cli_backend.py`)
- All nine verbs call `self._require("container_machines")` first.
- Pure parsers on `OutputParser`: `machine_list(text) -> list[MachineInfo]` (list shape §2.1), `machine_inspect(text) -> MachineInfo` (array→first; merges list+inspect fields), `machine_id(text) -> str` (echoed id).
- `machine_run`: `["machine", "run", "-n", id, *argv]` with `-e k=v`, `--gid`, `-i` per opts; `check=False` (captures exit codes); `machine_logs` returns raw text.

### 4.4 Engine (`daedalus/core/machina.py` + `daedalus/core/policy.py`)
- `MachineImageAdvisor` module-level pure statics (name Set + log check) with docstrings citing the two-tier rationale.
- `Machina.create(spec) -> MachineInfo` — policy `check_disk` (forge `_check_run` pattern: log + enforce); advisor check → `info.warning` set; audit `"machine_create"`.
- `list/inspect` — no audit. `stop` — audit `"machine_stop"`. `set` — audit `"machine_set"` with settings dict. `set_default` — audit. `run` — audit `"machine_run"` with argv. `logs` — no audit.
- `delete(machine_id, *, confirm=False)` — `check_machine_delete` CONFIRM branch raises `ValidationError("machine delete requires confirm=True")`; audit `"machine_delete"`.
- `explain_failure(machine_id) -> str | None` — fetch logs, `logs_indicate_missing_init` → human explanation string (used by surfaces after run errors).
- `daedalus/core/policy.py` — `check_machine_delete(*, confirm=False) -> PolicyResult` mirroring `check_network_delete`.

### 4.5 Surfaces

**CLI (`daedalus/cli/main.py`)** — `machine` Typer sub-app: `create` (image, --name, --cpus, --memory, --home-mount, --virtualization, --kernel, --set-default, --no-boot; prints warning line when advisor fires), `list [--json]` (Rich table), `inspect <id> [--json]`, `set <id> [cpus=] [memory=] [home-mount=] [virtualization=] [kernel=]` (repeatable k=v), `set-default <id>`, `stop <id>`, `delete <id> --confirm`, `run <id> [command...]` (prints stdout/stderr, exit code), `logs <id>`. Wire `machina` into `_bootstrap()`.

**API (`daedalus/api/server.py`)** — `GET /machines`, `POST /machines` (MachineCreateRequest incl. warning echo), `GET /machines/{id}`, `POST /machines/{id}/set` (MachineSetRequest), `POST /machines/{id}/stop`, `POST /machines/{id}/set-default`, `DELETE /machines/{id}?confirm=true` (400 guard), `POST /machines/{id}/run` (RunCommandRequest `{"command": [...]}`) → ExecResult dict, `GET /machines/{id}/logs`. Wire `machina` into `_state`.

**MCP (`daedalus/mcp/server.py`)** — `daedalus_machine_list`, `daedalus_machine_create` (spec fields as args; docstring documents the init requirement), `daedalus_machine_inspect`, `daedalus_machine_set`, `daedalus_machine_stop`, `daedalus_machine_set_default`, `daedalus_machine_run`, `daedalus_machine_logs`, `daedalus_machine_delete` (**destructiveHint=True**, CONFIRM_REQUIRED early guard mirroring daedalus_dns_delete). `_machine_dict` serializer. Lifespan constructs `Machina`.

**UI (`ui/src/`)** — `api/machines.ts` (typed client + `useMachines` 3s polling); new `views/Machines.tsx` (table: name, status badge, CPUs, memory humanized, disk, default marker; create form with image/name/cpus/memory/home-mount/virtualization + amber advisor warning when the API echoes one; per-row Inspect modal, Stop, Set-default, Delete-with-confirm, Logs pane; `run` one-shot command box with output); App.tsx sidebar entry. Machines view does NOT reuse container actions (no Term/Export — different semantics).

### 4.6 Tests
- **Unit**: `tests/test_cli_backend_parsers.py` — machine list/inspect literal shapes from §2.1; MachineSetSettings.to_set_args + MachineCreateSpec.to_cli_args ordering; advisor pure tests (`likely_lacks_init` hits/misses incl. `init`/`systemd` refs and @digest/:tag/registry stripping; `logs_indicate_missing_init` positive/negative). New `tests/test_machina.py` — MockBackend machine verbs (extend MockBackend in test_forge.py with call-recording stubs — allowed), policy CONFIRM branches, advisor warning propagation.
- **Integration** (new `tests/integration/test_machines.py`, `_skip_if_no_container` pattern, `@pytest.mark.integration`): no-boot lifecycle round-trip (create alpine --no-boot → list/inspect/set cpus=2/set-default/stop/delete; cleanup in finally); backing-container invisibility assertion; `test_machine_boot_systemd` marked `slow` — builds `tests/fixtures/ubuntu-machine.Dockerfile` via `mint.build` with `--dns 8.8.8.8` once, `machine create --no-boot`, `machine run uname -a` → exit 0 + `Linux` in stdout, logs, stop/delete; skip with clear message if the build fails (network).

## 5. Agent work breakdown

- **Wave 1 (2 agents, parallel):** W1 capabilities (`capabilities.py`, `test_capabilities.py`); W2 backend + L1 + parsers (`backend.py`, `cli_backend.py`, `test_cli_backend_parsers.py` additions, MockBackend stubs in `test_forge.py` only).
- **Wave 2 (1 agent, after W1+W2):** W3 machina + advisor + policy (`machina.py`, `policy.py`, `tests/test_machina.py`, `tests/fixtures/ubuntu-machine.Dockerfile`).
- **Wave 3 (3 agents, parallel, after W3):** W4a CLI (`cli/main.py`), W4b API (`api/server.py`), W4c MCP (`mcp/server.py`).
- **Wave 4 (1 agent, after Wave 3):** W5 UI (`ui/src/api/machines.ts`, `ui/src/views/Machines.tsx`, `ui/src/App.tsx`).
- **Wave 5 (1 agent, after Wave 4):** W6 integration tests + docs (`tests/integration/test_machines.py`, README, ARCHITECTURE §9 machines row).
- **Wave 6 (orchestrator):** full quality gates + macos-harness Safari drive-through of the Machines view (create-with-warning flow, list, inspect, set, stop, run against the systemd image, logs, delete) with DOM assertions, plus a re-run of the phase-1 UI flows to confirm no regression.

Every agent runs `mypy --strict` (their files), targeted `pytest -m "not integration"`, and `ruff check` on owned files; zero new findings.

## 6. Success metrics

1. `daedalus machine create alpine:latest --no-boot -n test-m` → listed + inspected with wire-accurate fields; advisor warning present.
2. `machine set` round-trips through `container machine set` and reflects in inspect.
3. Systemd-image machine boots and `daedalus machine run ubuntu uname -a` returns `Linux`, exit 0.
4. `machine delete` without confirm fails loudly on all surfaces; with confirm succeeds.
5. Missing-init failures get a human explanation (`explain_failure`) on every surface.
6. All quality gates green + macos-harness UI verdict all-pass; phase-1 UI flows still pass.

## 7. Non-goals (explicit)

- No machine resource stats via backing containers (deferred to sampling-engine phase).
- No interactive machine terminal in UI/API/MCP (non-interactive `run` only).
- No `--max-concurrent-downloads`/`--progress` surface exposure.
- No volume/k8s work (phase 3+).
