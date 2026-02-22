# Group 2 — RLM Assessment / Validation (QRE-300 to QRE-301)

## Scope

This group validates the DSPy/RLM execution path at two levels:

- deterministic unit-level loop semantics (`QRE-300`)
- live end-to-end long-document execution and tracing (`QRE-301`)

The goal is to establish confidence that v0.4.8 changes do not break RLM trajectory generation, streaming, and persistence/observability behavior.

## Ticket Inventory

| Ticket | Title | Status | Priority | Labels | Duplicate? | Explicit blockers | Explicit blocked items |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QRE-300 | RLM Assessment Step 2: Mock Execution — simulate 3-step RLM run | Triage | High | `v0.4.8`, `dspy`, `testing`, `backend` | No | None | None |
| QRE-301 | RLM Assessment Step 3: End-to-End Tracing — live long-document RLM query | Triage | High | `v0.4.8`, `dspy`, `e2e`, `backend`, `testing` | No | None | None |

## Chain of Command (Dependencies)

### Explicit Linear blockers / duplicates

No explicit `blockedBy` / `blocks` relations are defined in Linear for this group.

### Inferred prerequisites (**Assumptive Logic**)

1. **`QRE-300` should precede `QRE-301`**
   - Deterministic trajectory regression coverage reduces ambiguity before running credential-gated live validation.
   - Faster feedback loop catches parser/loop regressions before spending time on network-dependent E2E runs.

2. **`QRE-301` should be executed late in the milestone (after major telemetry/schema changes)**
   - The ticket explicitly validates streaming + persistence/observability behavior.
   - Running it after Groups 4 and 5 changes provides stronger release confidence coverage of the integrated system.

3. **`QRE-301` harness preparation can start early**
   - Command/harness design, event assertions, and documentation can be prepared in parallel with feature implementation.
   - Final pass/fail execution should occur after key backend changes land.

### Sequencing rationale tied to file/module overlap

- `QRE-300` primarily targets tests (`tests/unit/test_dspy_rlm_trajectory.py`, possibly shared mocks).
- `QRE-301` may touch integration scripts/tests and inspect runtime WS/persistence paths (`server/routers/ws*.py`, repository persistence flow), but should avoid unnecessary code changes unless it discovers gaps.

## Technical Deep Dive (Per Ticket)

### QRE-300 — Mock Execution: simulate 3-step RLM run

#### Technical Risks

- Over-mocking may validate a test-only path that diverges from production parser/trajectory semantics.
- Mock response formatting may not match the delimiter/shape expected by the real RLM loop.
- Test brittleness if assertions depend on incidental formatting instead of structural invariants.

#### Edge Cases

- Termination behavior after exactly 3 iterations (avoid off-by-one loop assertions).
- Observation payloads with partial/empty content still producing valid trajectory entries.
- Alternate configuration paths (max-steps, recursion depth) affecting deterministic execution unless pinned in the test.

#### Architectural Impact

- Adds a stable regression anchor for RLM loop semantics independent of external models.
- Reduces future refactor risk in trajectory extraction/streaming by making core invariants explicit.
- Improves maintainability of RLM runtime changes by separating semantic validation from E2E variability.

#### Dependency Notes

- No explicit blocker.
- **Assumptive Logic:** should precede `QRE-301` and can run as early milestone work.

#### Parallelization Notes

- Safe to run in parallel with most frontend work (Groups 3 and 5 UI portions).
- Can run in parallel with DB schema work if test helpers do not depend on in-flight persistence schema changes.

### QRE-301 — End-to-End Tracing: live long-document RLM query

#### Technical Risks

- Flaky results due to live LLM variability, credential issues, network latency, or provider-side changes.
- False negatives if streaming/persistence assertions are too strict on timing/order under async execution.
- Scope creep into backend fixes if validation discovers gaps in WS events or persistence fields.

#### Edge Cases

- Partial stream completion or delayed persistence writes requiring retry/polling.
- Long-document inputs triggering large trajectories or tool outputs that stress event parsing.
- Credential-gated environments where telemetry/analytics defaults or runtime settings differ from local assumptions.

#### Architectural Impact

- Validates the integrated contract between agent runtime, execution streaming, and persistence layers.
- Can become the canonical release-readiness validation procedure for RLM-heavy changes.
- Surfaces cross-layer compatibility issues (WS event shape, repository persistence, analytics context) earlier in release hardening.

#### Dependency Notes

- No explicit blocker.
- **Assumptive Logic:** complement to `QRE-300`, but best scheduled after Groups 4 and 5 feature work to maximize confidence.
- Related to canvas readability tickets (`QRE-306`, `QRE-309`) via event/trajectory semantics, but not blocked by them.

#### Parallelization Notes

- Preparation (harness/docs/assertion plan) can run in parallel with implementation tracks.
- Final live execution should be serialized near milestone end to validate the integrated codebase state.

## Execution Strategy for This Group

### Phase 1: Foundation

1. `QRE-300` — implement deterministic 3-step mock trajectory test and lock structural assertions.

### Phase 2: Features

1. `QRE-301` preparation tasks (harness, commands, expected event sequence, persistence checks)
2. Define environmental prerequisites and flakiness-handling policy (retries/polling/manual-run expectations)

### Phase 3: Integrated Validation

1. Execute `QRE-301` after major backend schema + telemetry changes are in place
2. Record pass/fail observations and known caveats
3. Feed discovered regressions back into relevant tracks (WS, persistence, analytics, runtime settings)

## Parallel Tracks (Group-Local)

### Safe parallel work

- `QRE-300` with any frontend artifact/settings work
- `QRE-301` harness authoring/documentation with backend feature implementation tracks

### Parallel with coordination

- `QRE-301` with DB schema migration work (`QRE-311`-`QRE-314`)
  - Coordination rule: avoid hard-coding schema expectations until migrations stabilize
- `QRE-301` with telemetry propagation work (`QRE-320`)
  - Coordination rule: test plan must specify telemetry-enabled vs disabled validation semantics

### Unsafe / not recommended parallelism

- Final `QRE-301` validation run during active migration churn or WS payload refactors (results become hard to interpret)

## Integration / Handoff Points

- **To Group 4 (Postgres Schema):** `QRE-301` should validate persistence assumptions after schema changes land, especially for run/step traceability.
- **To Group 5 (Telemetry/Settings):** `QRE-301` is a natural validation point for end-to-end telemetry preference propagation behavior in web-originated sessions (`QRE-320`).
- **To Group 3 (Canvas UX):** ticket outputs (trajectory/event quality) indirectly validate the payload shapes consumed by canvas graph/timeline/preview features.

## Validation Checklist

- [ ] `QRE-300` and `QRE-301` sequencing is documented as **Assumptive Logic**.
- [ ] Group distinguishes test/harness preparation from final live integrated validation.
- [ ] Credential/network flakiness risks are explicitly documented for `QRE-301`.
- [ ] Cross-track handoff expectations (DB schema, telemetry, WS events) are captured.
- [ ] Parallelization guidance prevents ambiguous E2E validation during active backend churn.
