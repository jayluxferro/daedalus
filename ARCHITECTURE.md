# DAEDALUS

**D**etonation, **A**nalysis & **E**xperimentation — **D**aedalus **A**rchitecture for **L**inux container **U**nits & **S**andboxing

A security-research control plane built on Apple's `container` runtime. DAEDALUS turns the
one-VM-per-container architecture of Apple silicon into a fleet of disposable, individually
instrumented, hardware-isolated sandboxes — drivable by humans (CLI), services (HTTP), and
autonomous agents (MCP), all through one core engine.

> In myth, Daedalus built the Labyrinth to contain the Minotaur — a maze nothing escapes.
> Each sandbox VM here is a Labyrinth; the control plane is Daedalus.

---

## 0. Why Apple `container` is the right substrate

Apple's `container` (the CLI) sits on the open-source **Containerization** Swift package and
allocates **one lightweight VM per container** via `Virtualization.framework`, rather than the
shared-kernel, namespace-isolated model of Docker. Three consequences drive this entire design:

1. **Hardware-enforced isolation per unit of work.** A guest-kernel exploit does not equal host
   compromise the way a Docker container escape can. This is what makes autonomous detonation of
   untrusted code defensible.
2. **Per-container kernel + init customization.** Each VM can boot a *different* kernel
   (`-k/--kernel`) and a *different* init image (`--init-image`) carrying eBPF/instrumentation.
   Every sandbox is independently instrumentable.
3. **Disposability.** Sub-second boot, dynamic resource allocation, `--rm` teardown. Thousands of
   throwaway VMs are cheap.

**Requirements:** macOS 26 (Tahoe) for full feature support — networking commands in particular
are macOS-26-only. Apple silicon. `container` installed (binary at `/usr/local/bin/container`).

---

## 1. Runtime topology of Apple `container` (what we build on)

```
┌──────────────┐        ┌───────────────────────────────────────────┐
│  CLI/Client  │ ─XPC─▶ │            container-apiserver             │
│ (`container`)│        │     (launchd-managed launch agent)        │
└──────────────┘        └────┬──────────────┬───────────────┬───────┘
                             │ XPC helpers   │               │
                    ┌────────▼───┐   ┌───────▼────┐   ┌──────▼─────┐
                    │  VM (#1)   │   │  VM (#2)   │   │  VM (#n)   │
                    │ ┌────────┐ │   │ ┌────────┐ │   │ ┌────────┐ │
                    │ │vminitd │ │   │ │vminitd │ │   │ │vminitd │ │   gRPC
                    │ │(PID 1) │◀┼───┼─┤(PID 1) │◀┼───┼─┤(PID 1) │◀┼── over
                    │ └───┬────┘ │   │ └───┬────┘ │   │ └───┬────┘ │   vsock
                    │  OCI proc  │   │  OCI proc  │   │  OCI proc  │
                    │ minimal FS │   │ minimal FS │   │ minimal FS │
                    │ own kernel │   │ own kernel │   │ own kernel │
                    └────────────┘   └────────────┘   └────────────┘

Native frameworks underneath:
  Virtualization.framework  → creates/manages the VMs
  vmnet.framework           → virtual network interfaces (container-network-vmnet plugin)
  XPC                       → CLI ⇄ apiserver ⇄ helpers IPC
  launchd                   → apiserver lifecycle (on-demand start, crash restart)
```

- **`container-apiserver`** — the daemon. Owns global state, exposes XPC. Versioned API (v0 XPC
  removed; client/server compat checks being added). Managed by launchd.
- **`vminitd`** — a Swift static init binary, PID 1 inside every VM. Assigns IPs, mounts the
  image (exposed as an **ext4 block device**), launches/supervises processes, and serves a
  **gRPC API over vsock** so the host can spawn and manage guest processes. Runs in a minimal FS
  with no standard core utils / libc — small attack surface.

### Three programmatic layers, ranked by stability

