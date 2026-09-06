# Refined runtime roadmap

Status: implementation in progress on branch `feat/runtime-roadmap` (0.7.6).
Implementation commits are recorded below, including the existing agent-instruction
changes requested by the operator. ADR 004/005 were created here because this
checkout lacked them. PR 2E and the production Phase 2 budget wiring are complete
as implementation work; no phase is closed. The live semantic baseline and
Postgres contention evidence remain pending operator authorization.

Implemented so far: explicit legacy selector and rejection tests; architecture
target ADRs; composite database lineage with preflight and reversible migration;
immediate SQLite FK enforcement and corrected fixtures; narrowly allowlisted claim
race reconciliation; scripted benchmark execution, deterministic semantic scoring,
digests and comparison; native FleetProgramSpec/tool catalog; deadline LM proxy
replacing instance method assignment; TurnBudget atomic reservations and
provider-proxy accounting, including adapter corrections, DSPy schema fallback,
provider retries, tool/child/output admission, and finalization settlement;
FleetOutputContract replacing private `_inject_execution_context` monkey-patching;
DSPy compatibility implementation and consumers migrated to `compat_3_3_1.py`;
composition-owned async Tool bridging; and comprehensive tests for caller-owned
interpreter, tool injection, output metadata, SandboxSerializable, async invocation,
output caps, budget paths, and trajectory shape.
Post-fix `make check` passes with 78.60% backend coverage and 538 TUI tests;
Ruff, ty, generated-contract, dependency-boundary, documentation, and harness
checks also pass. Runtime-v2 comparison tests cover receipt integrity, both
invocation modes, attempt accounting, semantic scorer evidence, and repair-policy
ablations. Source-root guards inspect the actual backend tree. SUBMIT syntax
validation is separately tested and is not a runtime safety sandbox.
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
requirements in Phase 4 snapshots. The experiments and exit gates for Phases 3–7
are defined below; a gate is closed only by the named executable receipt or test
and not by a design assertion.

## Phase 3 — Daytona native-interpreter feasibility

Objective: prove that a caller-owned native DSPy interpreter can execute one
Turn safely on Daytona before any warm-pool or snapshot policy is committed.

### Experiments

- **P3-E1 — Native call boundary:** run the pinned DSPy 3.3.1 `dspy.RLM` with a
  caller-owned `InterpreterContext`, typed `SUBMIT`, the Fleet output contract,
  host Tools, and the normal Runtime Event projection. Record the exact candidate,
  dependency versions, selected policy/profile, and bounded event assertions;
  never record prompts, answers, code, credentials, Sandbox IDs, or provider
  responses.
- **P3-E2 — Turn isolation:** execute two sequential and two overlapping Turns
  using fresh interpreter contexts and distinct Run bindings. Verify no globals,
  Tool aliases, output metadata, context capsules, or callbacks cross the Turn
  boundary. Include a legacy Session-reuse control so the difference is measured.
- **P3-E3 — Cancellation and cleanup:** cancel during interpreter execution,
  provider wait, and finalization. Verify the worker is joined, the interpreter
  lease remains owned until cleanup settles, no success or Artifact is published,
  and the durable Turn reaches the typed failure state.
- **P3-E4 — Protocol and budget parity:** replay empty, malformed, missing-field,
  late, retry, output-cap, and finalization responses through sync and async paths.
  Compare Runtime Event shape, provider admissions, and shared budget snapshots
  against the sealed deterministic adapter fixtures.
- **P3-E5 — Live feasibility canary:** when explicitly authorized, run the narrow
  Phase 1 Daytona stream canary, then the Phase 2 recursive-child canary only
  after the Phase 1 receipt and retrospective. Compare cold-start, first-action,
  cleanup, and failure timings against bounded policy thresholds; this is a
  feasibility result, not release or semantic-quality evidence.

### Phase 3 exit gates

- [ ] P3-G1: P3-E1 receipt proves caller-owned native execution and typed output on
  the exact pinned dependency set.
- [ ] P3-G2: P3-E2 proves fresh Turn context and no cross-Turn mutable state in
  sequential and overlapping execution.
- [ ] P3-G3: P3-E3 proves cancellation, timeout, claim loss, and cleanup ownership
  with no detached mutation or successful durable settlement.
- [ ] P3-G4: P3-E4 passes protocol parity, global admission, output-cap, and
  Runtime Event assertions in the credential-free lane.
- [ ] P3-G5: the authorized Daytona canary passes on a clean committed candidate;
  otherwise Phase 4 snapshot work does not start.

## Phase 4 — Daytona environment definitions and warm capacity

