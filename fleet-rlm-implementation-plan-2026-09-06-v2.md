# Fleet RLM - implementation plan

Revision: **2026-09-10 - simplification rewrite**. The filename is retained for stable links.

This document is the **forward implementation plan**. It intentionally does not duplicate the historical evidence ledger. Completed work, dated receipts, provider observations, and certification status belong in [ADR 006 implementation status](docs/decisions/006-implementation-status.md).

Reviewed input branch: `fix/adr006-runtime-continuation` at `dba3e3ab81144aec0b4e978c23c4fece52305cd7`, before this plan-only rewrite. At that revision the branch was 11 commits ahead of `main`; GitHub CI had passing quality/lint/TUI jobs but failing unit and Python 3.11/3.12/3.13 compatibility jobs. Re-establishing a reproducible green gate is the first implementation task.

Pinned runtime targets remain **DSPy 3.3.1**, **Daytona Python SDK 0.210.0**, and **MLflow 3.16.0**. Do not change those pins as part of architectural simplification unless a separately justified compatibility fix requires it.

## 1. Goal

Fleet should become easier to understand by removing overlapping owners and migration layers, not by adding another abstraction over them.

The desired execution model is:

```text
FastAPI / SSE
    -> TurnRuntime
        -> RunLifecycle + preparation
        -> RLM execution
            -> DSPy RLM
            -> TurnBudget
            -> bounded tools
            -> Daytona execution boundary
        -> settlement

Durable Session state
    -> PostgreSQL / supported local SQLite
    -> Daytona Workspace Volume

Ephemeral execution state
    -> one Run-owned RLM invocation
    -> one Run-owned interpreter/sandbox execution boundary
    -> one recursive scheduler
```

The final architecture must have **one production execution path**. The current branch is allowed to carry legacy and native feasibility paths only while the containment decision is unresolved.

### Keep these ownership boundaries

| Concern | Owner |
| --- | --- |
| Run claim, cancellation, settlement, committed Turn | `chat/` + persistence |
| DSPy program, LM/tool budget, output contract | `rlm/` |
| Daytona SDK, Sandbox/interpreter/provider lifecycle | `daytona/` |
| Workspace files and memory semantics | `workspace/` and durable storage owners |
| HTTP/SSE projection | `api/` |
| Engineering traces/evaluation | `observability/` and `optimization/` |
| Process wiring | `composition/` |

Do not create a second owner for any row above.

## 2. Simplification rules

These rules apply to every phase below.

1. **Delete or consolidate before adding.** A new permanent module, manager, registry, queue, task owner, compatibility layer, or test file needs a concrete reason an existing owner cannot handle the responsibility.
2. **Prefer one supported path over configurable duplication.** A migration flag is temporary. Once a path is selected and rollback evidence exists, remove the other implementation.
3. **Do not generalize for hypothetical providers.** Daytona is the canonical Run Environment. Keep an interface only when it enforces a real domain boundary or has more than one real implementation.
4. **Refactors should normally be net-negative in permanent complexity.** Line count is not an acceptance test, but a refactor that adds more permanent code/owners than it removes needs explicit justification.
5. **Do not split large files merely to improve file size.** First delete obsolete responsibilities. Split only when the remaining code has two independently meaningful owners.
6. **Do not create one test file per bug, issue, helper, or implementation class.** Add a regression to the existing behavior-owning suite unless it needs a distinct fixture, marker, process, or live environment.
7. **Test behavior boundaries, not implementation choreography.** Preserve race, security, settlement, containment, persistence, API, and public-event guarantees. Delete tests whose only purpose is to pin an internal that is being removed.
8. **Use parameterized scenario tables for equivalent cases.** Avoid near-duplicate test functions that differ only by state/error/status values.
9. **Keep live/certification evidence separate from deterministic unit tests.** A unit fake must not duplicate a provider campaign, and a provider campaign must not become the ordinary test suite.
10. **No new agent framework layer.** Do not add Flex, ReActV2, another `dspy.Module` wrapper, grandchildren, or another orchestration loop during this migration.
11. **Warm capacity is optional optimization.** It stays disabled until recursive demand and quality/cost evidence justify it.
12. **The plan stays short and reports completion.** When a task is completed, mark its heading/status as **complete** in this plan with a concise dated outcome, then update the ADR006 status ledger with the supporting evidence. Remove obsolete implementation detail instead of turning this file into another receipt archive.

## 3. What is already strong and should not be redesigned

Do not reopen these areas without a demonstrated defect:

- `TurnRuntime` / `RunLifecycle` authority and settlement ordering.
- database-enforced Run/Turn/Sandbox Binding lineage and binding generation fencing.
- exact DSPy 3.3.1 pin and native `dspy.RLM` use.
- `TurnBudget`, typed output validation, and process-scoped LM templates with Turn-local mutable state.
- generated HTTP/SSE/TUI contracts.
- provider error normalization and fail-closed resource cleanup semantics.
- explicit operator gates for provider, database, warm-pool, benchmark, and optimization mutations.
- the distinction between native `llm_query`/`llm_query_batched` and Fleet recursive child RLM work.

The objective is to make those guarantees require **less code**.

## 4. Complexity hotspots to reduce

The reviewed branch contains several very large execution modules, including approximately:

| Module | Current size | Desired direction |
| --- | ---: | --- |
| `rlm/session_runtime.py` | 115 KB | Delete after resident Session RLM reuse is no longer a production requirement |
| `runtime/daytona/run_environment.py` | 104 KB | Dissolve responsibilities into existing owners and delete the duplicate Daytona package boundary |
| `rlm/recursion.py` | 102 KB | Keep only capsule policy, one scheduler, typed outcomes, and model-facing recursion bindings |
| `daytona/broker.py` | 95 KB | Either reduce to Host Tool mediation or retain it as the chosen execution boundary; do not keep two execution servers |
| `rlm/runtime.py` | 87 KB | Remove resident/native compatibility branches after one production path is selected |
| `daytona/interpreter.py` | 59 KB | Keep one DSPy interpreter adapter and delete backend behavior superseded by the selected execution path |
| `rlm/program.py` | 58 KB | Keep program construction; remove migration-only conditionals and duplicated adapter policy |
| `rlm/compat_3_3_1.py` | 50 KB | Reduce to genuinely version-specific DSPy compatibility |

Do not set arbitrary line-count targets for these files. The acceptance criterion is fewer responsibilities, fewer owners, fewer state transitions, and fewer compatibility branches.

## 5. Target source shape

Do **not** merge real domains such as Sessions, Attachments, and Artifacts merely to reduce the number of top-level folders. The directory problem is duplicated execution layers, especially `daytona/` versus `runtime/daytona/`.

Preferred end state:

```text
src/fleet_rlm/
├── api/              # HTTP/SSE only
├── chat/             # Turn/Run orchestration and settlement
├── sessions/         # Session/Turn domain
├── workspace/        # Workspace files, memory, projects
├── attachments/      # durable input domain
├── artifacts/        # committed output domain
├── persistence/      # SQLAlchemy/Alembic adapters
├── rlm/              # DSPy program, budget, events, recursion
├── daytona/          # the only Daytona/runtime provider package
├── observability/    # MLflow/PostHog/diagnostics
├── optimization/     # offline evaluation/GEPA only
├── config/
├── skills/
├── composition/
├── cli/
├── app.py
└── main.py
```

