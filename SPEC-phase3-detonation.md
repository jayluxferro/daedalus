# SPEC Phase 3 — Close the detonation loop (the flagship)

Status: **Approved for implementation** · Branch: `feature/ungate-container-1.2.2` (phases 1–2 committed as `7ef9311`, `a8182e0`)

## 1. Objectives

DAEDALUS exists for §5.1 of ARCHITECTURE.md: policy → isolate → detonate → observe → export → judge → destroy. The substrate now exists (stats/copy/export/networks — phase 1) but the loop is unwired: `Minos.analyze`'s only caller passes logs, and no orchestrated detonation entrypoint exists. This phase:

1. **`Forge.detonate(sample)`** — the orchestrated 8-step workflow as a first-class engine verb.
2. **Wire the evidence legs** — pre/post FS export diff, stats snapshots during the run, boot/process logs → `Minos.analyze` → `BehavioralReport` + risk score.
3. **Stats→minos mapping** — one adapter from `StatsSample` (phase-1 shape) to minos's ResourceProfile inputs (rate-derived CPU%, bytes, pid count, duration).
4. **Surfaces** — CLI `detonate`, API `POST /detonate`, MCP `daedalus_detonate` (the §5.5 autonomous-triage verb), report rendering in the Experiments UI view.
5. **The §M7 acceptance test** — a file-dropping sample and a no-op sample produce **distinguishable reports**, live.

## 2. Verified environment (1.2.2, captured live)

- `container export -o <path> <id>` works on RUNNING containers (phase 1 verified).
- `container stats --no-stream --format json` → `StatsSample` shape (phase 1 §2.1); cumulative `cpu_usage_usec` — rates need two samples (`StatsSample.cpu_percent(prev)` exists).
- `icarus.copy_from/copy_to`, `forge.stats`, `machina.*` all live (phases 1–2).
- `--read-only`, `--cap-drop` exist in 1.2.2 run flags (phase-4 profile hardening will use them; phase 3 uses the current `detonation` profile: tmpfs /tmp,/var/tmp + no_dns).
- Detached alpine containers self-terminate without a keep-alive command — forge already defaults `DEFAULT_DETACHED_COMMAND = ["tail", "-f", "/dev/null"]`.
- `Minos._analyze_stats` currently expects dict keys `cpu_percent`, `memory_usage`, `pid_count`, `block_write`, `block_read`, `timestamp` — a mismatch with `StatsSample.to_dict()` (`cpu_usage_usec`, `memory_usage_bytes`, `num_processes`, `block_write_bytes`, `block_read_bytes`, `sampled_at` monotonic). **One mapping adapter fixes this; do not fork shapes.**

## 3. Architecture decisions

1. **`detonate` lives on Forge** (lifecycle orchestrator; it already owns policy/store/audit and `destroy`). Signature:
   ```python
   async def detonate(
       self,
       sample: str,                          # host path to the sample
       *,
       image: str = "alpine:latest",
       profile: str = "detonation",
       name: str | None = None,
       command: list[str] | None = None,     # how to execute the sample; default: sh <basename>
       observe_seconds: float = 30.0,
       stats_interval: float = 2.0,
       network_name: str | None = None,      # None → auto internal network via talos
   ) -> dict[str, Any]
   ```
   Returns `{"run_id", "report": <BehavioralReport.to_dict()>, "report_path", "artifacts": [...], "risk_score"}`.

2. **The loop** (all engine-internal, every step audited):
   1. Policy: `_check_run(image, **profile kwargs)` (concurrency/disk/image — reused as-is).
   2. Isolation: if `network_name` is None, `talos.apply_topology`/`network_create` an `--internal` network (`daedalus-detonation-<run>`), attach via `RunSpec.network`.
   3. `forge.run(image, profile=profile, detach=True, network=..., command=["tail","-f","/dev/null"])` (keep-alive, NOT the sample yet).
   4. **Baseline**: `icarus.export_fs(run_id, <store.root>/<run_id>.pre.tar)`.
   5. **Deliver**: `icarus.copy_to(run_id, sample, "/tmp/sample" + suffix)`. Preserve extension (`.elf`/`.sh`/`.py`) — use `os.path.splitext`.
   6. **Detonate + observe**: two concurrent asyncio tasks — (a) `icarus.exec(run_id, ["sh", "/tmp/sample..."] or explicit command, timeout=observe_seconds)` (check=False style; icarus.exec already returns ExecResult non-raising); (b) a sampler loop `while: forge.stats(run_id)` every `stats_interval`, collecting `StatsSample` dicts until (a) completes or `observe_seconds` elapses (bounded; use `asyncio.wait_for` on a).
   7. **Post-evidence**: process + boot logs (`icarus.logs`), post-export `<store.root>/<run_id>.post.tar`.
   8. **Judge**: `minos.analyze(run_id, image=..., pre_tar=..., post_tar=..., stats_data=<sampled dicts>, boot_log=..., )` → report.
   9. **Persist**: write `<store.root>/<run_id>.report.json`, `store.add_artifact` for pre/post tars + report; audit `"detonate"` with risk_score.
   10. **Destroy**: `destroy(run_id, confirm=True, report=<the rich report>)` — NEW optional param `report: BehavioralReport | None = None` on `destroy`: when provided, skip its logs-only `minos.analyze` and write the provided report (no duplication, no overwrite-by-thinner-report).
   Cleanup on any failure: destroy with confirm, re-raise wrapped in a `DetonationError` carrying partial evidence paths.

