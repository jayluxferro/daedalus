# DAEDALUS

**D**etonation, **A**nalysis & **E**xperimentation — **D**aedalus **A**rchitecture for **L**inux container **U**nits & **S**andboxing

A security-research control plane built on Apple's `container` runtime. DAEDALUS turns the one-VM-per-container architecture of Apple silicon into a fleet of disposable, hardware-isolated sandboxes — drivable by humans (CLI), services (HTTP API), and autonomous agents (MCP), all through one core engine.

> In myth, Daedalus built the Labyrinth to contain the Minotaur — a maze nothing escapes.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    CONSUMERS                         │
│  CLI (Typer)    HTTP API (FastAPI)    MCP (FastMCP) │
├─────────────────────────────────────────────────────┤
│                   CORE ENGINE                        │
│  forge      → lifecycle + system                    │
│  icarus     → exec, logs, copy, export              │
│  mint       → images (pull, list, tag, delete)      │
│  talos      → networks, DNS, topology               │
│  ariadne    → instrumentation, kernel variants      │
│  minos      → forensic analysis, risk scoring       │
│  machina    → machines (persistent stateful VMs)    │
├─────────────────────────────────────────────────────┤
│             CROSS-CUTTING                            │
│  profiles   → named security postures               │
│  policy     → pre-execution guardrails              │
│  audit      → tamper-evident operation log           │
│  store      → experiment manifests + artifacts      │
├─────────────────────────────────────────────────────┤
│              BACKEND (pluggable)                     │
│  CliBackend (L1) → containers CLI --format json     │
├─────────────────────────────────────────────────────┤
│            Apple container runtime                   │
│          (Virtualization.framework)                  │
│         One lightweight VM per container             │
└─────────────────────────────────────────────────────┘
```

---

## Prerequisites

- **macOS 26+** (Tahoe)
- **Apple silicon** (arm64)
- **container** installed at `/usr/local/bin/container` — **≥ 1.3.1 required** for the
  detonation workflow (its isolation promise depends on the 1.3.1 GHSA advisory fixes);
  **≥ 1.2.2** for everything else. Note: container 1.3.0 removed `--scheme auto` (the
  default is https) — daedalus never emits `auto`.
- **Python 3.12+**
- **uv** package manager
- **Node.js 22+** (for UI development only)

Check your system:

```bash
sw_vers                           # macOS version
uname -m                          # should be arm64
which container && container --version
python3 --version
```

---

## Supported Images

Any OCI-compatible Linux image works. Common choices:

| Image | Best for | Pull command |
|---|---|---|
| `alpine:latest` | Minimal (~5MB), fast boot | `daedalus image-pull alpine:latest` |
| `debian:latest` | Full Linux tools (curl, apt, tcpdump) | `daedalus image-pull debian:latest` |
| `ubuntu:latest` | Desktop/server workloads | `daedalus image-pull ubuntu:latest` |
| `kalilinux/kali-rolling:latest` | Penetration testing, tcpdump pre-installed | `daedalus image-pull kalilinux/kali-rolling:latest` |

Use **Alpine** for speed, **Debian** for proxy/Burp/pcap analysis (has proper curl + SSL).

## Quick Start

```bash
# 1. Clone
git clone https://github.com/jayluxferro/daedalus
cd daedalus

# 2. Install Python dependencies
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 3. Start the container daemon (required for anything to work)
container system start --disable-kernel-install

# 4. Install a kernel (required once)
container system kernel set --recommended

# 5. Pull your first image
daedalus image-pull alpine:latest

# 6. Run a container (detached, auto-keepalive)
daedalus run alpine:latest -d --command "echo hello from daedalus"

# 7. See it
daedalus ls --all
daedalus stats                        # live CPU/mem/net stats for running containers
                                       # a 2 s rolling history is served by the API server
                                       # (/stats/history, SSE /stats/stream — see below)

# Multi-arch on Apple silicon: run amd64 (x86_64) guests in the arm64 VM
daedalus run alpine:latest -d --arch x86_64              # amd64 guest (translated via Rosetta)
daedalus run alpine:latest -d --arch x86_64 --rosetta    # …explicit Rosetta environment
daedalus exec <id> -- uname -m                            # → x86_64 (verified live)

# 8. Machines (persistent stateful VMs — init-capable image required)
daedalus machine create alpine:latest --no-boot -n test-m   # will warn: alpine has no init system
daedalus machine list
daedalus machine run test-m uname -a                        # boots the machine, runs the command
daedalus machine stop test-m
daedalus machine delete test-m --confirm