The intended subtraction is therefore:

```text
src/fleet_rlm/runtime/daytona/  -> remove
src/fleet_rlm/runtime/          -> remove if its remaining helpers have no independent domain owner
resident RLM infrastructure     -> remove if native/Run-scoped execution is selected
one of broker/native execution  -> remove after containment decision and rollback proof
legacy recursive prompt path    -> remove after capsule path is selected
```

If `runtime/cleanup.py` or `runtime/owned_effect.py` remain genuinely shared after the migration, keep a tiny `runtime/` package rather than moving code only to satisfy the tree target. The rule is conceptual ownership, not folder aesthetics.

## 6. Phase sequence

| Phase | Purpose | Gate |
| --- | --- | --- |
| **0 - Stabilize and freeze growth** | Restore trustworthy CI and remove ambiguous policy | Green deterministic gate |
| **1 - Decide the execution containment boundary** | Resolve the detached-process blocker before more native work | One written production execution decision |
| **2 - Runtime subtraction and source simplification** | Remove duplicate owners and the unselected execution path | One comprehensible runtime graph |
| **3 - Test-suite consolidation** | Preserve guarantees with fewer, behavior-oriented suites | Green suite with less duplication |
| **4 - Recursive RLM simplification and value proof** | Make recursion small and optional | Recursion either proves value or is reduced/disabled |
| **5 - Operational certification without feature growth** | Close snapshots, DB, MLflow, capacity evidence | Reproducible production candidate |
| **6 - Promotion, rollback, and final deletion** | Select one supported runtime and remove migration residue | One production path, no orphan migration machinery |

The phases are intentionally sequential. In particular, do not continue expanding native production architecture before Phase 1 decides the containment boundary.

---

# Phase 0 - Stabilize and freeze growth

**Status: complete (2026-09-10).** The deterministic unit lane now passes on
Python 3.11, 3.12, and 3.13; `make check` passes on the clean candidate. The
ADR006 status ledger records the reproducible inventory and wall-time baseline.
Until Phase 1 selects a containment boundary, freeze runtime variants, recursive
Tool/depth expansion, warm capacity, persistence tables, and feature flags.

## P0.1 - Restore a reproducible green deterministic gate — complete

**Rationale:** Architectural simplification cannot be evaluated while local and CI evidence disagree.

**Implement:**
- reproduce the failing `test-unit` and Python 3.11/3.12/3.13 compatibility jobs from the reviewed branch head;
- fix the underlying code/test/environment mismatch, not the CI assertion;
- do not change architecture while diagnosing these failures unless the failure itself demonstrates an architectural defect.

**Validate:** the focused failing jobs first, then `make check`.

**Done when:** the same clean candidate passes unit, quality, lint/typecheck, TUI, and supported-Python compatibility lanes.

## P0.2 - Make trace-content policy simpler and explicit — complete

**Rationale:** Trace-content visibility must be explicit, bounded, and consistently sanitized so authorized engineering traces remain useful without changing public Runtime Event semantics.

**Implement:**
- preserve bounded, sanitized provider reasoning / chain-of-thought and system-prompt content in the authorized engineering trace destination when `mlflow.trace_content_enabled` is enabled;
- keep public reasoning/status events explicitly separate from MLflow engineering traces;
- retain user input/final output/tool evidence behind the same bounded trace-content policy;
- keep `mlflow.trace_content_enabled = false` as the operational-only trace policy;
- align tracing tests and config documentation when the implementation changes.

**Validate:** tracing privacy tests plus tests where content capture is enabled for bounded reasoning/system-prompt fields and disabled for operational-only traces.

**Done when:** readable MLflow content is bounded/sanitized by one policy switch, operational-only tracing remains available, and the policy is described in one place.

## P0.3 - Freeze new runtime features and schema growth — complete

**Rationale:** The branch already has sufficient migration machinery. More features before subtraction will compound the problem.

**Implement:**
- no new runtime variant;
- no new recursive depth/tool family;
- no new warm-capacity feature;
- no new persistence table unless a currently unresolved correctness issue requires durable state;
- route new findings into an existing owner or defer them.

**Validate:** review each Phase 0+ change for new managers/registries/tables/feature flags.

**Done when:** the next changes can focus on containment and deletion without another moving target.

## P0.4 - Record a small complexity baseline — complete

**Rationale:** Simplification should be observable without turning line count into the goal.

**Implement:** record, in the ADR006 status ledger or a small generated receipt:
- Python source file count under `src/fleet_rlm/`;
- Python test file count by unit/contract/live lane;
- top 10 source files by size;
- test collection count and deterministic-suite wall time;
- number of production runtime variants, recursive model-facing tools, process-global runtime owner collections, and explicit schedulers/executors.

Do not add a permanent framework solely to collect these numbers; a small script or existing tooling is sufficient.

**Validate:** rerun the same inventory after Phases 2 and 3.

**Done when:** later simplification can show which owners/files/tests actually disappeared.

**Phase 0 exit: complete.** Clean deterministic CI, stable policy, no new architecture growth.

---

# Phase 1 - Decide the execution containment boundary

**Status: complete (2026-09-10).** The minimal provider proof repeated four
negative results for `code_interpreter.delete_context`: an ordinary child
completed, but a detached process-session child survived context deletion. The
whole-Sandbox benchmark completed all deletion checks but measured 33.296 s p95
create-through-first-execution, above the 10 s operational threshold. Retained
broker execution (option B) remains the sole production execution boundary.
The bounded receipts and decision rationale are recorded in the ADR006 status
ledger.

## P1.1 - Reduce the native containment proof to one minimal provider test — complete

**Rationale:** One small, authoritative live test is easier to reason about than containment behavior spread across a broad feasibility campaign.

**Implement:** keep a focused live case that:
- creates one disposable sandbox and explicit interpreter context;
- starts one ordinary child process and one detached/process-session child;
- deletes/closes the interpreter context;
- verifies which processes remain;
- deletes the whole sandbox and confirms absence.

Do not add additional fake layers around this proof.

**Validate:** operator-gated Daytona live lane with a bounded receipt.

**Done when:** the provider behavior is reproducible and the result is independent of Fleet's higher-level RLM machinery.

## P1.2 - Test exactly one provider-supported process-tree containment mechanism — complete

**Rationale:** The clean native target only works if Fleet can terminate everything generated by one Run before reusing the Sandbox.

**Implement:** inspect the pinned Daytona 0.210.0 public process/interpreter capabilities and select the smallest supported candidate for whole-process-tree termination. Wire it into the minimal proof only. Do not patch Python's `subprocess`, monkey-patch generated code, or maintain a blacklist of process creation APIs.

**Validate:** the same detached-process test must pass repeatedly, including timeout/cancellation cleanup.

**Done when:** either process-tree containment is demonstrated or the provider mechanism is conclusively insufficient for Fleet's contract.

