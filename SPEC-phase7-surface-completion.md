# SPEC Phase 7 — RunSpec + verb surface completion (container 1.3.1 full scope)

Status: **Approved** · Branch: main (post-merge)

## 1. Objectives

Close every runtime-surface gap identified in the exhaustive 1.3.1 audit (75 commands, 471 flags):

1. RunSpec gains the 20 missing fields with single-source serialization + unit tests.
2. ExecOptions, machine run/logs, build, registry-login, stop/delete gain their missing options.
3. Surfaces expose the new fields (CLI options, API request models, MCP args) — especially `--arch/--os/--rosetta/--platform/--ssh/--init-image` (the x86_64 ask) and `--kernel-arg/--virtualization/--read-only-path/--masked-path/--publish-socket/--shm-size/--ulimit/--init`.
4. Capability gates: EXPECTED_FLAGS extended to the full 51-flag run set; runtime_features keys for the new gated families (rosetta, virtualization, ssh, init_image).

## 2. Verified flag inventory (captured live, 1.3.1)

run (51): cap-add, cap-drop, cidfile, dns, dns-domain, dns-option, dns-search, entrypoint, env-file, gid, init, init-image, kernel-arg, masked-path, max-concurrent-downloads, mount, name, network, no-dns, os, platform, progress, publish-socket, read-only, read-only-path, rm, rosetta, runtime, scheme, shm-size, ssh, tmpfs, uid, ulimit, version, virtualization, -a, -c, -d, -e, -h, -i, -k, -l, -m, -p, -t, -u, -v, -w
exec (13): env-file, gid, uid, ulimit, -d, -e, -i, -t, -u, -w
machine run (15): env-file, gid, root, uid, ulimit, -d, -e, -i, -n, -t, -u, -w
machine logs (6): boot, -f, -n
build (26): arch, build-arg, dns*, no-cache, os, platform, progress, pull, secret, ssh, target, vsock-port, -c, -f, -m, -o, -q, -t
registry login (6): password-stdin, scheme, -u
stop/delete/kill/volume delete/network delete/image delete: -a (all), kill -s, stop -s/-t

## 3. New RunSpec fields (backend.py — single source, emit order documented)

```python
init: bool = False                    # --init
init_image: str | None = None         # --init-image <image>   (already present — keep)
kernel_arg: list[str] = ...           # --kernel-arg <k=v>, repeatable
masked_path: list[str] = ...          # --masked-path <path>, repeatable
read_only_path: list[str] = ...       # --read-only-path <path>, repeatable
rosetta: bool = False                 # --rosetta
runtime: str | None = None            # --runtime <name>
shm_size: str | None = None           # --shm-size <size>
ulimit: list[str] = ...               # --ulimit <spec>, repeatable
publish_socket: list[str] = ...       # --publish-socket <spec>, repeatable
virtualization: bool = False          # --virtualization
platform: str | None = None           # --platform <os/arch/variant>  (takes precedence over os/arch — emit before them)
```

Emission order in to_cli_args(): resources (shm_size, ulimit) → filesystem (masked_path, read_only_path after read_only) → platform (platform before os/arch) → runtime (runtime, init, init_image, kernel_arg, rosetta, virtualization) → network (publish_socket after publish) → ssh flag.

## 4. Other verb extensions (backend.py + cli_backend.py)

- `ExecOptions`: `detached: bool = False` (`exec -d`) + `ulimit: list[str]` — wire in CliBackend.exec.
- `machine_run`: extend signature `root: bool = False, ulimit: list[str] | None = None, detached: bool = False, tty: bool = False, user: str | None = None, workdir: str | None = None` (flags -d/-t/-u/-w per the capture).
- `machine_logs(machine_id, *, boot: bool = False, follow: bool = False, tail: int | None = None)`.
- `BuildSpec`: `secret: list[str]`, `ssh: bool`, `pull: bool`, `vsock_port: int | None` + emission.
- `registry_login`: `username: str | None = None` (-u), `password_stdin: str | None = None` (--password-stdin — when provided, feed via stdin through `_run_cli_impl` input param if it supports one, else subprocess input=).
- `stop(container_id, *, all: bool = False)` — `stop -a`; `delete(..., all: bool = False)` — `delete -a` (with confirm at engine level where destroy-style gates exist).
- `_RUNSPEC_FIELDS` += every new RunSpec field (forge.py one-line edit).

## 5. Capabilities (capabilities.py)

- `EXPECTED_FLAGS` += the full 51-flag run set (so known_flags is honest; extra_flags only true extras).
- `runtime_features` additions: `"rosetta": "rosetta" in known_flags`, `"virtualization": ...`, `"ssh_forwarding": ...`, `"init_image": init_image gate is True`.

## 6. Surfaces

- **CLI** run/create: `--arch/-a`, `--os`, `--platform`, `--rosetta`, `--ssh`, `--init-image`, `--init`, `--kernel-arg` (repeatable), `--masked-path` (repeatable), `--read-only-path` (repeatable), `--shm-size`, `--ulimit` (repeatable), `--publish-socket` (repeatable), `--virtualization`, `--runtime`; exec: `-d/--detach`, `--ulimit`; stop: `--all`; delete/destroy: `--all` (with confirm).
- **API**: RunRequest + ExecRequest + machine RunRequest + BuildRequest + registry login gain the matching optional fields; `POST /containers/{id}/exec` accepts detached/ulimit; stop/delete accept `all`.
- **MCP**: daedalus_run/create/exec args mirror the above (arch/os/platform/rosetta/ssh/init_image/kernel_arg/read_only_path/shm_size/ulimit/publish_socket/virtualization).

## 7. Waves

- **W1 (1 agent)**: backend.py RunSpec + BuildSpec + ExecOptions + machine_run/machine_logs/registry_login/stop/delete signatures; cli_backend.py emissions; test_cli_backend_parsers serialization tests; MockBackend stubs.
- **W2 (1 agent, after W1)**: forge.py `_RUNSPEC_FIELDS` + capabilities EXPECTED_FLAGS/runtime_features + test updates.
- **W3 (4 agents, parallel, after W2)**: CLI / API / MCP / **UI** surfaces + live verification (incl. `daedalus run --arch x86_64` → `uname -m` = x86_64, `--rosetta`, `--init` smoke). **UI scope (user-mandated full parity)**: the Containers "+ New" create modal's "▸ Advanced options" section gains the new fields (arch/os/platform selects, rosetta, ssh, init-image, init, kernel-arg, masked-path, read-only-path, shm-size, ulimit, publish-socket, virtualization — data-driven from one field registry, not hand-copied); Terminal view gains exec advanced options (detach, ulimit, user, workdir); Machines view run box gains the new machine-run options (root, detach, tty, user, workdir). Every field the CLI/API expose must be reachable in the UI unless it is a pure-internal engine flag.
- **W4 (1 agent, after W3)**: integration round-trips (multi-arch run via daedalus, shm/ulimit flags reflected in container config JSON, stop --all, machine run -t) + docs (README flags table + ARCHITECTURE §9).
- **W5 (orchestrator)**: gates + macos-harness regression + commit + push.

## 8. Success metrics

1. `daedalus run alpine:latest --arch x86_64 -d` + `daedalus exec <id> uname -m` → `x86_64` (the user's x86_64 ask, end-to-end through daedalus).
2. Every one of the 51 run flags reachable through at least one surface (CLI or API or MCP) or explicitly documented as engine-only.
3. Live container config reflects shm-size/ulimit/masked-path/read-only-path/virtualization where the runtime reports them.
4. Gates green; no regressions in the 311-test suite.