# 9. K8s clusters (container 1.3.1 k8s plugin — local Kubernetes dev clusters)
# NOTE: cluster create is HEAVY — it pulls the k8s node image (hundreds of MB)
# and boots control-plane VMs, taking minutes. Run it deliberately.
daedalus k8s create --name dev            # create + start a single-node cluster
daedalus k8s list                         # grouped table: cluster, control-plane, workers, addresses
daedalus k8s write-config dev             # print the kubeconfig → pipe it to kubectl --kubeconfig
daedalus k8s load-image dev alpine:latest # load a local image into the cluster's containerd
daedalus k8s delete dev --confirm         # delete the cluster (confirm required)
```

---

## Detonation (the flagship workflow)

This is what DAEDALUS is for: take an untrusted artifact, execute it once, capture
everything it does, and destroy the VM — from one command. `Forge.detonate` runs the full
loop (SPEC phase 3 §3.2): policy gate → disposable **internal** network → VM boot →
pre-run filesystem export → sample delivery → execution while stats are sampled →
post-run logs + filesystem export → Minos behavioral report + risk score → evidence
persisted to the store → **VM and network destroyed**.

```bash
daedalus detonate ./suspicious.elf --observe 30
```

**The evidence you get back:**

| Evidence | Where it comes from | What it shows |
|---|---|---|
| Filesystem diff | Pre/post full-FS export (works on the running VM) | Every file added/modified/removed during the run — e.g. a dropper's `/root/dropped.txt` |
| Stats profile | Stats sampled every `--stats-interval` s while the sample runs | CPU / memory / process-count timeline |
| Logs | Boot + process logs | What booted and what the sample printed |
| Report + risk score | Minos judge (FS diff + stats + logs) | `report.json` in the store, `NONE/LOW/MEDIUM/HIGH` risk label |

The VM is **always destroyed afterward** — nothing survives but the evidence (pre/post
filesystem tars + report in the store) and the audit entry. Pass `--json` for the full
result dict (`run_id`, `report`, `report_path`, `artifacts`, `risk_score`, `exit_code`).

```bash
daedalus detonate ./suspicious.elf --observe 30 --json
```

Options: `--image` (default `alpine:latest`), `--profile` (default `detonation`), `--name`,
`--observe N` (observation window, default 30 s — an exec that runs past it reports exit
code 124, which is normal), `--stats-interval N`, `--command` (override how the sample
executes; default `sh <sample>`).

---

## Consumer Surfaces

DAEDALUS has three consumer surfaces — use whichever fits your workflow.

### 1. CLI (Terminal)

```bash
# System
daedalus probe                        # Host capability manifest
daedalus system-status                # Daemon + version + container counts
daedalus system property list         # System property values as dotted keys (build.cpus …)
daedalus system version               # Service versions (container, container-apiserver)
daedalus system df                    # Store disk usage (per-category + totals)

# Containers
daedalus run alpine:latest -d         # Create + start (detached, auto-keepalive)
daedalus run debian:latest -d -p detonation  # Detonation profile for malware analysis
daedalus run alpine:latest -d --arch x86_64 --rosetta --init --shm-size 64M --ulimit nofile=1024
                                       # 1.3.1 run surface: multi-arch + resources + PID-1
daedalus detonate ./suspicious.elf --observe 30   # THE flagship: detonate, report, destroy
daedalus ls                           # List running
daedalus ls --all                     # List all (including stopped)
daedalus inspect <id>                 # Full JSON inspect
daedalus exec <id> -- uname -a           # Run command inside ('--' before flag-like commands)
daedalus exec <id> --workdir /tmp -- pwd # exec options precede the id (wire contract)
daedalus logs <id>                    # View logs
daedalus logs <id> --boot             # Boot-time logs
daedalus destroy <id> --confirm       # Stop + delete
daedalus stop --all                   # Stop every container (stop -a)
daedalus destroy --all --confirm      # Delete every container (delete -a)
daedalus stats                        # One-shot stats for all running containers
daedalus stats <id>                   # One-shot stats for a single container
daedalus cp <id>:/tmp/x ./x           # Copy a file out of a container
daedalus cp ./x <id>:/tmp/x           # Copy a file into a container
daedalus export <id> -o fs.tar        # Export full filesystem to a tar archive
daedalus prune --confirm              # Remove stopped containers, reclaim disk
daedalus network list                 # List networks
daedalus network create lab-net --internal --subnet 172.31.0.0/24
daedalus network inspect lab-net
daedalus network delete lab-net --confirm