| Layer | Surface | Stability | DAEDALUS use |
|---|---|---|---|
| **L1** | `container` CLI + `--format json` | Stable within patch versions; covers 100% of features | **Primary engine backend** |
| **L2** | `vminitd` gRPC over vsock | Powerful, undocumented public contract | Optional advanced telemetry backend |
| **L3** | `Containerization` Swift pkg / XPC (`cctl` is the example app) | Most powerful, least stable | Optional native backend |

**Decision:** the DAEDALUS Core Engine targets **L1**. L2/L3 are pluggable backends behind the
same interface for users who need lower-level hooks and accept the churn.

---

## 2. Complete feature inventory (everything in scope)

This is the full surface DAEDALUS must wrap. Grouped by capability domain; the right column names
the DAEDALUS subsystem that owns it.

### 2.1 Lifecycle — *owned by `forge` (engine core)*
`create`, `run` (+`-d` detach), `start`, `stop` (signal+timeout), `kill`, `delete`/`rm` (+force),
`prune`, `list`/`ls`, `inspect`, `stats` (CPU/mem/net/block-IO/proc-count; streaming or
`--no-stream`). All read commands → JSON/YAML/TOML/table.

### 2.2 In-container interaction — *owned by `icarus` (entry/exec)*
`exec` (arbitrary cmd; env/user/uid/gid/tty/workdir) — the "ssh-in" primitive, no sshd needed;
`cp`/`copy` (bidirectional host↔guest); `logs` (`--follow`, `--boot`); `export` (full FS → tar,
container must be stopped).

### 2.3 Images — *owned by `mint` (image plane)*
`build` (BuildKit, Dockerfile/Containerfile, multi-arch, `--secret`, `--build-arg`,
`-o type=oci|tar|local`); `image pull/push/save/load/tag/delete/inspect/list/prune`.
OCI-compatible both ways (interops with Docker `save`/`load`). `builder start/stop/status/delete`.
`registry login/logout/list`.

### 2.4 Networking (macOS 26+) — *owned by `talos` (network guardian)*
- `network create` — `--subnet` (IPv4 CIDR), `--subnet-v6`, `--internal` (host-only, no host
  route), `--plugin` (default `container-network-vmnet`), `--option key=value`.
- `network delete/prune/list/inspect`.
- Per-container: `--network name,mac=…,mtu=…`, `-p/--publish` ports, `--publish-socket` (unix
  socket fwd), **dedicated per-container IPs** (direct container-to-container comms).
- DNS: `--dns`, `--dns-domain`, `--dns-search`, `--dns-option`, `--no-dns`;
  `system dns create/delete/list` (local resolvable domains; needs sudo).

### 2.5 Deep instrumentation hooks — *owned by `ariadne` (the thread/telemetry)*
- `--init-image` — custom init image: run VM-level daemons, **configure eBPF filters**, instrument
  boot *before* the OCI workload starts. **The core telemetry injection point.**
- `-k/--kernel` + `system kernel set` — per-container custom kernel (KASAN/KCOV/syzkaller, or
  deliberately vulnerable kernels for escape research).
- `--cap-add`/`--cap-drop` (incl. `ALL`); `--read-only` rootfs; `--tmpfs`; `--shm-size`;
  `--ulimit`; `--user`/`--uid`/`--gid`.
- `--rosetta` — x86_64 samples on Apple silicon (cross-arch malware).
- `--virtualization` — nested virt exposure (host+guest support permitting).
- `--mount`/`-v` volumes with ext4 journaling (`ordered`/`writeback`/`journal`).

### 2.6 System / control plane — *owned by `forge`*
`system start/stop/status/version/logs/df`, `system kernel set`,
`system property list` (TOML-config-backed).

### 2.7 Forensics & analysis — *owned by `minos` (the judge)*
Not a CLI feature set but a DAEDALUS layer built on `export` (FS tar), `stats --format json`
(resource fingerprint), `logs --boot`, and `ariadne` telemetry. Produces behavioral reports and
before/after diffs.

---

## 3. DAEDALUS architecture

### 3.1 Layered overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                          CONSUMERS (equal weight)                      │
│   daedalus CLI        HTTP/REST API           MCP server               │
│   (humans)            (services, web UI)      (autonomous agents)      │
└───────────┬───────────────────┬───────────────────┬───────────────────┘
            │                   │                   │
            └───────────────────┴───────────────────┘
                                │   one shared façade
