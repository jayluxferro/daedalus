# SPEC Phase 4 — Volumes + profile hardening (substrate completion)

Status: **Approved for implementation** · Branch: `feature/ungate-container-1.2.2`

## 1. Objectives

Wrap the last runtime storage surface (`container volume`, 1.2.2) and use the newly-verified hardening flags (`--read-only`, `--cap-drop`) in the security profiles:

1. **Volume verbs** — create/list/inspect/delete/prune across Backend/CliBackend, a thin `Volumes` engine service, and all four surfaces.
2. **Profile hardening** — `Profile` gains `read_only: bool` + `cap_drop: list[str]`; `RunSpec` gains `read_only` (+ `--read-only` emission); `detonation` profile hardened with `cap_drop=["ALL"]` but **NOT `read_only`** — a read-only rootfs would blind the phase-3 FS-diff forensics (nothing writable → no runtime writes to observe; the sample could not run); capability lockdown provides the privilege reduction instead. `fuzz` unchanged.
3. **Kernel reality** — `system kernel` only exposes `set` in 1.2.2 (already wrapped); add the CLI `kernel` command surface (`daedalus kernel set --recommended|--binary|--tar`), and fix the stub `system_kernel_list` (mark honestly: returns known kernels from ariadne registry or `[]` with a docstring noting 1.2.2 exposes no list verb).

## 2. Verified environment (captured live 2026-08-22)

`container volume` subcommands: `create` (`--label k=v`, `--opt k=v`, `-s <size>` e.g. `100M`), `delete/rm <name>`, `list/ls --format json`, `inspect <name>` (JSON array), `prune`. Create/delete echo the volume name.

`volume list --format json` (verified):
```json
[{"configuration": {"creationDate": "2026-08-22T06:10:50Z", "driver": "local",
   "format": "ext4", "labels": {}, "name": "daedalus-probe-v",
   "options": {"size": "100M"}, "sizeInBytes": 104857600,
   "source": "/Users/jay/Library/Application Support/com.apple.container/volumes/daedalus-probe-v/volume.img"},
  "id": "daedalus-probe-v"}]
```
`inspect` outputs the same shape as a JSON array.

`container run --read-only` and `--cap-drop <cap>` exist in 1.2.2 (verified). `system kernel set` is the ONLY kernel subcommand in 1.2.2 (verified — `list` errors with `Unexpected argument`).

## 3. Architecture decisions

1. **`VolumeInfo` + `VolumeSpec`** in `daedalus/core/backend.py`; `VolumeSpec` (name, size: str|None, labels: dict, options: dict) with `to_cli_args()`. Parsers `OutputParser.volume_list` / `volume_inspect` (pure; configuration.* mapping incl. `sizeInBytes`, `source`, `format`, `driver`).
2. **New engine module `daedalus/core/volume.py`** — class `Volumes(backend, capabilities, *, policy=None, audit=None)`: `create(spec) -> VolumeInfo`, `list()`, `inspect(name)`, `delete(name, *, confirm=False)` (policy `check_volume_delete` mirroring the existing confirm gates; volumes hold forensic artifacts — deletion is destructive), `prune(*, confirm=False) -> list[str]`. Audits each mutation.
3. **Policy**: `check_volume_delete(*, confirm=False) -> PolicyResult` (same `require_confirm_destroy` flag).
4. **RunSpec.read_only** + `--read-only` emission; `Profile.read_only`/`cap_drop` fields + `apply()` wiring; `detonation` profile → `read_only=True, cap_drop=["ALL"]`; `fuzz` → `cap_drop=[]` unchanged, `read_only=False` (fuzzing needs writes). **Backward-compatible**: existing runs without the fields behave identically.
5. **Volume attach**: `RunSpec.volumes` already emits `-v`; document that named volumes attach as `-v <name>:<path>` (no new engine work in this phase beyond the CRUD surface).
6. **Kernel CLI**: `daedalus kernel set` (--recommended | --binary PATH | --tar PATH, --arch) delegating to the existing `system_kernel_set`; `daedalus kernel status` prints the capability gate state. No fake list verb (1.2.2 has none) — honesty over API theater.

## 4. Agent work breakdown

- **Wave 1 (2 agents, parallel):**
  - **W1 — capabilities + backend** (`capabilities.py` volume gate `_probe_subcommand(binary, "volume")` + `runtime_features["container_volumes"]`; `backend.py` VolumeInfo/VolumeSpec + 5 abstract verbs + RunSpec.read_only; `cli_backend.py` impls + parsers; `test_cli_backend_parsers.py` additions; MockBackend stubs in `test_forge.py` only).
  - **W2 — engine + policy + profiles** (`volume.py` NEW + `tests/test_volume.py` NEW; `policy.py` check_volume_delete; `profiles.py` read_only/cap_drop fields + hardened detonation; `forge.py` — nothing (profiles flow through kwargs; verify `_RUNSPEC_FIELDS` has `read_only`? add it)); `tests/test_profiles.py` if absent — profile apply() field tests.
- **Wave 2 (3 agents, parallel, after Wave 1):** CLI `volume` sub-app + `kernel set/status` (`cli/main.py`); API `/volumes*` + kernel endpoint if trivial (`api/server.py`); MCP `daedalus_volume_*` + `daedalus_kernel_set` (`mcp/server.py`).
- **Wave 3 (1 agent, after Wave 2):** UI Volumes view (`ui/src/api/volumes.ts` NEW, `ui/src/views/Volumes.tsx` NEW, App.tsx entry) — list with size/source, create modal (name/size), delete with confirm.
- **Wave 4 (1 agent, after Wave 3):** integration (`tests/integration/test_volumes.py` — create/list/inspect/delete round-trip + prune; profile hardening smoke: run with detonation profile asserts `--read-only`/`--cap-drop` present in the recorded RunSpec args via audit log or container config `readOnly` field from `container list --format json`) + docs (README/ARCHITECTURE tables).
- **Wave 5 (orchestrator):** gates + macos-harness Safari drive-through of the Volumes view + regression spot-check.

## 5. Success metrics

1. Volume round-trip works on all surfaces; volume artifacts survive container deletion (ext4 images).
2. `detonation` profile now emits `--read-only --cap-drop ALL`; verified in the live container's configuration (`readOnly: true` in list JSON).
3. `daedalus kernel set --recommended` works; `kernel status` honest.
4. All gates green; macos-harness verdict all-pass.

## 6. Non-goals

- No volume attach UI beyond the existing `-v` form fields (documented).
- No kernel variant catalog (1.2.2 exposes no list verb).
- No `k8s` wrapping (deferred, off-mission).
