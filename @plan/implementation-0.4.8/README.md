# v0.4.8 Milestone Analysis Pack (Linear -> Markdown)

## Milestone Scope Summary

This documentation pack analyzes Linear milestone **`v0.4.8`** for project **`Fleet-RLM`** and converts the ticket set into a dependency-aware execution strategy intended for **GPT-5.3-codex** implementation.

Snapshot basis:

- Project: `Fleet-RLM`
- Milestone: `v0.4.8`
- Milestone target date: **2026-03-01**
- Analysis snapshot date: **2026-02-22**
- Linear usage: read-only (`list_projects`, `get_milestone`, `list_issues`, `get_issue(includeRelations=true)`)

This pack includes:

- one Markdown file per thematic ticket group
- this master index/overview (`README.md`)
- milestone-wide dependency graph (explicit + inferred)
- critical path and parallel-track recommendations
- duplicate ticket handling policy
- top 10 risk register

## Phase Execution Tracker (v0.4.8)

This directory is now also the **operational tracker** for milestone execution (not just analysis). The Codex multi-agent bootstrap for milestone delivery is tracked as:

- `QRE-321` — Phase 0: Codex Multi-Agent Delivery System for v0.4.8

### Phase Status Board

| Phase | Label | Status | Tracking | Notes |
| --- | --- | --- | --- | --- |
| Phase 0 | `phase-0` | Merged | `QRE-321` | Codex multi-agent bootstrap, runbooks, docs, Playwright smoke baseline ([PR #74](https://github.com/Qredence/fleet-rlm/pull/74)) |
| Phase 1 | `phase-1` | Merged | existing milestone tickets | Foundation tickets merged via `codex/v0-4-8-phase-1-foundation` ([PR #75](https://github.com/Qredence/fleet-rlm/pull/75), merge `844fb9d0b274735864df4303318ee69d25467ad7`) |
| Phase 2 | `phase-2` | In Review | existing milestone tickets | Feature enablers implemented on `codex/v0-4-8-phase-2-feature-enablers` (PR pending; see `phase-logs/phase-02-feature-enablers-outcome.md`) |
| Phase 3 | `phase-3` | Planned | existing milestone tickets | Feature delivery |
| Phase 4 | `phase-4` | Planned | existing milestone tickets | Integration validation |

### Codex Multi-Agent Operating Layer (Phase 0 Bootstrap)

Phase 0 introduces a project-scoped Codex multi-agent workflow used to execute the remaining phases with repeatable quality gates and Linear synchronization.

Artifacts added by Phase 0:

- `.codex/config.toml` (multi-agent enablement + role registry)
- `.codex/agents/*.toml` (role contracts and routing guidance)
- `.codex/prompts/v0_4_8/*` (deterministic phase runbooks and templates)
- `@plan/implementation-0.4.8/phase-logs/*` (phase continuity and handoff logs)
- `@plan/implementation-0.4.8/templates/phase-outcome-template.md`

### How to Start a Phase (Operator Checklist)

1. Read this `README.md`.
2. Read the previous phase outcome log in `phase-logs/`.
3. Read the relevant group analysis file(s) in this directory.
4. Use Linear (`linear_ops`) to confirm milestone/cycle/phase labels and move active tickets to `In Progress`.
5. Execute the phase via `.codex/prompts/v0_4_8/phase_control.md`.
6. Record outcomes in a new phase log before PR open.

## Deliverables in This Pack

- `01_codebase_hardening_qre_296_299.md`
- `02_rlm_assessment_qre_300_301.md`
- `03_canvas_ux_rendering_qre_302_310.md`
- `04_postgres_schema_neon_qre_311_314.md`
- `05_telemetry_settings_stabilization_qre_316_320.md`

## Group Mapping

### Group 1 — Codebase Hardening (Backend/Architecture)

- `QRE-296`, `QRE-297`, `QRE-298`, `QRE-299`
- Focus: server DI/import topology, demo/prod boundaries, router stub cleanup

### Group 2 — RLM Assessment / Validation

- `QRE-300`, `QRE-301`
- Focus: deterministic RLM loop regression + live end-to-end tracing validation

### Group 3 — Canvas UX / Rendering (Graph + Timeline + Preview)

- `QRE-302` through `QRE-310` (including duplicates `QRE-303`, `QRE-308`)
- Focus: artifact graph readability, expanded diagnostics, typed timeline and preview rendering

### Group 4 — Postgres Schema / Neon Optimization

- `QRE-311`, `QRE-312`, `QRE-313`, `QRE-314`
- Focus: schema cleanup, RLM/DSPy + Modal infra persistence, Neon optimization conventions

### Group 5 — Telemetry + Settings Stabilization

- `QRE-316`, `QRE-317`, `QRE-318`, `QRE-319`, `QRE-320`
- Focus: telemetry foundation, anonymous-only instrumentation, simplified settings UI, runtime-backed LM settings, end-to-end opt-out propagation

## Ticket Inventory (All v0.4.8 Tickets)

Total tickets in this milestone analysis: **24** (including duplicates)

| Ticket | Group | Title | Status | Priority | Duplicate? | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| QRE-296 | Group 1 | Merge `dependencies.py` -> `deps.py` | Triage | Medium | No | Server DI/import topology hardening |
| QRE-297 | Group 1 | Split `signatures.py` into prod vs demo signatures | Triage | Low | No | Demo/prod boundary cleanup |
| QRE-298 | Group 1 | Gate `runners_demos.py` behind env flag | Triage | Low | No | Production schema/runtime surface hardening |
| QRE-299 | Group 1 | Consolidate empty router stubs into `routers/planned.py` | Triage | Low | No | Router package clarity |
| QRE-300 | Group 2 | Mock execution — simulate 3-step RLM run | Triage | High | No | Deterministic regression test |
| QRE-301 | Group 2 | End-to-end tracing — live long-document RLM query | Triage | High | No | Live integrated validation |
| QRE-302 | Group 3 | Tool name badge on step nodes | Triage | Medium | No | Canonical graph-node detail stream |
| QRE-303 | Group 3 | Token/model indicator on LLM nodes | Duplicate | Low | Yes (`QRE-302`) | **No Implementation Stream** |
| QRE-304 | Group 3 | Code preview on REPL nodes | Triage | Medium | No | `GraphStepNode` expanded UI |
| QRE-305 | Group 3 | Error detail overlay on failed nodes | Triage | Medium | No | `GraphStepNode` expanded UI |
| QRE-306 | Group 3 | Trajectory thought→action→observation view | Triage | High | No | Highest-value graph interpretability feature |
| QRE-307 | Group 3 | Edge elapsed time labels | Triage | Low | No | `ArtifactGraph` edge labeling |
| QRE-308 | Group 3 | REPL Zod payload parsing split | Duplicate | Medium | Yes (`QRE-240`) | **No Implementation Stream** |
| QRE-309 | Group 3 | Timeline contextual summaries via Zod schemas | Triage | Medium | No | `ArtifactTimeline` typed summaries |
| QRE-310 | Group 3 | Strongly-typed final output rendering | Triage | Low | No | `ArtifactPreview` typed rendering |
| QRE-311 | Group 4 | Remove deprecated skills taxonomy from Postgres schema | Triage | Unspecified | No | Explicit blocker for `QRE-312`/`QRE-313` |
| QRE-312 | Group 4 | Implement core RLM & DSPy tables in Postgres | Triage | Unspecified | No | Explicitly blocked by `QRE-311` |
| QRE-313 | Group 4 | Implement Modal infrastructure tracking in Postgres | Triage | Unspecified | No | Explicitly blocked by `QRE-311` |
| QRE-314 | Group 4 | Apply Neon Postgres performance optimizations | Triage | Unspecified | No | Best after schema additions stabilize |
| QRE-316 | Group 5 | PostHog telemetry foundation (defaults + env canonicalization) | Triage | High | No | Foundation for telemetry/privacy tickets |
| QRE-317 | Group 5 | Anonymous-only PostHog instrumentation refactor | Triage | High | No | Frontend wrapper/no-PII instrumentation |
| QRE-318 | Group 5 | Simplify Settings UI to real settings only | Triage | High | No | Settings shell/IA simplification |
| QRE-319 | Group 5 | Wire LM Integration Settings to runtime APIs | Triage | Medium | No | Reuse `useRuntimeSettings` / runtime endpoints |
| QRE-320 | Group 5 | End-to-end telemetry preference propagation | Triage | High | No | Cross-layer UI->backend policy enforcement |

## Cross-Group Dependency Graph (Explicit + Inferred)

### Explicit Linear dependencies (confirmed)

- `QRE-311` -> `QRE-312` (blocks)
- `QRE-311` -> `QRE-313` (blocks)
- `QRE-303` -> duplicate of `QRE-302`
- `QRE-308` -> duplicate of `QRE-240` (outside v0.4.8 canonical implementation stream)

### Inferred dependencies (**Assumptive Logic**)

#### Assessment sequencing

- `QRE-300` -> `QRE-301`
  - Deterministic mock regression before credential-gated live E2E validation.

#### Telemetry/settings sequencing

- `QRE-316` -> `QRE-317`
- `QRE-316` -> `QRE-320`
- `QRE-318` -> `QRE-319` (or co-land with a locked settings-shell contract)
- `QRE-316` + `QRE-317` + `QRE-318` -> `QRE-320`

#### DB schema sequencing

- `QRE-312` + `QRE-313` -> `QRE-314`
  - Optimize after schema additions are known.

#### Canvas sequencing

- `QRE-302` -> `QRE-303` (duplicate bookkeeping only; no implementation stream)
- Shared payload parsing helper decisions -> `QRE-309`, `QRE-310`
- `QRE-302` baseline node layout conventions -> easier execution of `QRE-304`/`QRE-305`/`QRE-306`

#### Cross-group operational sequencing

- Group 4 (schema/migrations) completion substantially increases the value of Group 2 `QRE-301` live E2E validation.
- Group 5 (`QRE-320`) should be complete before final integrated telemetry/privacy validation in `QRE-301` (where applicable).

### Dependency Notes for Implementers

- Explicit Linear relations are authoritative.
- Inferred dependencies are execution-risk recommendations based on repository file overlap and ticket scope, and are marked as **Assumptive Logic** throughout the group files.

## Global Critical Path

Recommended critical path (highest risk-reduction / highest dependency leverage):

1. `QRE-311` — remove deprecated skills schema (explicit blocker)
2. `QRE-312` and `QRE-313` — schema additions (serialized migrations)
3. `QRE-314` — Neon optimization pass after schema additions stabilize (**Assumptive Logic**)
4. `QRE-316` — telemetry foundation defaults/env canonicalization (**Assumptive Logic**)
5. `QRE-318` — simplified settings shell (**Assumptive Logic**, shell for telemetry/LM settings UX)
6. `QRE-317` — anonymous-only frontend instrumentation wrapper (**Assumptive Logic**)
7. `QRE-320` — end-to-end telemetry preference propagation (**Assumptive Logic**, depends on 316/317/318)
8. `QRE-300` — deterministic RLM regression test (can happen earlier; included as release-confidence gate)
9. `QRE-301` — final live integrated RLM E2E tracing validation (late-stage confidence run)

Notes:

- Group 3 (Canvas UX) is important but mostly not a blocker for other groups; it is a broad parallel track with intra-group merge-risk.
- Group 1 (Codebase hardening) is also mostly parallelizable, but `QRE-296` can reduce backend import ambiguity before `QRE-320` WS/router changes.

## Recommended Execution Phases (Milestone-Wide)

### Phase 1: Foundation

Goal: reduce ambiguity and establish stable base layers.

Recommended tickets:

- `QRE-296` (DI consolidation)
- `QRE-297` (prod vs demo signatures split)
- `QRE-299` (router planned-stub consolidation; coordinate with `QRE-296`)
- `QRE-300` (deterministic RLM mock test)
- `QRE-311` (deprecated schema cleanup)
- `QRE-316` (telemetry foundation defaults/env canonicalization)
- `QRE-318` (settings shell simplification)

### Phase 2: Feature Enablers

Goal: enable later features while minimizing rework.

Recommended tickets:

- `QRE-298` (demo runner/schema gating)
- `QRE-319` (LM settings via runtime APIs; pairs with `QRE-318`)
- `QRE-317` (anonymous-only frontend instrumentation wrapper)
- `QRE-312` (core RLM/DSPy DB tables; after `QRE-311`)
- `QRE-313` (Modal infra tracking DB schema; after `QRE-311`)
- `QRE-302` (graph tool badge baseline)
- Duplicate bookkeeping confirmations: `QRE-303`, `QRE-308` (**No Implementation Stream**)

### Phase 3: Feature Delivery (Canvas + Settings/Telemetry Integration)

Goal: ship user-visible improvements and enforce cross-layer policy.

Recommended tickets:

- `QRE-304`, `QRE-305`, `QRE-306` (shared `GraphStepNode` expansion features)
- `QRE-307` (edge elapsed labels)
- `QRE-309` (timeline typed summaries)
- `QRE-310` (preview typed final output rendering)
- `QRE-320` (telemetry preference propagation UI -> backend AI analytics; after `QRE-316`/`317`/`318`)
- `QRE-314` (Neon optimization pass after schema additions stabilize)

### Phase 4: Integration Validation & Release Confidence

Goal: validate the integrated system after schema + telemetry + UI changes.

Recommended tickets:

- `QRE-301` (live long-document E2E tracing; run late)
- Final milestone regression checks and doc sync (fed by all group analyses)

## Global Parallel Tracks

### Safe / mostly independent tracks

1. **Canvas UX Track** (`QRE-302`, `QRE-304`-`QRE-310`, excluding duplicates)
   - Primary area: frontend artifact components and shared payload parsers.
   - Main risk is intra-track file overlap, not cross-milestone blocking.

2. **DB Schema Track** (`QRE-311`-`QRE-314`)
   - Primary area: backend DB models + Alembic migrations.
   - Highly isolated from frontend work, but internally migration sequencing is strict.

3. **Telemetry/Settings Track** (`QRE-316`-`QRE-320`)
   - Primary area: frontend settings UI + backend analytics/WS execution context.
   - Cross-layer but largely independent from DB schema and canvas UI tracks.

4. **RLM Assessment Track (early mock)** (`QRE-300`)
   - Primary area: tests/mocks.
   - Can run early and in parallel with most implementation tracks.

5. **Codebase Hardening Track** (`QRE-296`-`QRE-299`)
   - Primary area: backend refactors and production-surface cleanup.
   - Mostly parallelizable, with shared import/router touchpoints requiring coordination.

### Parallel with coordination (explicit file-overlap callouts)

- `QRE-296` and `QRE-299` can run in parallel **only with coordination**
  - Overlap: `src/fleet_rlm/server/main.py`, router import/registration topology

- `QRE-318` and `QRE-319` can run in parallel **only if** one owner defines the simplified settings container contract first
  - Overlap: `SettingsPage.tsx`, `SettingsDialog.tsx`, `SettingsPaneContent.tsx`

- `QRE-304` / `QRE-305` / `QRE-306` can run in parallel **only if** `GraphStepNode` subcomponent boundaries are agreed
  - Overlap: `GraphStepNode.tsx`, shared payload extraction helpers

- `QRE-312` and `QRE-313` can parallelize model design, but **Alembic migration generation/application must be serialized**
  - Overlap: `src/fleet_rlm/db/models.py`, Alembic migration files

- `QRE-317` and `QRE-320` should not parallelize backend suppression logic until telemetry wrapper + preference contract is stable
  - Overlap concern: telemetry semantics and propagation contract, not just files

## Duplicate Ticket Handling Policy (v0.4.8)

### Policy

- Duplicates **remain listed** in inventories and dependency graph sections.
- Duplicates are marked **No Implementation Stream**.
- Duplicates must reference the canonical ticket and include a “do not schedule independently” note.

### Tickets covered by this policy

- `QRE-303` -> duplicate of `QRE-302`
- `QRE-308` -> duplicate of `QRE-240` (outside v0.4.8 canonical implementation)

### Why this matters

This milestone contains a large canvas UX cluster; duplicate tickets can easily cause parallel implementation overlap and merge churn if not explicitly suppressed in the plan.

## Risk Register Summary (Top 10 Milestone Risks)

1. **DB migration conflicts and invalid upgrade path in Group 4**
   - Trigger: parallel Alembic autogen for `QRE-312` and `QRE-313`
   - Mitigation: serialize migration generation/application; designate one migration integrator

2. **Telemetry preference drift across HTTP vs WebSocket execution paths (`QRE-320`)**
   - Trigger: telemetry flag propagated on one path only
   - Mitigation: explicit propagation tests for both request/session flows; trace-context validation

3. **Async context propagation loss for backend analytics suppression (`QRE-320`)**
   - Trigger: context-local telemetry flag not preserved across async execution boundaries
   - Mitigation: unit tests around trace-context context managers and callback suppression behavior

4. **`GraphStepNode` merge conflicts in canvas feature wave (`QRE-304`/`305`/`306`)**
   - Trigger: parallel edits to the same expanded-node rendering logic
   - Mitigation: predefine subcomponent boundaries/section contracts before parallel work

5. **Payload schema drift causing inconsistent canvas rendering (`QRE-304`/`306`/`309`/`310`)**
   - Trigger: frontend parsers diverge from runtime event payload shapes
   - Mitigation: shared parsing helpers + defensive fallback paths + mixed-payload smoke tests

6. **Production schema/task surface still exposing demos after `QRE-298`**
   - Trigger: runtime dispatch gating implemented but request schema not tightened (or vice versa)
   - Mitigation: validate both startup/runtime path and advertised request schema

7. **Server import/circular dependency regressions from `QRE-296`**
   - Trigger: moving dependency helpers and agent construction without preserving lazy boundaries
   - Mitigation: import/startup checks and targeted router import tests early in the group

8. **Telemetry foundation misconfiguration (`QRE-316`)**
   - Trigger: frontend/backend default host/key precedence divergence or broken alias fallback
   - Mitigation: explicit precedence tests for canonical env key, alias key, and no-env defaults

9. **Neon optimization overreach in `QRE-314`**
   - Trigger: invasive PK/type changes exceeding release risk budget
   - Mitigation: document applied vs deferred optimizations; prefer low-risk index/type wins in v0.4.8

10. **Late-stage E2E validation ambiguity (`QRE-301`)**
   - Trigger: running live validation during active schema/telemetry churn, making failures hard to attribute
   - Mitigation: execute `QRE-301` after major changes settle; separate harness prep from final run

## How to Use This Pack (for GPT-5.3-codex)

1. Start with the group file for your assigned workstream.
2. Apply the group-local `Chain of Command` first (explicit dependencies, then **Assumptive Logic** sequencing).
3. Follow the group `Execution Strategy` phases.
4. Respect the `Parallel Tracks` section before starting parallel branches/tasks.
5. Return to this README for cross-group critical path and integration validation timing.

## Verification Notes for This Documentation Pack

- Coverage target: all 24 v0.4.8 tickets represented exactly once in group inventories (duplicates included).
- Duplicate suppression target: `QRE-303`, `QRE-308` marked **No Implementation Stream**.
- Explicit blocker preservation target: `QRE-311 -> QRE-312`, `QRE-311 -> QRE-313` documented here and in Group 4.
- Inferred dependencies are labeled **Assumptive Logic** in the relevant group files.