# Machines (persistent stateful VMs — init-capable image required)
# Machines boot the image's own /sbin/init as PID 1: plain alpine/debian/ubuntu
# images have no init system and will not boot (the create command warns).
# See tests/fixtures/ubuntu-machine.Dockerfile for the official systemd image.
daedalus machine create alpine:latest --no-boot -n test-m   # init warning; stays stopped
daedalus machine list                  # name, status, cpus, memory, disk, default
daedalus machine inspect test-m        # full config + image/platform/user info
daedalus machine set test-m cpus=2 memory=4G   # applies after restart
daedalus machine set-default test-m
daedalus machine run test-m uname -a   # boots if needed, runs as host-mapped user
daedalus machine logs test-m           # boot/stdout log dump
daedalus machine stop test-m
daedalus machine delete test-m --confirm

# K8s clusters (container 1.3.1 k8s plugin — opt-in heavy: create pulls the
# node image + boots control-plane VMs, minutes; see Quick Start §9)
daedalus k8s create --name dev            # create + start a cluster (-c cpus, -m memory, --rm)
daedalus k8s list                         # grouped table (--json for machine-readable)
daedalus k8s start dev                    # start a stopped cluster
daedalus k8s load-image dev alpine:latest # load a local image into the cluster
daedalus k8s write-config dev             # print kubeconfig (--kubeconfig PATH to write a file)
daedalus k8s delete dev --confirm         # delete (--confirm required)

# Images
daedalus image-pull debian:latest     # Pull from registry
daedalus image-list                   # List local images

