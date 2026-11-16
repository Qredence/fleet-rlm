# PRD: Simplify Fleet's source and application boundaries

Status: **Client stage complete — broader PRD deferred**
Updated: **2026-11-16**

## Purpose and problem

Make Fleet's source easy to follow, change, and maintain. The review covers all
of `src/fleet_rlm`, including root modules and subdirectories. The concern is
overlapping execution paths, unnecessary definitions and indirection, implicit
dependencies, and deeply branched control flow.

The primary users of this work are contributors maintaining Fleet and users
depending on consistent behavior across its terminal, browser, and CLI clients.
Retain Fleet's capabilities while consolidating their implementation. Moving
files, splitting functions, or reducing lines alone does not establish a simpler
system. Every abstraction and alternative path must have a concrete purpose.

## Agreed product and architecture decisions

- FastAPI owns all application workflows. TUI and application CLI commands submit
  HTTP/SSE requests, display results, and handle client-local input and file transfers.
- Startup, `doctor`, snapshot administration, and read-only trace verification
  remain local operator tools so they can inspect or repair an unavailable backend.
- Long-running jobs use API-managed subprocesses. These internal backend workers
  may access PostgreSQL and MLflow directly through existing implementations.
- Keep chat, reviewed memory, evaluation, experiments, tuning, and persisted data.
  Internal interfaces and redundant CLI forms may change with documented replacements.
- Keep terminal and browser-hosted Textual. Remove the alternate Rich interactive
  chat loop. Preserve completion, mentions, attachments, drafts, and Improve flows.
- Default local clients start or safely reuse the backend through one shared
  startup path. An explicit server URL connects only and starts no local services.
- Implement in independently validated stages. Do not turn consolidation into an
  ORM migration, generic dispatch framework, or another service/repository layer.

The Store's eventual folder location is not decided. Establish execution and
dependency ownership before proposing moves. The complete source audit is still
pending; the observations below are targeted findings, not a whole-codebase verdict.

## Success criteria

1. Every retained application capability has a documented API owner and client
   path. Clients do not construct runtimes, open Store connections, or orchestrate
   evaluation locally. Operator tools are the explicit exception.
2. Each operation has one canonical implementation. Public CLI syntax does not
   double as the internal worker protocol, and clients consume backend readiness
   and job results rather than independently assembling them.
3. A client connecting to an existing backend needs no local model, Daytona,
   database, or MLflow credentials. Local startup still uses host configuration.
4. Consolidation demonstrably removes duplicated decisions or indirection. Record
   before/after production source size and representative control-flow changes;
   do not promise a reduction percentage or equate fewer branches with correctness.
5. Existing behavior, persisted identities, and correctness contracts pass their
   acceptance checks. Report offline evidence, live canaries, and measured gains separately.

## Capability parity required before deleting paths

| Workflow | Current finding | Required outcome |
| --- | --- | --- |
| Chat and Improve | Existing clients already use HTTP/SSE | Preserve session, workspace, model, memory, Skill, job, and review operations |
| Optimization/check/review | Public CLI can execute directly; API launches it with managed flags | One API-owned admission/readiness/review path and a dedicated internal worker entry point; preserve options and approval gates |
| Trace labeling | CLI supports trace IDs, expected route, structured expectations, and reviewer; API label input is narrower | Expose all retained labeling semantics; do not silently require every label to identify a captured Turn |
| Trace scoring | CLI supports explicit trace IDs, registered scorer selection, and diagnostics-only mode | Preserve these selections; curated-example rescore jobs are not sufficient parity |
| Dataset creation and synthetic runs | CLI creates datasets and orchestrates runs with seed, limits, long-context, and collection-only options | Move application orchestration behind the API while preserving options, IDs, destination checks, and recovery |

The advertised `evaluate_synthetic` managed-job kind is currently deliberately
refused. Existing synthetic CLI behavior and that unfinished job path are distinct.
Do not enable the placeholder merely to redirect commands. Specify and validate
its execution, isolation, accounting, and cancellation requirements before use.

