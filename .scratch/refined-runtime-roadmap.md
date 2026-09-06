# Refined runtime roadmap

Status: implementation in progress on branch `feat/runtime-roadmap` (0.7.6).
Implementation commits are recorded below. Pre-existing agent-instruction edits
remain uncommitted and outside these implementation commits. ADR 004/005 were
created here because this checkout lacked them.
No phase is closed. Phase 2 budget integration and adapter contraction remain open;
live semantic baseline and Postgres evidence remain pending operator authorization.

Implemented so far: explicit legacy selector and rejection tests; architecture
target ADRs; composite database lineage with preflight and reversible migration;
immediate SQLite FK enforcement and corrected fixtures; narrowly allowlisted claim
race reconciliation; scripted benchmark execution, digests and comparison; native
FleetProgramSpec/tool catalog; deadline LM proxy replacing instance method assignment;
TurnBudget atomic reservation primitive and provider-proxy accounting;
FleetOutputContract replacing private `_inject_execution_context` monkey-patching;
DSPy compatibility implementation and consumers migrated to `compat_3_3_1.py`;
and comprehensive tests for caller-owned interpreter, tool injection, output metadata,
SandboxSerializable, async invocation, and trajectory shape.
Latest implementation validation: `make check` passed (78.45% backend coverage;
538 TUI tests), with output in `.scratch/roadmap-compat-check.log`.
The earlier factory/native-contract/SUBMIT suite passed all 85 tests.
The adapter suite now passes 29 tests, including five sync/async parity cases
and cancellation cleanup. The RLM/tracer/version-guard suite passed 546 tests
before the final single-home guard was added and covered by `make check`.
Follow-up: corrected three vacuous source-root guards, added source-file existence
assertions, and extracted SUBMIT syntax validation into `rlm/submit_validation.py`
with 14 direct test cases. This validator is not a runtime safety sandbox.
The 15-Turn receipt is `.scratch/runtime-v2-scripted-baseline.json`; it is explicitly
scripted lifecycle evidence, not a sealed live legacy semantic baseline.

## Sequence

1. Phase 1.1 — Close Phase 0/1.
2. Phase 2 — DSPy execution core.
3. Phase 3 — Daytona native-interpreter feasibility.
4. Phase 4 — Daytona environment definitions and warm capacity.
5. Phase 5 — Native Turn-scoped production cutover and subtraction.
6. Phase 6 — Recursive RLM v2.
7. Phase 7 — Evaluation, optimization, rollout, and final deletion.

Prove the Daytona native execution architecture in Phase 3 before encoding its
requirements in Phase 4 snapshots. Phases 3–7 have sequencing only in this revision;
their detailed implementation scope and exit gates remain to be defined.

## Phase 1.1 — Close the implemented foundation

Objective: stabilize Phase 0/1 before introducing runtime alternatives.

### PR 1.1A — Correct architecture contracts

- [x] Amend ADR 004: InterpreterContext is fresh per Turn (target; legacy cutover pending).
- [x] Define SemanticChild as Volume-less and warm-pool eligible.
- [x] Define WorkspaceChild as the restricted-data child.
- [x] Move BenchmarkSandbox into testing terminology.
- [x] State that correctness cannot depend on interpreter globals surviving a Turn.
- [x] Update diagrams and vocabulary checks.

### PR 1.1B — Collapse migration selectors

- [x] Introduce one `runtime.variant`; expose only implemented variants.
- [x] Keep native and capsule absent from the settings editor until their owning phases land.
- [x] Reject unsupported combinations at startup during migration.
- [x] Record `runtime_variant` in lifecycle and scripted benchmark receipts.
- [x] Update ADR 005 and test that the default cannot silently change.

### PR 1.1C — Complete benchmark v2

- [x] Rename the receipt-packaging command and add a real benchmark executor.
- [x] Convert the scenario catalog into executable, versioned fixtures.
- [x] Add explicit deterministic scorer IDs.
- [ ] Add semantic scorer IDs and executable semantic scoring; the scripted receipt
  currently records an empty `semantic_scorer_ids` list.
- [x] Add repeated samples and p50/p95 distributions for the scripted lane.
- [x] Record dataset/scorer digests and all relevant snapshot/profile identities.
- [x] Generate Runtime Event fixtures from deterministic scripted Runs.
- [ ] Seal one actual legacy 0.7.6 baseline receipt.
- [x] Add a comparison command producing pass/fail migration gates.

### PR 1.1D — Finish database lineage

- [x] Add composite Turn-to-Run Session lineage.
- [x] Enforce immediate SQLite FKs; no global deferral existed in this checkout.
- [x] Restrict claim reconciliation to expected claim constraints.
- [x] Add dirty-data migration preflight and upgrade/downgrade tests.
- [ ] Preserve live Postgres contention evidence.

### Exit criteria

- [x] One coherent runtime selector exists.
- [x] Architecture no longer endorses cross-Turn interpreter state.
- [x] A reproducible benchmark executes rather than merely packages receipts.
- [ ] A real legacy baseline exists.
- [x] Turn/Run Session lineage is database-enforced.
- [x] SQLite and Postgres FK timing semantics align where practical.

## Phase 2 — DSPy execution core

Objective: simplify the DSPy boundary without changing Daytona lifecycle.