# Profiles
daedalus profiles                     # List security profiles
```

**All CLI commands:**

| Command | Description |
|---|---|
| `probe` | Host capability manifest |
| `run` | Create and start a container |
| `detonate` | Run a sample in a disposable isolated VM → behavioral report + risk score, then destroy the VM |
| `ls` | List containers |
| `inspect` | Inspect a container |
| `exec` | Execute command inside container |
| `logs` | Fetch container logs |
| `destroy` | Stop + delete (requires --confirm) |
| `stats` | One-shot resource stats (all running, or one container) |
| `cp` | Copy files host↔container (`<id>:/path` on either side) |
| `export` | Export a container filesystem to a tar archive |
| `prune` | Remove stopped containers, reclaim disk (requires --confirm) |
| `network` | Network CRUD subcommands: `create`, `list`, `inspect`, `delete` |
| `machine` | Machine subcommands: `create`, `list`, `inspect`, `set`, `set-default`, `stop`, `delete` (requires `--confirm`), `run`, `logs` |
| `k8s` | Cluster subcommands: `create` (opt-in heavy — pulls node image + boots VMs), `list`, `start`, `load-image`, `write-config`, `delete` (requires `--confirm`) |
| `image-pull` | Pull image from registry |
| `image-list` | List local images |
| `system-status` | Daemon + version + container count |
| `system` | System plane: `property list` / `get` / `set` (get/set are list-only on 1.3.1 — see the runtime-version facts below), `version`, `df` |
| `registry-login` | Login to a registry (`-u`, `--password-stdin`) |
| `registry-logout` | Logout from a registry |
| `registry-default-inspect` | Show the default registry host (1.3.1: no `default` tree — see the runtime-version facts below) |
| `registry-default-set` | Set the default registry host (`--scheme`) |
| `registry-default-unset` | Clear the default registry host |
| `profiles` | List security profiles |

**1.3.1 run surface** (`run` / `create` — the full 51-flag set, serialised by `RunSpec.to_cli_args()`; exec takes `--workdir/-w`, `--user/-u`, `--uid`, `--gid`, `--tty/-t`, `--interactive/-i`, `--detach/-d`, `--ulimit`, `--env-file`):

| Flag | RunSpec field | Meaning |
|---|---|---|
| `--arch <arch>` / `-a` | `arch` | Guest architecture — `x86_64` runs amd64 images on Apple silicon |
| `--os <os>` | `os` | Guest OS (e.g. `linux`) |
| `--platform <os/arch/variant>` | `platform` | Takes precedence over `--os`/`--arch` when both are given |
| `--rosetta` | `rosetta` | Run x86_64 images via Rosetta translation |
| `--ssh` | `ssh` | Forward the host SSH agent socket into the container |
| `--init` | `init` | Custom PID-1 (default init image) |
| `--init-image <image>` | `init_image` | Custom init image (eBPF boot / instrumentation, ariadne) |
| `--kernel-arg <k=v>` | `kernel_arg` | Kernel command-line argument (repeatable) |
| `--masked-path <path>` | `masked_path` | Mask a path inside the container (repeatable) |
| `--read-only-path <path>` | `read_only_path` | Read-only bind path (repeatable) |
| `--shm-size <size>` | `shm_size` | `/dev/shm` size, e.g. `64M` (inspect reports `shmSize` bytes) |
| `--ulimit <spec>` | `ulimit` | Ulimit spec, e.g. `nofile=1024` (repeatable; inspect reports the rlimit) |
| `--publish-socket <spec>` | `publish_socket` | Forward a unix socket into the container (repeatable) |
| `--virtualization` | `virtualization` | Nested-virtualization exposure |
| `--runtime <name>` | `runtime` | Runtime handler name |

The 1.2.2 core (cpus, memory, mounts, tmpfs, volumes, kernel, dns family, network, publish, cap-add/drop, read-only, labels, cidfile, scheme, entrypoint, env, user/uid/gid, workdir, interactive/tty, detach, rm, name) remains as before. Multi-arch is verified end-to-end: `daedalus run --arch x86_64` on Apple silicon reports `uname -m` → `x86_64` inside the guest (inspect shows `platform.architecture: amd64`, `rosetta: true`).

### 2. HTTP API (REST + SSE + WebSocket)

Start the server:

```bash
python -m daedalus.api.server
# → Uvicorn running on http://127.0.0.1:8420
# → API docs at http://127.0.0.1:8420/docs
# → Labyrinth Control Center UI at http://127.0.0.1:8420/ui
```

**API endpoints:**

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Host capability manifest |
| GET | `/containers` | List containers (`?all=true` for all) |
| POST | `/containers` | Create + start a container |
| GET | `/containers/{id}` | Inspect container |
| DELETE | `/containers/{id}` | Destroy (`?confirm=true` required) |
| GET | `/stats` | One-shot stats for all running containers |
| GET | `/containers/{id}/stats` | One-shot stats for one container |
| GET | `/stats/history` | Trailing stats-history window from the sampling engine (`?seconds=120`, optional `?container_id=`) |
| GET | `/stats/stream` | SSE stream of the newest stats rate per container (every 2 s) |
| POST | `/containers/{id}/copy` | Copy to/from container (`{"source","destination","direction"}`) |
| POST | `/containers/{id}/export` | Export FS to tar (`{"output_path"?}`) |
| POST | `/containers/prune` | Remove stopped containers (`{"confirm":true}`) |
| POST | `/detonate` | Detonate a host-local sample (`{"sample","image"?,"profile"?,"name"?,"observe_seconds"?,"stats_interval"?}` — blocking, 30–90 s; VM destroyed afterward) |
| GET | `/experiments` | List experiment manifests (detonations and runs in the store) |
| GET | `/experiments/{run_id}` | Inspect an experiment manifest |
| GET | `/experiments/{run_id}/report` | The behavioral report for a detonation (404 when absent) |
| DELETE | `/experiments/{run_id}` | Delete an experiment manifest |
| GET | `/networks` | List networks |
| POST | `/networks` | Create a network |
| GET | `/networks/{name}` | Inspect a network |
| DELETE | `/networks/{name}` | Delete a network (`?confirm=true` required) |
| POST | `/containers/stop` | Bulk stop **all** containers (`{"all":true}` → `stop -a`) |
| POST | `/containers/delete` | Bulk delete **all** containers (`{"all":true,"confirm":true}` → `delete -a`) |
| POST | `/containers/{id}/stop` | Stop a running container |
| POST | `/containers/{id}/start` | Start a stopped container |
| POST | `/containers/{id}/exec` | Execute command (`{"command":["echo","hi"]}`) |
| GET | `/containers/{id}/logs` | Get logs (`?boot=true&tail=50`) |
| GET | `/containers/{id}/logs/stream` | SSE log stream |
| GET | `/images` | List images |
| GET | `/images/search` | Docker Hub image search (`?q=alpine&limit=25`) — server-side (Hub sends no CORS headers, so the UI cannot fetch directly); degrades to an empty list on failure |
| POST | `/images/pull` | Pull image (`?image=alpine:latest`) |
| GET | `/images/{name}` | Image detail |
| DELETE | `/images/{name}` | Delete image |
| GET | `/machines` | List machines |
| POST | `/machines` | Create a machine (`{"image","name"?,"cpus"?,"memory"?,"no_boot"?}` — advisor warning echoed) |
| GET | `/machines/{id}` | Inspect a machine |
| POST | `/machines/{id}/set` | Set machine settings (`{"cpus"?,"memory"?,"home_mount"?,"virtualization"?,"kernel"?}`) |
| POST | `/machines/{id}/set-default` | Make a machine the default |
| POST | `/machines/{id}/stop` | Stop a machine |
| DELETE | `/machines/{id}` | Delete a machine (`?confirm=true` required) |
| POST | `/machines/{id}/run` | Boot + run command (`{"command":["uname","-a"]}` → ExecResult) |
| GET | `/machines/{id}/logs` | Machine boot/stdout logs |
| GET | `/clusters` | List Kubernetes clusters, grouped per cluster (`name`, `status`, `control_plane`, `workers`) |
| POST | `/clusters` | Create + start a cluster (`{"name","node_image"?,"cpus"?,"memory"?,"remove"?,"scheme"?}` — opt-in heavy: pulls node image + boots VMs, minutes) |
| POST | `/clusters/{name}/start` | Start a stopped cluster |
| POST | `/clusters/{name}/load-image` | Load an image into the cluster's containerd (`{"image","platform"?}`) |
| GET | `/clusters/{name}/kubeconfig` | The cluster's kubeconfig text |
| DELETE | `/clusters/{name}` | Delete a cluster (`?confirm=true` required) |
| GET | `/profiles` | List security profiles |
| GET | `/system/status` | Daemon status + counts + disk usage |
| GET | `/system/version` | Service versions keyed by appName (`container`, `container-apiserver`) |
| GET | `/system/properties` | System property values as a flat dotted-key dict (`{"properties": {...}}`) |
| GET | `/system/properties/{key}` | Single property value (1.3.1: list-only — honest CapabilityError → 400, see the runtime-version facts below) |
| PUT | `/system/properties/{key}` | Set a property (`{"value": "..."}` — audited; 1.3.1: list-only — honest CapabilityError → 400) |
| POST | `/registry/login` | Registry login (`{"server","username"?,"password"?,"scheme"?}` — password via `--password-stdin`) |
| POST | `/registry/logout` | Registry logout (`?server=`) |
| GET | `/registry/default` | Default registry host (1.3.1: no `default` tree — see the runtime-version facts below) |
| POST | `/registry/default` | Set the default registry (`{"host","scheme"?}`) |
| DELETE | `/registry/default` | Clear the default registry |
| GET | `/system/audit` | Audit log (`?operation=run&limit=50`) |
| GET | `/system/events` | SSE container lifecycle events |
| WS | `/containers/{id}/exec` | WebSocket terminal |

The stats history is collected by the server's sampling engine (2 s cadence, derived
CPU%/delta rates with a 300-point ring per container) and **persists across server
restarts** at `~/.daedalus/store/stats-history.json` (saved every 10 ticks, load-on-boot).

**Run request fields:** `POST /containers` accepts the full 1.3.1 surface alongside the
1.2.2 fields — `arch`, `os`, `platform` (precedence over `os`/`arch`), `rosetta`, `ssh`,
`init`, `init_image`, `kernel_arg` (repeatable), `masked_path` (repeatable),
`read_only_path` (repeatable), `shm_size`, `ulimit` (repeatable), `publish_socket`
(repeatable), `virtualization`, `runtime`. `POST /containers/{id}/exec` additionally
accepts `detached` and `ulimit` (plus the existing `user`/`uid`/`gid`/`workdir`/`env`/
`tty`/`interactive`). The machine `POST /machines` request carries the machine-run
surface (`cpus`, `memory`, `no_boot`, …) and `POST /machines/{id}/run` accepts
`root`/`detached`/`tty`/`user`/`workdir`/`ulimit`.

**curl examples:**

```bash
curl http://127.0.0.1:8420/health | jq .
curl http://127.0.0.1:8420/containers?all=true | jq .
curl -X POST http://127.0.0.1:8420/containers \
  -H 'Content-Type: application/json' \
  -d '{"image":"alpine:latest","detach":true}'