API additions must reuse existing evaluation and labeling implementations, expose
explicit validated request/response models, and keep blocking operations off the
event loop. Update the HTTP reference and OpenAPI contract alongside changes.

## Current implementation scope

The current authorized stage focuses on `uv run fleet cli` and `uv run fleet web`
using their existing APIs. Optimization/evaluation ownership changes and new APIs
are deferred. This stage adds no test files, dependency upgrades, database cutover,
or new public API. Neon PostgreSQL and local MLflow remain the live environment.
The full-source audit and wider PRD remain pending; completing this stage does not
mean the entire PRD is complete.

## Implementation plan and task tracker

Statuses are **Not started**, **In progress**, **Blocked**, and **Complete**.
Mark a task complete only when its acceptance evidence is recorded. Add a short
blocker and next action when a task is blocked. Finish each stage's relevant
validation before deleting the paths replaced by the next stage.

| ID | Task | Status | Acceptance / dependency |
| --- | --- | --- | --- |
| A1 | Inventory all source modules and map retained workflows to client, API, executor, persistence, and tests | In progress | Targeted findings exist; complete runtime, sandbox, configuration, observability, and remaining client review |
| A2 | Classify branching and indirection as required guarantees, supported alternatives, obsolete paths, or duplication | Not started | Source references and caller evidence for every proposed deletion; no arbitrary complexity threshold |
| A3 | Complete the capability parity matrix and record baseline source metrics | Not started | Every retained CLI option and UI operation has a target owner and acceptance scenario |
| B1 | Route application CLI commands through existing API operations | Not started | Depends on A3; no direct runtime, Store, or evaluation orchestration in application client paths |
| B2 | Add missing labeling, scoring, dataset, and synthetic-run API capability | Not started | Depends on A3; parity tests cover the differences above before old paths are removed |
| B3 | Share local launch/reuse logic and remove unnecessary backend settings/authentication from connected clients | Complete | Explicit URL starts nothing; default local launch validates reuse and stops only owned services |
| C1 | Introduce one internal worker entry point and stop launching the public CLI as a worker protocol | Not started | Depends on relevant B1/B2 parity; API-managed jobs retain worker-owned progress and output draining |
| C2 | Delete standalone-versus-managed ownership branches and duplicate readiness assembly | Not started | One admission, cancellation, recovery, review, and terminal-arbitration path; preserve required persistence failure behavior |
| D1 | Remove alternate Rich chat loop and exclusively used event renderer/command branches | Complete | One Textual interactive path; non-TTY invocation exits clearly and directs users to API-backed commands |
| D2 | Verify terminal/browser composition, completion, file handling, activity, and Improve behavior | Complete | Preserve browser session isolation/sharing, draft recovery, cancellation, and evidence-gated review |
| E1 | Remove unused Store constructor state and redundant aliases/exports | Complete | Verify production callers; inspect actual pool arguments in tests; preserve pool, heartbeat, schema, and migration ownership |
| E2 | Remove retry helper imports back into the Store package | Complete | Use defining-module constants and sleep directly; patch that module in tests; preserve existing retry behavior |
| E3 | Remove the broad GEPA re-export facade | Complete | Update production, test, and script imports to owning modules; no replacement compatibility facade |
| E4 | Implement further source-audit-backed consolidations in coherent slices | In progress | Depends on A2; preserve cross-domain transactions and explain each simplification's concrete benefit |
| F1 | Update architecture, source layout, CLI/API references, and changed-command guidance | In progress | Documentation describes the final implementation and retained local operator exceptions |
| F2 | Complete validation and record results and remaining limitations | In progress | Required checks pass; before/after evidence includes actual removals, remaining branches, and unresolved risks |

### Implementation constraints retained after cleanup

