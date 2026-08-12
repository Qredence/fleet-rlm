# `dev-0.7` Replacement-Readiness Implementation Plan

I re-audited `dev-0.7` **only**. I did **not** inspect or compare against `main`.

The branch is currently at `c34e7d84d8dd753e94d08dc987fef686f1f65e62`.

This was a static code/config/test/release audit through GitHub. I did not execute the repository locally or run live Daytona infrastructure, so runtime certification steps below remain actual implementation gates rather than claims of current success.

---

## 1. Assumptions and constraints

I'm making these assumptions explicitly:

1. `dev-0.7` **is intended to become the complete canonical code line.** We do not need to preserve internal implementation compatibility with `main`.
2. **Current public behavior should remain stable through the replacement.** HTTP routes, SSE framing, durable Result shapes, Workspace semantics, database semantics, wheel contents and TUI behavior are compatibility contracts unless we deliberately identify a bug.
3. This is a **clarity + readiness program**, not a feature program. In particular, I would defer the native-async DSPy RLM experiment until after the branch replacement.
4. A refactor is justified only when it removes concepts, duplication or ownership ambiguity. **A smaller file is not by itself a better module.**
5. Security/lifecycle comments explaining *why* something unusual exists stay. Comments/docstrings that merely restate code can be deleted.
6. No new runtime configuration knobs unless unavoidable. In particular, the Workspace Agent timeout should reuse an existing execution policy or a single internal bound.
7. Simple ternaries remain acceptable. **Nested ternaries do not.**

The branch is already substantially better structured than a typical pre-replacement branch. A sweeping rewrite would increase replacement risk rather than reduce it.

---



# 2. Executive assessment

I would **not replace the default branch today**.

The architecture itself is strong enough. The remaining work falls into two categories:

### Replacement blockers

1. Configuration policy inventory is not fully synchronized with runtime configuration.
2. The newest multi-child `rlm_query_batched` path lacks a real Daytona two-child live certification.
3. Workspace Agent calls need a provider-level execution timeout so owned post-commit work is provably bounded.
4. Declared Python support and DSPy dependency policy are not completely aligned with CI.
5. `dev-0.7` currently has no branch protection / required status checks configured.
6. The explicit no-nested-ternary clarity requirement isn't mechanically enforced and there are current TUI violations.



### High-value clarity debt

The largest useful deepening opportunities are concentrated in:

- `chat/turn_coordinator.py`
- `rlm/runner.py`
- `rlm/recursive_calls.py`
- `daytona/interpreter.py`
- `daytona/workspace_agent.py`
- reload `UIMessagePart`
- TUI live/durable projection

I would **not** refactor large-but-cohesive areas such as the persistence state machine, URL fetch security, Workspace gateway, TUI store reducer or supervisor simply because they are long.

---



# 3. Architecture I would preserve

These should effectively be architectural invariants for the replacement program:

```text
FastAPI
  ↓
TurnCoordinator
  ↓
claim + preparation
  ↓
PreparedRun
  ↓
RunExecutionDriver
  ↓
RLMRunner
  ↓
native dspy.RLM
  ├── Python
  ├── llm_query
  ├── llm_query_batched
  ├── rlm_query
  └── Root-only rlm_query_batched
         ├── isolated child Sandbox A
         └── isolated child Sandbox B
  ↓
RunLifecycle
  ↓
Result / Artifact commit
  ↓
owned post-commit Memory promotion
  ↓
settlement + resource release

```

Also preserve:

- `RLM_NATIVE_CHILD_DEPTH = 1`.
- Width rather than arbitrary recursive depth.
- Fresh child Sandboxes and interpreters.
- Per-child copied Root/Sub DSPy runtimes.
- Root as sole final-answer authority.
- RuntimeEvent ≠ durable AssistantPart ≠ live UI transport chunk.
- exact Alembic-head production schema validation.
- accepted-stream SSE semantics.
- mounted Workspace Agent as the single privileged Workspace mutation boundary.
- immediate explicit Workspace/Memory writes versus post-commit autonomous Memory Candidates.

---



# 4. Implementation sequence

I would use **13 focused PRs**, followed by one release-candidate gate.

