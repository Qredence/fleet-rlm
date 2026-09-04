# Recommended Fleet RLM implementation roadmap

This roadmap is based on the current `main` branch at commit `b673587d05d6ec5901a5fcfd4747b82dcf683288`.

The repository is not starting from zero:

* Fleet already has an immutable Daytona snapshot definition and operator-managed snapshot creation/checking.
* It already has a repository-owned dependency manifest.
* It already has Session prewarming.
* It already has a custom Daytona interpreter, broker, recursive child runtime, and extensive lifecycle tests.

The plan should therefore **replace and contract existing paths incrementally**, not introduce parallel abstractions that remain forever. The current snapshot implementation and operator script should become the foundation of the new environment-definition system rather than being discarded.

---

# Target architecture

```text
Workspace
└── Session
    ├── PostgreSQL history and Run state
    ├── Daytona Volume
    └── SessionSandbox
        ├── reused across Turns
        └── auto-stopped while idle

Turn
└── Run
    ├── TurnBudget
    ├── fresh FleetProgram
    │   └── dspy.RLM
    │       ├── llm_query             DSPy built-in
    │       ├── llm_query_batched     DSPy built-in
    │       ├── print                 DSPy built-in
    │       ├── SUBMIT                DSPy built-in
    │       └── Fleet custom tools
    │           ├── rlm_query
    │           ├── rlm_query_batched
    │           ├── workspace tools
    │           ├── artifact tools
    │           └── memory tools
    ├── fresh Daytona InterpreterContext
    ├── Runtime Events
    └── atomic settlement

Recursive child
├── SemanticChild
│   ├── no Volume
│   ├── warm-pool eligible
│   └── receives SubproblemCapsule
└── WorkspaceChild
    ├── selected files or restricted Volume scope
    └── provisioned only when needed
```

DSPy should continue to own its native RLM loop and built-in tools. Its official RLM interface provides `llm_query`, `llm_query_batched`, `print`, `SUBMIT`, additional custom tools, a configurable `sub_lm`, and external `CodeInterpreter` support. RLM is currently marked experimental, so all private integration should be isolated and every major cutover should remain reversible until validated. ([DSPy][1])

---

# Architectural decisions to freeze first

These decisions should be recorded before code changes begin.

1. **Durable Session state**

   * PostgreSQL contains authoritative Session, Turn, Run, artifact, and memory metadata.
   * Daytona Volume contains durable workspace files.
   * Python variables and interpreter globals are not authoritative.

2. **Turn-scoped model runtime**

   * A new `FleetProgram` and Daytona interpreter context are created for each Turn.
   * The Session Sandbox may persist, but the Python context does not.

3. **DSPy tool ownership**

   * DSPy owns `llm_query`, `llm_query_batched`, `print`, and `SUBMIT`.
   * Fleet does not duplicate them.
   * Fleet owns `rlm_query`, authorization-sensitive capabilities, budgets, events, and settlement.

4. **Daytona control plane**

   * Daytona Python SDK/API is the production source of truth.
   * Daytona Declarative Builder defines snapshots.
   * Daytona Agent Skill assists implementation.
   * Daytona MCP is used for development and operator inspection, not as Fleet’s production provisioning layer.

5. **Sandbox classes**

   * `SessionSandbox`: persistent Volume, Fleet-level prewarm, not a Daytona warm-pool candidate.
   * `SemanticChild`: no Volume, no secrets, warm-pool candidate.
   * `WorkspaceChild`: restricted workspace data, provisioned only for heavier tasks.

6. **Recursive depth**

   * Keep one native child level initially.
   * Do not introduce native grandchildren before depth-one recursion demonstrates measurable value.

---

# Dependency sequence

```text
Phase 0: Contracts and baseline
   ├── Phase 1: Database correctness
   └── Phase 2: DSPy core simplification
                      ↓
Phase 3: Daytona environment definitions
                      ↓
Phase 4: Daytona-native interpreter
                      ↓
Phase 5: Turn-scoped runtime subtraction
                      ↓
Phase 6: Recursive RLM v2
                      ↓
Phase 7: Evaluation, optimization and legacy deletion
```

Phases 1 and 2 can proceed independently after Phase 0. The remaining phases should remain sequential.

---

# Phase 0 — Freeze contracts and establish the baseline

## Objective

Create a stable measurement and behavioral baseline before changing the runtime.

This phase should produce **no intentional API behavior change**.

## PR 0A — Architecture decisions and naming

### Tasks

* [x] **0.1 Create an execution-state ADR**

  * Define `Workspace`, `Session`, `Turn`, `Run`, `RunClaim`, `SessionSandbox`, `InterpreterContext`, and `ChildEnvironment`.
  * State explicitly that a Run is an execution attempt and a Turn is the durable conversational result.

* [x] **0.2 Create a persistence-boundary ADR**

  * Database and Volume are durable.
  * Interpreter namespace is ephemeral.
  * A sandbox may be deleted and recreated without losing committed Session correctness.

* [x] **0.3 Create a DSPy-ownership ADR**

  * List the native RLM built-ins.
  * List Fleet custom tools.
  * Prohibit Fleet implementations named `llm_query` or `llm_query_batched`.

* [x] **0.4 Create a Daytona-environment ADR**

  * Define `SnapshotDefinition`, `SandboxProfile`, and `WarmPoolPolicy`.
  * Define the three Sandbox profiles.
  * Document why Session Sandboxes and semantic child Sandboxes have different lifecycle policies.

* [x] **0.5 Create a migration ADR**

  * Every major runtime change must support a temporary legacy/v2 comparison.
  * Temporary compatibility modes must have an explicit deletion phase.

* [x] **0.6 Add an architecture vocabulary check**

  * Update architecture docs and code comments to use the canonical names.
  * Avoid using “Run,” “Turn,” “Session runtime,” and “Sandbox lease” interchangeably.

### Suggested files

```text
docs/architecture/
  001-execution-state.md
  002-persistence-boundary.md
  003-dspy-tool-ownership.md
  004-daytona-environments.md
  005-runtime-migration.md
```

## PR 0B — Runtime measurement baseline

### Tasks

* [x] **0.7 Define a stable benchmark-result schema**

```text
fleet.runtime-benchmark/v1
```

Include:

```text
scenario
runtime_mode
root_model
sub_model
turn_duration_ms
provider_attempts
root_action_calls
sub_lm_calls
parse_repairs
tool_calls
recursive_calls
child_sandboxes
delegated_context_chars
input_tokens
output_tokens
sandbox_acquire_ms
interpreter_context_ms
sandbox_seconds
terminal_status
score
```

* [x] **0.8 Instrument logical LM calls separately from provider attempts**

  * Root action calls.
  * DSPy sub-LM calls.
  * Child root calls.
  * Child sub-LM calls.
  * Adapter repair calls.
  * Provider retries.

* [x] **0.9 Record Daytona lifecycle timing**

  * Sandbox lookup.
  * Sandbox creation.
  * Sandbox start.
  * Volume mount verification.
  * Broker/interpreter startup.
  * Code execution.
  * Child deletion confirmation.