- Store pool configuration is verified at pool construction. Keep heartbeat,
  schema, migration, and pool ownership in their existing implementations.
- Retry behavior uses constants and sleep in its defining module; tests patch
  that module directly. Do not restore parent-package lookup seams.
- The GEPA facade is removed. Consumers import owning modules directly; retain
  native DSPy implementations without a replacement compatibility facade.
- Textual is the only interactive client. Prompt Toolkit still supplies its
  completion, and Rich still supports Textual and operator output. Retain both.
- Preserve one transaction for completed Turn state, messages, files, and replay
  promotion. Do not split atomic work to make modules appear more independent.

## Validation and release gates

Reuse focused tests in the existing API, TUI, Store, runtime, sandbox, and
optimization suites. Add regression coverage for changed behavior without
creating a parallel test hierarchy or tests that merely mirror implementation.

| Area | Required scenarios |
| --- | --- |
| Clients and startup | Automatic launch/reuse, explicit URL, incompatible backend rejection, owned-service shutdown, credential-free connected client |
| Textual | Terminal/browser parity, completion and quoting, mentions/uploads, partial upload failure, draft/history recovery, SSE idle periods and interrupted streams |
| API parity | Trace/Turn label identity and gold fields, scorer selection, datasets, synthetic-run options, readiness, idempotent submission, approval/rejection |
| Jobs | Disconnect/reconnect, cancellation, restart recovery, bounded private logs, worker exit before terminal arbitration, required persistence failure |
| Runtime and Store | Routing, native DSPy behavior, atomic commits, retry behavior, ownership fencing, additive migrations, historical readability, teardown guarantees |

Run focused `uv run pytest` checks for each slice, followed by `make check`.
For HTTP changes, update docs and run `make api-sync` and `make api-check`.
For this PRD and documentation-only changes, run `make check-docs` and
`git diff --check`.

Maintain loopback-only defaults and host-only credentials. Preserve Turn budgets,
isolated children, success-only artifact promotion, cancellation and reply fences,
native tracing, worker-owned counters, both cancellation signals, and checked
installation. References: [architecture](../ARCHITECTURE.md),
[source layout](reference/source-layout.md), [HTTP API](reference/http-api.md),
[evaluation](reference/evaluation.md), and [database](reference/database.md).

Live provider, Daytona, database, and evaluation checks require explicit
authorization for the run. No migration, database cutover, dependency upgrade,
commit, push, or release is part of this PRD's creation.

## Progress and evidence

| Item | State as of 2026-11-16 | Evidence / limitation |
| --- | --- | --- |
| Product and boundary decisions | Complete | Stakeholder interview established the decisions above |
| Targeted source investigation | Complete | CLI/API parity, managed/standalone workers, alternate chat loop, completion dependencies, and Store seams inspected |
| Whole-source assessment | Partial | Source inventory exists; detailed analysis across all subsystems remains pending |
| Source refactor | Partial | Store, facade, and client-stage changes implemented; wider application ownership remains deferred |
| Refactor validation and measured gains | In progress | Offline and live client evidence recorded below; no measured optimization gain claimed |
| Earlier focused checks | Prior evidence only | Seven pooling/lifespan tests passed before the refactor; they do not validate planned changes |
| Earlier database connection | Prior evidence only | Read-only query reached PostgreSQL 17.12; local settings point to a different project from the identified PostgreSQL 18 project. This does not prove PostgreSQL 18 compatibility |

For each completed implementation task, append the affected behavior, validation
command/result, and any remaining limitation. Preserve the distinction between
completed investigation and completed implementation.

## Decisions still requiring source analysis

- Final package layout and whether any modules should move. Do not infer folder
  ownership solely from FastAPI being the application entry point.
- Exact API additions and canonical CLI forms needed for full capability parity.
- Which complex control-flow branches can safely disappear after consolidating
  entry paths, and which enforce necessary failure or recovery behavior.