## P1.3 - If native context containment fails, compare the two real fallbacks — complete

**Rationale:** Fleet should choose one safe execution boundary rather than carrying broker and native paths indefinitely.

**Implement:** compare only:

**Option A - retained broker execution**
- keep the current broker as the process-containment/execution boundary;
- still remove resident DSPy/session complexity where it is not required.

**Option B - Turn-scoped Sandbox**
- create a fresh Sandbox per Run;
- mount/reconnect the durable Workspace Volume as required;
- delete the whole Sandbox before successful final cleanup;
- no Session Sandbox reuse assumption.

Use the existing lifecycle benchmark/evaluation harness. Do not invent a third architecture.

**Validate:** containment, public behavior, latency, failure rate, cleanup success, and cost on matched tasks.

**Done when:** one fallback is clearly safer and operationally acceptable.

## P1.4 - Record the production execution decision — complete

**Rationale:** Every later deletion depends on knowing which execution boundary is authoritative.

**Implement:** update ADR006/status with exactly one selected target:

```text
A. reusable Session Sandbox + proven process-tree containment + fresh Run context
B. retained broker execution boundary
C. Turn-scoped Sandbox + whole-Sandbox deletion
```

State why the other two are rejected/deferred and what rollback means.

**Validate:** architecture review against Run claim, cancellation, containment, Volume continuity, and settlement invariants.

**Done when:** there is one target execution model and no unresolved "maybe both" production design.

**Phase 1 exit: complete.** Retained broker execution is selected. No Phase 2 deletion begins before this decision.

---

# Phase 2 - Runtime subtraction and source simplification

**Progress (2026-09-10):** P2.1 through P2.7 are complete. New immutable Session
`fleet-rlm-python313-v10` and SemanticChild `fleet-rlm-python313-child-v5` images
were probed and certified; configured/code fallbacks still resolve to v7/v2 until
operator promotion (P5.1).

## P2.1 - Remove the duplicate `runtime/daytona` package boundary — complete

**Status: complete (2026-09-10).** `runtime/daytona/` and every
production/test import of it were removed. Turn preparation now lives in
`composition/daytona_run_preparation.py`; composition retains the Daytona-backed Workspace
gateway wiring in `composition/daytona_workspace_gateway.py`. P2.2 reduces the
remaining overlapping lifecycle owners.


**Rationale:** Daytona-specific runtime code currently exists both under `daytona/` and `runtime/daytona/`, including a ~104 KB `run_environment.py`. This creates an artificial ownership layer without a second provider.

**Implement:**
- move provider lifecycle responsibilities to existing `daytona/` owners;
- keep Turn preparation/orchestration in `chat/`;
- keep Workspace semantics in `workspace/`;
- keep composition-only wiring in `composition/`;
- eliminate `runtime/daytona/run_environment.py` and `runtime/daytona/workspace_gateway.py` once their responsibilities have existing homes;
- do not replace them with a renamed mega-manager.

**Validate:** dependency-boundary checks, focused Run preparation tests, Daytona tests, and import search proving no `runtime.daytona` references remain.

**Done when:** Daytona runtime behavior has one package boundary.

## P2.2 - Collapse Daytona resource ownership to one graph — complete

**Status: complete (2026-09-10).** `DaytonaRuntimeResources` owns
process-lifetime cleanup, client close, late lookup, and provider retention;
`DaytonaSessionManager` owns active Session claims and late lease/acquisition
records. Production root acquisition now delegates directly to `DaytonaRuntime`,
which solely owns root replacement and cleanup. The preparation adapter keeps
only Run assembly; its remaining local root index is compatibility-only and is
not used by the composed Daytona runtime.

**Rationale:** `session_manager.py`, Run-environment owners, lease helpers,
provider task sets, late-acquisition maps, cleanup supervisors, and root
replacement previously overlapped. The selected production graph now assigns
those responsibilities to the resource owner, Session manager, and
`DaytonaRuntime` respectively.

**Implement:** define the minimal ownership chain for the selected Phase 1 architecture:

```text
application lifespan
    -> one AsyncDaytona client
    -> one Session/Run resource owner
        -> Sandbox binding/generation
        -> interpreter/execution owner
        -> cleanup obligation
```

Then migrate responsibilities one at a time and delete the old owner immediately after its last consumer moves. Do not add another registry during migration.

**Validate:** cancellation, late provider completion, replacement generation, cleanup retry, and application shutdown scenarios.

**Done when:** a resource has one obvious owner at every lifecycle state and the process-global owner collections are materially reduced.

## P2.3 - Remove the unselected Session runtime model — complete

**Status: complete (2026-09-10).** Production `RLMRunner` creates a fresh
DSPy program, direct tool bindings, callbacks, and worker executor for each
Run. Sequential-Run coverage proves committed durable history remains the
cross-Turn input while Python program state is not reused. `DaytonaRuntime`
retains broker roots independently of a Run program. `SessionRLMRegistry`,
program fingerprints, resident generations, and stable Tool proxy rebinding
were deleted with their production integrations and direct tests.

**Rationale:** `rlm/session_runtime.py` and its fingerprints/generations/tool rebinding exist primarily to keep DSPy/interpreter state resident across Turns.

**Implement if Phase 1 selects Run-scoped DSPy state:**
- remove `SessionRLMRegistry` from production composition;
- delete `ProgramFingerprint` and fingerprint component canonicalization;
- delete RLM generation rotation;
- delete stable tool-proxy rebinding;
- delete resident observer/LM/output-schema rebinding;
- delete interpreter transfer logic;
- delete `rlm/session_runtime.py` when no supported path imports it.

**Implement if broker execution remains:** still separate "broker execution" from "resident DSPy program"; keep resident state only if a product behavior actually requires cross-Turn Python/DSPy state and is covered by an explicit behavior contract.

**Validate:** two sequential Turns preserve durable Session history/workspace behavior while no Python global/tool/callback state leaks unintentionally.

**Done when:** cross-Turn correctness depends on PostgreSQL/Volume/committed history rather than a resident DSPy object graph, unless that behavior is explicitly retained as a product feature.

## P2.4 - Keep only one code-execution implementation — complete

**Status: complete (2026-09-10).** Removed native Turn preparation and the
runner's native branch, `TurnScopedRuntimeLease`, duplicate worker builder, and
production-composition native context factory/cancellation owner. Prepared
execution rejects all variants except `legacy`. The unreferenced
`NativeInterpreterBackend` feasibility probe, its native binding-watch support,
benchmark, and direct tests were deleted. No alternate code-execution
implementation remains in the source tree.

**Rationale:** The branch currently carries broker execution plus a native interpreter path. Keeping both permanently defeats the migration.

**Implement:**
- if native/process-tree containment wins: shrink `daytona/broker.py` to the smallest Host Tool gateway required for sandbox-to-host callbacks, then delete broker-owned Python namespace/execution/output lifecycle;
- if retained broker wins: delete native-production cutover scaffolding that no longer has a product path, while keeping small provider probes only where useful;
- if Turn-scoped Sandbox wins: use the simplest interpreter transport that passes the containment/public-contract tests and delete the other execution path.