Objective: turn the proven native boundary into explicit environment contracts
without hiding cold-start or capacity costs.

### Experiments and gates

- Define immutable `daytona-recursive` snapshot contents, mount policy, network
  policy, and interpreter bootstrap; reject drift at startup.
- Compare per-Turn creation, retained Session root, and bounded warm-pool candidates
  using the same P3 scenario mix. Measure readiness, first action, idle reuse,
  replacement, and cleanup distributions; do not optimize before correctness.
- Exercise capacity admission, provider outages, stale bindings, snapshot mismatch,
  and disposal with pending owners. Verify no capacity mode bypasses Run claims,
  deadlines, or cleanup fencing.
- **Exit:** [ ] snapshot identity and policy are immutable; [ ] cold/warm choice
  has a retained receipt with thresholds; [ ] capacity/replacement cleanup passes;
  [ ] no warm mode changes public Runtime Events or durable semantics.

## Phase 5 — Native Turn-scoped production cutover and subtraction

Objective: migrate production from legacy Session reuse to the native Turn-scoped
architecture with a reversible, observable rollout.

### Experiments and gates

- Run a shadow or canary cohort with the same requests through legacy and native
  paths, comparing terminal status, committed output schema, Runtime Events,
  Tool effects, Artifact candidates, latency, provider admissions, and cleanup.
- Roll back on any integrity, authorization, event-order, budget, or durability
  mismatch; preserve the legacy selector until the native candidate is proven.
- Switch the default only after the canary is stable across restart, cancellation,
  claim loss, provider failure, and Session concurrency, then delete the legacy
  reuse path and its compatibility-only tests in a separate reviewed change.
- **Exit:** [ ] native canary meets parity/error/latency thresholds; [ ] rollback
  drill succeeds; [ ] operator sign-off records the cutover; [ ] legacy subtraction
  leaves no supported selector or stale lifecycle owner.

## Phase 6 — Recursive RLM v2

Objective: extend the proven Turn-scoped boundary to isolated recursive children,
without allowing child work to become final authority or escape the parent deadline.

### Experiments and gates

- Measure one direct SemanticChild and one restricted WorkspaceChild with fresh
  child interpreters, explicit Volume scope, bounded child calls, and Root
  verification/synthesis.
- Compare sequential and ordered all-or-nothing batches under child, Tool, provider,
  output, cancellation, and cleanup budgets. Inject child timeout, authorization
  revocation, partial acquisition, and late cleanup failures.
- Verify depth-one enforcement, Sub-LM fallback, prompt/answer bounds, no parent
  interpreter globals in children, and no child lease after parent settlement.
- **Exit:** [ ] child isolation and Volume policy pass; [ ] ordered batch and shared
  budget receipts pass; [ ] failure/quarantine cleanup is bounded; [ ] Root remains
  the only final authority.

## Phase 7 — Evaluation, optimization, rollout, and final deletion

Objective: use semantic and operational evidence to optimize only after correctness
and lifecycle gates are closed, then remove migration scaffolding.

### Experiments and gates

- Capture a clean legacy semantic baseline with explicit scorer IDs and model/profile
  identity, followed by the equivalent native candidate; keep deterministic
  lifecycle scores separate from live judge quality.
- Run the MLflow evaluation dataset/scorers, align judges with SME labels, and
  record quality, latency, provider admissions, tool/recursive counts, and costs
  under named configurations. No live scores are inferred from scripted receipts.
- Optimize one bounded dimension at a time (prompt, routing, batching, snapshot,
  or capacity), retain reproducible candidate receipts, and reject regressions in
  correctness, safety, durability, or budget ceilings.
- **Exit:** [ ] semantic quality and lifecycle parity meet the signed thresholds;
  [ ] rollout/rollback and observability are rehearsed; [ ] best configuration is
  promoted through the repository release process; [ ] legacy selector, adapter
  shims, and obsolete tests/docs are deleted only after final evidence is archived.

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
- [x] Add semantic scorer IDs and executable deterministic semantic scoring. The
  `semantic-keywords/v1` scorer normalizes Unicode/case/whitespace and checks
  expected concept markers without a provider call; the live semantic gate remains
  explicitly unexercised.
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
- [x] Reserve finalization capacity before exploration.
- [x] Share the budget across root action calls, DSPy built-in sub-LM calls, parse
  repair, extraction, child root/sub-LM calls, provider retries, host Tools, and
  execution output bytes.
- [x] Add concurrency-safe child reservations and complete budget-path tests.