┌───────────────────────────────▼───────────────────────────────────────┐
│                         DAEDALUS CORE ENGINE                            │
│                                                                        │
│  forge    lifecycle + system (create/run/stop/stats/system)           │
│  mint     image plane (build/pull/push/save/load/registry)            │
│  talos    network guardian (networks/DNS/topology/traffic policy)     │
│  icarus   exec/cp/logs/export — interaction & extraction              │
│  ariadne  instrumentation (init-image/eBPF/kernel/telemetry capture)  │
│  minos    forensics (FS diff, behavioral report, scoring)            │
│                                                                        │
│  ─ cross-cutting ─                                                      │
│  profiles   named security postures (locked-down / permissive / …)    │
│  policy     guardrails (egress, blast-radius, quotas, confirms)       │
│  audit      append-only structured log of every operation            │
│  store      experiment/run metadata + artifact index                  │
└───────────────────────────────┬───────────────────────────────────────┘
                                │  pluggable backend interface
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
   L1: CLI backend       L2: vminitd/vsock        L3: Containerization
   (`container` + JSON)   (gRPC, advanced)         (Swift/XPC, native)
        │                       │                       │
        └───────────────────────┴───────────────────────┘
                                │
                        Apple `container` runtime
                     (apiserver → per-VM vminitd)