**Validate:** typed `SUBMIT`, stdout/output bounds, host-tool authorization, cancellation, and final cleanup.

**Done when:** a developer does not have to understand two Python execution engines to change Fleet.

## P2.5 - Reduce DSPy compatibility code to compatibility only

**Status: complete (2026-09-10).** `FleetJSONAdapter` and its shared
sync/async repair/finalization policy now belong to the existing `program.py`
owner, alongside `DeadlineLMProxy`; accounting remains in `budget.py`.
Retry and wrap-up field insertion share one collision-safe helper. The pinned
iteration-marker interpretation, private DSPy type imports, version guard,
callback projection and interpreter contracts remain in `compat_3_3_1.py`.
Adapter consumers and the ownership test use the new home. Validation is
recorded in the ADR006 continuation ledger; no DSPy version or retry policy changed.

**Rationale:** `rlm/compat_3_3_1.py` currently includes both pinned-version adaptation and substantial Fleet retry/finalization policy.

**Implement:**
- keep exact DSPy version checks, version-specific type/contract adaptation, and unavoidable private compatibility in `compat_3_3_1.py`;
- move or merge Fleet-owned budget/retry/finalization behavior into the existing `budget.py`, `program.py`, output, or submit owners rather than creating another compatibility package;
- remove duplicate sync/async logic where one shared policy can drive both paths.

**Validate:** native DSPy contract tests, adapter parse-repair/finalization tests, and provider-attempt accounting.

**Done when:** upgrading DSPy primarily requires reviewing one small compatibility seam, not a large Fleet policy module.

## P2.6 - Simplify Workspace file execution selectively — complete

**Status: complete (2026-09-10).** The pinned Daytona SDK capability gate now
proves its filesystem API lacks Fleet's bounded download and cursor controls,
and has no append, patch, checksum/CAS, or atomic-publication contract. The
operation audit therefore classifies every custom Workspace operation as
semantic rather than historical. No unsafe SDK substitution was made; the
Workspace Agent remains the sole filesystem semantics owner.

**Rationale:** The custom Workspace Agent has stronger write/patch semantics than generic Volume APIs in some places, but read/list/search wrappers may duplicate Daytona SDK functionality.

**Implement:** use the existing operation audit to classify each operation:
- keep custom code for path confinement, CAS/checksum, locking, atomic replace, or other guarantees not matched by the SDK;
- replace only simple read/list/stat/search paths where SDK parity is proven;
- delete the replaced protocol/client/server code in the same change.

**Validate:** path traversal/symlink safety, checksum/CAS behavior, write atomicity, and read/list parity.

The pinned-SDK capability gate and operation audit must also prove that no
current custom operation is historical; path traversal, symlink, checksum/CAS,
atomicity, and bounded-read contracts remain mandatory.

**Done when:** there is no custom remote filesystem operation whose only reason for existence is historical.

## P2.7 - Minimize Daytona snapshot dependencies

**Status: complete (2026-09-10).** Audited Fleet's two `SandboxSerializable`
implementations, the pinned DSPy serialization prelude, and broker setup:
committed history and attachment context reconstruct using the standard
library and broker helpers. Future Session/WorkspaceChild definitions omit
DSPy and retain the four analysis packages; SemanticChild adds no Python
packages. Runtime verification imports and checks the versions of the selected
profile's declared dependencies. A socket-free `python -I -S` subprocess
regression proves broker history/context reconstruction and typed SUBMIT
without DSPy. New immutable Session `fleet-rlm-python313-v10` and
SemanticChild `fleet-rlm-python313-child-v5` images were created and passed
runtime probes; each probe's disposable Sandbox was deleted. Configured names
and prior images are unchanged rollback references.

An opt-in aggregate certification controller now preserves the configured
rollback references while it checks and probes both immutable candidates, then
runs a narrow Session host-tool/RLM stream proof and the SemanticChild recursive
proof as separate evidence lanes. The broad Session MVP remains an independent
lifecycle/durability proof and is not a P2.7 snapshot-dependency prerequisite.
The 2026-09-10 live attempt did not pass: both disposable image probes succeeded,
but the candidate-scoped Session MVP failed in its first RLM Turn. Its bounded
aggregate receipt is `.fleet-evidence/receipts/adr006/p27-reduced-snapshots-20260910-r2.json`;
the retained diagnostic receipt is `.scratch/p27-debug-mvp-test-20260910.json`.
The replacement focused certification passed on candidate
`caa4fd83b578f610e28f6f0e792b04aa0da41d9e`: both immutable image probes,
the Session host-tool/RLM stream proof, and the SemanticChild recursive proof
passed with disposable cleanup confirmed. Its sealed bounded receipt is
`.fleet-evidence/receipts/adr006/p27-reduced-snapshots-20260910-r4.json`.
This completes P2.7; manual promotion remains a separate operator decision.

**Rationale:** DSPy controls the RLM loop on the host. Installing DSPy inside every sandbox is unnecessary unless generated remote setup actually imports it.

**Implement:**
- search every `SandboxSerializable`/setup-code path for remote `dspy` imports;
- if no runtime import is required, remove DSPy from Session/SemanticChild snapshot requirements;
- retain only packages generated Python is expected to use;
- regenerate immutable snapshot identities instead of mutating existing names.

**Certification contract:** certify only immutable Session and SemanticChild
candidates; preserve the configured snapshot references until manual promotion;
reject mutable names, dirty candidates, failing probes, and malformed receipts
before promotion. The existing snapshot probe, focused Session stream verifier,
and focused recursive verifier are the supported evidence paths.

**Remaining operator action:** after receipt review, promote the configured
snapshot references atomically using the documented deployment procedure; keep
current images as rollback references.

**Validate:** snapshot import probe, typed `SUBMIT`, committed history/context reconstruction, host tools, and representative RLM execution.

**Done when:** the sandbox image contains only dependencies required inside the sandbox.

**Phase 2 exit:** one runtime graph, one provider package,
one execution implementation, fewer resident/global owners.

---

# Phase 3 - Test-suite consolidation

**Status: consolidation implemented; affected live certification incomplete
(2026-09-10).** P3.1–P3.4 and P3.6 are implemented, and the deterministic full
gate passed. P3.5 preserves the existing live entry points and narrow matrix;
its affected live run has three failing cases (two MVP cases and the failed-run
memory-discard case). Phase 3 is not marked complete. The ownership inventory,
scenario reconciliation, exact candidate SHA, passing receipts, and remaining
certification failures are recorded in `docs/testing/phase3-consolidation-ledger.md`.

The goal is **not** to concatenate hundreds of tests into a few giant files. The goal is to organize tests around stable behavior contracts and remove repeated setup/assertions for internals that no longer exist.

## P3.1 - Inventory tests by behavior owner

**Rationale:** Test deletion should be evidence-driven.

**Implement:** classify every backend test into one primary contract:

```text
Run lifecycle / settlement
RLM program / budget / output
Daytona resource lifecycle / containment
recursive behavior
persistence / migrations
Workspace files / memory
API / SSE public contracts
observability privacy / lifecycle
packaging / configuration
live provider certification
```

Mark duplicate scenarios and tests that exist only to pin migration internals.

**Validate:** every test has an owner and every hard invariant has at least one owning suite.

**Done when:** there is a deletion/consolidation list before files are moved.

## P3.2 - Consolidate micro-test files into behavior suites

**Rationale:** A new test file for every regression makes navigation and maintenance expensive even when individual tests are small.

**Implement:** within each affected domain:
- choose an existing canonical suite;
- move equivalent regressions into it;
- parameterize state/error variants;
- delete the old micro-files in the same change;
- avoid files named after issue/phase/task identifiers when the behavior has a durable domain name.

Examples of preferred suite names:

```text
test_turn_lifecycle.py
test_run_claims.py
test_rlm_program.py
test_rlm_budget_adapter.py
test_recursion.py
test_daytona_session_lifecycle.py
test_daytona_interpreter.py
test_persistence_lineage.py
test_mlflow_tracing.py
```

These are examples, not a requirement to create all of them.

**Validate:** test collection before/after, unchanged behavior coverage, and `make check`.

**Done when:** equivalent regressions are discoverable in one behavior suite instead of scattered across one-off files.

## P3.3 - Consolidate fakes and fixtures only where repetition is real

**Rationale:** Duplicated fake Daytona clients/sandboxes/LMs create their own inconsistent mini-implementations, but an over-abstracted test framework is equally harmful.

**Implement:**
- keep simple local fakes close to a suite when used once;
- move only repeated stable fixtures/fakes to `conftest.py` or a small `tests/support` module;
- expose state directly for assertions rather than building production-like mock hierarchies;
- remove helper layers that merely wrap `pytest` or `unittest.mock`.

**Validate:** affected suites remain readable without chasing a large fixture framework.

**Done when:** repeated test infrastructure is reduced without creating another internal framework.

## P3.4 - Delete tests of removed implementation details

**Rationale:** Keeping tests for deleted registries, fingerprinting, generations, proxies, or dual execution paths preserves the old architecture indirectly.

**Implement:** after each Phase 2 deletion, remove tests whose contract was only the deleted internal. Replace them only when a user/domain invariant would otherwise become uncovered.

Preserve behavior tests for:
- claim exclusion and idempotency;
- cancellation/timeout/claim loss;
- settlement and artifact publication;
- resource containment/cleanup;
- durable Session/Volume continuity;
- typed output and budget boundaries;
- public Runtime Events/API shapes.

**Validate:** map each deleted test to either a surviving behavior test or an intentionally removed internal contract.

**Done when:** the test suite describes the target architecture, not the migration history.

## P3.5 - Keep live certification narrow

**Rationale:** Provider behavior cannot be proven by fakes, but live tests should not become a second full suite.

**Implement:** retain a small operator-gated set for:
- Daytona containment/deletion;
- snapshot/runtime capability;
- PostgreSQL contention/migration rehearsal;
- MLflow configured-backend export;
- one matched quality/performance campaign.

Move complex campaign accounting into reusable benchmark helpers rather than duplicating it across each live test.

**Validate:** live tests are skipped cleanly without credentials and each produces a bounded receipt when explicitly run.

**Done when:** every live lane proves a provider-specific fact that deterministic tests cannot prove.

## P3.6 - Adopt a test-growth rule

**Rationale:** Consolidation will regress unless future additions follow a simple policy.

**Implement:** document in `AGENTS.md`/testing guidance:

> Add a regression to the existing behavior-owning test file by default. Create a new test file only for a distinct contract, fixture/process boundary, generated-contract lane, or live marker.

Also keep coverage as a coarse floor, not a reason to test every internal branch.

**Validate:** review future test additions against the rule.

**Done when:** the default development behavior stops creating one-off test files.

**Phase 3 exit:** materially fewer duplicated/migration-specific tests, preserved hard-invariant coverage, green deterministic suite.

---

# Phase 4 - Recursive RLM simplification and value proof

Recursive child RLMs are an optimization, not a required architectural feature. DSPy's native `llm_query` and `llm_query_batched` remain the default semantic delegation mechanisms.

**Status: complete (2026-09-12) — disable.** P4.1–P4.4 remain the simplified capsule path. The 144-trial P4.5 continuation receipt is `.scratch/benchmark-reports/phase4-ablation-20260912-continue.json` (108 retained rows + 36 remaining admissions, including retry of the HTTP 400 control). Mechanical `phase4_decision` is `incomplete` solely because 14 completed frozen-baseline C rows lack call-shape usage. Retain gates independently fail: paired bootstrap CI lower bound is 0.0 (point 0.167). Suitable verified successes: D 4 / A 0 / B 1 / C 1 of 18. P4.6 therefore disables Fleet child RLM tools by default (`recursion_enabled = false`); native `llm_query` stays. Recursion remains available on explicit profiles such as `phase4-campaign`.

The comparison baseline is `9b526f50f0aeec37ca399bc8ef19ec8a95d3bead`. The current local slice
renames capsule allocation to bytes, replaces the duplicate capsule result type
with typed `ChildOutcome`/`ChildUsage`, records child-local LM usage, and stops
echoing unread authorization references as evidence. Selected Session/Project
text reads have a bounded access ledger; inline delivery is separate from reads.
Selected Artifact UUIDs now resolve through the existing Turn-bound
ArtifactReader, with authorization/integrity checks and a pre-fetch byte limit.
Actual delivered read bytes are included in child input accounting. Child citation claims now use
canonical bracketed reference/fragment identifiers and are validated against
actual access/delivery before an answer is returned. Required citations cannot
be silently omitted; `cited_evidence` is separate from access and is not a claim
of semantic verification.

The recursive scheduler now uses the application loop, one semaphore, and owned
blocking native child execution, without private child loops or a recursive
batch thread pool. Root worker execution remains unchanged. The local slice
passed `make check` (79.0% coverage; 543 TUI tests); subsequent ownership
refinements passed the focused RLM/routing/campaign tests. Live scheduler parity
has not been exercised: the existing recursive verifier requires a clean,
committed candidate. Local checkpoint commits are now operator-authorized;
publication and deployment remain outside the approved scope.

P4.4 is locally complete: Root exposes exactly the two typed capsule tools and
the active routing evaluator no longer presents a depth-fallback route. P4.5
now has a sealed 12-case corpus, balanced 144-trial schedule, four immutable
arm specifications (including the frozen `9b526f50f0aeec37ca399bc8ef19ec8a95d3bead`
baseline), public-rate reservations, content-safe receipts, and task-clustered
bootstrap/retention logic. The explicit `phase4-campaign` profile constrains
the root to six iterations/eight LM calls and children to four/four. No paid
campaign has been launched. P4.5 live evidence and P4.6's resulting
retention/deletion/default decision therefore remain open; no recursion default
has changed.