The order matters. Later clarity refactors should happen only after the branch has stronger behavioral certification.

---



# P0 — Establish the replacement gates



## PR 1 — Enforce the clarity rules first



### Goal

Make your clarity requirements executable so later refactors cannot reintroduce the problems we're removing.

### Files

Modify:

```text
tools/fleet-tui/biome.json
tools/fleet-tui/src/tui/projection.ts
tools/fleet-tui/src/tui/commands.ts

scripts/check_codebase_tree.py
tests/.../test_check_codebase_tree.py   # use existing checker-test location

```



### Current problems

There are already nested ternaries in the TUI.

`projection.ts` computes prior RLM content through nested conditionals.

`commands.ts` also builds `/profiles` status text with a multi-level ternary.

Biome's current rule set supports `style.noNestedTernary`; it is not part of the default recommended rules, so Fleet needs to enable it explicitly. ([Biome](https://biomejs.dev/linter/rules/no-nested-ternary/?utm_source=chatgpt.com))

### Implementation

Enable:

```json
{
  "linter": {
    "rules": {
      "recommended": true,
      "style": {
        "noNestedTernary": "error",
        "noNonNullAssertion": "off"
      }
    }
  }
}

```

Rewrite the existing TUI cases as explicit branches.

For example, instead of:

```ts
const state =
  conditionA ? valueA
  : conditionB ? valueB
  : conditionC ? valueC
  : "";

```

use:

```ts
let state = "";

if (conditionA) {
  state = valueA;
} else if (conditionB) {
  state = valueB;
} else if (conditionC) {
  state = valueC;
}

```

For Python, extend the **existing** AST checker rather than adding another repository script. `check_codebase_tree.py` already parses every backend Python file.

Detect an `ast.IfExp` whose body or else branch contains another `ast.IfExp`.

### Do not do

- Don't ban ordinary single ternaries.
- Don't enable a formatter/rule that rewrites simple `if` statements *into* ternaries.
- Don't create `check_nested_ternaries.py`.



### Acceptance

```text
TUI Biome check passes
Python repository checker passes
Existing tests pass
A checker fixture with nested Python IfExp fails
A simple Python conditional expression remains allowed

```

---



# PR 2 — Make configuration policy one coherent contract



### Goal

Remove the most concrete runtime/configuration drift and reduce duplicated policy parsing.

### Current bug

Runtime `Settings` contains:

```python
rlm_autonomous_memory_categories: tuple[str, ...]

```

and the TOML validator and flattener support:

```toml
rlm.autonomous_memory_categories

```

But `ConfigPolicyService._FIELDS` enumerates the editable RLM policy and omits it.

That means an intentionally supported policy setting is invisible from the editable policy surface.

### Files

Modify:

```text
src/fleet_rlm/config.py
src/fleet_rlm/config_policy.py

tests/unit/backend/test_config.py
tests/unit/backend/test_config_policy.py

```

Use actual existing test filenames if their names differ.

### Implementation



#### A. Add the missing field

Use the canonical Memory Candidate categories as choices if they are a closed set.

Conceptually:

```python
PolicyField(
    "rlm.autonomous_memory_categories",
    "RLM",
    "Autonomous Memory categories",
    "multi_choice",
    MEMORY_CANDIDATE_CATEGORIES,
    settings_field="rlm_autonomous_memory_categories",
)

```

Do **not** duplicate the category vocabulary in `config_policy.py`.

#### B. Add a policy-inventory invariant

Test that:

```text
Every ConfigPolicyService field:
  → is accepted by the TOML schema

Every explicitly editable non-secret runtime field:
  → is represented in ConfigPolicyService

```

That turns drift into a test failure.

#### C. Remove duplicate root-document parsing

`config.py` currently repeats root/config/defaults/profiles parsing and validation between runtime settings resolution and profile-contract resolution.

Extract one small helper, e.g.:

```python
@dataclass(frozen=True)
class PolicyDocument:
    default_profile: str | None
    defaults: Mapping[str, Any]
    profiles: Mapping[str, Any]

```

and:

```python
def _read_policy_document(path: Path) -> PolicyDocument:
    ...

```

Keep environment-variable resolution separate.

### Explicitly reject

