# DAEDALUS

**D**etonation, **A**nalysis & **E**xperimentation — **D**aedalus **A**rchitecture for **L**inux container **U**nits & **S**andboxing

A security-research control plane built on Apple's `container` runtime. DAEDALUS turns the one-VM-per-container architecture of Apple silicon into a fleet of disposable, hardware-isolated sandboxes — drivable by humans (CLI), services (HTTP), and autonomous agents (MCP).

## Requirements

- macOS 26+ (Tahoe)
- Apple silicon (arm64)
- `container` installed at `/usr/local/bin/container`
- Python 3.12+

## Install

```bash
git clone <this-repo>
cd daedalus
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Quick start

```bash
# Check what your host supports
daedalus probe

# Pull an image
daedalus image-pull alpine:latest

# Run a disposable container (detonation profile by default)
daedalus run alpine:latest --command "echo hello"

# List containers
daedalus ls --all

# Execute a command inside a running container
daedalus exec <id> uname -a

# Fetch logs
daedalus logs <id>

# Destroy a container
daedalus destroy <id> --confirm
```

## Surfaces

| Surface | Entry | For |
|---|---|---|
| **CLI** | `daedalus` | Humans |
| **HTTP API** | `python -m daedalus.api.server` (port 8420) | Services, web UIs |
| **MCP** | `python -m daedalus.mcp.server` | Autonomous agents |

## MCP tools

`daedalus_health` `daedalus_run` `daedalus_start` `daedalus_stop` `daedalus_kill` `daedalus_list` `daedalus_inspect` `daedalus_destroy` `daedalus_exec` `daedalus_logs` `daedalus_image_pull` `daedalus_image_list` `daedalus_image_delete` `daedalus_image_inspect` `daedalus_image_push` `daedalus_image_build` `daedalus_image_load` `daedalus_image_save` `daedalus_image_tag` `daedalus_image_prune` `daedalus_registry_login` `daedalus_registry_logout` `daedalus_builder_status` `daedalus_builder_start` `daedalus_builder_stop` `daedalus_profiles` `daedalus_system_status` `daedalus_system_restart` `daedalus_audit` `daedalus_experiments` `daedalus_dns_list` `daedalus_dns_create` `daedalus_dns_delete`

Set up via `mcp.json`:
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

## Security profiles

| Profile | Description |
|---|---|
| `detonation` | Maximum lockdown — internal DNS, controlled resolver (default) |
| `bench` | Permissive — for benchmarking and development |
| `fuzz` | Kernel fuzzing — KASAN kernel |
| `isolated` | Full network isolation — no DNS |
| `deception` | Network deception — controlled DNS, fake resolver |

## Architecture

```
daedalus/
  core/          # Engine: forge, icarus, mint, talos, ariadne, minos
  cli/           # Typer CLI
  api/           # FastAPI REST server
  mcp/           # MCP server (FastMCP)
tests/
  integration/   # Real container CLI tests
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

## Quality

```bash
mypy --strict daedalus/     # zero errors
pytest -m unit -q            # 108 passed
pytest -m integration -q     # integration tests (requires container daemon)
DAEDALUS_LIVE=1 pytest tests/integration/test_mcp_live.py -v  # all 42 MCP tools live
```

## License

MIT
