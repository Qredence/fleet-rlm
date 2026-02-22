# Group 5 — Telemetry + Settings Stabilization (QRE-316 to QRE-320)

## Scope

This group stabilizes the v0.4.8 web settings/telemetry experience across frontend and backend boundaries:

- consistent PostHog initialization defaults and env naming (`QRE-316`)
- anonymous-only frontend instrumentation (`QRE-317`)
- simplified settings UI showing only real settings (`QRE-318`)
- LM integration settings wired to runtime APIs (`QRE-319`)
- end-to-end telemetry preference propagation to backend AI analytics (`QRE-320`)

This group spans the widest set of layers in the milestone (frontend settings UI, frontend analytics wrapper, runtime API integration, backend analytics trace context, WebSocket execution paths, and docs/tests).

## Ticket Inventory

| Ticket | Title | Status | Priority | Labels | Duplicate? | Explicit blockers | Explicit blocked items |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QRE-316 | PostHog Web Telemetry Foundation: default project-owned config + env canonicalization | Triage | High | `v0.4.8`, `backend`, `observability`, `monitoring`, `enhancement`, `Frontend` | No | None | None |
| QRE-317 | Anonymous-Only PostHog Instrumentation Refactor (remove identify/email payloads) | Triage | High | `v0.4.8`, `ui`, `observability`, `enhancement`, `Frontend` | No | None | None |
| QRE-318 | Simplify Settings UI to Real Settings Only (single grouped settings surface) | Triage | High | `v0.4.8`, `ui`, `enhancement`, `Frontend`, `Design` | No | None | None |
| QRE-319 | Wire LM Integration Settings to Runtime APIs (LiteLLM copy + custom endpoint/key) | Triage | Medium | `v0.4.8`, `api`, `backend`, `enhancement`, `Frontend`, `ui` | No | None | None |
| QRE-320 | End-to-End Telemetry Preference Propagation (UI toggle disables backend AI analytics) + Tests/Docs | Triage | High | `v0.4.8`, `dspy`, `testing`, `backend`, `observability`, `Frontend`, `documentation`, `ui`, `enhancement`, `e2e`, `infrastructure`, `architecture` | No | None | None |

## Chain of Command (Dependencies)

### Explicit Linear blockers / duplicates

No explicit `blockedBy` / `blocks` relations are defined in Linear for this group, and there are no duplicate tickets.

### Inferred prerequisites (**Assumptive Logic**)

1. **`QRE-316` should precede `QRE-317` and `QRE-320`**
   - Stable frontend/backend PostHog initialization and env canonicalization should exist before privacy gating and end-to-end propagation are implemented/tested.

2. **`QRE-318` should precede or co-land with `QRE-319`**
   - `QRE-318` defines the simplified settings shell and visible sections.
   - `QRE-319` then mounts/filters runtime-backed LM fields within that shell.

3. **`QRE-317` + `QRE-318` + `QRE-316` should precede `QRE-320`**
   - `QRE-320` depends on a truthful UI toggle (`QRE-318`), frontend wrapper/no-op behavior (`QRE-317`), and stable telemetry initialization (`QRE-316`).

4. **`QRE-319` can land before `QRE-320`, but runtime field persistence semantics must be stable before expanding settings integration tests**
   - Both touch settings UI and runtime API plumbing.

### Sequencing rationale tied to file/module overlap

- Frontend settings shell overlap: `SettingsPage.tsx`, `SettingsDialog.tsx`, `SettingsPaneContent.tsx` (`QRE-318`, `QRE-319`, likely parts of `QRE-320`).
- Frontend telemetry overlap: `src/frontend/src/main.tsx`, analytics wrapper/new hook, multiple feature/page instrumentation call sites (`QRE-316`, `QRE-317`, `QRE-320`).
- Backend telemetry overlap: `analytics/trace_context.py`, `analytics/posthog_callback.py`, WS/chat execution paths (`server/routers/ws*.py`) for `QRE-320`, plus config defaults for `QRE-316`.

## Technical Deep Dive (Per Ticket)

### QRE-316 — PostHog Web Telemetry Foundation: defaults + env canonicalization

#### Technical Risks

- Frontend and backend defaults may diverge (host/key precedence mismatch), fragmenting telemetry behavior.
- Misconfigured project-owned defaults could cause runtime init failures if guardrails are weak.
- Alias support (`VITE_PUBLIC_POSTHOG_KEY`) may linger and create long-term ambiguity if not documented as temporary.

#### Edge Cases

- Only legacy env key present; canonical key absent.
- No PostHog env vars present in local/dev environments (default path must work safely).
- Backend analytics unavailable or explicitly disabled while frontend initialization succeeds.