I would **not** implement a generic `SettingSpec` framework that dynamically generates Pydantic settings, TOML validation, APIs and editors.

That would solve duplication by introducing a more difficult abstraction.

### Acceptance

- all existing profiles load;
- default profile still resolves identically;
- unknown TOML fields fail closed;
- autonomous Memory categories appear/edit correctly;
- secret/environment values remain unavailable through the policy API;
- no changes to `Settings` public behavior.

---



# PR 3 — Make Workspace Agent execution provably bounded



### Goal

Finish the post-commit ownership fix by bounding the provider operation itself.

### Why this matters

`OwnedPostCommitMemoryPromotion` correctly retains ownership after its short visible wait and `PreparedRun.aclose()` waits before releasing the resources the operation uses.

That's the correct resource-ordering rule.

But `run_workspace_agent()` / `run_workspace_agent_async()` currently call Daytona:

```python
sandbox.process.code_run(code)
await sandbox.process.code_run(code)

```

without supplying an explicit execution timeout.

Daytona's process API supports a bounded `timeout` parameter. ([daytona.io](https://www.daytona.io/docs/en/python-sdk/async/async-process/?utm_source=chatgpt.com))

So an indefinitely stalled provider operation can theoretically convert correct ownership into indefinitely retained ownership.

### Files

Modify:

```text
src/fleet_rlm/daytona/workspace_agent.py
src/fleet_rlm/daytona/workspace_fs.py
src/fleet_rlm/daytona/workspace_memory.py
src/fleet_rlm/daytona/run_environment.py   # only if needed for timeout injection

```

Tests:

```text
Workspace Agent contract tests
Workspace Memory promotion tests
Run preparation/cleanup tests

```



### Interface

Prefer:

```python
def run_workspace_agent(
    sandbox: Any,
    *,
    timeout_s: float,
    **arguments: Any,
) -> dict[str, object]:

```

and equivalent async version.

But don't expose another public/TOML setting.

Use an already-owned Runtime timeout if it represents this boundary correctly; otherwise define **one internal constant**.

### Required semantics

```text
post-commit visible wait expires
        ↓
promotion remains owned
        ↓
provider process has its own bounded timeout
        ↓
provider call settles/fails
        ↓
PreparedRun closes
        ↓
lease releases

```

Not:

```text
visible wait expires
        ↓
detach thread forever

```

and not:

```text
visible wait expires
        ↓
release Sandbox while thread still uses it

```



### Acceptance

Hostile fake:

```text
process.code_run never completes normally

```

must prove:

- public post-commit wait remains short;
- resource ownership remains correct;
- provider call eventually times out;
- `PreparedRun.aclose()` eventually returns;
- lease releases exactly once.

---



# PR 4 — Add the missing real multi-child certification



### Goal

Live-certify the newest and most concurrency-sensitive RLM path before making it canonical.

### Current state

The existing live recursive test proves a real single child RLM and child Sandbox lifecycle.

The deterministic recursive tests cover batch ordering, reservations, concurrency, deadline behavior and cleanup.

What is missing is the combined proof:

```text
real DSPy
+
real Daytona
+
2 simultaneous child RLMs
+
real cleanup

```



### Create

Prefer a focused new test:

```text
tests/live/backend/test_daytona_recursive_batch.py

```

rather than enlarging the already-purposeful single-child test.

### Scenario

Root must call:

```python
rlm_query_batched([prompt_a, prompt_b])

```

with exactly two independent investigations.

Verify:

```text
Child A Sandbox ID != Child B Sandbox ID
Child A recursive Volume path != Child B path

both children started
both children completed

peak_child_concurrency == 2

results correspond to [prompt_a, prompt_b]
regardless of completion order

Root receives both
Root performs final synthesis
Root SUBMIT succeeds

all child Sandboxes removed
all admission permits returned
no active child lease remains

```

Also assert child fan-out isn't exposed recursively.

### Do not do

- no 8-child stress test;
- no general distributed scheduler test;
- no new concurrency config;
- no arbitrary recursive depth.

One golden two-child live canary is enough.

---



# PR 5 — Align package support, dependency policy and CI



### Goal

Make the branch's declared support equal what the build system actually certifies.

