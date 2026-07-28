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
│  icarus     → exec, logs                            │
│  mint       → images (pull, list, tag, delete)      │
│  talos      → DNS, network topology                 │
│  ariadne    → instrumentation, kernel variants      │
│  minos      → forensic analysis, risk scoring       │
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
- **container** installed at `/usr/local/bin/container` (v0.1.0+)
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

# 6. Run a container
daedalus run alpine:latest --command "echo hello from daedalus"

# 7. See it
daedalus ls --all
```

---

## Consumer Surfaces

DAEDALUS has three consumer surfaces — use whichever fits your workflow.

### 1. CLI (Terminal)

```bash
# System
daedalus probe                        # Host capability manifest
daedalus system-status                # Daemon + version + container counts

# Containers
daedalus run alpine:latest            # Create + start (general profile, detached)
daedalus run alpine:latest -p detonation --command "sleep 300"
daedalus ls                           # List running
daedalus ls --all                     # List all (including stopped)
daedalus inspect <id>                 # Full JSON inspect
daedalus exec <id> uname -a           # Run command inside
daedalus logs <id>                    # View logs
daedalus logs <id> --boot             # Boot-time logs
daedalus destroy <id> --confirm       # Stop + delete

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
| `ls` | List containers |
| `inspect` | Inspect a container |
| `exec` | Execute command inside container |
| `logs` | Fetch container logs |
| `destroy` | Stop + delete (requires --confirm) |
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
| POST | `/containers/{id}/stop` | Stop a running container |
| POST | `/containers/{id}/start` | Start a stopped container |
| POST | `/containers/{id}/exec` | Execute command (`{"command":["echo","hi"]}`) |
| GET | `/containers/{id}/logs` | Get logs (`?boot=true&tail=50`) |
| GET | `/containers/{id}/logs/stream` | SSE log stream |
| GET | `/images` | List images |
| POST | `/images/pull` | Pull image (`?image=alpine:latest`) |
| GET | `/images/{name}` | Image detail |
| DELETE | `/images/{name}` | Delete image |
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
  -d '{"image":"alpine:latest","detach":true,"command":["sleep","3600"]}'
curl http://127.0.0.1:8420/system/audit?limit=5 | jq .
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
3. Choose an image (e.g. `alpine:latest`)
4. Select a profile — `general` for normal use, `detonation` for malware analysis
5. Keep **Detach** checked (container runs in background)
6. Add a command like `sleep 3600` (keeps it alive)
7. Click **Create & Run**

**Opening a terminal:**

1. Create a running container (detached with `sleep 3600`)
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
| `daedalus_list` | List containers |
| `daedalus_inspect` | Inspect container |
| `daedalus_exec` | Execute command inside container |
| `daedalus_logs` | Fetch container logs |
| `daedalus_stop` | Stop a running container |
| `daedalus_destroy` | Destroy container (requires `confirm=true`) |
| `daedalus_image_pull` | Pull image from registry |
| `daedalus_image_list` | List local images |
| `daedalus_profiles` | List security profiles |

---

## Security Profiles

| Profile | Use case | Key settings |
|---|---|---|
| `general` | Normal Linux VM use | No restrictions |
| `detonation` | Malware analysis (default for security) | Controlled DNS, tmpfs for /tmp, no external DNS |
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
mypy --strict daedalus/     → Success: no issues found in 23 source files
pytest -m unit              → 115 passed
pytest -m integration       → 11 passed (requires container daemon)
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
Networking is not yet wired in container v0.1.0. DNS control via `--dns` flags still works.

**Containers go straight to "stopped" state**
Make sure Detach is checked AND you've provided a command like `sleep 3600`. Alpine's default CMD `/bin/sh` exits instantly without stdin in detached mode.

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