Evolve the existing RLMFactory into a FleetProgramFactory driven by an immutable
FleetProgramSpec and returning native `dspy.RLM`. Do not add a redundant
`FleetProgram(dspy.Module)`: RLM is already a Module. A wrapper becomes justified
only by an evaluated deterministic pipeline such as route → retrieve → RLM →
verify → revise.

Compatibility premise to verify against official DSPy documentation and the exact
3.3.1 pin before implementation: experimental RLM exposes its public constructor,
native `llm_query`, `llm_query_batched`, `print`, `SUBMIT`, custom tools, configurable
`sub_lm`, and a caller-owned CodeInterpreter that may be passed positionally and
remains caller-managed.

### PR 2A — Program specification and tool ownership

- [x] Define immutable FleetProgramSpec and one builder returning native `dspy.RLM`.
- [x] Keep Daytona, database, Run authority, and event-stream objects out of the spec.
- [x] Introduce one immutable FleetToolCatalog (membership and authority; DSPy Tool objects remain runtime objects).
- [x] Classify tools as sandbox-local, host-authorized, recursive, or settlement-only.
- [x] Never expose catalog settlement-only tools to the model.
- [x] Reject collisions with DSPy built-ins, including a runtime assertion against
  the final constructed namespace rather than only an AST vocabulary check.

### PR 2B — Global TurnBudget

Enforce absolute deadline, provider attempts, tool calls, recursive children,
execution output bytes, and finalization reserve. Token counts remain observed
accounting until usage is reliably available before further calls for hard admission.

- [x] Define TurnBudget with atomic reservations and explicit exhaustion categories.
- [ ] Reserve finalization capacity before exploration.
- [ ] Share the budget across root action calls, DSPy built-in sub-LM calls, parse
  repair, extraction, child root/sub-LM calls, and provider retries.
- [ ] Add concurrency-safe child reservations and complete budget-path tests.

Integration evidence: production reservations currently cover provider attempts only.
Tool/child/output-byte admission, configured limits, finalization capability, and
settlement wiring still need implementation and end-to-end budget-path tests.

### PR 2C — Replace LM monkey-patching

- [x] Introduce a real budget/deadline LM proxy.
- [x] Stop assigning replacement `forward`/`aforward` methods to LM instances.
- [ ] Establish one retry owner; debit every physical provider attempt.
- [x] Preserve model role, usage, and callback visibility.
- [x] Test synchronous and asynchronous DSPy paths under the exact 3.3.1 pin.

### PR 2D — Remove private RLM mutation

- [x] Remove replacement of `_inject_execution_context`.
- [x] Introduce FleetOutputContract; let the interpreter adapter bind output metadata.
- [x] Isolate unavoidable DSPy 3.3.1 compatibility in `compat_3_3_1.py`.
- [x] Prohibit private DSPy imports elsewhere.
- [x] Directly test caller-owned interpreter, tool injection, output metadata,
  SandboxSerializable, async invocation, final output, and trajectory shape.

Compatibility status: implementation and consumers now use `compat_3_3_1.py`;
`_dspy_compat.py` is removed. The import guards allow only the versioned module,
and a regression test verifies that the implementation has one home.

### PR 2E — Contract FleetJSONAdapter

- [ ] Compare stock JSONAdapter against Fleet's adapter using benchmark v2.
- [ ] Keep only measured repair behavior.
- [ ] Move deadlines and attempt limits to TurnBudget/LM proxy.
- [x] Extract SUBMIT validation into a small validator.
- [x] Use one state machine for sync and async. Requests, error transitions,
  wrap-up metadata, and cancellation cleanup are regression-tested.
- [ ] Debit all corrective calls from the global budget.
- [ ] Keep finalization reserve independently testable.

### Exit criteria

- [x] Native DSPy RLM built-ins remain unmodified.
- [x] No LM method assignment or private RLM method replacement remains.
- [ ] Every model/provider attempt is globally accounted for.
- [x] DSPy core can be tested without Daytona or the database.
- [x] Public Turn output and Runtime Event fixtures remain unchanged.

## Next implementation steps

1. Complete PR 2B admission and settlement wiring, including independently usable
   finalization reserve and tests exercising root, child, and corrective calls.
2. Close PR 2C retry accounting with end-to-end admission evidence; do not treat
   primitive-only tests as completion evidence.
3. Complete PR 2E adapter comparison and budget integration, preserving existing
   repair behavior until measurements justify policy changes.
4. Define Phase 3 feasibility experiments and exit gates before snapshot work.
5. When explicitly authorized, capture the live legacy baseline and Postgres
   contention evidence. Scripted receipts do not satisfy either live gate.

## Implementation commits

- `9c2526dc` — Extract finalization syntax validation.
- `577f0c84` — Share sync/async repair policy and test parity/cancellation.
- `8ee6ff65` — Native program/tool boundaries, budget primitives, output contracts,
  and actual exact-version compatibility relocation.
- `597c5396` — Database lineage, immediate SQLite FKs, and claim reconciliation.
- `ec582393` — Legacy runtime selector, target ADRs, and scripted migration receipts.

## Evidence and execution boundaries

The roadmap is explicitly tracked for review despite the `.scratch/` ignore rule;
local logs and benchmark receipts remain ignored. No phase is closed.
Commits on `feat/runtime-roadmap` are authorized. Pushes, PR creation, production
cutover, and credentialed live execution have not been authorized.

Close checkboxes only with implementation and validation receipts. Live baseline,
provider, Daytona, and Postgres tests require explicit operator authorization.
Creating this roadmap does not authorize commits, PR creation, or live execution.