### Current mismatch 1 — Python

The package advertises:

```toml
requires-python = ">=3.11,<3.14"

```

and classifiers for 3.11, 3.12 and 3.13.

Current main CircleCI Python jobs run on Python 3.13.13.

The release preflight runs Python 3.12.

So 3.11 is declared but not part of the visible regular/release certification surface.

### Current mismatch 2 — dead dependency

The package also declares:

```toml
tomli>=2.4.1; python_version < '3.11'

```

while the package itself requires Python `>=3.11`.

Delete it.

### Current mismatch 3 — DSPy policy

`pyproject.toml` deliberately supports:

```text
dspy >= 3.3.0, < 3.4

```

and comments explicitly describe a `3.3.x` compatibility contract.

But the dedicated dependency workflow says:

```text
Verify dspy==3.3.0

```

and probes exactly that version.

Those are two different policies.

### Recommendation

Keep the patch-range runtime policy and make the availability workflow check the **locked DSPy version**.

Do not exact-pin the project to `3.3.0` merely to make the workflow accurate.

### CI architecture

Keep Python 3.13 as the full gate.

Add lightweight compatibility jobs for:

```text
Python 3.11
Python 3.12

```

that run:

```text
lock/install
import/package validation
unit + contract tests

```

They do not need the full Daytona-coverage and E2E workload.

### Existing full gates

CircleCI already has separate:

- quality,
- lint/typecheck,
- unit,
- e2e,
- Daytona coverage,
- TUI

jobs.

Keep that structure.

---



# P1 — Simplify ownership and orchestration



## PR 6 — Give Turn preparation one explicit owner



### Goal

Reduce the densest orchestration nesting without changing the Run state machine.

### Problem

`TurnCoordinator.open()` has to simultaneously reason about:

```text
claim
heartbeat
preparation task
claim-loss task
client cancellation
preparation timeout
quarantine
cleanup failure
handoff into execution

```

Some helpers are also imported privately from `run_execution.py`, creating a backwards ownership dependency between sibling orchestration modules.

### Create

```text
src/fleet_rlm/chat/run_ownership.py
src/fleet_rlm/chat/preparation_attempt.py

```

Only if both materially simplify the callers.

### `run_ownership.py`

Move only operations genuinely shared by preparation and execution:

```text
claim heartbeat ownership
shielded owned cleanup
task result consumption
terminal-state helper if genuinely shared

```

These should stop being `_private_symbol` imports across modules.

### `PreparationAttempt`

Give one object responsibility for:

```text
preparation task
claim-loss waiter
cancel/settle
quarantined owned task

```

Something conceptually like:

```python
class PreparationAttempt:
    async def wait(self) -> PreparedRun: ...
    async def cancel_and_settle(self) -> None: ...

```

No framework, no polymorphic attempt hierarchy.

### Keep

```text
TurnCoordinator
RunExecutionDriver
RunLifecycle

```

as separate top-level roles.

### Acceptance

Existing behavior tests should cover:

- claim lost during preparation;
- cancellation during preparation;
- timeout;
- late provider completion;
- client disconnect during `open`;
- cleanup failure;
- successful handoff to execution;
- claim heartbeat termination.

A reviewer should be able to understand `TurnCoordinator.open()` primarily by reading its top-level branches, not five local closures.

---



# P2 — Deepen the RLM implementation



## PR 7 — Extract pure trajectory reconciliation from `RLMRunner`



### Goal

Let `runner.py` read as an execution orchestration module.

### Current mixed concern

`runner.py` contains a substantial pure transformation subsystem:

```text
_trajectory_details
_preserve_stream_id
_stream_text
_align_trajectory_detail
_same_stream_payload
_detail_position
_trajectory_insertion
_reconcile_trajectory
...

```

alongside worker ownership, cancellation, DSPy invocation, recursive execution, usage and outcome construction.

### Create

```text
src/fleet_rlm/rlm/trajectory_projection.py
tests/unit/backend/rlm/test_trajectory_projection.py

```



### Move

Only pure trajectory/live-detail reconciliation.

Do **not** introduce a `TrajectoryService` class.

Prefer straightforward functions with domain names.

