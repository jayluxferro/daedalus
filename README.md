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
- **container** installed at `/usr/local/bin/container` (1.2.2+)
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

# 8. Machines (persistent stateful VMs — init-capable image required)
daedalus machine create alpine:latest --no-boot -n test-m   # will warn: alpine has no init system
daedalus machine list
daedalus machine run test-m uname -a                        # boots the machine, runs the command
daedalus machine stop test-m
daedalus machine delete test-m --confirm
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

# Containers
daedalus run alpine:latest -d         # Create + start (detached, auto-keepalive)
daedalus run debian:latest -d -p detonation  # Detonation profile for malware analysis
daedalus detonate ./suspicious.elf --observe 30   # THE flagship: detonate, report, destroy
daedalus ls                           # List running
daedalus ls --all                     # List all (including stopped)
daedalus inspect <id>                 # Full JSON inspect
daedalus exec <id> uname -a           # Run command inside
daedalus logs <id>                    # View logs
daedalus logs <id> --boot             # Boot-time logs
daedalus destroy <id> --confirm       # Stop + delete
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
| `image-pull` | Pull image from registry |
| `image-list` | List local images |
| `system-status` | Daemon + version + container count |
| `profiles` | List security profiles |

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
| POST | `/containers/{id}/stop` | Stop a running container |
| POST | `/containers/{id}/start` | Start a stopped container |
| POST | `/containers/{id}/exec` | Execute command (`{"command":["echo","hi"]}`) |
| GET | `/containers/{id}/logs` | Get logs (`?boot=true&tail=50`) |
| GET | `/containers/{id}/logs/stream` | SSE log stream |
| GET | `/images` | List images |
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
| GET | `/profiles` | List security profiles |
| GET | `/system/status` | Daemon status + counts + disk usage |
| GET | `/system/audit` | Audit log (`?operation=run&limit=50`) |
| GET | `/system/events` | SSE container lifecycle events |
| WS | `/containers/{id}/exec` | WebSocket terminal |

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
| **Dashboard** | System health, container counts, disk usage, capabilities |
| **Containers** | Live table (3s polling), search/filter, create form with advanced options, start/stop/destroy, inspect modal |
| **Terminal** | Interactive xterm.js — type commands, press Enter to execute. Line buffered with ↑ history |
| **Images** | Image list with name/tag/size/digest, pull dialog, delete |
| **Logs** | Container selector, boot/stdout toggle, live log stream |
| **Audit** | Tamper-evident audit trail with filters |
| **Profiles** | Security profile cards showing configured settings |

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
| `daedalus_run` | Create + start container |
| `daedalus_detonate` | The safe-detention verb: detonate a host-local sample in a disposable isolated VM, return the behavioral report + risk score, destroy the VM (`readOnlyHint=false`, `destructiveHint=true` — a VM is created and destroyed; blocks ~30–90 s) |
| `daedalus_list` | List containers |
| `daedalus_inspect` | Inspect container |
| `daedalus_exec` | Execute command inside container |
| `daedalus_logs` | Fetch container logs |
| `daedalus_stop` | Stop a running container |
| `daedalus_destroy` | Destroy container (requires `confirm=true`) |
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
| `daedalus_profiles` | List security profiles |

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
mypy --strict daedalus/     → Success: no issues found in 24 source files
pytest tests/ -q            → 236 passed, 1 skipped (214 unit + 22 integration)
pytest -m integration       → 22 passed, 1 skipped (requires container daemon)
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
