# Fleet RLM Architecture

Fleet RLM is a Session-first FastAPI application. DSPy owns the native RLM
reasoning loop, Daytona runs generated Python in Sandboxes, and the maintained
pi-tui client consumes the public HTTP/SSE contract. This document records
current ownership and trust boundaries. For the literal package tree, use the
[source layout](docs/reference/source-layout.md).

Canonical Run Environment set: `daytona`.

## Request and ownership flow

```text
fleet CLI / ASGI entry point
  → FastAPI lifespan and RouteServices
  → API validation and SSE projection
  → TurnRuntime: claim → prepare → execute → settle → cleanup
       ├─ DSPy RLM + Daytona interpreter and tool broker
       └─ Session/Run repositories and commit-gated Artifacts
  → committed Turn replay → pi-tui
```

`src/fleet_rlm/main.py` exports the ASGI app; `cli/main.py` exposes the `fleet`
and `fleet-rlm` commands. `app.py` builds FastAPI and owns its lifespan.
`app_lifecycle.py` composes provider resources and one typed `RouteServices`
inventory, defined in `app_services.py`. Routes receive those services after
composition is ready; they do not construct a second runtime.

The Turn route validates the request, opens an owned `TurnRuntime` stream, and
projects transport-neutral Runtime Events into SSE. Transient preparation
status can precede the canonical Run stream. The TypeScript client under
`tools/fleet-tui/` renders live and durable projections; it does not execute
models or own backend lifecycle.

## Turn lifecycle and durable state

A Workspace owns Sessions, files, memory, Skills, Attachments, and Artifacts. A
Session owns ordered committed Turns. A Turn is one user request; a Run is an
attempt to execute it. The public answer and Artifacts become durable only
through successful settlement and Turn Commit.

| Owner | Responsibility |
| --- | --- |
| `turns.py` (`TurnRuntime`) | Coordinates Run claim, preparation, execution, settlement, cancellation, and cleanup. |
| `turn_preparation.py` | Prepares authorized context, Attachments, Skills, capabilities, and the acquired execution environment for a claimed Run. |
| `turn_settlement.py` | Defines the `RunLifecycle` contract and settlement flow, including result validation and candidate publication. |
| `persistence/` | Owns SQL-backed Session and Run state, durable repositories, and commit operations; Alembic owns live schema changes. |
| `sessions/` | Projects committed history and owns the bounded, revisioned task checkpoint. |
| `attachments/`, `artifacts/`, `workspace/` | Own durable content, scoped file access, host I/O, and workspace memory. |

Committed Turns are projected into native `dspy.History` for a new invocation.
The interpreter may receive a serialized projection of that history, while
durable conversation authority remains with the Session and its persistence
owners. The task checkpoint separately retains bounded goal and progress; Python
variables and DSPy's private `REPLHistory` are not application-owned durable
state. Workspace memory is cross-Session file state with process-local
coordination, not a substitute for Turn Commit.

## Reasoning and execution

`rlm/program.py` constructs pinned native `dspy.RLM` and `dspy.LM` instances;
`rlm/execution.py` owns invocation and observation, and `rlm/recursion.py`
owns Fleet child policy and admission. DSPy owns its loop, `REPLHistory`, and
trajectory semantics. Fleet supplies Turn-scoped tools, deadlines, and the
shared budget. There is no second planner or model router.

Use ordinary Sandbox Python for deterministic inspection and reduction. Native
`llm_query` and `llm_query_batched` provide bounded semantic calls inside the
current invocation. Fleet `rlm_query` and `rlm_query_batched` are separate,
opt-in child investigations that run their own iterative DSPy invocation.
The configured default `daytona-native` profile disables full-child recursion;
`daytona-recursive` enables it. Full children stop at depth one, share the
parent Turn budget, and have bounded admission and ordered outcomes.

`daytona/runtime.py` owns provider Sandboxes, retained Session roots, disposable
children, host I/O leases, and cleanup. `daytona/interpreter.py` submits
generated Python to Daytona. Its authenticated, JSON-only broker in
`daytona/broker.py` dispatches authorized host tools and DSPy semantic calls;
model-authored code does not run in the Fleet process. A healthy root Sandbox
may be reused across clean sequential Turns, but each invocation gets fresh
bindings, tools, budget, and DSPy history.

The active semantic-child path requests a Volume-less Sandbox. Fleet resolves
selected child inputs under existing Session authority, stages bounded copies
in private scratch, validates and harvests declared result files, and then
cleans up the child. Children receive no parent Workspace tools, memory, task
checkpoint, publication capability, credentials, or writable parent Volume.
Root verifies child findings and owns any durable updates. An unresolved child
or provider cleanup cannot become a successful committed Turn.

Public search, page retrieval, Git inspection, and package installation run as
ordinary Sandbox Python or subprocess work. The retired host URL-fetching
subsystem is not an alternate execution path. Daytona network and mount policy
requests do not by themselves prove provider enforcement; the recorded Phase 5
network-policy waiver remains in force.

## Events, policy, and optional services

`rlm/events.py` defines transport-neutral Runtime Events. API projection owns
the public SSE shape and durable replay; the TUI consumes that contract and
generated HTTP types. `observability/` may record MLflow traces and evaluation
data, but tracking failures do not decide execution or commit. `optimization/`
is separate from serving. Bundled Skills provide strategy and manifested
resources without granting tools, permissions, budgets, or scheduling authority.

Non-secret runtime policy comes from `config/fleet.toml` through typed settings
in `config/`. The selected profile names the environment variables from which
secrets are read. Policy changes require a restart. Runtime state such as
callbacks, bindings, deadlines, and budgets stays scoped to a Turn or
invocation; process resources are composed and closed by the lifespan.

## Boundaries and contracts for changes

- Keep API routes as validation and projection adapters. Keep provider errors,
  credentials, private paths, and raw infrastructure failures out of public
  events and traces.
- Preserve Session authority for child paths and files. Validate sizes,
  symlinks, and declared outputs; do not infer permissions from model or Skill
  text.
- Keep `DaytonaRuntime` responsible for provider-resource ownership and
  cleanup, and `TurnRuntime` responsible for the Run lifecycle. Late work
  cannot mutate settled state.
- Change `config/fleet.toml` and Alembic migrations through their owning
  workflows. Do not add a parallel loop, scheduler, or execution path without
  an explicit boundary change.

Public contracts originate in backend models and generators. Never hand-edit
generated artifacts:

| Generated artifact | Regenerate / verify |
| --- | --- |
| `openapi.yaml`, `tools/fleet-tui/src/generated/openapi.ts` | `make api-sync` / `make api-check` |
| TUI stream fixtures and validators | `make stream-sync` / `make stream-check` |
| `docs/reference/profile-matrix.md` | `make profile-matrix` / `make check-docs` |

Use [AGENTS.md](AGENTS.md#work-and-validate-safely) for validation selection
and the [testing strategy](docs/how-to-guides/testing-strategy.md) for test
lanes. `make check-codebase-tree` and `make check-dependency-boundaries` guard
ownership. Local checks do not certify provider behavior, containment,
comparative quality, or release promotion.