### Also simplify Memory candidate typing

Current `PreparedCapabilities` says:

```python
def drain_memory_candidates(self) -> tuple[Any, ...]

```

and the implementation also returns `Any`.

Make the contract:

```python
def drain_memory_candidates(self) -> tuple[MemoryCandidate, ...]:

```

Then the runner can call it directly rather than supporting a dynamic legacy path.

No `getattr`, no `callable`, no hidden compatibility behavior if every current adapter implements the protocol.

### Acceptance

Given identical trajectory + live observations:

```text
emitted RuntimeEvents identical
durable ExecutionDetails identical
stream IDs identical
correction behavior identical

```

---



# PR 8 — Separate batched scheduling from recursive child semantics



### Goal

Make `RecursiveRLMExecutor` easier to verify without creating another public abstraction.

### Keep in `recursive_calls.py`

- recursive policy validation;
- single-child semantics;
- depth fallback;
- child native RLM construction;
- per-child cleanup;
- public `tool` and `batched_tool`;
- summary/metrics.



### Move to

```text
src/fleet_rlm/rlm/recursive_batch.py

```

only:

```text
bounded ThreadPool scheduling
context copy
deadline-aware future aggregation
input-order restoration
queued cancellation
running-child retention
batch error settlement

```



### Narrow interface

For example:

```python
run_reserved_batch(
    reservations: Sequence[RecursiveCallReservation],
    *,
    execute: Callable[[RecursiveCallReservation], str],
    deadline: float,
    max_parallel: int,
    ...
) -> list[str]

```

Use concrete domain types rather than an extensible scheduler abstraction.

### Naming

If internal `call_count` represents reserved recursive calls—not only completed calls—rename it to remove ambiguity.

For example:

```text
reserved_call_count

```

Metrics such as completed children remain separate.

### Acceptance

Preserve all current batch tests for:

- atomic reservation;
- order;
- max parallelism;
- first failure;
- timeout;
- queued cancellation;
- running lease retention;
- worker-local spans;
- cleanup error propagation.

---



# PR 9 — Extract the DSPy synchronous Daytona bridge



### Goal

Make `daytona/interpreter.py` describe the Fleet interpreter rather than also implementing the entire sync/async transport bridge.

### Create

```text
src/fleet_rlm/daytona/dspy_sync_bridge.py

```



### Move

```text
_SyncBridgeLoop
_sync_await
_SyncCodeInterpreter
_SyncProcess
_SyncFileSystem
_SyncDaytonaSandbox
sync_sandbox
bridge_service_loop
set_bridge_service_loop

```



### Rename

I would rename:

```python
_SyncDaytonaSandbox

```

to:

```python
_DSPySyncSandboxView

```

because the underlying provider authority is still an async Daytona Sandbox.

The current runtime already constructs async Daytona clients/sandboxes and only uses this synchronous view for DSPy's synchronous interpreter seam.

### Keep in `interpreter.py`

```text
DaytonaCodeInterpreter
broker wiring
host Tool mediation
SUBMIT
execution observations
repair feedback
DSPy CodeInterpreter contract

```



### Explicitly do not do

- no Daytona synchronous client stack;
- no `RLM.acall()` migration;
- no worker-thread removal;
- no async-interpreter fork of DSPy.

That belongs after branch replacement.

---



# P3 — Remove the largest readability liability



## PR 10 — Turn Workspace Agent into real Python source

This is the highest-value pure readability refactor in the branch.

### Problem

`workspace_agent.py` currently generates its remote program as a very large Python tuple/list of quoted source lines.

The implementation contains security-sensitive code such as CAS checking, fd-relative path access, locking, inode revalidation, delete semantics, atomic replacement/fallback and fsync.

That logic being encoded as strings makes:

- code review harder;
- syntax navigation worse;
- static analysis weaker;
- changes noisier;
- indentation control manual.



### Target

```text
daytona/
  workspace_agent.py
  workspace_agent_runtime.py

```

`workspace_agent_runtime.py` should be **actual Python source** containing the remote stdlib-only program.

`workspace_agent.py` should own:

```text
request validation
argument serialization
runtime-source loading
response decoding
host-side exception mapping

```



### Important constraint