Integration evidence: `build_run_preparation` creates one TurnBudget from the
configured `BudgetLimits`; the bound Root/Sub LMs, FleetJSONAdapter, host Tools,
recursive children, Daytona output cap, and cleanup settlement share it. Explicit
finalization limits reserve capacity away from exploration while invocation-local
caps remain available for adapter-only compatibility seams.

### PR 2C — Replace LM monkey-patching

- [x] Introduce a real budget/deadline LM proxy.
- [x] Stop assigning replacement `forward`/`aforward` methods to LM instances.
- [x] Establish one retry owner; debit every physical provider attempt.
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

Complete for deterministic protocol behavior; not a live semantic or provider-cost
claim. Implementation: `8752eb2a`.

- [x] Compare stock JSONAdapter against Fleet's adapter using benchmark v2.
- [x] Keep only measured repair behavior: retain two bounded parse corrections,
  reserve-boundary transition, and SUBMIT-only finalization correction. Removing
  or reducing parse repairs loses expected outcomes in the replay fixtures.
- [x] Move deadlines and attempt limits to the budget layer/LM proxy. AdapterBudget
  uses the shared TurnBudget; DeadlineLMProxy enforces each provider admission.
- [x] Extract SUBMIT validation into a small validator.
- [x] Use one state machine for sync and async. Requests, error transitions,
  wrap-up metadata, and cancellation cleanup are regression-tested.
- [x] Debit all corrective calls from the global provider ledger, including native
  schema fallback and transport retries; reject mismatched Turn budgets.
- [x] Keep finalization reserve independently testable. Finalization slots count
  physical admissions, not just adapter requests; children cannot spend Root's
  reserved attempts. Late responses are reclassified without double debit.

#### Comparison evidence

```bash
uv run python -m scripts.benchmarks.runtime_v2 compare-adapters \
  --repetitions 5 --output .scratch/runtime-v2-adapter-comparison-sealed.json
```

The local receipt was generated from clean commit `8752eb2a`: 14 fixtures ×
4 adapter settings × 2 invocation modes × 5 repetitions = 560 replays.
Use a new output filename when rerunning; sealed files are never overwritten.

| Adapter setting | Expected outcomes | Provider admissions | Local p50 / p95 |
| --- | --- | --- | --- |
| Stock JSONAdapter | 40/140 | 160 | 0.292 / 0.724 ms |
| Fleet, no parse repairs | 100/140 | 220 | 0.539 / 1.360 ms |
| Fleet, one parse repair | 130/140 | 270 | 0.757 / 1.373 ms |
| Fleet, two parse repairs | 140/140 | 290 | 0.920 / 1.516 ms |

All gates pass: Fleet expected outcomes/attempt ceilings, provider accounting,
sync/async parity, and no extra calls for valid output. Fixtures cover empty,
invalid, and missing-field responses; exhausted repair; non-SUBMIT finalization;
late responses and provider timeout; transport retry; and DSPy schema fallback.
These are scripted protocol outcomes, not answer-quality scores. Recorded latency
is local replay overhead, not production inference latency. No provider, Daytona,
Postgres, or MLflow server was contacted.

Receipt digest: `0a13fe7e6dde8bc1ad89304b3d39bde0908fc7b1b24dfc3647f71203480229cf`.
The receipt includes dataset, scorer, and implementation digests. Raw local
receipts/logs stay ignored; fixtures, replay code, tests, and this summary are tracked.

### Exit criteria

- [x] Native DSPy RLM built-ins remain unmodified.
- [x] No LM method assignment or private RLM method replacement remains.
- [x] Every model/provider attempt is globally accounted for in the production
  Turn wiring; live provider confirmation remains an operator gate.
- [x] DSPy core can be tested without Daytona or the database.
- [x] Public Turn output and Runtime Event fixtures remain unchanged.

## Next implementation steps

1. Complete: run the post-fix `make check` and review the resulting diff.
2. Regenerate clean scripted baseline/candidate receipts with semantic scorer IDs
   after this change is committed; these remain lifecycle evidence only.
3. When explicitly authorized, capture the live legacy semantic baseline and
   Postgres contention evidence. Scripted receipts do not satisfy either live gate.
4. Execute Phase 3 gates, starting with the credential-free parity lane and then
   the authorized Daytona canary; do not begin snapshot work before P3-G5.
5. Advance through the Phase 4–7 gates in order, retaining one bounded receipt per
   experiment and keeping the legacy selector until native cutover is proven.

## Implementation commits

- `35067612` — Commit the existing repository/TUI agent-instruction changes.
- `8752eb2a` — Complete PR 2E budget/proxy contraction and adapter comparison.

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
