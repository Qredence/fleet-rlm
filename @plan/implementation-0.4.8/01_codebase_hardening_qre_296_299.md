# Group 1 — Codebase Hardening (QRE-296 to QRE-299)

## Scope

This group covers internal backend/server hardening work intended to reduce architectural ambiguity and tighten release-facing boundaries before broader v0.4.8 feature work lands.

Included tickets:

- `QRE-296` — Consolidate FastAPI dependency modules (`dependencies.py` -> `deps.py`)
- `QRE-297` — Separate production vs demo DSPy signatures
- `QRE-298` — Gate demo runners/task types from default production server surface
- `QRE-299` — Consolidate empty router stubs into `routers/planned.py`

Primary outcomes:

- Lower import/circular-dependency risk in server boot/runtime paths
- Cleaner distinction between production and demo code paths
- Reduced API/schema surface ambiguity for production deployments
- Improved server router package navigability for follow-on milestone work

## Ticket Inventory

| Ticket | Title | Status | Priority | Labels | Duplicate? | Explicit blockers | Explicit blocked items |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QRE-296 | Merge `dependencies.py` -> `deps.py` (eliminate dual DI files) | Triage | Medium | `v0.4.8`, `backend`, `architecture`, `developer-experience`, `agent-framework`, `agent` | No | None | None |
| QRE-297 | Split `signatures.py` into prod vs demo signatures | Triage | Low | `v0.4.8`, `backend`, `developer-experience` | No | None | None |
| QRE-298 | Gate `runners_demos.py` behind env flag | Triage | Low | `v0.4.8`, `backend`, `architecture` | No | None | None |
| QRE-299 | Consolidate empty router stubs into `routers/planned.py` | Triage | Low | `v0.4.8`, `backend`, `developer-experience` | No | None | None |

## Chain of Command (Dependencies)

### Explicit Linear blockers / duplicates

No explicit `blockedBy` / `blocks` relations are defined in Linear for this group.

### Inferred prerequisites (**Assumptive Logic**)

1. **`QRE-296` should run before or be coordinated with `QRE-299`**
   - Both touch server import topology (`src/fleet_rlm/server/main.py`, router imports, module load paths).
   - Running `QRE-299` first can be valid, but increases churn if `QRE-296` simultaneously rewires imports.

2. **`QRE-297` should precede `QRE-298`**
   - `QRE-297` isolates demo signatures from production signatures.
   - `QRE-298` then gates demo runners/schemas without mixing code movement and behavior gating in one step.

3. **`QRE-298` should be completed before release-facing schema audits in the telemetry/settings track**
   - This reduces noise in production request schemas and avoids accidental demo task exposure while documenting runtime settings/UI changes.

### Sequencing rationale tied to file/module overlap

- `QRE-296` is the highest merge-risk in this group because it affects the dependency layer used by multiple routers (`server/routers/*`) and likely `server/main.py`.
- `QRE-299` overlaps import/registration points but is otherwise a contained cleanup.
- `QRE-297` and `QRE-298` overlap on `src/fleet_rlm/signatures.py`, `src/fleet_rlm/runners_demos.py`, and server schema/task registration paths, so sequencing them reduces hidden import breakage.

## Technical Deep Dive (Per Ticket)

### QRE-296 — Merge `dependencies.py` -> `deps.py` (eliminate dual DI files)

#### Technical Risks

- Circular imports if agent/runtime construction is moved without preserving lazy boundaries.
- Runtime behavior drift if request-scoped vs app-scoped dependency semantics change during refactor.
- Latent external/internal imports may still point at `server/dependencies.py`.

#### Edge Cases

- Compatibility shim requirement if non-router code imports `get_react_agent` from the old module.
- Import side effects during server startup (`server.main`) when routers are imported eagerly.
- WebSocket helper modules (`ws_*`) may indirectly rely on the existing import graph.

#### Architectural Impact

- Positive long-term effect: a single canonical dependency module clarifies ownership and simplifies future auth/runtime refactors.
- Encourages moving heavy object construction out of route glue and into factories/services.
- Reduces maintenance ambiguity in the server layer for future contributors/agents.

#### Dependency Notes

- No explicit Linear blocker.
- **Assumptive Logic:** treat as a group-first item because it stabilizes import topology for `QRE-299` and reduces risk for server-adjacent work in `QRE-316`/`QRE-320`.

#### Parallelization Notes

- Can run in parallel with `QRE-297` if different owners avoid touching shared server startup/import files.
- Should not be uncoordinated parallel work with `QRE-299` due overlap in router import registration paths.

### QRE-297 — Split `signatures.py` into prod vs demo signatures

#### Technical Risks

- Hidden imports (tests/examples/demo tools) may still reference moved demo signatures.
- Import path churn can cause runtime failures if `runners_demos.py` and other modules are not updated atomically.
- Production code may have implicit dependency on demo signatures that is not obvious from naming.

#### Edge Cases

- Shared helper constants/types used by both prod and demo signatures may need a third neutral module to avoid duplication.
- Type checking may fail if circular imports are introduced by the new module split.
- Docs/examples may reference old signature import paths.

#### Architectural Impact

