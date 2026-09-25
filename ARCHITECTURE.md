# Fleet RLM Architecture

Fleet RLM is a durable, Session-first system for long-running model work.
FastAPI exposes HTTP and SSE, DSPy owns native RLM reasoning, and Daytona
provides execution environments. The maintained interactive client is the
pi-tui application in `tools/fleet-tui/`.

This document describes current ownership and trust boundaries. It is not a
roadmap. Change it alongside code, tests, policy, and generated contracts when
those boundaries change.

Canonical Run Environment set: `daytona`.

## Conceptual package map

The following sketch records the plan's ownership target. It is not a literal
description of every current file and is not a rename checklist. The current
package layout and source-of-truth owners are listed in the ownership map
below; keep working packages in place unless a structural change is separately
justified.

```text
src/fleet_rlm/
├── __init__.py
├── main.py                  # Existing application entry point
├── app.py                   # FastAPI construction and lifespan
├── config.py                # Typed configuration and loading
├── turns.py                 # One turn lifecycle coordinator
├── sessions.py              # Conversation and active-task context
├── events.py                # Application event types
├── paths.py                 # Canonical workspace/path policy
├── memory.py                # Memory retrieval and mutation policy
├── telemetry.py             # Optional observation, not execution ownership
│
├── rlm/
│   ├── __init__.py
│   ├── program.py           # Signatures, model setup, native RLM construction
│   ├── execution.py         # Invocation boundary and observation relay
│   ├── recursion.py         # Optional child-RLM delegation
│   └── budget.py            # Necessary shared resource accounting
│
├── daytona/
│   ├── __init__.py          # Small exports only
│   ├── runtime.py           # Sandbox/volume acquisition and lifecycle
│   ├── interpreter.py       # Native DSPy CodeInterpreter implementation
│   └── broker.py            # Authenticated remote execution/tool transport
│
├── workspace/
│   ├── __init__.py
│   ├── files.py             # Concrete workspace file operations
│   └── tools.py             # Small model-facing capability functions
│
├── api/                     # Existing HTTP/SSE contract; transport only
├── persistence/             # Existing database models and durable operations
│
├── skills/
│   ├── __init__.py
│   ├── loader.py            # Loading, selection, resource installation
│   └── bundled/             # Packaged skill content
│
├── cli/
│   ├── __init__.py
│   ├── main.py              # Preserve published entry points
│   ├── supervisor.py
│   └── doctor.py
│
└── optimization/            # Optional workflow, isolated from serving
```

For example, the current implementation keeps typed settings in
`config/settings.py`, active-task persistence in `sessions/task.py`, memory in
`workspace/memory.py`, and optional tracing in `observability/`. Keep these
established owners; do not add the sketch's `config.py`, `sessions.py`,
`memory.py`, or `telemetry.py` just to match its shape.

## Runtime model

```text
Workspace -> Session -> Turn -> Run claim and preparation
  -> native DSPy RLM in a Daytona interpreter
  -> Runtime Events -> HTTP/SSE -> pi-tui
  -> settlement -> artifact promotion -> Turn Commit -> durable replay
```

A Workspace owns Sessions, Skills, Attachments, Artifacts, and workspace-scoped
state. A Session owns ordered Turns and committed conversation history. A Turn
is one user request; a Run is an attempt to execute it. An answer or Artifact
becomes public only after durable settlement and Turn Commit.

## Reasoning and delegation

DSPy owns the reasoning loop, `REPLHistory`, and native trajectory behavior.
Fleet constructs native `dspy.RLM` programs and supplies invocation-scoped
interpreters, tools, and budgets. There is no mandatory planner or model
router.

Use the least costly mechanism that fits the work:

| Mechanism | Use |
| --- | --- |
| Ordinary Python | Deterministic search, parsing, joins, calculations, and validation. |
| Native `llm_query` / `llm_query_batched` | Bounded semantic interpretation, extraction, or comparison within the current invocation. |
| Fleet `rlm_query` / `rlm_query_batched` | A distinct investigation that benefits from its own iterative DSPy invocation. |