- Whether replacing Textual's completion dependency offers a demonstrated benefit.

Record resolutions here with source evidence before executing the affected task.
Database target correction is separate from this source simplification effort;
do not overwrite existing configuration as part of the refactor.

### Store cleanup implementation (2026-11-16)

E1 constructor-state cleanup removes unused `dsn`, `connection_class`, and
`connection_kwargs` parameters/state from Store; production construction uses
`Store.open`, which continues to configure both pools directly. Existing tests
inspect captured pool arguments, schema, pool recycling and shutdown ownership.
The follow-up export audit below removes unused re-exports and moves private
migration imports in tests to their defining module.

E2 removes parent-package retry lookups and their two helper functions. The
retry decorator uses its defining module's constant and `time.sleep` directly;
the existing dropped-connection regression patches that module. Retry count and
OperationalError behavior are unchanged.

The original Store slice passed `uv run pytest tests/store/test_store.py -q`
(94 passed, three skipped). The completed client-stage full-check result is
recorded below; this offline evidence is separate from live client checks.

### GEPA facade removal (2026-11-16)

Removed the 133-line `optimize/gepa.py` re-export facade. CLI execution imports
`cli_commands`; proposer and progress consumers import their defining modules.
Optimization tests now reference owning modules directly, including monkeypatches,
without a replacement facade. `uv run pytest tests/optimize/test_optimization.py
-xq --tb=short` passed all 294 tests. Repository Ruff and diff whitespace checks
passed. The completed client-stage `make check` also passed, as recorded below.
Optimization/evaluation ownership changes remain deferred.

### CLI/browser consolidation (2026-11-16)

Both commands now use `_run_client` for local launch/reuse, compatibility checking,
and owned-service shutdown. Explicit URLs bypass `get_settings`, MLflow auth,
launch planning, and service management. Connected clients retain runtime-digest
and ready-destination validation; explicit backend selectors must match the server.
MLflow launch overrides are rejected with explicit URLs. For an explicit-URL web
host, external listener opt-in uses `FLEET_ALLOW_NON_LOOPBACK_BIND` in the process
environment; it does not read backend TOML or dotenv configuration. Origin and
web authentication protections remain.

Browser child argv now carries only URL and optional Session identity. Session
state keeps the server's MLflow mode rather than backend settings. The terminal
and browser both use Textual; the Rich input loop, event renderer, and its exclusive
helpers are removed. Non-TTY CLI invocation fails before backend startup. Rich,
Prompt Toolkit, the shared Turn tracker, and Textual stream handling remain.

| Retained client capability | Existing API owner | Client behavior |
| --- | --- | --- |
| Chat, cancel, history and drafts | Session Turn/chat routes | Existing Textual app and stream consumer; local drafts retained |
| Sessions, models, mode and config | Session routes and `/models` | HTTP commands and completion |
| Workspace, mentions and file transfers | Workspace routes | Local selection/upload/download; server owns workspace operations |
| Reviewed memory and Skills | Session memory and Skills routes | Existing HTTP commands |
| Improve and job review | `/improve` routes | Existing screen and commands; no optimizer redesign |
| Startup and health | Launcher and `/health` | One launch/reuse path; connected clients use safe server metadata |

Existing client tests are updated in place. Only two targeted test functions were
added (one parameterized over CLI/web), covering configuration-free explicit URLs
and non-TTY rejection. Tests exclusive to removed Rich behavior were deleted;
shared tracker rejection/child-status regressions and Textual streaming tests remain.

Live evidence: terminal Flex conversion completed with `22.2` Celsius, persisted
Turn `8bae4f1cae56443bab85be762418ea2b`, and readable local MLflow trace
`tr-b1701d1402151bef51190bf937f3acc7` (59 spans, OK). Browser Direct returned
`browser-ok`, persisted Turn `0e086337e74043328eef0b6e22ef99cb`, and readable trace
`tr-e8c7f3c042b5a9383e7bb2dbfdae73a2` (18 spans, OK). Both owned Session runtimes
were evicted successfully (HTTP 200), retaining history. Terminal and browser
local launch both reported reuse of the existing API. Explicit-URL terminal and
browser startup succeeded, and the browser rendered history and exited cleanly.