- Strongly improves boundary clarity between production runtime and experimental/demo DSPy surfaces.
- Supports safer packaging and server schema hardening (especially `QRE-298`).
- Reduces cognitive load in a foundational module used by runtime code.

#### Dependency Notes

- No explicit blocker.
- **Assumptive Logic:** should precede `QRE-298` so demo gating can target already-separated demo modules.

#### Parallelization Notes

- Safe to parallelize with `QRE-299` (minimal file overlap).
- Parallel with `QRE-298` only if one owner locks the new module names/paths first.

### QRE-298 — Gate `runners_demos.py` behind env flag

#### Technical Risks

- Demo task types may remain exposed in request schemas even if runtime dispatch is gated (partial hardening).
- Default server startup may fail if gating logic still imports demo dependencies eagerly.
- Drift between schema gating and runtime dispatch gating can create inconsistent behavior.

#### Edge Cases

- Local/dev enablement path must be explicit and documented (`APP_ENV` / custom env flag behavior).
- Frontend or tests may expect demo task types in schema and fail when removed.
- CLI/server help output may still mention demo flows unless filtered consistently.

#### Architectural Impact

- Improves production surface hygiene and reduces accidental exposure of experimental behavior.
- Clarifies supported vs optional/demo capabilities in server API contracts.
- Creates a repeatable pattern for feature gating in the server.

#### Dependency Notes

- No explicit blocker.
- **Assumptive Logic:** easier and lower risk after `QRE-297` splits demo signatures.
- Related to `QRE-296`/`QRE-299` through server schema/router wiring touchpoints.

#### Parallelization Notes

- Can be parallelized with `QRE-299` if router/task registration ownership is coordinated.
- Avoid parallel work with `QRE-316` if both touch `.env.example` / config semantics in the same branch without coordination.

### QRE-299 — Consolidate empty router stubs into `routers/planned.py`

#### Technical Risks

- Removing stubs may break indirect imports if any code relies on placeholder module names.
- Over-consolidation can obscure future route split boundaries if the planned module becomes unstructured.
- Router registration cleanup may accidentally remove real routers if stubs are misidentified.

#### Edge Cases

- Some “empty” router modules may contain TODO metadata or comments worth preserving.
- `server/routers/__init__.py` may be used by tests/import checks and need compatibility exports.
- Startup import order changes can expose hidden module side effects.

#### Architectural Impact

- Improves router package signal-to-noise and reduces implied API completeness.
- Creates a clearer staging area (`planned.py`) for future route placeholders.
- Helps future contributors navigate active vs planned server surfaces.

#### Dependency Notes

- No explicit blocker.
- **Assumptive Logic:** coordinate with `QRE-296` because both alter router import/registration topology.

#### Parallelization Notes

- Can run in parallel with `QRE-297`.
- Parallel with `QRE-296` only under explicit shared ownership of `server/main.py` and router import updates.

## Execution Strategy for This Group

### Phase 1: Foundation

1. `QRE-296` — stabilize dependency module ownership (`deps.py` as canonical source)
2. `QRE-297` — separate production/demo signatures to reduce coupling before runtime/schema gating

### Phase 2: Features

1. `QRE-299` — router stub consolidation (ideally after `QRE-296`, or co-landed with coordinated import edits)
2. `QRE-298` — demo runner/schema gating once demo code boundaries are clearer (`QRE-297` complete)

### Phase 3: Hardening Validation

- Import/startup verification (`import fleet_rlm.server.main`)
- Server schema/router targeted tests (`tests/unit/test_ws_router_imports.py`, `tests/ui/server/*` as needed)
- Search-based confirmation of old module path/demo task exposure removal

## Parallel Tracks (Group-Local)

### Safe parallel work

- `QRE-297` + `QRE-299` (low direct file overlap)
- `QRE-296` + `QRE-297` (if `QRE-296` owner avoids signature modules)

### Parallel with coordination

- `QRE-296` + `QRE-299`
  - Shared risk: `src/fleet_rlm/server/main.py`, router imports/registration
  - Coordination rule: one owner controls final import graph and rebases the other
- `QRE-297` + `QRE-298`
  - Shared risk: demo module names/import paths
  - Coordination rule: finalize new demo-signature module path first

### Unsafe / not recommended parallelism

- `QRE-298` uncoordinated with any work changing task schemas/router registration paths (high risk of partial gating)

## Integration / Handoff Points

- **To Group 5 (Telemetry/Settings):** `QRE-296` reduces server dependency ambiguity before telemetry propagation work (`QRE-320`) touches WS/router execution paths.
- **To milestone-wide release confidence:** `QRE-298` ensures production-facing schemas exclude demo noise before validating settings/runtime UX and telemetry claims.
- **To contributor docs:** group outcomes may require minor AGENTS/docs updates for demo enablement paths and server dependency module references.

## Validation Checklist

- [ ] All four tickets are represented with explicit vs inferred dependencies clearly separated.
- [ ] `QRE-296` / `QRE-299` file-overlap coordination risk is documented.
- [ ] `QRE-297` -> `QRE-298` sequencing is marked as **Assumptive Logic**.
- [ ] Production-surface hardening implications (schemas, startup imports, demo gating) are covered.
- [ ] Group-local parallelization guidance distinguishes safe vs coordinated vs unsafe cases.