* [x] **0.10 Add recursive routing measurements**

  * Whether Python-only execution was used.
  * Whether `llm_query` was used.
  * Whether `llm_query_batched` was used.
  * Whether `rlm_query` was used.
  * Whether recursion improved the final task score.

* [x] **0.11 Create a fixed benchmark corpus**

  * Exact deterministic calculation.
  * Long-document evidence extraction.
  * Repository/codebase analysis.
  * Tabular-data analysis.
  * Multi-source comparison.
  * Artifact creation.
  * Cancellation during code execution.
  * Timeout during recursive child execution.
  * Child cleanup failure.

* [x] **0.12 Capture the current `0.7.6` baseline**

  * Store aggregate results, not provider prompts or private reasoning.
  * Record the exact commit, config digest, snapshot name, and model IDs.

* [x] **0.13 Freeze public Runtime Event fixtures**

  * Save representative event sequences for successful, failed, cancelled, and recursive Runs.
  * Future implementations must preserve the public event contract unless deliberately versioned.

* [x] **0.14 Add temporary runtime modes**

Use a small, bounded set:

```toml
[defaults.runtime]
implementation = "legacy"        # legacy | v2

[defaults.daytona]
interpreter = "broker"           # broker | native

[defaults.rlm]
recursion_policy = "legacy"      # legacy | capsule
```

Do not add a separate flag for every internal component.

## Phase 0 exit criteria

* Baseline results are reproducible.
* Public Runtime Event fixtures exist.
* All architectural ownership decisions are documented.
* No API behavior intentionally changed.
* All later performance claims can be compared with the baseline.

---

# Phase 1 — Database correctness and concurrency

## Objective

Fix persistence correctness independently of DSPy and Daytona redesign.

## Progress (2026-09-04)

* Phase 0 baseline contracts were completed and committed as `b2b85ca3b`.
* PR 1A and PR 1B are implemented locally and validated by `make check`.
  SQLite now enables foreign keys per connection, checks constraints atomically
  at transaction commit, uses a 5-second busy timeout, and uses WAL for
  file-backed databases. The SQLite suite covers valid lineage,
  `foreign_key_check`, invalid parent references, and local policy settings.
* Alembic revision `019fb7e2c4d1` adds Session checks/composite uniqueness,
  Turn-to-Run lineage, SandboxBinding workspace/session lineage, and a closed
  provider-state check. SQLite upgrade/downgrade coverage is present.
* SQL claim races now re-read durable state and return replay, idempotency
  mismatch, or in-progress domain outcomes rather than generic lifecycle
  unavailability. File-backed SQLite coverage includes separate-key,
  identical-claim, and same-key/different-input races.
* Remaining Phase 1 work is explicit live PostgreSQL concurrency coverage
  (1.12) and any demonstrated need for additional database-specific repository
  strategies (1.13). The credentialed live `db` lane has not been run and no
  shared database has been migrated.
* 2026-09-04 update: 1.7 is complete (claim-vs-completion and
  claim-vs-cancellation races added). The live PostgreSQL concurrency suite
  (1.12) is implemented behind the `db` marker and pending an operator-run
  `make test-db` execution; 1.13 has a recorded decision gate.
* 2026-09-04 live-lane result: `make test-db` executed against a disposable
  local PostgreSQL 16 instance — 9 passed, including all five concurrency
  scenarios. Phase 1 is complete; only the Phase 1 commit remains.

## PR 1A — SQLite integrity and error translation

### Tasks

* [x] **1.1 Enable SQLite foreign keys on every connection**

Install a SQLAlchemy connection hook that runs:

```sql
PRAGMA foreign_keys = ON;
```

* [x] **1.2 Assert SQLite foreign-key state during tests**

  * Verify `PRAGMA foreign_keys` returns `1`.
  * Fail the test setup when it is disabled.

* [x] **1.3 Add `PRAGMA foreign_key_check` integration coverage**

  * Create valid parent/child data.
  * Attempt invalid Run, Turn, artifact, attachment, and binding relationships.
  * Verify constraint failures.

* [x] **1.4 Add a SQLite local-development policy**

  * Consider WAL mode for file-backed local databases.
  * Add a bounded busy timeout.
  * Document that production concurrency requires PostgreSQL.

* [x] **1.5 Catch `IntegrityError` separately from generic SQL failures**

  * Do not translate expected unique-index races into generic lifecycle unavailability.

* [x] **1.6 Re-read authoritative state after a claim conflict**

  * Detect prior identical idempotent Run.
  * Detect idempotency-key/input mismatch.
  * Detect another active Run.
  * Return the corresponding domain outcome.

* [x] **1.7 Add deterministic race tests**

  * [x] Two identical claims.
  * [x] Two claims with the same key but different input.
  * [x] Two different keys for the same Session.
  * [x] Claim racing completion (`test_sql_claim_racing_completion_resolves_from_durable_state`).
  * [x] Claim racing cancellation (`test_sql_claim_racing_cancellation_keeps_in_progress_fence`).

## PR 1B — Relational lineage constraints

### Tasks

* [x] **1.8 Add `fleet_turns.run_id -> fleet_runs.id`**

  * Define deletion behavior deliberately.
  * Add migration and downgrade coverage.

* [x] **1.9 Add `fleet_sandbox_bindings.workspace_id -> fleet_workspaces.id`**

* [x] **1.10 Add role/status database checks where currently application-only**

  * Session status.
  * Turn role.
  * Binding provider state, if the set is truly closed.
  * Memory/outbox states.

* [x] **1.11 Evaluate composite lineage constraints**

Candidate invariants:

```text
Artifact(run_id, session_id) belongs to the same Run
Artifact(session_id, user_id, workspace_id) belongs to the same Session
SandboxBinding(session_id, workspace_id) belongs to the same Workspace
```

Implement only constraints supported cleanly by both PostgreSQL and SQLite migrations.

* [x] **1.12 Add live PostgreSQL concurrency tests**

  * Implemented in `tests/live/backend/test_postgres_concurrency.py` (marker
    `db`) and executed against a live disposable PostgreSQL 16 instance
    (2026-09-04, loopback-only Homebrew instance, scratch data directory,
    trust auth; stopped and deleted afterwards).
  * Covers many simultaneous claims for one Session (16 concurrent `begin()`
    → 1 winner, 15 typed refusals), parallel Sessions, stale-claim recovery
    under three competing reconcilers (seeded run fenced and settled exactly
    once via the CAS owner swap), outbox worker competition (4 workers × 12
    intents, no double claims), and cancellation racing settlement (one
    terminal state).
  * Full lane result: `FLEET_LIVE=1 FLEET_DATABASE_URL=<disposable> make
    test-db` → 9 passed (3 Lakebase resilience + 5 concurrency tests).
  * Live-only finding: the unit-of-work does not order parent-first inserts
    for plain column FKs (no `relationship()`), so test seeding flushes
    parents before children; this matches the existing
    `test_lakebase_create_session_seeds_parents_before_child` repro and the
    `SqlAlchemySessionCatalog.create()` fix. SQLite never surfaces it because
    Fleet defers SQLite FK checks to commit.
  * The configured shared Lakebase database remains untouched (it is behind
    head; the suite fails closed rather than migrating it implicitly).

