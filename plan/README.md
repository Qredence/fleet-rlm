# fleet-rlm implementation continuation

This directory keeps the current implementation continuation map. Older files in
`plan/` are useful historical notes, but this file is the durable status summary
for cleanup and remaining phase work.

## Source-of-truth constraints

- Product surfaces for this cleanup are Workbench, Volumes, and Settings.
- Retired taxonomy, skills, memory, and analytics routes remain retired unless the
  product contract is changed before implementation.
- Generated OpenAPI and frontend artifacts must be updated through the repo sync
  commands, never by hand.
- Daytona-backed runtime work stays under `src/fleet_rlm/runtime/` and
  `src/fleet_rlm/integrations/daytona/`.
- Frontend route/navigation changes must keep retired paths falling through to
  `/404`.

## Current Audit Plan

This audit verifies that closed phase claims match current code, tests, docs, and bundled
scaffold skills.

1. Build a phase-to-code evidence matrix from `plan/PLANS.md`, phase progress files,
   runtime code, tests, and agent-harness docs.
2. Check each phase for implementation, validation coverage, generated-artifact drift, and
   stale documentation.
3. Inventory `src/fleet_rlm/scaffold/skills/` and verify each skill uses current repo paths,
   command names, Daytona volume roots, runtime defaults, and efficient helper scripts.
4. Patch only verified gaps, then run focused tests plus docs/static lanes for touched areas.

### Audit Results

| Area | Result | Evidence |
| --- | --- | --- |
| Phase 0 | Verified | Cleanup targets remain removed or deactivated in active runtime contracts. |
| Phase 1 | Verified | Volume layout creates legacy roots plus `memories`, `knowledge`, `skills`, `sessions`, `logs`, and `uploads`; restore paths have focused tests. |
| Phase 2 | Verified | `build_chat_agent()` and `AgentRuntime` default to `EscalatingFleetModule`; rollback uses `FLEET_RLM_USE_ESCALATING_RUNTIME=false`. |
| Phase 3 | Verified after docs reconciliation | Essential tools, typed schemas, memory DB, knowledge index, and skill loading exist; Brave live evidence remains skipped without provider credentials. |
| Phase 4 | Verified after docs reconciliation | Sandbox concurrency uses `FLEET_MAX_CONCURRENT_SANDBOXES`, slot release is tested, and live concurrency verification is recorded as passed. |
| Phase 5 | Verified with one runtime fix | Versioned `memories/core.db`, knowledge index compatibility, retired public routes, and seeded skills exist; volume browsing now authorizes all durable roots. |
| Phase 6 | Verified | Runtime metadata and degraded fallback payloads are surfaced; analytics public routes remain retired. |
| Phase 7 | Verified | Runtime docs cover backup/restore, retries/failures, live verification commands, and drift checks. |
| Scaffold skills | Updated | Skills now describe current runtime defaults, durable roots, `.codex/rlm_state` helper paths, current test files, and script help behavior. |

## Phase status

| Phase | Status | Current continuation |
| --- | --- | --- |
| 0. Cleanup and simplification | Closed | Cleanup is represented by `PHASE-0-GAP-MATRIX.md`; no replay required. |
| 1. Session-scoped Daytona volume | Closed | Durable volume/session layout is implemented and live restore was verified. |
| 2. Unified DSPy agent | Closed | `EscalatingFleetModule` is the default factory/runtime path, with `FLEET_RLM_USE_ESCALATING_RUNTIME=false` as the rollback toggle. |
| 3. Essential tools | Closed with skipped provider evidence | Tool implementations, volume fallback, typed schemas, and unit tests are in place. Live Daytona layout verification passes; Brave web-search live evidence is skipped when `BRAVE_SEARCH_API_KEY`/`BRAVE_API_KEY` is absent. |
| 4. Child RLM and concurrency | Closed | Slot accounting uses a bounded semaphore, lifecycle release is double-release safe, and live concurrency verification passes at limit `2`. |
| 5. Persistent memory, knowledge, skills | Closed | `memories/core.db` is versioned/migratable, remote bootstrap stages SQLite work under `/tmp` before copying to the Daytona volume, knowledge indexes are versioned and legacy-compatible, and public memory/skills routes remain retired. |
| 6. Observability and continuity | Closed | Stream completion payloads include runtime metadata, degraded RLM fallback remains explicit, and session export/import preserves conversation summaries and loaded document paths without reviving analytics. |
| 7. Hardening and docs | Closed | Runtime docs cover live verification commands, backup/restore file sets, and retry/failure behavior. Final merged drift validation remains the closeout gate. |

## Required implementation order

1. Reconcile product and route contract drift.
   - Keep only Workbench, Volumes, and Settings as supported product surfaces.
   - Retire or hide Optimization and History routes/navigation.
   - Keep taxonomy, skills, memory, and analytics public paths retired.
   - Remove or isolate unreachable public memory router code after import checks.

2. Close Phase 2 default runtime wiring.
   - Make `EscalatingFleetModule` the canonical `build_chat_agent()` path.
   - Provide a rollback toggle that defaults to escalation on.
   - Add factory, runtime, and websocket tests for both default and rollback paths.
   - Surface RLM fallback as degraded metadata instead of silent success.

3. Run the safe `/fleet` batch.
   - Phase 2 runtime default wiring.
   - Phase 3 live tool closeout.
   - Phase 4 concurrency hardening.

4. Continue serial hardening.
   - Phase 5 persistence hardening.
   - Phase 6 observability and session continuity.
   - Phase 7 backup, restore, docs, and end-to-end validation.

5. Finish with a merged-state drift pass.
   - Run the narrow validation lanes for touched areas first.
   - Escalate to `make quality-gate` and release checks when the merged change
     touches contracts, docs, packaging, or release-sensitive code.

## Closeout requirements

- Record live Daytona and web-provider evidence when credentials are available.
- If credentials are missing, document the skipped live check explicitly.
- Keep docs indexes and AGENTS files aligned when product surfaces or workflows
  change.
- Regenerate OpenAPI and frontend API artifacts only through the documented sync
  commands when API contracts change.

## Latest validation evidence

- `uv run python scripts/live_daytona_verify.py` — pass with Daytona credentials.
- `FLEET_MAX_CONCURRENT_SANDBOXES=2 uv run python scripts/live_concurrency_verify.py` — pass.
- Live Brave web search — skipped because neither `BRAVE_SEARCH_API_KEY` nor
  `BRAVE_API_KEY` is configured in the local environment.
- Public taxonomy, skills, memory, and analytics HTTP paths remain retired by
  contract tests; Optimization and History frontend paths are intentionally
  unsupported and fall through to `/404`.