curl http://127.0.0.1:8420/system/audit?limit=5 | jq .
curl -X POST http://127.0.0.1:8420/machines \
  -H 'Content-Type: application/json' \
  -d '{"image":"alpine:latest","name":"test-m","no_boot":true}'
curl -X DELETE http://127.0.0.1:8420/machines/test-m?confirm=true | jq .
curl http://127.0.0.1:8420/clusters | jq .
curl http://127.0.0.1:8420/clusters/dev/kubeconfig | jq -r .kubeconfig > ~/.kube/daedalus-dev.yaml
kubectl --kubeconfig ~/.kube/daedalus-dev.yaml get nodes
curl -X DELETE http://127.0.0.1:8420/clusters/dev?confirm=true | jq .
curl -X POST http://127.0.0.1:8420/detonate \
  -H 'Content-Type: application/json' \
  -d '{"sample":"/path/to/suspicious.elf","observe_seconds":30}' | jq .
```

### 3. Labyrinth Control Center (Web UI)

The dashboard is served by the API server at `/ui` — no separate process needed.

```bash
# Start the server (serves both API and UI)
python -m daedalus.api.server

# Open in browser
open http://127.0.0.1:8420/ui
```

**Pages:**

| Page | What it shows |
|---|---|
| **Dashboard** | System health, container counts, disk usage, capabilities, **System section** (per-service versions + property key-values, data-driven) |
| **Containers** | Live table (3s polling), search/filter, create form with advanced options, start/stop/destroy, inspect modal |
| **Terminal** | Interactive xterm.js — type commands, press Enter to execute. Line buffered with ↑ history |
| **Images** | Image list with name/tag/size/digest, pull dialog with **Docker Hub search** (server-side, debounced ≥ 2 chars), delete |
| **Logs** | Container selector, boot/stdout toggle, live log stream, **multi-pane split view** (select 2–4 containers → side-by-side colored panes) |
| **Registries** | Registry login state (default host), login form (`--password-stdin`), logout, default set/unset — mirrors the registry API endpoints |
| **Audit** | Tamper-evident audit trail with filters |
| **Profiles** | Security profile cards showing configured settings |
| **Clusters** | Kubernetes cluster cards (status, nodes, addresses), create form, per-cluster start / load-image / view-kubeconfig / delete |

**Creating a container in the UI:**

1. Go to **Containers** page
2. Click **+ New**
3. Choose an image (e.g. `alpine:latest` for speed, `debian:latest` for proxy tools)
4. Select a profile — `general` for normal use, `detonation` for malware analysis
5. Keep **Detach** checked — the container stays running automatically
7. Click **Create & Run**

**Opening a terminal:**

1. Create a running container (detached with any profile)
2. Click **Term** on the container row
3. Type commands at the `λ` prompt — press Enter to execute

### 4. MCP Server (Autonomous Agents)

The MCP server lets coding agents (Claude Code, Cursor, etc.) control DAEDALUS.

```bash
python -m daedalus.mcp.server
```

**Configuring for Claude Code / synapse / cartograph:**

Add to your `mcp.json` or `~/.claude/config.json`:

```json
{
  "mcpServers": {
    "daedalus": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/daedalus", "python", "-m", "daedalus.mcp.server"]
    }
  }
}
```

**Available MCP tools:**

| Tool | Description |
|---|---|
| `daedalus_health` | Host capability manifest — call first |
| `daedalus_run` | Create + start container — args include `arch`/`os`/`platform` (multi-arch; `arch="x86_64"` runs amd64 guests on Apple silicon, `rosetta=True` for Rosetta translation), `ssh`, `init`, `init_image`, `kernel_arg`, `masked_path`, `read_only_path`, `shm_size`, `ulimit`, `publish_socket`, `virtualization`, `runtime` |
| `daedalus_detonate` | The safe-detention verb: detonate a host-local sample in a disposable isolated VM, return the behavioral report + risk score, destroy the VM (`readOnlyHint=false`, `destructiveHint=true` — a VM is created and destroyed; blocks ~30–90 s) |
| `daedalus_list` | List containers |
| `daedalus_inspect` | Inspect container |
| `daedalus_exec` | Execute command inside container (args include `workdir`, `detached`, `ulimit`, `user`/`uid`/`gid`, `env`) |
| `daedalus_logs` | Fetch container logs |
| `daedalus_stop` | Stop a running container (`all=true` stops every container — `stop -a`) |
| `daedalus_destroy` | Destroy container (requires `confirm=true`; `all=true` + `confirm=true` destroys every container — `delete -a`) |
| `daedalus_stats` | One-shot resource stats (all or one container) |
| `daedalus_copy` | Copy a file to/from a container |
| `daedalus_export` | Export a container filesystem to a tar (store-indexed artifact) |
| `daedalus_prune` | Remove stopped containers (requires `confirm=true`) |
| `daedalus_network_list` | List networks |
| `daedalus_network_create` | Create a network (policy-gated when routable) |
| `daedalus_network_inspect` | Inspect a network |
| `daedalus_network_delete` | Delete a network (requires `confirm=true`) |
| `daedalus_image_pull` | Pull image from registry |
| `daedalus_image_list` | List local images |
| `daedalus_machine_list` | List machines |
| `daedalus_machine_create` | Create a machine (init-capable image required — machines boot the image's own `/sbin/init`; `no_boot=true` to create without booting) |
| `daedalus_machine_inspect` | Inspect a machine |
| `daedalus_machine_set` | Update machine settings (apply after restart) |
| `daedalus_machine_stop` | Stop a machine |
| `daedalus_machine_set_default` | Make a machine the default |
| `daedalus_machine_run` | Boot + run a command inside a machine |
| `daedalus_machine_logs` | Machine boot/stdout logs |
| `daedalus_machine_delete` | Delete a machine (requires `confirm=true`) |
| `daedalus_cluster_list` | List Kubernetes clusters, grouped per cluster (`readOnlyHint=true`) |
| `daedalus_cluster_create` | Create + start a cluster (opt-in heavy — pulls node image + boots VMs, minutes; `remove=true` cleans up on stop) |
| `daedalus_cluster_start` | Start a stopped cluster |
| `daedalus_cluster_load_image` | Load a local image into a cluster's containerd |
| `daedalus_cluster_write_config` | Print the cluster's kubeconfig text (`kubeconfig` path to write a file instead) |
| `daedalus_cluster_delete` | Delete a cluster (`destructiveHint=true`, requires `confirm=true`) |
| `daedalus_profiles` | List security profiles |
| `daedalus_system_version` | Service versions keyed by appName (`container`, `container-apiserver`) — `readOnlyHint=true` |
| `daedalus_system_property_list` | System property values as dotted keys (on 1.3.1 the runtime exposes only this verb) |
| `daedalus_system_property_get` | Read a single property (`key`) — on 1.3.1 returns the honest list-only isError result |
| `daedalus_system_property_set` | Set a property (`key`, `value` — audited) — on 1.3.1 returns the honest list-only isError result |

**Runtime-version facts (verified live, container 1.3.1):**

- **`system property` is list-only.** The runtime exposes just `list, ls` — `get`/`set` answer `Error: N unexpected arguments`. DAEDALUS surfaces that fact honestly on every surface instead of leaking the usage text as a property value: CLI prints the error and exits 1, the API returns 400, and the MCP tools return an isError result (the MCP tool also records the attempt in the audit log).
- **No `registry default` subcommand.** The runtime exposes only `registry login/logout/list`; the `default` tree answers `Error: N unexpected arguments`. DAEDALUS reports the absence rather than pretending a default registry exists.

Both facts are runtime-version facts, not daedalus gaps — they come from the host CLI, and the wrappers fail loudly (CapabilityError) rather than silently misreporting. The rest of the registry surface (login/logout/list) works.

---

## Security Profiles

| Profile | Use case | Key settings |
|---|---|---|
| `general` | Normal Linux VM use | No restrictions |
| `detonation` | Malware analysis (default for security) | DNS fully blocked; deliberately **no tmpfs** — the VM is destroyed after analysis, and tmpfs writes would be invisible to the pre/post FS diff that powers the report |
| `bench` | Benchmarking and development | Permissive |
| `fuzz` | Kernel fuzzing / escape research | KASAN kernel |
| `isolated` | Air-gapped analysis | No DNS |
| `deception` | Network deception labs | Fake DNS resolver, custom domains |
| `proxy` | Proxy analysis (Burp/mitmproxy) | Routes through --proxy, injects CA cert via --cert |

Use `general` when you just want a Linux VM. Use `detonation` when you're analyzing suspicious binaries.

---

## Proxy & Cert Injection

Route container traffic through Burp Suite or mitmproxy for MITM analysis. Proxy and cert are fully optional across all surfaces (CLI, API, MCP).

### CLI

```bash
# Convert Burp's DER CA cert to PEM
openssl x509 -inform DER -in burp.der -out /tmp/burp-ca.pem