* [x] **1.13 Add database-specific repository strategies only where necessary**

  * Decision recorded (2026-09-04) with live evidence: no dialect-specific
    strategy change is needed. Claim fencing uses `FOR UPDATE` on Session/Run
    rows plus partial unique indexes, expected `IntegrityError` races are
    re-read into domain outcomes, and outbox/recovery claims use CAS rowcount
    updates. Under live PostgreSQL contention the CAS paths produced zero
    double claims and exactly-once recovery, so `FOR UPDATE SKIP LOCKED` is
    not warranted; it remains available if future contention evidence shows
    lock-wait serialization. SQLite ignores `FOR UPDATE` and is fenced by
    the unique indexes plus the reread path.

## Phase 1 exit criteria

* SQLite foreign keys are demonstrably enforced.
* Expected claim conflicts never surface as infrastructure failures.
* Exactly one Run obtains authority for a Session.
* SQLite and PostgreSQL satisfy the same externally observable lifecycle contract.

---

# Phase 2 — Simplify the DSPy program boundary

## Objective

Make DSPy usage explicit, public-API-oriented, independently testable, and bounded by one Fleet Turn budget.

DSPy’s built-ins remain native. `max_iters` bounds REPL iterations, while `max_llm_calls` bounds built-in sub-LM calls; Fleet’s global budget must sit outside both. ([DSPy][1])

## Target package

```text
src/fleet_rlm/dspy_runtime/
  program.py
  signatures.py
  models.py
  budget.py
  tools.py
  output_contract.py
  adapter.py
  callbacks.py
  compat_3_3_1.py
```

## PR 2A — Introduce `FleetProgram`

### Tasks

* [ ] **2.1 Define `FleetProgramSpec`**

```python
@dataclass(frozen=True, slots=True)
class FleetProgramSpec:
    signature: type[dspy.Signature]
    max_iters: int
    max_llm_calls: int
    max_output_chars: int
    tools: tuple[dspy.Tool, ...]
    sub_lm: dspy.LM
```

It contains only DSPy program configuration—not Session leases, Daytona resources, database objects, or event queues.

* [ ] **2.2 Introduce `FleetProgram(dspy.Module)`**

  * Internally owns one `dspy.RLM`.
  * Exposes `aforward(interpreter, inputs)`.
  * Does not acquire or close Daytona resources.
  * Does not commit Runs or artifacts.

* [ ] **2.3 Keep DSPy built-ins untouched**

  * `llm_query`
  * `llm_query_batched`
  * `print`
  * `SUBMIT`

* [ ] **2.4 Introduce `FleetToolCatalog`**

  * Contains only additional Fleet tools.
  * Validates unique Python-safe names.
  * Separates read-only and mutating capabilities.
  * Produces an immutable tuple of `dspy.Tool`.

* [ ] **2.5 Classify existing tools**

  * Sandbox-local deterministic tools.
  * Host-authorized tools.
  * Recursive tools.
  * Settlement-only operations that must never be model tools.

* [ ] **2.6 Add program-construction tests**

  * Exact signature fields.
  * Built-in tools still available.
  * Fleet custom tool schemas.
  * Duplicate/reserved names rejected.
  * Program contains no Run-specific values.

## PR 2B — Add one shared `TurnBudget`

### Tasks

* [ ] **2.7 Define `TurnBudget`**

```python
@dataclass(slots=True)
class TurnBudget:
    deadline: float
    max_provider_attempts: int
    max_input_tokens: int | None
    max_output_tokens: int | None
    max_tool_calls: int
    max_recursive_children: int
    max_sandbox_seconds: float | None
    finalization_reserve_seconds: float
```

* [ ] **2.8 Add atomic reservation methods**

  * `reserve_provider_attempt()`
  * `reserve_tool_call()`
  * `reserve_child()`
  * `reserve_tokens()`
  * `remaining_seconds()`
  * `enter_finalization()`

* [ ] **2.9 Define `BudgetExhausted` categories**

  * Deadline.
  * Provider attempts.
  * Tokens.
  * Tool calls.
  * Child calls.
  * Sandbox time.

* [ ] **2.10 Implement `BudgetedLM`**

  * Wraps an immutable `dspy.LM`.
  * Debits the shared Turn budget.
  * Calculates remaining provider timeout.
  * Records provider attempts and usage.
  * Does not mutate the wrapped LM’s `forward` or `aforward`.

* [ ] **2.11 Wrap both root and sub-LMs**

  * Root RLM action calls use the budgeted root LM.
  * DSPy’s native `llm_query` calls the budgeted sub-LM.
  * Custom child RLMs receive child-scoped views backed by the same global budget.

* [ ] **2.12 Keep DSPy local limits**

  * `max_iters` and `max_llm_calls` remain per-RLM hard limits.
  * `TurnBudget` is the outer global limit.

* [ ] **2.13 Add budget accounting tests**

  * Root only.
  * Built-in sub-LM only.
  * Recursive child.
  * Batched children.
  * Parse repair.
  * Provider retry.
  * Finalization reserve.

* [ ] **2.14 Verify no execution path can exceed the global budget**

  * Include failures and retries.
  * Include late provider responses.
  * Include child fallback behavior.

## PR 2C — Remove private DSPy mutation

### Tasks

* [ ] **2.15 Remove assignment to `LM.forward` and `LM.aforward`**

  * Replace with `BudgetedLM`.

* [ ] **2.16 Remove the `dspy.RLM._inject_execution_context` replacement**

  * Move Fleet output metadata into a Fleet-owned output contract.
  * Bind the contract to the interpreter before execution.

* [ ] **2.17 Define `FleetOutputContract`**

```text
schema_id
schema_version
output_fields
requiredness
defaults
JSON constraints
```

* [ ] **2.18 Make the Daytona interpreter understand DSPy’s public binding protocol**

  * Mutable `tools`.
  * Output metadata.
  * `CodeInterpreter` execution contract.
  * `SandboxSerializable` inputs.

* [ ] **2.19 Isolate unavoidable DSPy 3.3.1 compatibility**

  * All private imports belong in `compat_3_3_1.py`.
  * Add comments linking each compatibility behavior to a contract test.
  * No private DSPy access elsewhere.

* [ ] **2.20 Add exact-version compatibility tests**

  * Construction.
  * Tool injection.
  * Caller-owned interpreter.
  * `SandboxSerializable`.
  * Async execution.
  * Typed final output.
  * Trajectory shape.

DSPy documents that a caller-owned interpreter can be passed positionally; RLM updates its tools and output metadata but does not close it. That is the appropriate path for an asynchronously acquired Daytona interpreter. ([DSPy][1])

## PR 2D — Contract `FleetJSONAdapter`