Recovered earlier GEPA canary: serving and native instruction probes passed, but
the candidate source did not change, so the canary status is failed. Its report
recorded no remaining provider sandboxes and no cleanup errors. This is not an
optimizer quality claim and no further optimization run was launched in this stage.

Production source baseline at `7c69b5474`: 91 Python files / 33,529 lines. At client-stage
completion: 90 Python files / 32,991 lines (538 fewer lines), including the earlier
Store and GEPA facade cleanup. Count includes bundled Python scripts. Concrete
simplifications are one startup decision path, no connected-client backend auth,
and one interactive implementation; line count alone is not correctness evidence.
The removed Rich-only command branches include blocking console model selection
and synchronous job polling; Textual model selection and Improve callbacks remain.

Final client-stage validation: `make check` passed on the completed source tree:
2,361 tests passed, four skipped, one existing SQLAlchemy deprecation warning;
coverage 86%. Ruff, format check, type checking, documentation links, OpenAPI
contract check, and sdist/wheel builds passed. The focused client checks also
passed. `git diff --cached --check` passed after staging the PRD. No test files,
dependencies, or API endpoints were added. Local backend configuration files
were left untouched.

Explicit-URL browser host and child were additionally launched with an invalid
`FLEET_PORT` value and still connected and exited successfully, demonstrating
that backend settings validation is bypassed. All temporary web listeners were
stopped; the pre-existing API and MLflow health endpoints remained HTTP 200.
Neon destination identity was confirmed without printing connection details.
Live evidence validates the changed clients against the existing backend, not
freshly restarted server internals or comprehensive provider compatibility.

B3, D1 and D2 are complete for the selected client scope; E2 and E3's earlier
cleanup now has full offline validation. F1/F2 are complete for this stage only.
The full A inventory, application CLI/evaluation API migration, internal worker
consolidation and broader E4 work remain deferred.

### Pre-PR import cleanup (2026-11-16)

Turn preparation no longer resolves constants through `sys.modules` and
`engine.turn`. Its memory limit is read directly in `turn_prepare`; preparation
and skipped-replay event reporting both use `replay.MAX_REPLAY_FILE_BYTES`.
The three lookup helpers and the corresponding orchestration exports are removed.
Existing memory truncation, oversized replay, and no-download regressions patch
those defining modules; their behavior and replay-skip metadata remain unchanged.

Store exports retain `Store`, `PooledLease`, `LeaseHeartbeatService`,
`IdempotencyConflictError`, and `SCHEMA_VERSION`, which have production callers.
Unused schema/lease re-export imports are removed; private migration constants
in tests come from `store.schema`. Constants needed by Store itself remain local
imports, excluded from the package's public export list. No pool, migration,
transaction, sandbox, or optimization implementation changed. No test files or
test functions were added.

Validation: 35 selected replay/memory cases passed using
`uv run pytest tests/api/test_turns.py -k 'replay or reviewed_memories' tests/store/test_store.py -q --tb=short`.
The full `uv run pytest tests/store/test_store.py -q --tb=short` also passed
(94 passed, three skipped). `make check` passed: 2,361 tests passed, four skipped,
one existing SQLAlchemy deprecation warning; lint, formatting, types, docs,
OpenAPI verification and package builds passed. Whitespace checks passed.
This follow-up adds no live-service evidence and needs no configuration change.

E1 is complete. E4 now includes this validated Turn-import consolidation; wider
source-audit work remains deferred. Production source is 90 Python files and
32,949 lines, 42 fewer than the client-stage commit (580 fewer than `7c69b5474`).