# Run with proxy + cert (both optional)
daedalus run debian:latest -d --proxy 192.168.64.1:8083 \
  --cert /tmp/burp-ca.pem --name analysis

# Verify proxy env vars are set system-wide
daedalus exec analysis -- sh -c "echo \$HTTP_PROXY \$http_proxy"
# HTTP_PROXY=http://192.168.64.1:8083 http_proxy=http://192.168.64.1:8083

# HTTPS through Burp with cert trust
daedalus exec analysis -- curl -s -x http://192.168.64.1:8083 https://sperixlabs.org/
```

**Critical**: Use the host gateway IP (`192.168.64.1`), not `127.0.0.1` — inside the container, `127.0.0.1` is the container's own loopback.

### Proxy without cert (works)

```bash
daedalus run alpine:latest -d --proxy 192.168.64.1:8083 --name proxy-only
```

### Cert without proxy (works)

```bash
daedalus run debian:latest -d --cert /tmp/burp-ca.pem --name cert-only
```

### With NO_PROXY exclusions

```bash
daedalus run debian:latest -d --proxy 192.168.64.1:8083 \
  --no-proxy "localhost,127.0.0.1,.internal" --name analysis
```

### API

```bash
curl -X POST http://127.0.0.1:8420/containers \
  -H 'Content-Type: application/json' \
  -d '{"image":"debian:latest","proxy":"192.168.64.1:8083","cert_path":"/tmp/burp-ca.pem","command":["tail","-f","/dev/null"]}'