### Tasks

* [ ] **2.21 Measure stock `JSONAdapter` versus `FleetJSONAdapter`**

  * Parse success.
  * Additional model calls.
  * completed-Run rate.
  * invalid `SUBMIT` rate.
  * wrap-up success.

* [ ] **2.22 Extract final-action validation**

  * Keep the safe `SUBMIT` AST validator in a small independent module.

* [ ] **2.23 Move deadline accounting into `TurnBudget` and `BudgetedLM`**

* [ ] **2.24 Consolidate sync and async adapter state**

  * One state machine.
  * Two invocation functions.
  * No duplicated retry policy.

* [ ] **2.25 Keep bounded parse repair only if measured**

  * Parse correction must debit the global provider-attempt budget.
  * Provider output must not be echoed into corrective prompts.

* [ ] **2.26 Keep finalization reserve independently testable**

  * Enter reserve once.
  * Reject new exploratory actions.
  * Permit at most the configured number of final attempts.

## Phase 2 exit criteria

* No runtime assignment to LM methods.
* No private RLM method replacement.
* Native DSPy built-ins remain unchanged.
* Every model request debits one global Turn budget.
* DSPy program tests run without Daytona or database composition.
* DSPy-private compatibility is isolated in one file.

---

# Phase 3 — Daytona environment definitions and prewarming foundation

## Objective

Make Sandbox environments declarative, versioned, testable, and operationally reconcilable before replacing the interpreter implementation.

Daytona’s Declarative Builder can define dependencies programmatically and create pre-built snapshots. It supports Python packages, requirements or `pyproject.toml`, local files, environment settings, system commands, work directories, users, entrypoints, and commands. ([Daytona][2])

## Target package

```text
src/fleet_rlm/sandbox/
  definitions.py
  profiles.py
  manifest.py
  dependencies.py
  reconciliation.py

src/fleet_rlm/daytona/
  client.py
  snapshot_service.py
  warm_pool_service.py
```

## PR 3A — First-class environment definitions

### Tasks

* [ ] **3.1 Introduce `SnapshotDefinition`**

```python
@dataclass(frozen=True, slots=True)
class SnapshotDefinition:
    name: str
    base_image: str
    python_version: str
    system_packages: tuple[str, ...]
    python_dependencies: tuple[str, ...]
    capabilities: frozenset[str]
    cpu: int
    memory_gib: int
    disk_gib: int
    manifest_version: int
```

* [ ] **3.2 Introduce `SandboxProfile`**

```python
@dataclass(frozen=True, slots=True)
class SandboxProfile:
    name: str
    snapshot: SnapshotDefinition
    persistence: Literal["workspace_volume", "none", "selected_files"]
    ephemeral: bool
    network_policy: NetworkPolicy
    lifecycle: LifecyclePolicy
    warm_pool: WarmPoolPolicy | None
```

* [ ] **3.3 Introduce `WarmPoolPolicy`**

  * Desired pool size.
  * Region/target.
  * Snapshot name.
  * Enabled flag.
  * Minimum ready capacity.
  * Quota failure handling.

* [ ] **3.4 Define three profiles**

```text
session
semantic-child
workspace-child
```

* [ ] **3.5 Preserve the existing immutable-name policy**

  * Never mutate `session-v6` after creation.
  * Any dependency change becomes `session-v7`.
  * Rollback means changing the selected immutable name.

* [ ] **3.6 Move current dependency parsing into `sandbox/dependencies.py`**

  * Exact version pins.
  * Stable sorted representation.
  * SHA-256 digest.
  * Distribution-to-import-name mapping.

## PR 3B — Split the snapshots

### Tasks

* [ ] **3.7 Define `fleet-rlm-session-v6`**

  * Preserve pinned Python/base image.
  * Preserve non-root user.
  * Preserve `git` and CA certificates.
  * Add only common, measured dependencies.
  * Add useful deterministic CLI tools such as `ripgrep` and `jq` if benchmark scenarios use them.

* [ ] **3.8 Define `fleet-rlm-child-v1`**

  * Minimal Python environment.
  * No Volume requirement.
  * No credentials.
  * Default `daytona` OS user to remain warm-pool compatible.
  * Smaller default resources where supported and benchmarked.

* [ ] **3.9 Create separate dependency manifests**

```text
sandbox-requirements/
  common.txt
  session.txt
  child.txt
```

* [ ] **3.10 Keep the dependency list intentionally small**

  * Current packages remain the seed.
  * Add document/data packages only when benchmark or missing-import evidence supports them.
  * Do not create a universal scientific Python image by default.

* [ ] **3.11 Add snapshot capability manifests**

Example:

```json
{
  "schema": "fleet.sandbox-runtime/v1",
  "snapshot": "fleet-rlm-child-v1",
  "python": "3.13.13",
  "dependency_sha256": "...",
  "runtime_protocol": 1,
  "capabilities": [
    "python",
    "json",
    "dataframe",
    "html",
    "ripgrep"
  ]
}
```

* [ ] **3.12 Bake the manifest into the snapshot**

  * No secrets.
  * No Fleet backend configuration.
  * No tenant or Session identifiers.

* [ ] **3.13 Verify the manifest at Sandbox acquisition**

  * Snapshot name.
  * Runtime protocol.
  * Dependency digest.
  * Python version.
  * Required capabilities.

## PR 3C — Provisioning and reconciliation

### Tasks

* [ ] **3.14 Evolve `scripts/daytona_snapshot.py`**

  * Reuse it rather than adding a second provisioning script.
  * Split into reusable service and CLI layers.

* [ ] **3.15 Add non-mutating `plan`**

```bash
fleet daytona plan
```

Report:

```text
snapshot missing
snapshot mismatch
snapshot inactive
warm pool missing
warm pool size mismatch
runtime manifest mismatch
```

* [ ] **3.16 Add idempotent `apply`**

```bash
fleet daytona apply
```

It may:

* create a missing immutable snapshot;

* activate an explicitly selected inactive snapshot, when operator policy permits;

* create or resize the semantic-child warm pool;

* never overwrite an immutable snapshot.

* [ ] **3.17 Add `check`**

  * Snapshot active.
  * Generated image/Dockerfile matches.
  * Resources match.
  * Runtime manifest matches.
  * Dependency import probes pass.

* [ ] **3.18 Extend `fleet doctor daytona`**

  * Session snapshot state.
  * Child snapshot state.
  * Warm-pool desired versus ready count.
  * Volume state.
  * Interpreter-context support.
  * Region.
  * Capability manifest.

* [ ] **3.19 Use one `AsyncDaytona` client in application lifespan**

  * Inject it into services.
  * Do not construct clients per repository/service operation.
  * Remove private SDK transport access where current public configuration supports the requirement.

* [ ] **3.20 Add a reconciliation result type**

```text
DaytonaPlan
DaytonaChange
DaytonaApplyReceipt
DaytonaDoctorReport
```