The native-only `daytona-native` profile is the configured default and keeps
Fleet full-child recursion disabled. `daytona-recursive` is the explicit
opt-in. Full child depth is limited to one level; children may use native
semantic calls but Fleet does not start full grandchildren. Child work shares
the owning Turn's resource budget and remains subject to bounded admission,
deadlines, and cleanup.

## Execution and trust boundaries

The root interpreter runs model-authored Python inside a Session Daytona
Sandbox. That Sandbox mounts the authorized Session workspace at `/workspace`;
ordinary Python and Session workspace tools address those files. Each Run has
invocation-local scratch. A sandbox-local broker forwards JSON-only tool
requests to host tools and returns bounded, sanitized results; model code does
not run in the Fleet process.

The current recursive executor selects the `semantic-child` profile. Fleet
requests a child Sandbox without a Volume mount, resolves only the selected
files or subtree under existing Session/Project authority, and copies those
files into private child-local scratch. File sizes and available modification
metadata are checked around bounded materialization; bounded
resolve/stage spans record file counts, bytes, and elapsed time. Declared child
result paths are checked for traversal and symlinks, harvested, size-bounded,
and persisted in the parent Run before child cleanup. Children receive no
parent Workspace tools, memory, task checkpoints, attachment storage,
publication capability, credential-bearing files, or Root mutable state. The
Root verifies findings and performs durable updates through existing owners.
This describes Fleet's request and data flow, not independent proof of provider
mount or network enforcement. The canary confirms the provider reports no
Volume mount for its child; the requested network block remains unverified. It
does not prove network blocking, timeout or claim-loss containment, or
comparative quality.

`DaytonaRuntime` owns reusable Session resources, ephemeral child resources,
host I/O Sandboxes, and cleanup. The Turn coordinator owns claim, preparation,
execution, settlement, and commit. Each Run retains interpreter, worker, and
remote-resource ownership through settlement. Cancellation, timeout, or
authority loss stops new child admission; unresolved containment cannot be
reported as a successful committed Turn.

`TurnPreparationPlan` owns preparation orchestration and receives one bound
environment-acquisition callable from application composition. It does not
retain a stateful provider wrapper. The callable acquires the Session root
through `DaytonaRuntime`, while the separate Workspace gateway provisions the
volume layout; these authorities remain distinct.
`workspace/host_io.py` owns `DaytonaRunStorage`: asynchronous private
attachment, artifact, and result-snapshot operations route between Run scratch
and authorized host storage. Its narrow synchronous `volume_fs` view bridges
the interpreter's storage tools. Turn preparation builds model-facing
capabilities from the resulting scoped handles; it does not build the tool
catalog or project task, memory, attachment, or Skill context.
`sessions/history.py` projects the claimed checkpoint, and
`sessions/history_transport.py` owns the Sandbox-serializable history form;
the environment selects which format the interpreter boundary accepts.

Application composition constructs one complete `RouteServices` value and
stores it in the lifespan-owned `RuntimeInventory` beside optional process
resources. Routes receive that same typed value; readiness is published only
after the inventory has been built.

The runtime's `DaytonaSessionRecord` is the registry entry for a retained
Session root and its active invocation. It points directly to the
`InterpreterLease`; the record owns Session cleanup state, while the runtime
retains pending provider operations. Each disposable child has one
Sandbox-keyed cleanup record that owns its active lease, admission permit, and
close task. Temporary Workspace I/O Sandboxes use the same runtime-owned
`SandboxLease` records used for confirmed cleanup. Children do not pass through
the root-session registry or a second asynchronous context-manager acquisition
path. Cleanup tasks remain attached to their owning `DaytonaRuntime`. Daytona
owns child lifecycle errors; the RLM executor owns delegation policy. The
provider runtime does not import recursion policy.

Public search, known-page downloads, Git inspection, and package installation
run as ordinary Sandbox Python or subprocess work. The host URL-fetching
subsystem remains retired under the recorded Phase 5 network-policy waiver.
Sandbox network requests reflect deployed Daytona policy; the waiver does not
establish public-only egress or child network isolation.

## Durable state, observation, and interaction