#### Architectural Impact

- Establishes a single initialization contract for web telemetry across frontend and backend.
- Creates a stable foundation for privacy/opt-out enforcement (`QRE-317`, `QRE-320`).
- Reduces configuration drift between `.env.example`, frontend code, and backend analytics config.

#### Dependency Notes

- No explicit blocker.
- **Assumptive Logic:** should be first in this group because it normalizes initialization semantics used by downstream tickets.

#### Parallelization Notes

- Can run in parallel with `QRE-318` (settings shell simplification) with minimal file overlap.
- Coordinate with `QRE-317` if both introduce frontend telemetry abstraction changes around initialization.

### QRE-317 — Anonymous-Only PostHog Instrumentation Refactor

#### Technical Risks

- Missing a direct `posthog.capture(...)` or `identify(...)` call site leaves inconsistent telemetry behavior.
- Overly aggressive payload scrubbing removes useful non-PII dimensions and weakens analytics quality.
- Wrapper migration can break event timing/side-effect behavior in React components if effects are refactored poorly.

#### Edge Cases

- Components capturing events before PostHog client initialization is ready.
- Telemetry disabled state must no-op cleanly without throwing or changing UX behavior.
- Event payloads containing nested PII-like keys or inconsistent naming conventions.

#### Architectural Impact

- Moves instrumentation control into a centralized frontend wrapper/hook, improving policy enforcement and testability.
- Creates a clean insertion point for `QRE-320` preference gating.
- Aligns frontend telemetry with anonymous-by-default product expectations.

#### Dependency Notes

- No explicit blocker.
- **Assumptive Logic:** depends on stable initialization contract from `QRE-316` and should land before `QRE-320`.
- Related to `QRE-318` because the settings UI will expose telemetry semantics/copy.

#### Parallelization Notes

- Can run in parallel with `QRE-319` if settings UI telemetry toggle contract is not changing underfoot.
- Avoid backend suppression parallelism (`QRE-320`) until wrapper API and telemetry preference persistence contract are stable.

### QRE-318 — Simplify Settings UI to Real Settings Only

#### Technical Risks

- Removing category navigation may break assumptions in page/dialog wrappers or tests.
- Shared settings content for page and dialog may diverge if props/entrypoints are handled inconsistently.
- Placeholder removal can accidentally hide still-needed runtime controls without replacement.

#### Edge Cases

- Desktop vs mobile layout behavior for a single grouped surface.
- Deep-linking or navigation state that expects category identifiers.
- Existing tests/snapshots expecting placeholder category labels.

#### Architectural Impact

- Simplifies settings information architecture and reduces UI noise.
- Establishes the user-facing settings shell used by `QRE-319` and `QRE-320`.
- Encourages composable grouped settings sections instead of placeholder pane sprawl.

#### Dependency Notes

- No explicit blocker.
- **Assumptive Logic:** should precede or co-land with `QRE-319` to define the shell and section contract.
- Related to `QRE-316`/`QRE-317`/`QRE-320` through telemetry copy and toggle placement.

#### Parallelization Notes

- Can run in parallel with `QRE-316`.
- Parallel with `QRE-319` only if one owner defines the grouped settings container/section interfaces first.

### QRE-319 — Wire LM Integration Settings to Runtime APIs

#### Technical Risks

- Reused runtime form logic may accidentally expose admin/secret/runtime fields that should remain hidden.
- Masked API key handling may overwrite existing stored values if empty-string semantics are wrong.
- `APP_ENV` local-only write restrictions may be bypassed or represented inconsistently in the simplified UI.

#### Edge Cases

- Non-local environment where reads are allowed but writes must be blocked.
- Runtime endpoint schema drift vs frontend runtime types/helpers.
- Optional model field visibility and save payload filtering semantics.

#### Architectural Impact

- Reuses real runtime API plumbing (`useRuntimeSettings`, `/api/v1/runtime/*`) instead of inventing a second settings persistence path.
- Connects user-facing LM settings to existing backend runtime configuration primitives.
- Defines a clearer separation between user-facing LM fields and admin/runtime operations.

#### Dependency Notes

- No explicit blocker.
- **Assumptive Logic:** pair with `QRE-318`; the shell/section layout should be agreed before field wiring proceeds.
- Related to runtime endpoint/frontend type alignment work (`QRE-293`) but not explicitly blocked.

#### Parallelization Notes

- Parallel with `QRE-317` is usually safe (different concerns) if shared settings modules are not being refactored simultaneously.
- Parallel with `QRE-318` only under a defined section/component contract.