* [ ] **3.21 Add deterministic definition tests**

  * Same definition produces same Dockerfile and digest.
  * Package-order changes do not alter the canonical result.
  * Dependency changes require a new immutable snapshot name.
  * Secrets cannot appear in generated manifests.

## PR 3D — Warm-pool setup

Daytona warm pools provide pre-created running sandboxes, but matching requires the same snapshot and region, default snapshot resources and OS user, and no custom environment variables, Volumes, or secrets. That makes them appropriate for `SemanticChild`, not the Volume-backed `SessionSandbox`. ([Daytona][3])

### Tasks

* [ ] **3.22 Add warm-pool eligibility validation**

  * Reject a profile with Volume mounts.
  * Reject secrets.
  * Reject custom environment variables.
  * Reject non-default resources.
  * Reject non-default OS user.

* [ ] **3.23 Add `DaytonaWarmPoolService`**

  * List.
  * Create.
  * Resize.
  * Delete.
  * Report current ready size and provider error reason.

* [ ] **3.24 Reconcile one pool per child snapshot and region**

* [ ] **3.25 Add quota-aware failure reporting**

  * Pool unavailable must not prevent cold child creation.
  * Report degraded readiness through diagnostics.

* [ ] **3.26 Add a live warm-pool claim test**

  * Create a matching child.
  * Confirm the requested profile contains no disqualifying fields.
  * Record acquisition latency.
  * Verify a replacement warm Sandbox begins provisioning.

## PR 3E — Agent Skill and MCP development workflow

Daytona publishes an official Agent Skill with API, CLI, and SDK references, including an OpenAI Codex installation location. ([Daytona][4])

The documented MCP surface covers Sandbox management, filesystem, Git, process/code execution, computer use, and preview. It does not currently list Snapshot or warm-pool reconciliation tools, so production definitions should remain SDK/API driven. ([Daytona][5])

### Tasks

* [ ] **3.27 Document use of the official Daytona Agent Skill**

  * Development prerequisite for Daytona-focused Codex tasks.
  * Do not vendor a stale copy unless Fleet deliberately takes ownership of updating it.

* [ ] **3.28 Add a project-local Daytona implementation guide**

  * Required official-doc checks.
  * Supported SDK surfaces.
  * Prohibited private SDK access.
  * Snapshot naming policy.
  * Warm-pool constraints.

* [ ] **3.29 Add an optional MCP development guide**

  * Create disposable Sandboxes.
  * Inspect filesystem.
  * Execute smoke tests.
  * Inspect previews.
  * Never use MCP output as the committed environment definition.

* [ ] **3.30 Keep credentials outside committed MCP configuration**

  * Provide configuration shape only.
  * Resolve auth from operator environment.

## Phase 3 exit criteria

* Two immutable snapshot definitions exist.
* Definitions generate deterministic images and manifests.
* `plan`, `apply`, `check`, and `doctor` are distinct operations.
* Semantic-child warm-pool policy is declarative and idempotently reconciled.
* Session profile is explicitly rejected as warm-pool-ineligible.
* No production runtime path has switched yet.

---

# Phase 4 — Replace custom execution with Daytona’s native interpreter

## Objective

Use Daytona for Python execution, contexts, output callbacks, and context cleanup. Retain only the Fleet-specific host-tool bridge.

Daytona’s `AsyncCodeInterpreter` supports Python execution, isolated contexts, persistent state within a context, stdout/stderr/error callbacks, timeouts, and explicit context deletion. ([Daytona][6])

## PR 4A — Native Daytona interpreter adapter

### Tasks

* [ ] **4.1 Define `FleetCodeInterpreter` protocol**

  * Narrow surface required by `FleetProgram`.
  * Do not expose the entire Daytona SDK.

* [ ] **4.2 Implement `DaytonaCodeInterpreterV2`**

  * Implements DSPy’s `CodeInterpreter` contract.
  * Holds:

    * `AsyncSandbox`;
    * Daytona `InterpreterContext`;
    * Turn-local tools;
    * output contract;
    * event observer;
    * Turn authority;
    * Turn budget.

* [ ] **4.3 Acquire Daytona asynchronously before invoking DSPy**

  * Do not force asynchronous acquisition into DSPy’s zero-argument `interpreter_factory`.
  * Pass the caller-owned interpreter positionally to `rlm.acall(...)`.
  * Fleet remains responsible for closing it.

* [ ] **4.4 Create one Daytona context per Turn**

  * Set the Session workspace as `cwd`.
  * Never use the shared default context for production Turns.
  * Store the context ID only as temporary Run state.

* [ ] **4.5 Map `run_code` results**

  * stdout.
  * stderr.
  * execution error name.
  * bounded error message.
  * timeout.
  * final `SUBMIT` result.

* [ ] **4.6 Map callbacks to Fleet Runtime Events**

  * Stream stdout deltas.
  * Emit step start/finish.
  * Sanitize provider tracebacks.
  * Do not expose private model reasoning.

* [ ] **4.7 Delete the context during Run cleanup**

  * Success.
  * failure.
  * cancellation.
  * timeout.
  * partial startup failure.

* [ ] **4.8 Add context-leak diagnostics**

  * List user-created contexts.
  * Identify contexts with no active Run.
  * Delete stale contexts during bounded recovery.

## PR 4B — Input and output compatibility

### Tasks

* [ ] **4.9 Implement `SandboxSerializable` transport**

  * Send serialized bytes into Daytona.
  * Run setup/assignment code.
  * Verify maximum payload sizes.
  * Preserve attachment checksum validation.

* [ ] **4.10 Bind DSPy tool metadata per invocation**

  * Replace prior tool names completely.
  * Reject reserved names.
  * Revoke tools immediately when Run authority is lost.

* [ ] **4.11 Bind `FleetOutputContract`**

  * Typed output names.
  * Required/default behavior.
  * strict JSON validation.
  * bounded output size.

* [ ] **4.12 Preserve native `SUBMIT` semantics**

  * `SUBMIT` terminates the RLM action.
  * It returns only declared fields.
  * It cannot bypass settlement or artifact validation.

* [ ] **4.13 Add parity tests against the existing interpreter**

  * Variable persistence within one Turn.
  * No persistence across isolated contexts.
  * syntax error.
  * runtime error.
  * stdout streaming.
  * stderr streaming.
  * tool call.
  * final output.
  * large output truncation.
  * cancellation.

## PR 4C — Contract the broker into a Host Tool Gateway

### Tasks

* [ ] **4.14 Define the minimal gateway protocol**

```text
invoke:
  run_id
  capability_token
  tool_name
  arguments
  invocation_id
```

Response:

```text
status
result
error_category
```

* [ ] **4.15 Add invocation authorization**

  * Run still owns the claim.
  * Tool is authorized for the Turn.
  * Arguments satisfy the DSPy tool schema.
  * Invocation is within budget.
  * Mutation is allowed for the capability.

* [ ] **4.16 Add request idempotency**

  * A retried tool invocation with the same ID returns the same durable result where safe.
  * Non-idempotent tools must reject ambiguous retries.