P4.4 migration is complete locally: Root registration exposes only
`rlm_query(capsule=...)` and `rlm_query_batched(capsules=...)`, both returning
typed outcomes. The batch returns ordered ordinary partial failures, and capsule
children receive no Fleet recursion tool. Unused capsule/read-only tool
constructors and properties are removed. Recursive Session snapshot construction,
immutable history-copy wrappers, nested executors, prompt-only batch execution,
and Fleet depth fallback have also been deleted. Capsule children use native
semantic calls under the child budget. Deterministic callers and contract
fixtures and credentialed live caller signatures are migrated; executing those
live callers and the full completion audit remain open. The deletion revision passed `make check` after a batch deadline
race was fixed by rechecking parent authority/deadline before return. Later
live-caller and citation changes passed a fresh full `make check`. The subsequent
Artifact wiring passed focused Root-to-child tests for successful scoped reads,
missing/malformed references, and authority revocation during a read. Its full
`make check` also passed (79.0% coverage; 543 TUI tests). No live evidence has
been collected for these revisions.
Earlier `make check` receipts above do not certify this later migration.

The Phase 4 adapter boundary is explicit: arms C and D submit their isolated
Session/attachment Turns through the FastAPI ASGI application and consume the
public SSE stream, so lifecycle, authority, selected-input delivery, Daytona
creation/deletion, and cleanup measurements come from the same transport used
by Fleet. Arms A and B remain intentionally direct DSPy ablations; they do not
claim to certify the FastAPI production path.

The API-first campaign slice is now implemented locally. `serve-api`,
`fleet web`, and the supervised `fleet cli` accept an explicit `--profile` and load
that profile before resource initialization; explicit profile selection is
rejected with `--reload`. The campaign driver supervises disposable candidate
and frozen-baseline FastAPI services, waits for `/health` and `/health/ready`,
uses one Session per trial, parses only the public UI-v1 SSE chunks, and joins
process groups before cleanup. A credential-free fake service exercises the
same HTTP/SSE client and lifecycle observer. The local protocol receipt at
`.scratch/benchmark-reports/phase4-api-dry-run-20260910-v8.json` contains all
144 scheduled attempts (12 cases × 3 repeats × 4 balanced rotations), but it is
fixture transport evidence only and is not provider value proof.

For the operator-approved API-first exploratory slice, use the ordinary
committed profile (no `--profile`) and keep the candidate FastAPI service on
loopback. `--partial-live` starts only the disposable frozen-baseline C service
and routes C/D through the public attachment, Session, Turn, and UI-v1 SSE
contract; A/B remain direct DSPy ablations. It admits exactly ten sealed rows
(A3/B3/C2/D2 from `p4-suitable-01`) in three balanced rotations, retains the
candidate-created Session/attachment records, and treats spend/optional D
telemetry as explicitly non-gating observations. The resulting receipt is an
exploratory partial sample and must remain `incomplete`; it cannot close P4.5,
P4.6, or the Phase 4 exit.

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/run_phase4_campaign.py \
  --partial-live --candidate-url http://127.0.0.1:8000 \
  --output .scratch/benchmark-reports/phase4-api-partial-YYYYMMDD.json