```

### MCP

```json
{
  "image": "debian:latest",
  "proxy": "192.168.64.1:8083",
  "cert_path": "/tmp/burp-ca.pem"
}
```

### Verified across all 7 profiles

Proxy + cert tested and verified with `general`, `detonation`, `bench`, `fuzz`, `isolated`, `deception`, and `proxy` profiles. All four proxy env vars (`HTTP_PROXY`, `http_proxy`, `HTTPS_PROXY`, `https_proxy`) are set system-wide. Cert is injected at `/etc/ssl/certs/daedalus-ca.pem`.

### tcpdump / Wireshark

```bash
daedalus exec analysis -- apt-get install -y tcpdump
daedalus exec analysis -- tcpdump -i eth0 -w /tmp/capture.pcap port 8083 &
# ... make proxied requests ...
daedalus exec analysis -- tcpdump -r /tmp/capture.pcap
```

The container's network interface is `bridge100` on the host. For host-side capture: `tcpdump -i bridge100 -w capture.pcap`.

---

## Development

```bash
# Install with dev dependencies
uv pip install -e ".[dev]"

# Run all checks
mypy --strict daedalus/          # type checking
ruff check daedalus/ tests/      # linting
pytest -m unit -q                 # unit tests (fast)
pytest -m integration -q          # integration tests (requires container daemon)

