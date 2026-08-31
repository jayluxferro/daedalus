# SPEC Phase 9 — System plane completion + parity polish

Status: **Approved** · Branch: main (post-P8)

## 1. Objectives

Close the final unwrapped runtime surface (`system property`, `system version`) and bring the remaining Orchard-parity features into daedalus's mission scope:

1. **`system property`** — get/set/list system property values (1.3.1 verb family, currently unwrapped).
2. **`system version`** — structured version verb.
3. **Orchard-parity polish** (mission-scoped): Registries UI view, multi-pane log viewer, Docker Hub image search (host-side API, like Orchard), SSH-agent forwarding UX note.

## 2. Verified surface (captured live, 1.3.1)

- `system property` subcommands exist (run `container system property --help` to enumerate: expected get/set/list — verify live before coding).
- `system version --format json` exists (verified in the capture: flags --format).
- `container system df --format json` exists (backend already wraps system_df — verify whether --format json is emitted; the phase-1 note said df used OS-level fallback; re-check the live `container system df --format json` output and update the backend if structured output now exists).

## 3. Architecture

1. **Backend verbs**: `system_property_list() -> dict[str, str]`, `system_property_get(key) -> str`, `system_property_set(key, value) -> None`, `system_version() -> dict` (--format json). Wire per the live help; gate with existing system-family capability or add `system_property` tri-state probe mirroring the others.
2. **Engine**: `forge.system_property_*` wrappers (system plane lives on forge) with audit on set; `forge.system_version()` folding into `system_status`.
3. **Surfaces**: CLI `system` sub-app (`property get/set/list`, `version`); API `GET /system/version`, `GET/POST /system/properties[/{key}]`; MCP `daedalus_system_property_list/get/set` + `daedalus_system_version`; UI Dashboard gains a System section (version + property key-values, data-driven).
4. **Registries UI view** (`views/Registries.tsx`): list login state (registry default inspect), login form (server/username/password via --password-stdin), logout, default set/unset — mirrors the existing registry API endpoints.
5. **Multi-pane logs**: the Logs view gains container multi-select with per-container colored panes (orchard-style split panes) — moderate UI scope; implement as 2–4 panes grid.
6. **Docker Hub search** (`mint.image_search(query, limit)`) — host-side HTTPS call to hub.docker.com search API (no auth); CLI `image search <query>`, API `GET /images/search?q=`, UI search box in Images view pull modal. Respect the existing no-new-dependencies rule (stdlib urllib).

## 4. Waves

- **W1 (1 agent)**: backend system property/version verbs + parsers + capability gate + forge wrappers + tests.
- **W2 (3 agents, parallel, after W1)**: CLI+API+MCP surfaces.
- **W3 (3 agents, parallel, after W2)**: UI (Dashboard System section + Registries view + Images search + multi-pane Logs).
- **W4 (1 agent, after W3)**: integration + docs.
- **W5 (orchestrator)**: gates + macos-harness drive-through + commit + push.

## 5. Success metrics

1. `daedalus system property set foo bar` round-trips through list/get on all surfaces.
2. `daedalus system version` matches `container system version --format json`.
3. Registry login via UI persists; Docker Hub search returns real results in the pull modal.
4. Multi-pane logs show two containers side-by-side with distinct colors.
5. Gates green; UI drive-through all-pass.

## 6. Non-goals

- No AI-model bridging (orchard's identity feature; daedalus's model-adjacent story is the agent-facing MCP).
- No menu-bar app or global command palette (desktop-UX polish outside the control-plane scope).
- No in-cluster workload UI beyond the phase-8 clusters view.