* [ ] **4.17 Keep gateway errors bounded**

  * No credentials.
  * No host paths.
  * No database connection details.
  * No provider payload bodies.

* [ ] **4.18 Remove broker responsibility for**

  * Python execution.
  * interpreter namespace.
  * stdout/stderr buffering.
  * execution result polling.
  * interpreter lifecycle.
  * code compilation.
  * final execution-result storage.

* [ ] **4.19 Retain gateway responsibility for**

  * host-side tool mediation;
  * Run-authority checks;
  * recursive child allocation;
  * database-backed capabilities;
  * bounded result transport.

## PR 4D — Controlled cutover

### Tasks

* [ ] **4.20 Run broker and native interpreter contract suites**

  * Same inputs.
  * Same public events.
  * Same typed outputs.
  * Same error classifications.

* [ ] **4.21 Add live Daytona native-interpreter tests**

* [ ] **4.22 Add benchmark comparison**

  * Context creation.
  * first code execution.
  * subsequent code execution.
  * stdout streaming.
  * tool round-trip.
  * context deletion.

* [ ] **4.23 Switch the default to `native` after gates pass**

* [ ] **4.24 Retain broker mode for one migration phase only**

## Phase 4 exit criteria

* Daytona executes Python through `AsyncCodeInterpreter`.
* Every Turn uses an isolated named context.
* The custom broker no longer executes Python.
* Public Runtime Events and typed outputs remain compatible.
* The legacy broker remains available only as a temporary rollback.

---

# Phase 5 — Turn-scoped execution and lifecycle subtraction

## Objective

Retain the Session Sandbox and Volume, but rebuild DSPy/interpreter state for every Turn.

This is the phase expected to remove the most code.

## PR 5A — Introduce one `SessionSandboxManager`

### Tasks

* [ ] **5.1 Define one Session Sandbox owner**

```python
class SessionSandboxManager:
    async def acquire(session, run, deadline) -> SessionSandbox
    async def prewarm(session) -> PrewarmResult
    async def stop_idle(session) -> None
    async def retire(session) -> None
```

* [ ] **5.2 Make the database binding authoritative**

  * `session_id`.
  * `workspace_id`.
  * `sandbox_id`.
  * `snapshot`.
  * `volume_id`.
  * `volume_subpath`.
  * `provider_state`.
  * generation.
  * last verification.

* [ ] **5.3 Consolidate provider state verification**

  * Sandbox exists.
  * Expected snapshot.
  * Expected Workspace label.
  * Expected Volume and subpath.
  * Expected runtime manifest.
  * Expected lifecycle state.

* [ ] **5.4 Consolidate create/start/replace**

  * One path for missing Sandbox.
  * One path for stopped Sandbox.
  * One path for incompatible Snapshot.
  * One path for tainted Sandbox.

* [ ] **5.5 Remove parallel root registries**

  * The manager is the process-local optimization layer.
  * The database binding is the durable authority.
  * No independent RLM root registry remains.

## PR 5B — Fresh Turn runtime

### Tasks

* [ ] **5.6 Create a fresh `FleetProgram` for every Run**

* [ ] **5.7 Create a fresh Daytona InterpreterContext for every Run**

* [ ] **5.8 Create Turn-local tools**

  * No stable proxy rebinding.
  * No implementation replacement on resident tools.

* [ ] **5.9 Create Turn-local callbacks and adapter state**

* [ ] **5.10 Create Turn-local root/sub `BudgetedLM` wrappers**

* [ ] **5.11 Materialize committed Session History from PostgreSQL**

* [ ] **5.12 Materialize workspace context from the Volume manifest**

* [ ] **5.13 Destroy the interpreter context before releasing the Session Sandbox lane**

* [ ] **5.14 Add an explicit persistence test**

  * Write a committed file during Turn A.
  * Delete/recreate the interpreter context.
  * Verify the file exists during Turn B.
  * Verify an unpersisted Python variable from Turn A does not exist during Turn B.

## PR 5C — Session prewarming

The current repository already has a `prewarm_session()` concept. Refactor this behavior into `SessionSandboxManager` rather than adding another prewarm subsystem.

### Tasks

* [ ] **5.15 Trigger best-effort prewarm after Session creation**

  * Must not block Session creation.
  * Must yield to a real Run.

* [ ] **5.16 Trigger start/prewarm during attachment finalization**

  * When a Session is likely to need the Sandbox soon.

* [ ] **5.17 Trigger start concurrently with initial Turn preparation**

  * Database claim and context preparation proceed while Daytona starts.

* [ ] **5.18 Preserve active-Run priority**

  * Prewarm never blocks an actual Run beyond a tightly bounded coordination point.

* [ ] **5.19 Add idle-stop policy**

  * Stop the Session Sandbox after inactivity.
  * Preserve Volume data.
  * Record provider state in the binding.

* [ ] **5.20 Add prewarm metrics**

  * attempted.
  * completed.
  * superseded.
  * failed.
  * first-Turn latency saved.

## PR 5D — Durable cleanup ownership

### Tasks

* [ ] **5.21 Introduce `sandbox_cleanup_intents`**

Candidate fields:

```text
id
sandbox_id
workspace_id
session_id
run_id
sandbox_generation
action
state
attempt_count
next_attempt_at
last_error_category
created_at
completed_at
```

* [ ] **5.22 Persist an intent when cleanup cannot complete synchronously**

  * Exact Sandbox ID.
  * Exact generation.
  * Restricted action.
  * No authority to commit Turn outputs.

* [ ] **5.23 Add a cleanup worker**

  * Claim pending intents.
  * Confirm resource generation.
  * Perform stop/delete.
  * Confirm terminal provider state.
  * Mark complete.
  * Retry transient errors.

* [ ] **5.24 Retain minimal ownership for provider calls without a known resource ID**

  * Once a late create operation yields a Sandbox ID, hand ownership to the durable intent.
  * Do not pretend a still-running provider request can be made durable before its result is known.

* [ ] **5.25 Replace process-global late cleanup maps where possible**

  * Remove one map at a time.
  * Add a deletion test for each removed ownership path.

## PR 5E — Delete resident DSPy machinery

### Tasks

* [ ] **5.26 Remove `SessionRLMRegistry` from production composition**

* [ ] **5.27 Remove `ProgramFingerprint` and canonical fingerprint machinery**

* [ ] **5.28 Remove RLM-generation rotation**

* [ ] **5.29 Remove resident `sub_lm` mutation**

* [ ] **5.30 Remove stable Session tool proxies**

* [ ] **5.31 Remove observer rebinding on resident RLM objects**

* [ ] **5.32 Remove interpreter/root-lease transfer between program generations**

* [ ] **5.33 Delete tests that exist only to validate removed internal mechanisms**

  * Replace with behavior-level Session persistence and concurrency tests.

## Phase 5 exit criteria

* Session continuity survives Sandbox restart through database and Volume state.
* Python globals do not survive between Turns.
* One Session manager owns Sandbox reuse.
* A fresh DSPy RLM is created per Turn.
* No production `SessionRLMRegistry` or program fingerprint remains.
* Late cleanup can survive application restart once a concrete resource identity is known.