# UI development
cd ui
npm install
npm run dev                       # Vite HMR, proxies API to :8420
npm run build                     # production build → ui/dist/
```

**Running the full stack in dev:**

```bash
# Terminal 1: API server
python -m daedalus.api.server

# Terminal 2: UI dev server (auto-reload on changes)
cd ui && npm run dev
# → opens http://localhost:5173 with API proxied to :8420
```

---

## Quality Gates

```
mypy --strict daedalus/     → Success: no issues found in 27 source files
pytest tests/ -q            → 421 passed, 2 skipped (386 non-integration + 35 integration)
pytest -m integration       → 35 passed, 2 skipped (requires container daemon)
Detonation §M7 test         → 2 live detonations: dropper vs no-op reports distinguishable, no leaks
Proxy across 7 profiles     → All pass (CLI + API + MCP surfaces)
UI build                    → TypeScript clean, 8/8 checks pass
ruff check                  → cosmetic only (E501, SIM*, UP042)
Import-time I/O             → zero subprocess calls
```

---

## Troubleshooting

**"Default kernel not configured"**
```bash
container system kernel set --recommended
```

**"failed to find plugin named container-network"**
Networking requires macOS 26+ and the `container-network-vmnet` plugin. On container 1.2.2 the `network` subcommand is fully wired — `daedalus network create/list/inspect/delete` drives real vmnet networks. If the plugin is missing, reinstall the runtime or check `/usr/local/libexec/container/plugins/`. DNS control via `--dns` flags works regardless.

**Containers go straight to "stopped" state**
Detached containers automatically stay alive in the background. If you override the command, make sure it doesn't exit immediately — some default CMDs like `/bin/sh` need stdin and exit instantly when detached. Leave the command blank for automatic keep-alive.

**"No module named daedalus"**
```bash
source .venv/bin/activate
uv pip install -e .
```

**API returns 503 "not yet initialised"**
The server just started — wait 1-2 seconds for the lifespan startup to complete, then retry.

**Terminal shows "sh: s: not found" on each keystroke**
Make sure you're on the latest build — the terminal now uses line buffering (type freely, press Enter to send).
```

## License

MIT