```

The 2026-09-10 exploratory execution is retained at
`.scratch/benchmark-reports/phase4-api-partial-20260910.json`. It attempted
all ten scheduled rows (A3/B3/C2/D2) across `ABCD`, `BCDA`, and `CDAB`; three
A trials completed, seven rows failed or lacked required observations, no row
was a verified success, and the mechanical result is `incomplete`. The frozen
C service was verified at `9b526f50f0aeec37ca399bc8ef19ec8a95d3bead` and its
process cleanup completed. C recorded baseline timeout/cleanup loss, B's
native-RLM rows were ordinary malformed-result failures, and D used the
already-running ordinary candidate so its lifecycle telemetry and spend were
unknown by design. The two D Sessions and attachment-backed Turns were kept
in the candidate database and labeled `phase4-D-p4-suitable-01-r1` and
`phase4-D-p4-suitable-01-r2`. This receipt is failed exploratory evidence,
not P4.5 value proof; P4.5, P4.6, and the Phase 4 exit remain open.

The immutable prior receipt at
`.scratch/benchmark-reports/phase4-ablation-decf0da7.json` remains failed,
incomplete, and superseded. Its top-level observed spend is unknown, so the
full 144-trial live driver refuses to treat it as zero when applying the
cumulative US$50 cap. The ten-row exploratory path above intentionally does
not use that cap as an admission gate; it records unknown spend explicitly and
does not qualify as value evidence. Consequently P4.5 live evidence, the
mechanical P4.6 decision, and the Phase 4 exit remain open; no live
certification or recursive-default change is claimed.

The follow-up failure-accounting slice keeps single-capsule measurements and
successful access identifiers even when execution fails, without treating those
accesses as a valid answer. Ownership cancellation remains fatal, and an
unsettled worker cannot be converted into an ordinary partial result. The
redundant executor constructed for each capsule has been removed; selected
access and usage are invocation-local without a copied Session snapshot.

Pricing preflight verified the official Databricks base-region conversion of
US$0.07/DBU in the [pricing page's published data](https://www.databricks.com/en-pricing-assets/page-data/product/pricing/foundation-model-serving/page-data.json).
At the listed V4 Flash rates
of 2 input / 4 output DBU per million tokens, that is US$0.14 input / US$0.28
output per million tokens in base regions. Regional applicability and complete
worst-case admission bounds remain required before paid work; these figures
alone are not a certified campaign cost bound.

The agreed pilot is 12 tasks (six multi-document/data, three conflicting or
incomplete-evidence, three simple controls), three repeats, four arms: at most
144 runs, US$50 total model/Daytona spend, four hours including a 15-minute
cleanup reserve, standard public pricing, one root trial and at most five
Sandboxes concurrently. Retention requires at least +10 percentage points
verified success on suitable tasks over the better non-recursive arm, a positive
paired task-clustered 95% confidence-interval lower bound, and at most 2x cost per
success and 2x p95 latency. Controls must not regress; safety failures are fatal.
Ordinary cleaned-up sibling failures will become ordered typed partial results.
An inconclusive outcome disables recursion by default; an incomplete campaign
must remain explicitly incomplete rather than being called value proof.

## P4.1 - Normalize the capsule/result contract

**Rationale:** Small naming/schema inconsistencies multiply across tools, tests, traces, and budgets.

**Implement:**
- rename byte-enforced fields such as `allocation_chars` to a byte-accurate name;
- use one child status enum (`completed`, `failed`, `timed_out`, `cancelled` or equivalent);
- define one small typed child usage/result shape;
- remove legacy aliases after migration tests are updated.

**Validate:** serialization, bounds, typed output, and trace projection tests.

**Done when:** there is one recursive request/result vocabulary.

## P4.2 - Report evidence actually used by children

**Rationale:** Authorized references describe what a child could inspect, not what supports its answer.

**Implement:**
- keep authorized references in the capsule;
- record actual selected-file/reference accesses;
- return only bounded evidence identifiers the child actually used;
- let Root verification distinguish available context from used evidence.

**Validate:** child reads one of several authorized references and reports only the accessed evidence; unauthorized paths remain rejected.

**Done when:** recursive evidence is useful for verification and evaluation rather than merely echoing authorization.

## P4.3 - Use one recursive scheduler

**Rationale:** Thread pools, private event loops, Futures, and a second async scheduler create more lifecycle ownership than recursion warrants.

**Implement:**
- one application event loop;
- one bounded scheduler/semaphore;
- structured child tasks;
- one synchronous bridge only where DSPy's interpreter callback boundary requires it;
- one cancellation/join path;
- remove legacy `ThreadPoolExecutor`/private-loop child scheduling from the selected runtime.

**Validate:** ordering, batch reservation, cancellation, timeout, sibling failure, and no detached child work after settlement.

**Done when:** there is one concurrency owner for recursive work.

## P4.4 - Collapse the model-facing recursive tool surface

**Rationale:** The model should not need to understand Fleet's migration history.

**Implement:** expose at most the stable conceptual tools:

```text
rlm_query
rlm_query_batched
```

Internally they may accept/construct a `SubproblemCapsule`, but remove legacy prompt/capsule/read-only migration variants from production model context after parity is proven. If partial sibling outcomes are valuable, make them result semantics rather than another top-level tool name unless a distinct model choice is necessary.

**Validate:** tool catalog/reserved-name tests and routing evaluation.

**Done when:** recursive capability is understandable from two names and one contract.

## P4.5 - Run the matched recursion ablation — complete (2026-09-12)

**Status: complete as a 144-admission campaign; mechanical decision `incomplete` due to frozen-C call-shape telemetry, retain gates failed.** Receipt `.scratch/benchmark-reports/phase4-ablation-20260912-continue.json`. All authorization and per-row sandbox cleanup confirmed. Charged spend on the continuation plus prior bound remained under US$50.

**Rationale:** Child RLM infrastructure should remain only if it improves results enough to justify sandboxes, scheduling, tests, and operational cost.

**Implement:** compare matched tasks with identical models/context/budgets where possible:

```text
A. direct/simple DSPy baseline
B. native dspy.RLM + llm_query only
C. current Fleet recursive child
D. simplified capsule child
```

Include tasks where recursion should not be used.

Measure correctness, evidence validity, completion, root/child LM calls, known tokens, delegated bytes, Sandbox seconds, latency, failure rate, and cost per successful task.

**Validate:** retained campaign with raw sample counts and uncertainty; do not use tool invocation itself as a quality metric.

**Done when:** the result clearly identifies when child RLMs help and when they do not.

## P4.6 - Delete recursive infrastructure that does not justify itself — complete (2026-09-12)

**Status: complete — disable by default.** `[defaults.rlm] recursion_enabled = false`. Default Root instructions omit Fleet `rlm_query` tools. Recursive executor and tools still exist for explicit opt-in profiles. Recursion code is not deleted in this step so `phase4-campaign` can still run; P6.3 may remove unused machinery after cutover.

**Rationale:** Recursion is not sacred. The simplest successful RLM should win.

**Implement:**
- if capsule child RLMs show meaningful value, keep only the simplified path and delete legacy recursion machinery;
- if they do not, disable/remove Fleet child RLM tools and rely on native `llm_query`/`llm_query_batched` until a future evaluated use case justifies reintroduction.

**Validate:** matched quality gate plus full deterministic suite.

**Done when:** Fleet pays the code/runtime complexity cost of recursive children only when evidence supports it.

**Phase 4 exit:** Fleet child RLMs are intentionally absent from the default architecture. Native `llm_query` / `llm_query_batched` remain. Opt-in recursion is an explicit profile override, not a production default.

---

# Phase 5 - Operational certification without feature growth

**Status: not started.** Do not begin Phase 5 until explicitly requested. P2.7 snapshot probes, a 2026-09-12 Lakebase preflight, and a local MLflow receipt are not Phase 5 closeout.

## P5.1 - Reconcile immutable snapshot and configuration identities

**Rationale:** Provider-probed snapshot identities and configured identities must form one auditable chain.

**Implement:**
- verify the selected Session/child image definitions;
- create new immutable identities only when contents/resources differ;
- retain old identities as rollback references;
- update deployment configuration only after a sealed probe receipt exists;
- do not silently mutate an existing snapshot name.

**Validate:** image manifest, Python/user/cwd/import probe, interpreter execution, cleanup.

**Done when:** configuration -> immutable snapshot -> manifest digest -> receipt is unambiguous.

## P5.2 - Close managed PostgreSQL/Lakebase deployment evidence

**Rationale:** The persistence design is already strong; remaining work is deployment proof, not another persistence redesign.

**Implement:**
- inventory deployed heads;
- run read-only preflight;
- rehearse the one-time SQLite -> PostgreSQL migration only in an explicit maintenance window if required;
- run the existing contention/query-plan checks on the actual supported topology;
- make no schema additions unless this rehearsal finds a correctness defect.

**Validate:** migration/head receipt, integrity/lineage checks, representative Run/Turn/recovery/outbox behavior.

**Done when:** supported production persistence is certified without adding another state store.

## P5.3 - Keep warm pools off the critical path

**Rationale:** Warm-pool code is optional infrastructure and should not drive runtime architecture.

**Implement:**
- retain `warm_pool_enabled=false` / size zero by default;
- do not expand warm-pool ownership/reconciliation features before Phase 4 proves recurring child demand;
- if recursion value is low, consider deleting child warm-pool support entirely;
- if value is high, run one explicit canary for eligibility, clean-instance behavior, quota, latency benefit, and cost.

**Validate:** cold path remains fully functional; canary receipt if activated.

**Done when:** capacity complexity exists only for demonstrated demand.

## P5.4 - Simplify and certify MLflow

**Rationale:** Observability should be optional and small enough that execution remains understandable without it.

**Implement:**
- keep one application-lifespan MLflow owner;
- keep tracing fail-soft and content sanitization fail-closed;
- remove duplicate configuration/tagging/span helpers where canonical attributes already exist;
- keep trace feedback Session-scoped;
- certify one configured backend after deterministic privacy/lifecycle tests pass.

**Validate:** tracing disabled/unavailable, queue saturation, shutdown flush, repeated lifespans, concurrent Sessions, feedback authorization, and configured-backend export.

**Done when:** MLflow failure cannot change a Turn and tracing logic does not create a second lifecycle graph.

## P5.5 - Simplify benchmark/certification scripts

**Rationale:** The benchmark directory is becoming a second application with many large scripts.

**Implement:**
- reuse the existing campaign/receipt primitives;
- merge or delete scripts that only differ by setup/receipt boilerplate;
- keep executable entry points thin;
- share identity, spend/deadline, receipt, and metric helpers;
- do not move serving runtime logic into benchmark utilities.

Also separate promotion gates:
- correctness/safety/containment: mandatory;
- latency/error/cost non-regression: explicit production tolerance;
- large improvements such as 50% p50 reduction: optimization target, not an architectural prerequisite unless product policy explicitly requires it.

**Validate:** benchmark unit tests plus one retained campaign from the simplified entry point.

**Done when:** adding a new benchmark scenario usually adds data/scoring, not another orchestration script.

**Phase 5 exit:** production candidate identities and external dependencies are certified without new product features.

---

# Phase 6 - Promotion, rollback, and final deletion

**Status: not started.** Phase 6 follows Phase 5.

## P6.1 - Build one clean promotion candidate

**Rationale:** Promotion evidence is only meaningful for an immutable, reproducible code/config/image combination.

**Implement:** select one clean candidate SHA with:
- chosen runtime architecture;
- exact dependency lock;
- exact config/profile;
- exact snapshot identities;
- database head;
- dataset/scorer identities for quality evidence.

**Validate:** full deterministic suite plus required live gates from Phases 1, 4, and 5.

**Done when:** no receipt depends on a dirty worktree or mixed runtime identities.

## P6.2 - Rehearse rollback before deleting the fallback

**Rationale:** Legacy code should not remain forever, but deletion should follow a proven rollback mechanism rather than fear of removal.

**Implement:**
- define rollback as selecting a previous complete release/config/image/database-compatible state;
- drain/fence active Runs before switching;
- verify additive migrations remain compatible;
- run one controlled rollback rehearsal.

**Validate:** Session history, workspace files, artifacts, and new Runs work after rollback.

**Done when:** rollback does not require keeping two production runtimes inside one process.

## P6.3 - Delete migration-only code immediately after cutover

**Rationale:** This is the point of ADR006. A successful migration that leaves every compatibility layer behind is not complete.

**Implement:** delete, as applicable to the selected architecture:
- legacy runtime selector values and dead configuration;
- resident Session RLM/fingerprint/generation/proxy machinery;
- unselected broker/native execution path;
- duplicate root/session maps;
- legacy recursive prompt tools and scheduler;
- obsolete process-global cleanup owner collections;
- tests and documentation that only describe those removed internals.

Run import/code search after each deletion group.

**Validate:** dependency boundaries, generated contracts where applicable, `make check`, and focused live smoke.

**Done when:** the codebase exposes one production execution architecture and no compatibility path is retained "just in case".

## P6.4 - Run GEPA only against the stable program

**Rationale:** Optimizing prompts while the runtime/tool/capsule contract is changing creates evidence for a disappearing program.

**Implement:** after P6.3:
- use the existing curated train/selection/held-out data contract;
- keep authorization/infrastructure rules non-optimizable;
- evaluate real candidates under bounded sandbox/model budgets;
- promote only immutable instruction/program artifacts that reload into a fresh Run;
- keep the development synthetic smoke explicitly non-promotable.

**Validate:** held-out correctness/evidence/operational gates and fresh-process load test.

**Done when:** optimization targets the final runtime rather than migration scaffolding.

## P6.5 - Reconcile architecture and developer documentation

**Rationale:** Documentation should describe the resulting code, not preserve every migration state.

**Implement:** update:
- `ARCHITECTURE.md` to the single selected runtime;
- source-layout docs after package deletion;
- DSPy/Daytona guides to the selected interpreter/containment contract;
- testing guidance with the behavior-suite rule;
- ADR006 status with final evidence and deprecated paths;
- generated API/profile contracts only through their owning generators.

Archive or shorten obsolete migration guides instead of layering new caveats on top.

**Validate:** `make check-docs`, dependency/tree checks, generated-contract checks where affected.

**Done when:** a new contributor can understand the current runtime without reading the migration chronology.

---

## 7. Test strategy after consolidation

The final suite should have four clear lanes:

| Lane | Purpose | What belongs here |
| --- | --- | --- |
| Unit | deterministic domain/runtime behavior | budgets, claims, state transitions, parsers, authorization, small lifecycle scenarios |
| Contract | stable boundaries | DSPy pinned contract, API/SSE schemas, generated client contract, provider adapter shape |
| Integration | cross-owner local behavior | persistence settlement/recovery, complete Turn orchestration, filesystem semantics |
| Live/certification | facts that require external systems | Daytona containment, managed PostgreSQL, MLflow backend, provider quality/performance |

Rules:

- A bug regression goes into the existing owner suite by default.
- Parameterize repeated state/error cases.
- Prefer one end-to-end lifecycle scenario over five unit tests that each mock one internal handoff.
- Keep security/race/cancellation tests even when they are expensive to understand; they protect real invariants.
- Delete internal tests with the internal implementation.
- Do not optimize for maximum test count or maximum coverage. Maintain the repository coverage floor, but prioritize meaningful contracts.
- Test filenames should describe durable behavior, not temporary phase/task IDs.

## 8. Review sequence

Prefer small deletion-oriented changes in this order:

1. CI reproduction/fix only.
2. trace-content policy only.
3. minimal native containment proof and containment decision.
4. `runtime/daytona` ownership removal.
5. resource-owner consolidation.
6. resident runtime or unselected execution-path deletion.
7. DSPy compatibility cleanup.
8. test consolidation corresponding to the deleted source.
9. recursive scheduler/tool-surface simplification.
10. recursive value ablation and deletion decision.
11. snapshot/managed DB/MLflow certification closeout.
12. production cutover, rollback rehearsal, final migration deletion.
13. stable-runtime evaluation/GEPA.

This is sequencing guidance, not a required PR count. A change should be reviewable and should usually remove the old implementation in the same change that makes it unreachable.

## 9. Final acceptance criteria

ADR006 is complete only when all of the following are true:

- [ ] deterministic CI is green on every supported Python version;
- [ ] one production execution/containment boundary is selected and certified;
- [ ] there is one Daytona provider package and no duplicated `runtime/daytona` layer;
- [ ] each provider resource has one lifecycle owner;
- [ ] one RLM invocation has one `TurnBudget` and one recursive scheduler;
- [ ] no cross-Turn correctness depends on accidental resident Python/DSPy state;
- [ ] only the selected broker/native execution implementation remains in production code;
- [ ] recursive child RLMs remain only if matched evaluation shows meaningful value;
- [ ] model-facing recursive tools have one small stable contract;
- [ ] test files are organized around behavior contracts rather than migration tasks/internals;
- [ ] tests for deleted internals are deleted while safety/lifecycle/public-contract coverage remains;
- [ ] managed PostgreSQL, selected Daytona snapshot/runtime, and configured MLflow gates have honest retained evidence;
- [ ] warm capacity is either evidence-backed or disabled/removed;
- [ ] one clean candidate has passed public-contract, containment, settlement, quality, operational, and rollback gates;
- [ ] obsolete runtime flags, registries, fingerprints, schedulers, broker/native compatibility branches, and migration docs are removed;
- [ ] `ARCHITECTURE.md` and source-layout documentation describe only the supported end state.

## 10. Guiding decision rule

When two implementations satisfy the same Fleet invariant, choose the one with:

1. fewer lifecycle owners;
2. fewer mutable cross-Turn objects;
3. fewer background tasks/executors/event loops;
4. fewer model-facing tools;
5. fewer provider round trips;
6. fewer custom protocols;
7. fewer tests needed to prove the same behavior;
8. clearer failure and cleanup semantics.

If a new abstraction does not remove more complexity than it introduces, do not add it.