---

# Phase 6 — Recursive RLM v2

## Objective

Make native recursive children selective, cheaper, minimally contextualized, and globally budgeted.

## PR 6A — Typed recursive contracts

### Tasks

* [ ] **6.1 Define `SubproblemCapsule`**

```python
class SubproblemCapsule(dspy.SandboxSerializable):
    purpose: str
    question: str
    selected_context: tuple[ContextFragment, ...]
    context_refs: tuple[ContextRef, ...]
    expected_output: ChildOutputContract
    constraints: tuple[str, ...]
    budget: ChildBudgetRequest
```

* [ ] **6.2 Define strict capsule limits**

  * Maximum fragments.
  * Maximum total bytes.
  * Maximum individual fragment size.
  * Maximum reference count.
  * No arbitrary complete Session History by default.

* [ ] **6.3 Define `ContextFragment`**

  * ID.
  * source type.
  * bounded text or bytes.
  * checksum.
  * trust classification.

* [ ] **6.4 Define `ContextRef`**

  * Authorized workspace-relative path or durable object reference.
  * Never an unrestricted host filesystem path.

* [ ] **6.5 Define `ChildResult`**

```text
status
answer
structured_output
evidence_refs
usage
iterations
termination_mode
error_category
```

* [ ] **6.6 Define partial batch output**

  * Preserve result order.
  * Each child has an independent status.
  * Successful siblings are not discarded automatically.

## PR 6B — Explicit routing hierarchy

### Tasks

* [ ] **6.7 Define route categories**

```text
python
semantic_single
semantic_batch
recursive_semantic
recursive_workspace
```

* [ ] **6.8 Establish route guidance**

  * Python for deterministic parsing, search, aggregation, and verification.
  * `llm_query` for one semantic judgment.
  * `llm_query_batched` for independent semantic judgments.
  * `rlm_query` only for iterative exploration.
  * Workspace child only when the subproblem requires real workspace state.

* [ ] **6.9 Keep `rlm_query` as a Fleet custom `dspy.Tool`**

* [ ] **6.10 Keep `rlm_query_batched` only if benchmarks justify it**

  * Otherwise let `RecursiveScheduler` accept a list internally behind one custom tool.

* [ ] **6.11 Add a deterministic pre-router only for obvious cases**

  * Do not build a second general-purpose agent.
  * The root RLM remains responsible for ambiguous decomposition.

* [ ] **6.12 Add routing telemetry**

  * Selected route.
  * Alternative eligible routes.
  * Context size.
  * cost.
  * latency.
  * quality result.

## PR 6C — Two child environments

### Tasks

* [ ] **6.13 Implement `SemanticChildEnvironment`**

  * Uses `fleet-rlm-child-v1`.
  * No Volume.
  * No secrets.
  * No custom environment values that prevent warm-pool matching.
  * Receives only the serialized capsule.
  * Ephemeral.
  * Hard TTL/auto-delete safety.

* [ ] **6.14 Claim semantic children from the warm-pool-compatible profile**

* [ ] **6.15 Implement `WorkspaceChildEnvironment`**

  * Uses selected uploaded files or a restricted Volume subpath.
  * Never receives the entire Workspace by default.
  * Not expected to use a warm pool.
  * Higher budget/cost classification.

* [ ] **6.16 Add child profile selection**

  * Derived from capsule requirements.
  * Explicitly observable.
  * Fail closed if required data cannot be provided safely.

* [ ] **6.17 Preserve one native child level**

  * A child may use its own built-in `llm_query`.
  * A child request for another native RLM uses a bounded sub-LM fallback.
  * No grandchild Sandbox.

## PR 6D — Shared budget and structured concurrency

### Tasks

* [ ] **6.18 Reserve child capacity from `TurnBudget` before acquisition**

  * Provider attempts.
  * token allocation.
  * Sandbox count.
  * maximum child duration.
  * finalization reserve.

* [ ] **6.19 Refund only explicitly refundable reservations**

  * Never refund consumed provider attempts.
  * Unused token allowance may return to the parent.
  * Sandbox allocation remains counted once created.

* [ ] **6.20 Replace nested child event loops with one structured scheduler**

  * One application event loop.
  * `asyncio.TaskGroup`.
  * One bounded semaphore.
  * One sync-to-async bridge only where DSPy’s synchronous tool invocation requires it.

* [ ] **6.21 Implement sibling cancellation**

  * User cancellation.
  * global deadline.
  * global budget exhaustion.
  * fatal authorization failure.

* [ ] **6.22 Preserve successful sibling results**

  * Parent decides whether partial evidence is sufficient.
  * Settlement remains fail-closed for required artifacts or mutations.

* [ ] **6.23 Add deterministic cleanup ordering**

  * stop useful work;
  * close interpreter context;
  * delete disposable child;
  * confirm deletion or persist cleanup intent;
  * return result to parent.

* [ ] **6.24 Add recursive failure taxonomy**

  * acquisition.
  * authorization.
  * timeout.
  * budget.
  * execution.
  * output validation.
  * cleanup.

## PR 6E — Recursive evaluation gate

### Tasks

* [ ] **6.25 Evaluate five modes**

```text
A. Predict/simple DSPy baseline
B. stock dspy.RLM
C. dspy.RLM with built-in sub-LM only
D. current Fleet recursive implementation
E. capsule-based recursive implementation
```

* [ ] **6.26 Measure**

  * answer score;
  * evidence score;
  * completion rate;
  * latency;
  * provider attempts;
  * root/sub tokens;
  * delegated context;
  * Sandbox seconds;
  * child failures;
  * cleanup failures;
  * cost per successful task.

* [ ] **6.27 Define an enablement threshold**

  * Recursion should not be the default merely because it works.
  * It should provide a documented quality-per-cost improvement on target scenarios.

* [ ] **6.28 Tune default limits from results**

  * `max_iters`.
  * `max_llm_calls`.
  * recursive calls.
  * parallel children.
  * child output.
  * context capsule size.

## Phase 6 exit criteria

* Children receive bounded capsules, not complete Session state.
* Semantic children can claim Daytona warm capacity.
* Workspace children are explicit and comparatively rare.
* Every child consumes the global Turn budget.
* One sibling failure does not automatically destroy valid sibling evidence.
* Native child depth remains fixed at one.
* Recursion is enabled based on measured value.

---

# Phase 7 — Evaluation, DSPy optimization, rollout, and deletion

## Objective

Use actual task metrics to optimize stable DSPy programs, then delete all temporary legacy paths.

## PR 7A — Production evaluation dataset

### Tasks

* [ ] **7.1 Create a versioned evaluation dataset**

  * Representative successful tasks.
  * Representative failures.
  * Synthetic sensitive-data substitutes.
  * Long-context tasks.
  * file and artifact tasks.
  * recursive and non-recursive tasks.