Do not rewrite the security algorithm while extracting it.

This PR should be nearly semantic-zero.

### Recommended loading

Load the packaged source text using `importlib.resources`.

Do not:

- import it as host runtime behavior;
- duplicate it in a constant;
- `inspect.getsource()` a loaded module;
- generate a second template language.



### Packaging

Add a release assertion that the source exists in the wheel.

`validate_release.py` already has an explicit required-wheel-file contract.

Add the runtime source there.

### Golden behavior test

Before moving the source, lock the protocol against:

- traversal;
- symlink;
- FIFO;
- oversized read;
- checksum mismatch;
- atomic write;
- append;
- patch unique/missing/ambiguous;
- empty-directory delete;
- non-empty-directory conflict;
- Memory mutation;
- fsync warning;
- WORM fallback.

Then perform extraction.

### Success criterion

The remote agent is now readable as ordinary Python, while:

```text
number of provider round trips
wire payload
response shape
error mapping
security behavior

```

remain unchanged.

---



# P4 — Make public contracts easier to reason about



## PR 11 — Replace reload `UIMessagePart` mega-envelope with a discriminated union



### Current issue

Live SSE chunks are already well modeled as a strict Pydantic discriminated union.

Reload still uses:

```python
class UIMessagePart(BaseModel):
    type: Literal[...many...]
    text: str | None
    state: str | None
    id: str | None
    tool_name: str | None
    tool_call_id: str | None
    input: JsonValue
    output: JsonValue
    error_text: str | None
    provider_executed: bool | None
    data: JsonValue

```

That allows invalid combinations to exist in the schema even though runtime producers are much stricter.

### Target

Define exact models:

```text
TextUIMessagePart
ReasoningUIMessagePart
DynamicToolUIMessagePart
StepStartUIMessagePart
DataStatusUIMessagePart
DataStepUIMessagePart
DataRLMCodeUIMessagePart
...

```

and:

```python
UIMessagePart = Annotated[
    TextUIMessagePart
    | ReasoningUIMessagePart
    | ...,
    Field(discriminator="type"),
]

```



### Critical constraint

**Serialized JSON must not change.**

This is a schema/internal clarity refactor, not an API redesign.

### Regenerate

```text
openapi.yaml
tools/fleet-tui/src/generated/openapi.ts

```

through the existing generation path.

### Contract tests

For every durable part variant:

```text
old fixture JSON
→ validates under exact variant
→ serialization equals fixture

```



### Do not remove snake/camel compatibility yet

The live transport deliberately supports compatibility fields, and the TUI still reads both forms.

Removing those during a branch replacement would combine an architectural rebase with a public protocol break.

Defer that.

---



# PR 12 — Split TUI live projection from durable reload projection



### Problem

`tui/projection.ts` contains two different state transformations:

```text
LiveTurnProjector
    SSE chunks → StoreEvents

projectDurableTurns
    persisted UIMessage → StoreEvents

```

Those have different source contracts and different lifecycle semantics.

### Target

```text
tui/live-projection.ts
tui/durable-projection.ts

```



### Keep explicit

Do not create:

```text
AbstractProjectionEngine
ProjectionStrategy
ProjectionRegistry

```

The whole point is to make the two semantics obvious.

### Shared helpers

Extract only tiny pure message constructors if they are substantially shared.

For example:

```text
message factory functions
numeric/string normalization
artifact/attachment result payload conversion

```

may remain in a small `projection-helpers.ts`.

### Keep unchanged

`fleet-turn-stream.ts` should remain the stream-order grammar. It currently clearly enforces start, terminal, `[DONE]`, step, reasoning, text and Tool lifecycle rules.

`store.ts` should remain the single store reducer; despite its size, its discriminated event/state model is understandable and cohesive.

---



# P5 — Small consolidation pass



## PR 13 — Delete proven low-risk duplication

This is deliberately last because these edits are useful but not worth destabilizing the earlier work.

### A. Composition construction

Where `composition/daytona.py` creates a complete immutable inventory and then reconstructs it just to replace one member, use:

```python
dataclasses.replace(...)

```

instead of repeating every field.

This is exactly the sort of redundancy that can create drift.