3. **Stats adapter** — new pure function `daedalus.core.minos: stats_samples_to_snapshots(samples: list[StatsSample]) -> list[dict]` producing the dict keys `_analyze_stats` expects: `cpu_percent` (pairwise `cpu_percent(prev)`, first sample 0.0), `memory_usage`, `pid_count`, `block_write`, `block_read`, `timestamp` (str of `sampled_at` for ordering; duration computed from first/last). `_analyze_stats` stays untouched except its duration branch must tolerate `timestamp` being monotonic strings — change it to compute `duration = last.sampled_at - first.sampled_at` style: cleaner: adapter emits `"monotonic": <float>` and `_analyze_stats` computes duration from `snapshots[-1]["monotonic"] - snapshots[0]["monotonic"]` when present, else falls back to the existing datetime parse. ONE small edit to `_analyze_stats`, documented.

4. **Network lifecycle**: detonation networks are disposable — after destroy, `talos.network_delete(name, confirm=True)`; on failure too (finally). No network leak on aborted runs.

5. **Surfaces are thin**: CLI `detonate <sample>` (options: --image, --profile, --name, --observe N, --command overrides; prints report.summary()); API `POST /detonate` blocking (sample path must be host-local — same trust domain as the rest of the API; document); MCP `daedalus_detonate` returns `_ok({...report...})` and is the agent-facing flagship. UI: Experiments view gains a report viewer (fetch the `.report.json` artifact — API: extend `GET /experiments/{run_id}` to include `report` when the artifact exists, or add `GET /experiments/{run_id}/report` — choose the latter, cleaner).

## 4. Agent work breakdown

- **Wave 1 (2 agents, parallel):**
  - **W1 — minos adapter + destroy param** (`daedalus/core/minos.py`, `daedalus/core/forge.py` destroy signature only, `tests/test_minos.py` NEW): `stats_samples_to_snapshots` pure fn + unit tests (empty, single, pairwise rates, mapping keys); `_analyze_stats` monotonic-duration branch + tests; `destroy(report=None)` param + unit test (report passthrough, no logs-only re-analyze).
  - **W2 — detonation loop** (`daedalus/core/forge.py` `detonate` method + `DetonationError` in `daedalus/core/exceptions.py`, `tests/test_forge.py` additions): the orchestration per §3.2 with MockBackend recording call ORDER (assert create/run → export pre → copy → exec+stats interleave → logs → export post → delete; network cleanup in finally; failure path destroys + re-raises). Audit entries asserted.
- **Wave 2 (3 agents, parallel, after Wave 1):**
  - **W3a — CLI** (`daedalus/cli/main.py`): `detonate` command, prints report summary + risk label, `--json` for full dict.
  - **W3b — API** (`daedalus/api/server.py`): `POST /detonate` (pydantic DetonateRequest{sample, image?, profile?, name?, observe_seconds?, stats_interval?}) → detonate result dict; `GET /experiments/{run_id}/report` (reads the stored report artifact; 404 when absent).
  - **W3c — MCP** (`daedalus/mcp/server.py`): `daedalus_detonate` (args mirror DetonateRequest; readOnlyHint=False, destructiveHint=True — it creates and destroys a VM; docstring frames it as the safe-detention verb; returns report dict via `_ok`).
- **Wave 3 (1 agent, after Wave 2):**
  - **W4 — UI** (`ui/src/api/experiments.ts`, `ui/src/views/Experiments.tsx`): report viewer (fetch `/experiments/{id}/report`, render risk score + factors + findings + FS counts as cards; reuse GlassBox/Badge).
- **Wave 4 (1 agent, after Wave 3):**
  - **W5 — integration + docs** (`tests/integration/test_detonation.py` NEW, README, ARCHITECTURE): THE §M7 test — build two samples in tmp: (a) benign file-dropper (`#!/bin/sh; echo hi > /tmp/pwned.txt; curl-free` — plain sh writes a file, sleeps 2), (b) no-op (`#!/bin/sh; exit 0`); run `forge.detonate` on each (alpine, detonation profile, observe ~15s); assert (a).report has filesystem total_changes > 0 and mentions added file; assert (b).report has ~0 changes; assert risk_score(a) >= risk_score(b) and the two report dicts differ; cleanup assertions (no leftover networks/containers). Docs: README quick-start detonate example + the loop diagram; ARCHITECTURE §5.1 marked implemented.
- **Wave 5 (orchestrator):** full gates + macos-harness Safari drive-through: detonate via the UI/API path with a benign dropper sample, report renders in Experiments, phase-1/2 regression spot-check.

## 5. Success metrics

1. `daedalus detonate <dropper.sh>` prints a report with non-zero FS changes + LOW/MEDIUM risk; a no-op sample prints ~zero changes; reports are **distinguishable** (the §M7 contract).
2. Evidence artifacts (pre/post tars + report) land in the store and appear in Experiments.
3. MCP `daedalus_detonate` returns the full report dict to an agent (handshake-verified).
4. Failed detonations destroy the VM and clean the disposable network (no leaks) and re-raise with partial-evidence context.
5. All quality gates green; macos-harness UI verdict all-pass.

## 6. Non-goals (explicit)

- No eBPF/init-image telemetry (`--init-image` still absent in 1.2.2; ariadne stays gated).
- No detonation of non-ELF/non-script artifacts yet (v1 runs `sh <sample>` or an explicit command).
- No async/background detonation queue in the API (blocking call; queue is a later concern).
- No profile hardening changes (that is phase 4).