Committed Turns enter native `dspy.History`. The versioned Session task
checkpoint separately stores bounded goal, decisions, source references and
revisions, completed work, and pending work. Python variables and interpreter
history do not survive as application-owned durable state. Memory coordination
remains single-process; trace concurrency does not strengthen that guarantee.

MLflow observes execution attempts, including child invocation, staging,
harvesting, settlement, and cleanup. One trace belongs to one attempt; children
are nested under it. Content is bounded and sanitized before export. Tracking
and export failures remain fail-soft and never grant execution authority or
prove a Turn committed.

Runtime Events are transport-neutral. The API projects live events and durable
replay into the same public shapes; pi-tui renders child progress and task
continuity without owning backend lifecycle or execution policy. Skills are
progressively loaded strategy guidance with versioned, manifested resources;
they do not grant tools, permissions, budgets, or scheduling authority.

## Ownership map

| Area | Owner and boundary |
| --- | --- |
| HTTP, schemas, OpenAPI, SSE | `src/fleet_rlm/api/` validates and projects transport; routes use lifespan services. |
| Process wiring | `app.py` owns FastAPI lifespan; `app_lifecycle.py` builds and closes provider resources. |
| Turn lifecycle | `src/fleet_rlm/turns.py` coordinates claim, preparation, execution, settlement, and commit. |
| Reasoning and budgets | `src/fleet_rlm/rlm/` owns DSPy construction, semantic and child tools, shared accounting, and transport-neutral events. |
| Daytona integration | `src/fleet_rlm/daytona/` owns SDK interaction through `runtime.py`, `interpreter.py`, and `broker.py`; `diagnostics.py` and `errors.py` provide support functions, not resource lifecycle. |
| Workspace and host I/O | `workspace/` resolves authorized files and performs short-lived host operations. |
| Durable task state | `sessions/task.py` owns the bounded checkpoint; API and TUI project it without creating another task database. |
| Durable domain data | `sessions/`, `workspace/`, `attachments/`, `artifacts/`, and `persistence/` own their policies and adapters. |
| Observation and evaluation | `observability/` and `optimization/` own tracing and evaluation, not serving execution. |
| Terminal client | `tools/fleet-tui/` consumes generated HTTP types and public SSE; it owns interaction only. |

## Boundaries for changes

- Keep API handlers as adapters. Runtime Events stay provider-neutral until
  projected to clients.
- Keep mutable adapters, callbacks, deadlines, retries, tools, and budgets
  scoped to a Turn or invocation. Process-scoped model objects are templates.
- Keep provider exceptions, credentials, private paths, and raw infrastructure
  failures out of public events, traces, and API errors.
- Validate child paths, file types, sizes, and symlinks before staging or
  harvesting. Do not infer authorization from prompts or Skill text.
- Keep root-owned task, memory, artifact publication, and settlement under
  their existing services. Alembic owns live schema evolution.
- Preserve Phase 5's explicit network-policy risk acceptance. Do not add a
  host research service or claim stronger egress guarantees without evidence.
- Do not add arbitrary-depth recursion, another scheduler, shared writable
  child state, a source/snapshot service, or an alternate execution path
  without a concrete measured need and an updated ownership boundary.

## Contracts and validation

Public contracts originate in backend models and owning generators. Never
hand-edit generated artifacts or client types.

| Artifact | Regenerate / verify |
| --- | --- |
| `openapi.yaml`, `tools/fleet-tui/src/generated/openapi.ts` | `make api-sync`, then `make api-check` |
| TUI stream fixtures and validators | `make stream-sync`, then `make stream-check` |
| `docs/reference/profile-matrix.md` | `make profile-matrix`, then `make check-docs` |

Use [AGENTS.md](AGENTS.md#work-and-validate-safely) to select local checks and
the [testing strategy](docs/how-to-guides/testing-strategy.md) for suite
boundaries. `make check-codebase-tree` and `make check-dependency-boundaries`
guard ownership; `make api-check` and `make stream-check` guard public
contracts. Passing local tests does not certify provider behavior, release
readiness, or promotion.