### B. Memory injection

Where the normalized injection query is already computed, reuse it instead of deriving it a second time.

### C. Skills wording

Fix stale/inaccurate diagnostics and comments in the manifest layer.

Do not alter allowed-tools semantics; the current model is now clear:

```text
allowed-tools = advisory/model-visible information
allowed-tools ≠ authorization

```



### D. Remove obvious comments/docstrings

Only in files already touched.

Delete things like:

```text
"Returns: The result"
"Parameters: foo (Foo): The Foo"

```

when Python typing already states it.

Keep comments like:

```text
Why executor shutdown uses wait=False
Why a Sandbox lease cannot release yet
Why O_NOFOLLOW exists
Why the stream accepts startless error terminal
Why a provider operation is shielded

```

Those carry architecture.

---



# 5. Refactors I would *not* require before replacement

These are tempting but don't currently pass the simplicity test.

## A. Do not generically merge Workspace and Project Tool hosts

`workspace_tools.py` and `project_tools.py` contain obvious duplication.

But a shared declarative Tool framework could easily produce more indirection than code deletion.

If you revisit it, extract only pure payload/error helpers and require a clear net deletion.

Not a blocker.

---



## B. Do not split `dspy_contract.py` into six modules

It is large, but it is intentionally Fleet's single DSPy compatibility boundary.

If, after the other refactors, it still feels difficult, the one extraction I would consider is:

```text
rlm/dspy_tracing.py

```

because tracing/provider telemetry is the most independent concern.

Don't automatically create:

```text
dspy_constructor.py
dspy_usage.py
dspy_callbacks.py
dspy_trajectory.py
dspy_versions.py
...

```

That would replace one broad compatibility seam with many files every maintainer needs to chase.

---



## C. Do not collapse durable dataclasses and Pydantic AssistantPart models yet

There is representation duplication between durable committed-turn types and Pydantic AssistantPart validation.

It may be removable, but it is high-risk because it affects:

- persistence,
- model equality,
- schema validation,
- reload,
- serialization.

Run a deletion spike **after replacement**.

Proceed only if one representation can delete the converters and preserve all behavior.

---



## D. Do not rewrite `AsyncDaytonaVolumeFS` / sync volume adapters

`DaytonaSessionWorkspaceFS` already has a good architecture: native async implementation as source of truth, with a narrow synchronous DSPy bridge.

Some byte-I/O duplication remains, but this isn't where branch-replacement risk lies.

---



## E. Do not start the async DSPy RLM migration

Keep:

```text
worker thread
→ native dspy.RLM
→ DSPy synchronous interpreter
→ sync view
→ Daytona AsyncSandbox

```

for the replacement.

Only revisit `RLM.acall()` + native async interpreter after the canonical branch is stable.

---



# 6. Release-candidate certification

Once PRs 1–13 have landed, stop changing architecture.

Create one RC commit and run the following gates.

## Deterministic gate

```bash
make check
make check-security
make build-release
make check-release
make api-check
make stream-check
git diff --check

```

The current CircleCI structure already separates quality, static checks, unit, E2E, Daytona coverage and TUI gates.

Require every one.

---



## Python compatibility gate

Require green compatibility lanes for:

```text
3.11
3.12
3.13

```

matching the package metadata.

---



## Persistence gate

On a clean database:

```text
alembic upgrade head
start Fleet
compatibility check passes
basic Session → Turn → Result persistence

```

On a representative existing **dev-0.7** database:

```text
upgrade to head
start Fleet
existing Sessions reload
new Turn commits

```

There are currently three migration revisions on this branch: canonical coordinated baseline, settling recovery and recovery-scan index.

No need to inspect a `main` database.

---



## Live Daytona gate

Run:

1. ordinary Root Turn;
2. Workspace read/write/edit/delete;
3. attachment read;
4. artifact commit;
5. explicit Memory CRUD;
6. post-commit Memory Candidate promotion;
7. one single recursive child;
8. **new two-child batched recursion canary**;
9. cancellation during execution;
10. deadline/timeout cleanup.

After every Run:

```text
no leaked admission permit
no leaked child Sandbox
no leaked interpreter
no resource-owning background task after settlement

```

---