* [ ] **7.2 Define deterministic metrics where possible**

  * exact output;
  * JSON/schema validity;
  * file checksum;
  * artifact presence;
  * tool-policy compliance;
  * budget compliance;
  * citation/evidence reference validity.

* [ ] **7.3 Add semantic metrics only where necessary**

  * correctness.
  * completeness.
  * grounding.
  * usefulness.

* [ ] **7.4 Separate quality and efficiency scores**

  * A correct but unnecessarily recursive execution should score lower on efficiency.
  * A cheap but incorrect execution should fail quality.

* [ ] **7.5 Version every metric**

  * No candidate is promoted against an unversioned metric.

## PR 7B — Model and route optimization

### Tasks

* [ ] **7.6 Differentiate model roles**

  * Stronger root model.
  * Cheaper/faster sub-LM.
  * Child root model selected by task class.
  * Avoid using the strongest model everywhere by default.

* [ ] **7.7 Establish unoptimized baselines**

  * No optimizer should be introduced before these exist.

* [ ] **7.8 Optimize stable instructions**

  * Root RLM guidance.
  * route-selection guidance.
  * capsule-construction guidance.
  * child completion guidance.

* [ ] **7.9 Use GEPA only after metrics and dataset are representative**

  * Candidate instructions remain isolated.
  * Every candidate records dataset and metric digests.
  * Promotion requires a held-out improvement.
  * Latency and cost regressions are part of the gate.

* [ ] **7.10 Do not add Flex yet**

  * Reassess after the fixed architecture reaches stable quality and the evaluation suite can judge alternative code structures reliably.

## PR 7C — Controlled rollout

### Tasks

* [ ] **7.11 Add shadow comparison where safe**

  * Do not duplicate user-visible side effects.
  * Shadow only pure/evaluation scenarios or use recorded inputs.

* [ ] **7.12 Roll out in sequence**

  1. Native Daytona interpreter.
  2. Turn-scoped runtime.
  3. Capsule recursion.
  4. Warm semantic children.
  5. Optimized instructions.

* [ ] **7.13 Monitor**

  * failure rate;
  * p50/p95 latency;
  * provider attempts;
  * child utilization;
  * cleanup backlog;
  * warm-pool hit rate;
  * cost per successful Turn.

* [ ] **7.14 Define automatic rollback thresholds**

  * Public contract regression.
  * cleanup backlog growth.
  * budget violations.
  * increased unclassified failures.
  * material quality regression.

## PR 7D — Delete migration code

### Tasks

* [ ] **7.15 Remove broker execution mode**

* [ ] **7.16 Remove legacy resident runtime mode**

* [ ] **7.17 Remove legacy recursion policy**

* [ ] **7.18 Remove obsolete configuration fields**

* [ ] **7.19 Remove compatibility aliases and migration-only adapters**

* [ ] **7.20 Delete tests that validate removed internals**

* [ ] **7.21 Update architecture and operational documentation**

* [ ] **7.22 Produce a code-subtraction report**

  * Files deleted.
  * lines removed.
  * state machines removed.
  * process-global registries removed.
  * remaining private DSPy/Daytona dependencies.

## Phase 7 exit criteria

* Only one production runtime implementation remains.
* No migration feature flags remain.
* Optimized DSPy instructions have held-out evidence.
* Recursive execution has a documented selection policy.
* Daytona definitions, snapshots, warm pools, and runtime behavior are operationally inspectable.
* Architecture documentation matches the production implementation.

---

# Temporary migration modes

Keep only these temporary switches:

| Setting                  | Legacy                  | New                        |
| ------------------------ | ----------------------- | -------------------------- |
| `runtime.implementation` | resident RLM            | Turn-scoped RLM            |
| `daytona.interpreter`    | custom broker execution | native Daytona interpreter |
| `rlm.recursion_policy`   | full Session snapshot   | `SubproblemCapsule`        |

They should not become permanent configuration choices. Each must have a deletion task in Phase 7.

---

# Recommended PR order

A practical merge order is:

1. **ADR and terminology**
2. **Baseline instrumentation**
3. **SQLite foreign keys**
4. **Run-claim race translation**
5. **Postgres concurrency suite**
6. **`FleetProgram`**
7. **`TurnBudget` and `BudgetedLM`**
8. **DSPy private-API contraction**
9. **Snapshot/Profile definitions**
10. **Session and child snapshot manifests**
11. **Daytona plan/apply/check/doctor**
12. **Semantic-child warm-pool reconciliation**
13. **Native Daytona interpreter adapter**
14. **Native input/output parity**
15. **Broker contraction**
16. **`SessionSandboxManager`**
17. **Fresh RLM and interpreter context per Turn**
18. **Durable cleanup intents**
19. **Resident-runtime deletion**
20. **`SubproblemCapsule` and `ChildResult`**
21. **Semantic and workspace child profiles**
22. **Structured recursive scheduler**
23. **Recursive evaluation gate**
24. **Model-role and instruction optimization**
25. **Legacy implementation deletion**

No PR should simultaneously:

* change database claim semantics;
* replace Daytona execution;
* alter recursive routing;
* and change public Runtime Events.

Those boundaries need independent review and rollback.

---

# Global definition of done

The full roadmap is complete when all of these statements are true:

* A Session can survive process and Sandbox replacement using only PostgreSQL and its Daytona Volume.
* A Turn always uses a new DSPy RLM and isolated interpreter context.
* DSPy’s built-in RLM tools are used directly.
* Fleet custom tools are clearly separated and authorized.
* Every root, sub-LM, retry, and child request consumes one global Turn budget.
* Daytona snapshots are immutable declarative definitions.
* Session and child environments use separate profiles.
* Semantic children can use warm pools.
* Volume-backed Session Sandboxes use Fleet-level prewarming instead.
* Daytona’s native code interpreter executes model-generated Python.
* Fleet’s custom broker has been reduced to host-tool mediation or removed entirely if a safer direct mechanism becomes available.
* Recursive children receive bounded capsules rather than complete Session state.
* One child failure does not erase successful sibling evidence.
* SQLite enforces foreign keys.
* PostgreSQL concurrency behavior is tested under real contention.
* Expected database conflicts are not reported as infrastructure failures.
* Late cleanup has durable ownership once the resource identity is known.
* No resident RLM registry, program fingerprint, or stable tool-rebinding framework remains.
* Recursion and optimized prompts are enabled because evaluation demonstrates value, not merely because the features exist.

[1]: https://dspy.ai/api/modules/RLM/ "RLM - DSPy"
[2]: https://www.daytona.io/docs/en/declarative-builder/ "Declarative Builder | Daytona"
[3]: https://www.daytona.io/docs/en/warm-pools/ "Warm Pools | Daytona"
[4]: https://www.daytona.io/docs/en/agent-skills/ "Agent Skills | Daytona"
[5]: https://www.daytona.io/docs/mcp/ "Daytona MCP Server | Daytona"
[6]: https://www.daytona.io/docs/en/python-sdk/async/async-code-interpreter/ "AsyncCodeInterpreter | Daytona"