### QRE-320 — End-to-End Telemetry Preference Propagation (UI -> backend AI analytics)

#### Technical Risks

- Telemetry preference may not propagate across all request paths (HTTP vs WebSocket drift), producing inconsistent suppression.
- Context-local telemetry flags can be lost across async boundaries, causing intermittent backend event emission.
- Incorrect default parsing/persistence could disable analytics globally or ignore user opt-out.

#### Edge Cases

- Telemetry disabled in UI but backend `$ai_generation` still emits for WS-originated runs.
- Telemetry preference toggled mid-session (existing session context vs new session context semantics).
- Non-web CLI flows should remain unaffected unless explicitly expanded later.

#### Architectural Impact

- Introduces an end-to-end privacy control contract spanning frontend settings state, web API/WS metadata, backend analytics trace context, and DSPy callback emission.
- Strengthens truthfulness of the user-facing telemetry toggle.
- Provides a reusable pattern for request-scoped execution policy propagation across async runtime paths.

#### Dependency Notes

- No explicit blocker.
- **Assumptive Logic:** depends on `QRE-316` (stable init), `QRE-317` (frontend wrapper/no-op), and `QRE-318` (UI toggle surface); should follow those tickets.
- `QRE-319` is not a hard blocker, but shared settings integration test changes may be easier after `QRE-319` settles.

#### Parallelization Notes

- Preparation (backend context-manager design, test plan) can happen in parallel with `QRE-317`/`QRE-318`.
- Full implementation should not parallelize with unstable telemetry wrapper or settings state contracts.

## Execution Strategy for This Group

### Phase 1: Foundation

1. `QRE-316` — stabilize telemetry initialization/env naming/defaults (frontend + backend)
2. `QRE-318` — simplify settings UI shell and define grouped sections (theme, telemetry, LM integration)

### Phase 2: Features

1. `QRE-317` — migrate frontend instrumentation to anonymous-only wrapper/hook
2. `QRE-319` — wire LM integration fields into the simplified settings surface via runtime APIs

### Phase 3: End-to-End Policy Enforcement

1. `QRE-320` — propagate telemetry preference into backend execution/analytics contexts and add tests/docs

### Phase 4: Cross-Surface Validation

- Settings page + dialog parity checks (desktop/mobile)
- Telemetry enabled/disabled frontend capture behavior validation
- Backend `$ai_generation` suppression/emission validation by web session preference
- Runtime LM settings local-write / non-local-read-only behavior validation

## Parallel Tracks (Group-Local)

### Safe parallel work

- `QRE-316` with `QRE-318` (different primary files: init/config vs settings shell)
- `QRE-317` with `QRE-319` after settings section boundaries are stable

### Parallel with coordination

- `QRE-318` + `QRE-319`
  - Shared file risk: `SettingsPage.tsx`, `SettingsDialog.tsx`, `SettingsPaneContent.tsx`
  - Coordination rule: define grouped settings container + section component contract first
- `QRE-317` + `QRE-320`
  - Shared concern: telemetry preference semantics and frontend wrapper API
  - Coordination rule: freeze telemetry wrapper interface and persisted preference source-of-truth before backend propagation coding
- `QRE-316` + `QRE-317`
  - Shared concern: PostHog frontend initialization and wrapper expectations
  - Coordination rule: finalize init precedence (canonical env, alias, default fallback) first

### Unsafe / not recommended parallelism

- Full `QRE-320` implementation while `QRE-317` telemetry wrapper contract and `QRE-318` toggle placement/state contract are both still changing

## Integration / Handoff Points

- **To Group 1 (Codebase Hardening):** `QRE-296` can reduce dependency ambiguity in backend router/dependency wiring before `QRE-320` touches WS execution paths.
- **To Group 2 (RLM Assessment):** `QRE-301` live validation should include telemetry-enabled vs disabled behavior checks for web-originated runs where feasible.
- **To master README:** this group is a cross-layer critical path because `QRE-320` depends on both frontend and backend prerequisites.

## Validation Checklist

- [ ] `QRE-316` before `QRE-317`/`QRE-320` is documented as **Assumptive Logic**.
- [ ] `QRE-318` before/co-land with `QRE-319` is documented as **Assumptive Logic**.
- [ ] `QRE-320` dependency on `QRE-316` + `QRE-317` + `QRE-318` is explicitly captured.
- [ ] HTTP/WS propagation drift risk and async context propagation risk are documented for `QRE-320`.
- [ ] Group-local parallelization guidance distinguishes safe vs coordinated vs unsafe implementation windows.
