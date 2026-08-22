# SPEC Phase 5 — Stats sampling engine + sparklines

Status: **Approved for implementation** · Branch: `feature/ungate-container-1.2.2`

## 1. Objectives

Turn one-shot stats into a real observation engine (the port of Orchard's `StatsService` pattern, adapted to daedalus's L1 + Python stack):

1. **`StatsSampler`** — periodic `container stats --no-stream --format json` sampling with two-tick rate derivation (CPU% via existing `StatsSample.cpu_percent`), a bounded history ring per `(host, container_id)` with persistence across restarts.
2. **API surfaces** — `GET /stats/history?seconds=N`, SSE `GET /stats/stream` (the data spine for live UI).
3. **UI sparklines** — inline SVG polyline component (no chart dependency) on Dashboard tiles + Containers CPU/Memory cells.
4. **Detonation integration** — phase 3's inline sampler switches to the shared `StatsSampler` (single source of truth; no second sampling loop).

## 2. Design (ported from Orchard StatsService, L1-adapted)

- **Rate derivation**: previous raw sample per id + monotonic instants; `cpu_percent(prev)` (exists, phase 1). Memory is a gauge (no delta).
- **History**: ring buffer per `(host, id)` — `StatsHistory` class: `append(sample)`, `series(id, seconds)` (returns ordered list), cap ~300 points (~10 min at 2 s cadence); `host` is "local" today (data structure supports multi-host later — same note as orchard's).
- **Persistence**: `~/.daedalus/store/stats-history.json` (single JSON file, load-on-boot, save every N ticks like orchard's `StatsPersistence`); corrupted file → start empty (fail loudly in logs, not crash).
- **Sampler lifecycle**: started by the API lifespan as an asyncio background task; interval 2.0 s; skips ticks when no containers running (cheap); pauses when no consumer + no history requested (keep simple: always-on while server runs — the detonation path needs it on demand too). Forge/MCP/CLI use it through a shared singleton per process (`get_sampler(backend)`).
- **Machine stats**: explicitly OUT — the 1.2.2 CLI hides machine backing containers from `container list`/`stats` (verified), so backing-container re-keying is impossible on L1; documented as runtime-gated.
- **Module**: `daedalus/core/sampling.py` — `StatsSampleRate` (derived sample: id, cpu_percent, memory_bytes, mem_limit_bytes, net_rx_delta, net_tx_delta, block_r_delta, block_w_delta, num_processes, at), `StatsHistory`, `StatsSampler(start/stop/snapshot/series)`, `get_sampler()`.

## 3. Agent work breakdown

- **Wave 1 (1 agent):** `daedalus/core/sampling.py` + `tests/test_sampling.py` NEW — unit tests: two-tick rate math (incl. zero/negative delta guards), ring cap eviction, series() window filtering, persistence round-trip, corrupted-file recovery. Wire `Forge.detonate` (phase 3) to use `get_sampler()` instead of its inline loop (small edit to forge.py — coordinated, phase 3 will already be committed).
- **Wave 2 (1 agent, after Wave 1):** API (`api/server.py`) — sampler start in lifespan; `GET /stats/history?seconds=` + SSE `/stats/stream` (reuse the existing SSE patterns from logs/stream).
- **Wave 3 (1 agent, after Wave 2):** UI — `Sparkline` component (pure SVG, ~60 lines) in `ui/src/views/shared.tsx` (or `components/`); Dashboard tiles get sparklines (history fetch 10 s cadence); Containers CPU/Memory cells get mini sparklines; `npm run build` clean, lint at baseline.
- **Wave 4 (1 agent, after Wave 3):** integration (`tests/integration/test_sampling.py` — run a container under load (`dd if=/dev/zero of=/dev/null` via exec loop), assert history accumulates and CPU% > 0 at least once) + docs + macos-harness drive-through of the sparkline UI (orchestrator).

## 4. Success metrics

1. History accumulates while a busy container runs; CPU% rate derived correctly (matches `container stats` deltas within tolerance).
2. SSE stream yields samples to a live client; history endpoint serves persisted data after server restart.
3. Sparklines render on Dashboard + Containers; zero new lint/build findings.
4. All gates green; macos-harness verdict all-pass.

## 5. Non-goals

- Machine live-stats (L1 hides backing containers; runtime-gated).
- Alerting/thresholds, multi-host sampling, chart libraries.