```

### 3.2 Subsystem responsibilities

**forge** — the lifecycle/system core. Wraps every command in 2.1 and 2.6. Owns the `RunSpec`
object (the full set of run/create flags) and serializes it to backend calls. Single source of
truth for "what containers exist and what state are they in."

**mint** — image plane. Build (incl. multi-arch + secrets), registry auth, pull/push, and the
import/export tar bridge (interop with Docker images). Maintains a local image inventory with
provenance (where each image came from, when, digest).

**talos** — network guardian. The centerpiece of "control the networking." Builds named
topologies declaratively: define networks (internal/routed, custom subnets, v6), attach containers
with fixed MAC/MTU/IP, wire DNS (including *lying* to a sample via custom `--dns` + local
`system dns` domains pointing at a controlled resolver), and publish/forward ports & sockets.
Supports topology templates (victim+attacker+C2-honeypot on an internal subnet with no host route).

**icarus** — interaction & extraction. `exec` (the no-sshd interactive/scripted shell), bidirectional
`cp`, `logs` (live + boot), and `export` of the stopped FS. Also the place to optionally bake sshd
into an image and publish 22 for users who want real ssh/scp tooling.

**ariadne** — the thread that traces what happens inside the maze. Manages **init-image** variants
that load eBPF programs (syscall tracing, network capture, file-access auditing) at boot, invisible
to the workload; manages **kernel** variants (instrumented/vulnerable); applies capability and
hardening flags. Streams telemetry out (via published socket, vsock, or a captured volume) to minos.

**minos** — the judge. Consumes ariadne telemetry + `stats` fingerprints + before/after `export`
tar diffs + boot logs, and produces a structured behavioral report (filesystem deltas, network
attempts, syscall summary, resource profile) and an optional risk score.

**Cross-cutting:**
- **profiles** — named, reusable security postures. e.g. `detonation` = `--cap-drop ALL`,
  `--read-only`, internal-only network, no host route, ariadne-eBPF init image; `bench` = permissive.
- **policy** — guardrails enforced *before* any backend call: max concurrent VMs, total disk
  (checked via `system df`), egress allow/deny, mandatory `confirm` on destructive ops, image
  allow-lists. The agent-facing safety boundary.
- **audit** — append-only structured record of every operation, args, actor (human/agent/service),
  result. Tamper-evident; the forensic chain for "what did the agent do."
- **store** — experiment & run metadata (which image, profile, network, kernel, init-image),
  artifact index (tars, pcaps, reports), reproducibility manifest.

### 3.3 The backend interface (why L1/L2/L3 are swappable)

Every subsystem calls an abstract `Backend` with verbs like `run(spec)`, `exec(id, argv, opts)`,
`inspect(id)`, `export(id, path)`, `network_create(...)`, etc. Three implementations:

- **CliBackend (L1, default & complete):** shells `container … --format json`, parses structured
  output. Stable; the reference implementation.
- **VsockBackend (L2, optional):** talks vminitd gRPC over vsock for finer process control / live
  telemetry without `exec` overhead. Layered *on top of* L1 for lifecycle, L2 only for the hot path.
- **NativeBackend (L3, optional):** links the Containerization Swift package / XPC directly
  (model on `cctl`). Highest fidelity, tracks unstable API.

A capability-detection step at startup records which backend features are live on this host/macOS
version (e.g. networking present? kernel-set allowed? init-image supported?) and gates tools
accordingly.

---

## 4. The three consumer surfaces (equal weight)

All three are thin adapters over the **same** Core Engine façade. No business logic lives in an
adapter — this guarantees identical behavior across surfaces and one place to enforce policy.

### 4.1 CLI (`daedalus`) — humans
Verb-first ergonomics mapped to subsystems, e.g.:
```
daedalus lab create --profile detonation --net isolated   # forge+talos+profiles
daedalus run <image> --profile detonation --sample ./x    # forge+icarus
daedalus net topology apply ./victim-attacker-c2.yaml      # talos templates
daedalus trace <id> --ebpf syscalls,net,file               # ariadne
daedalus report <id>                                       # minos
daedalus destroy <id> --confirm                            # forge+policy
```
Outputs human tables by default, `--json` for scripting.

### 4.2 HTTP/REST API — services & web UIs
Resource-oriented: `/containers`, `/networks`, `/images`, `/profiles`, `/experiments`,
`/containers/{id}/exec|logs|stats|export|trace|report`. Bind localhost-only by default; auth
required before exposing. SSE/websocket endpoints for `logs --follow`, live `stats`, and streaming
ariadne telemetry. This is also the substrate a GUI (think the existing AppleContainerDesktop, but
research-flavored) would consume.

### 4.3 MCP server — autonomous agents
Tools map 1:1 onto engine verbs, typed and documented for tool-use. Critical agent-safety design:
- Destructive verbs (`destroy`, `kill`, `network delete`) require an explicit `confirm=true`.
- Network egress and "make this routable to the host" decisions are **policy-gated**, never
  defaulted on.
- A `health`/`capabilities` tool lets the agent discover what this host supports before acting.
- Detonation tools default to the `detonation` profile (cap-drop ALL, read-only, internal net).
- Every agent action flows through `audit`.

The same MCP server can be pointed at the user's own connectors when an experiment needs external
data, but that is opt-in per policy.

---

## 5. Reference workflows (what the platform is *for*)

### 5.1 Malware detonation (the flagship) — **implemented**

`Forge.detonate(sample, *, image, profile, name, command, observe_seconds, stats_interval, network_name, profile_kwargs)` (`daedalus/core/forge.py`, SPEC phase 3 §3.2) is the orchestrated loop, surfaced by the CLI `detonate`, the API `POST /detonate`, the MCP `daedalus_detonate`, and the Experiments UI report viewer. The loop:

1. **policy** gate — concurrency / disk / image, the same `_check_run` as `run`/`create`.
2. **isolation** — `talos` creates a disposable `--internal` network (`daedalus-detonation-<run>`; `network_name=` reuses an existing one). DNS is fully blocked (the `detonation` profile's `no_dns`).
3. **run** the VM detached with a keep-alive command (NOT the sample yet).
4. **baseline** — `icarus.export_fs` the pre-run FS to `<store>/<run_id>.pre.tar` (works on the running VM).
5. **deliver** — `icarus.copy_to` the sample to `/root/sample<ext>`, preserving its extension.
6. **detonate + observe** — `icarus.exec` runs `sh /root/sample…` (or an explicit `command`) while a sampler collects `forge.stats` every `stats_interval` s until exec completes or `observe_seconds` elapses.
7. **post-evidence** — boot + process logs, then the post-run export `<store>/<run_id>.post.tar`.
8. **judge** — `minos.analyze` diffs pre/post FS, folds in the stats snapshots + logs → `BehavioralReport` + risk score; persisted as `<store>/<run_id>.report.json`, store artifacts, and an `audit` entry.
9. **destroy** — `forge.destroy(run_id, confirm=True, report=<the rich report>)`; the disposable network is deleted in a `finally`. Failures mid-loop destroy the VM and network (best-effort) and re-raise `DetonationError` carrying `partial_evidence`.

**Design decision — no tmpfs, samples land in `/root`:** the `detonation` profile deliberately
carries **no tmpfs**. A tmpfs `/tmp` would shadow the disk layer there (a copied sample would be
invisible to `exec`) *and* hide runtime writes to `/tmp` from the post-run export — which would
blind the filesystem-delta evidence that is the whole point of the report. Samples are delivered
to `/root` for the same reason, so a dropper's runtime writes (e.g. `/root/dropped.txt`) land in
the diff. The VM is destroyed after analysis, so anti-persistence is moot.

The aspirational v1 sketch above differs in three ways that stay gated: ariadne's eBPF
init-image (`--init-image` absent in container 1.2.2), `--cap-drop ALL`/`--read-only` (phase 4
profile hardening), and a fake-DNS resolver (the profile currently blocks DNS outright).

### 5.2 Kernel fuzzing / escape research
`ariadne` boots a KASAN/KCOV/syzkaller-instrumented kernel (`-k`) per VM; `forge` runs disposable
fuzzer VMs in parallel; crashes captured via boot logs + export; each VM hardware-isolated so a
guest-kernel crash never touches the host. Reproduce known CVE escapes against the VM boundary to
characterize what does/doesn't cross it.

### 5.3 Network-deception lab
`talos` applies a topology template: `victim` + `attacker` + `c2-honeypot` on one internal subnet,
DNS pointed at a fake resolver, all egress captured. Study lateral movement and C2 behavior with
the sample believing it has real connectivity.

### 5.4 Supply-chain / image analysis
`mint` pulls or loads an OCI image; static layer scan (external scanner) + dynamic detonation;
`minos` diffs declared manifest vs actual runtime behavior to catch droppers that fetch payloads
at runtime.

### 5.5 Autonomous triage agent
MCP agent receives a suspicious artifact → calls `capabilities` → `run` (detonation profile) →
`trace` → `report` → reasons over the structured report → `destroy --confirm`. Each detonation is
hardware-isolated and disposable; policy caps the blast radius.

---

## 6. Build guide for an implementing agent

A dependency-ordered plan. Each milestone is independently testable.

**M0 — Host capability probe.** Detect `container` binary, macOS version, networking availability
(`network ls` succeeds?), kernel-set permission, init-image support. Emit a capability manifest.
*Test:* manifest matches `container system status` + `system version`.

**M1 — CliBackend + forge.** Implement the `Backend` interface (L1): `run/create/start/stop/kill/
delete/list/inspect/stats/system*`. Build the `RunSpec` serializer covering every run/create flag
in §2.1/§2.5. *Test:* round-trip create→inspect→stop→delete; JSON parsing of list/inspect/stats.

**M2 — icarus.** `exec`, `cp` (both directions), `logs` (+`--boot`, +`--follow` stream), `export`.
*Test:* exec returns output; cp in→exec cat→cp out byte-identical; export tar opens.

**M3 — mint.** `build` (Dockerfile, multi-arch, build-args, secrets), pull/push/save/load/tag/
delete/inspect/list, registry login, builder mgmt. *Test:* build → run → exec; save→load round trip;
Docker `save | container image load` interop.

**M4 — talos.** network create (internal/routed, subnet, v6, options), delete/prune/list/inspect;
per-container attach (MAC/MTU/IP), publish ports & sockets, full DNS control, `system dns`.
Topology templates (YAML → networks+attachments). *Test:* two containers on an internal net ping by
IP; host route absent when `--internal`; DNS override resolves to controlled IP inside the guest.

**M5 — profiles + policy + audit + store.** Named profiles (`detonation`, `bench`, …); policy gates
(quota, disk via `system df`, egress, mandatory confirms, image allow-list); append-only audit; run/
experiment store + reproducibility manifest. *Test:* policy blocks an over-quota run; audit records a
full run; manifest reproduces an identical run.

**M6 — ariadne.** Build init-image variants that load eBPF (syscall/net/file) at boot; kernel
variant management (`-k` + `system kernel set`); telemetry egress channel (published socket / vsock /
captured volume) → normalized event stream. *Test:* eBPF init image emits syscall events for a known
binary; chosen kernel reflected in `uname` inside guest.

**M7 — minos.** Pre/post FS tar differ; fold ariadne telemetry + `stats` fingerprint + boot log into
a structured behavioral report + score. *Test:* a benign vs a file-dropping sample produce
distinguishable reports.

**M8 — Surfaces.** CLI (verbs over façade), HTTP/REST (+SSE/ws for streams), MCP (typed tools,
confirm-gated destructive ops, capabilities tool, detonation defaults). *Test:* identical operation
produces identical engine calls + audit entries across all three surfaces.

**M9 — Optional backends.** VsockBackend (L2) for hot-path telemetry; NativeBackend (L3) via
Containerization/XPC. Behind capability flags. *Test:* same `Backend` contract tests pass.

---

## 7. Hard constraints & safety posture

- **Platform:** macOS 26 + Apple silicon only. Networking is macOS-26-only. Gate features by the M0
  capability manifest, never assume.
- **API churn:** build on L1 (CLI + JSON), stable within patch versions. L2/L3 track unstable
  surfaces — isolate them behind the backend interface so a flag change touches one file.
- **Isolation is strong but not absolute.** Per-VM hardware isolation is far better than Docker's
  shared kernel and is what makes autonomous detonation defensible — but the *host operator* still
  owns blast radius: run the daemon/server under a dedicated low-priv macOS user, prefer `--internal`
  networks for detonation, gate egress, cap concurrency/disk, require explicit confirms on
  destructive and network-exposing operations.
- **Agent boundary:** every agent action is policy-checked and audited. Destructive/egress/host-route
  operations are never defaulted on.
- **Provenance:** every run is reproducible from the store manifest (image digest, profile, network,
  kernel, init-image, args) and traceable in the audit log.

---

## 8. Naming map (subsystem ↔ myth ↔ function)

| Name | Myth | Function |
|---|---|---|
| **DAEDALUS** | Builder of the Labyrinth | The control plane |
| **Labyrinth** | The maze | A single sandbox VM unit |
| **forge** | Daedalus's workshop | Lifecycle + system core |
| **mint** | — (coinage) | Image build/registry plane |
| **talos** | Bronze guardian of Crete | Network guardian / topology |
| **icarus** | Flew into the maze's sky | Exec / cp / logs / export — entry & extraction |
| **ariadne** | Gave the thread through the maze | Instrumentation & telemetry (eBPF/kernel/init) |
| **minos** | King who judged the Labyrinth | Forensics, behavioral report, scoring |
| **machina** | deus ex machina — the god-machine | Persistent machine plane: stateful VMs (create/inspect/set/run/logs), init-capability advisor |

---

---

## 9. Implementation status vs container 1.2.2

The DAEDALUS control plane is fully implemented (Python, 24 modules, mypy strict,
MCP + API + CLI surfaces).  The host runs container CLI **1.2.2** (commit `0190097`) — the
same release Orchard pins.  Everything in this section is **implemented and working** against
that release; anything still gated lives in the "container 1.2.2 reality" note below.

### Working today (container 1.2.2)

| Subsystem | Capability | Surface |
|---|---|---|
| forge | create, run, start, stop, kill, delete, list, inspect, logs | CLI, API, MCP |
| forge | stats — one-shot CPU/mem/net/block/proc samples (`stats --no-stream --format json`), sampled in `StatsSample.sampled_at` for later rate derivation | CLI, API, MCP |
| forge (RunSpec) | `--network <name>`, `-p/--publish` (repeatable), `--cap-add`, `--cap-drop` serialised by `RunSpec.to_cli_args()` | CLI, API, MCP |
| icarus | exec (no `--` separator), logs (boot + stdio) | CLI, API, MCP |
| icarus | copy — `copy_from`/`copy_to` (bidirectional, `id:` prefix built in code), byte-identical round-trip | CLI, API, MCP |
| icarus | export — full-FS tar via `export_fs` (works on **running** containers in 1.2.2), indexed as a store artifact (`kind="fs"`) | CLI, API, MCP |
| mint | pull, push, save, load, tag, delete, inspect, list, prune, build | CLI, API, MCP |
| talos | network CRUD — create/list/inspect/delete (default plugin `container-network-vmnet`), topology materialization via `apply_topology` | CLI, API, MCP |
| machina | machine create/list/inspect/set/set-default/stop/delete/run/logs — persistent stateful VMs with init-capability advisor (name heuristic + log check) and confirm-gated delete | CLI, API, MCP |
| talos | DNS control (`--dns`, `--dns-domain`, `--dns-search`, `--no-dns`), system DNS (create/delete/list) | CLI, API, MCP |
| forge | prune — stop + reclaim disk via `container prune` | CLI, API, MCP |
| forge | detonate — the full orchestrated loop (policy gate → disposable internal network → pre/post FS export diff → sample delivery to /root → exec + stats sampling → Minos report + risk score → store artifacts + audit → destroy). §5.1, SPEC phase 3 | CLI, API, MCP |
| profiles | detonation, bench, fuzz, isolated, deception | CLI, API, MCP |
| policy | concurrency, disk, image allow/block-list, confirm gates (destroy, network delete, network expose, kernel) | Engine |
| audit | append-only, checksummed JSONL audit log | Engine |
| store | run manifests with artifact index (fs tars, reports) | Engine |
| capabilities | host probe (binary, version, feature gates incl. stats/copy/export/prune/network, flag inventory) | CLI, MCP |

### container 1.2.2 reality (what still isn't wrapped)

The 1.2.2 `network` subcommand is fully wired and wrapped (create/list/inspect/delete, default
plugin `container-network-vmnet`); `stats`, `cp`/`copy`, `export`, and `prune` are present and
wrapped by daedalus.  The `machine` subcommand is fully wired and wrapped by the `machina`
subsystem (create/list/inspect/set/set-default/stop/delete/run/logs, phase 2): machines require
**init-capable images** — the machine boots the image's own `/sbin/init` as PID 1, so plain
alpine/debian/ubuntu images will not boot (daedalus warns via the advisor and explains failures
from the boot logs); machine **backing containers are hidden by the CLI** (the daemon's
`container list` never shows them), and `container prune` does not touch machines.  The CLI also
ships `volume` and a `k8s` plugin — these exist but are **not yet wrapped** by daedalus.
Run/create flags beyond the RunSpec extension (`--rosetta`, `--virtualization`, `--init-image`,
`--publish-socket`, `--shm-size`, `--ulimit`) are present in 1.2.2 but not yet serialised (the
machine `--virtualization` flag *is* serialised on `MachineCreateSpec`); per-container kernels
(`-k` + `system kernel set`) work.

### Quality gates (all passing)

```
mypy --strict daedalus/     → Success: no issues found in 24 source files
pytest tests/               → 236 passed, 1 skipped (214 unit + 22 integration; the slow systemd machine-boot test skips on the container 1.2.2 exec limitation, upstream apple/container#1669)
Detonation §M7 test         → 2 live detonations: dropper vs no-op reports distinguishable, no VM/network leaks (tests/integration/test_detonation.py)
Import-time I/O             → zero subprocess calls
MCP (stdio)                 → 61 tools, handshake verified
MCP (cartograph federation) → discover + call verified
API (FastAPI)               → /health, /stats, /networks, /containers, /images, /profiles, /system/status, /detonate, /experiments
```

---

*DAEDALUS is a defensive security-research tool: malware analysis, kernel hardening research,
network-deception study, and supply-chain inspection, executed inside disposable hardware-isolated
VMs. The same isolation that enables safe detonation is the property the architecture is built to
preserve.*