## Public transport gate

Use the same semantic Turn through:

```text
live SSE
replay SSE
durable Session reload
TUI live projection
TUI durable projection

```

Prove equivalent final user-visible content.

Do not require identical internal transport events—only the intended semantic equivalence.

The backend intentionally uses different live and durable contracts; that separation should remain.

---



## Wheel gate

Build and inspect the actual wheel.

The release checker already verifies mandatory backend/Skill payloads and rejects forbidden files.

After PR 10 also require the Workspace Agent runtime source to be present.

Then perform the existing TestPyPI installed-wheel smoke rather than testing only the source checkout. The release workflow already has a dedicated TestPyPI path.

---



# 7. Repository-level cutover gate

The code isn't the only part of replacing the default branch.

Right now `dev-0.7` is not protected and has no required status checks configured in GitHub branch protection.

After the full rebase / default-branch switch, configure the replacement branch so merges require the canonical gates.

At minimum:

```text
quality
lint-typecheck
test-unit
test-e2e
daytona-coverage
tui
Python compatibility

```

I would do this **after** the rebase/default-branch operation so you don't spend effort configuring a temporary branch that will disappear.

---



# 8. Priority / stop points

There are three sensible stopping levels.

### Minimum safe replacement

I would not replace the default branch without:

```text
PR 1  clarity enforcement
PR 2  config correctness
PR 3  bounded Workspace Agent execution
PR 4  live batched-child certification
PR 5  CI/runtime/dependency alignment
PR 6  explicit preparation ownership

full RC certification
branch protection after cutover

```



### Recommended replacement

For a branch intended to become the long-lived canonical codebase, I would additionally complete:

```text
PR 7   trajectory extraction
PR 8   batch scheduler extraction
PR 9   DSPy sync bridge extraction
PR 10  Workspace Agent real source
PR 11  reload discriminated union
PR 12  TUI projection separation
PR 13  small deletion sweep

```

This is the cut I recommend.

### Defer until after replacement

```text
native async DSPy interpreter
Root/Sub model tuning
public casing removals
AssistantPart/durable-model unification
generic Workspace/Project Tool deduplication
additional dspy_contract splitting
Volume FS consolidation
general comment/docstring cleanup campaign

```

---



# 9. What the codebase should look like afterward

The goal is **not** many more abstractions. It should become easier to draw:

```text
chat/
  turn_coordinator.py
  preparation_attempt.py
  run_execution.py
  run_lifecycle.py
  run_ownership.py

rlm/
  runner.py
  trajectory_projection.py
  recursive_calls.py
  recursive_batch.py
  dspy_contract.py

daytona/
  interpreter.py
  dspy_sync_bridge.py
  workspace_agent.py
  workspace_agent_runtime.py

```

The important change is what each filename means:

```text
TurnCoordinator
    coordinates

PreparationAttempt
    owns in-flight preparation

RunExecutionDriver
    drives one prepared Run

RunLifecycle
    settles durable state

RLMRunner
    orchestrates one native RLM

trajectory_projection
    reconciles canonical DSPy trajectory with live details

RecursiveRLMExecutor
    owns recursive semantics

recursive_batch
    owns only sibling scheduling

DaytonaCodeInterpreter
    owns DSPy Tool/SUBMIT execution semantics

DSPySyncSandboxView
    adapts DSPy's sync interpreter to Daytona AsyncSandbox

WorkspaceAgent host
    owns protocol

WorkspaceAgent runtime
    is ordinary readable Python

```

That's the kind of simplification I would expect a senior engineer to notice immediately: **fewer responsibilities per module, but no explosion of interfaces.**

## Bottom line

The branch is already architecturally close to replacement quality. I would **not use this phase for another large rewrite**.

The remaining program should be:

```text
1. Close the concrete correctness/certification gaps.
2. Mechanically enforce the requested clarity rules.
3. Deepen only the four genuinely overloaded seams.
4. Make reload/TUI contracts explicit.
5. Freeze.
6. Run one release-candidate certification.
7. Full rebase / default-branch replacement.
8. Enable required checks.

```

The biggest gains now come from **making ownership and representation boundaries obvious**, not from reducing raw line count at any cost.