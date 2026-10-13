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
`app_lifecycle.py` composes provider resources owned by `ApplicationComposition` and one typed `RouteServices`
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
| `turns/coordinator.py` (`TurnRuntime`) | Coordinates Run claim, preparation, execution, settlement, cancellation, and cleanup. |
| `turns/preparation.py` and `app_preparation.py` | Prepare authorized context, Attachments, Skills, capabilities, and the acquired execution environment for a claimed Run. |
| `turns/settlement.py` | Defines the `RunLifecycle` contract and settlement flow, including result validation and candidate publication. |
| `persistence/` | Owns SQL-backed Session and Run state, durable repositories, and commit operations; Alembic owns live schema changes. |
| `sessions/` | Owns the Session, Run, and Turn domain contracts shared by `persistence/`, `rlm/`, and `turns/` (it shares event and result value types with `rlm/`), projects committed history, and owns the bounded, revisioned task checkpoint. It must not import `turns/` or `persistence/`. |
| `workspace/` | Owns durable content, scoped file access, host I/O, artifacts, attachments, and workspace memory. |

One Daytona Volume holds each Workspace under `workspaces/<workspace_id>`, and
Fleet mounts it two ways:

| Sandbox | Mount | Holds |
| --- | --- | --- |
| Root `session` (and `workspace-child`) | `workspaces/<wid>/sessions/<sid>/workspace` at `/workspace` | The Session Workspace, the only durable path model code sees. Run scratch is local at `/tmp/fleet/<run>`, including staged Attachment copies. |
| Short-lived host I/O | `workspaces/<wid>` at the configured `volume_mount_path` | Workspace memory, task checkpoints, Attachment originals, Artifacts, result snapshots, Projects, and public `files/`, reached only through host owners. |

Committed Turns are projected into native `dspy.History` for a new invocation.
The interpreter may receive a serialized projection of that history, while
durable conversation authority remains with the Session and its persistence
owners. The task checkpoint separately retains bounded goal and progress; Python
variables and DSPy's private `REPLHistory` are not application-owned durable
state. Workspace memory is cross-Session file state with process-local
coordination, not a substitute for Turn Commit.

## Reasoning and execution

`rlm/program.py` constructs pinned native `dspy.RLM` and stock `dspy.LM`
instances; `rlm/adapter.py` owns the pinned JSON/iteration compatibility boundary; `rlm/execution.py` owns invocation and observation, and
`rlm/recursion.py` owns Fleet child policy and admission. DSPy owns its loop,
`REPLHistory`, trajectory semantics, and provider retries. Fleet supplies
Turn-scoped tools, per-Turn LM copy isolation with direct trace attribution,
and the shared finalization ledger; nothing wraps the LMs, and Fleet enforces no
per-Turn LM deadline. Shared admission uses the absolute Turn deadline and
closes before settlement drains owned work. Its reserve refuses new tool/child
work while validated root finalization output can still be accounted. There is
no second planner or model router.

`RLMRunner` retains non-cancellable work through `WorkerOwnership`. If a Turn
ends while that worker is still running, its execution trace stays open until
the owned work drains; trace export completion does not delay Turn settlement
or allow late work to change committed state.

Use ordinary Sandbox Python for deterministic inspection and reduction. Native
`llm_query` and `llm_query_batched` provide bounded semantic calls inside the
current invocation. Fleet `rlm_query` and `rlm_query_batched` are separate,
bounded child investigations that run their own iterative DSPy invocation.
Every Root invocation receives child tools and a child factory. Full children stop at depth one, share the
parent Turn budget, and have bounded admission and ordered outcomes.

`daytona/runtime.py` coordinates retained Session roots, binding fences and shutdown.
Concrete child and host-I/O owners live in `daytona/children.py` and
`daytona/host_io.py`; existing SDK operations and lease cleanup live in
`daytona/provider.py` and `daytona/leases.py`. The
[resource ownership table](docs/reference/daytona-lifecycle.md) records their
transitions and release conditions. `daytona/interpreter.py` submits
generated Python to Daytona. Its authenticated, JSON-only broker in
`daytona/broker.py` dispatches authorized host tools and DSPy semantic calls;
model-authored code does not run in the Fleet process. A healthy root Sandbox
may be reused across clean sequential Turns, but each invocation gets fresh
bindings, tools, budget, and DSPy history.

The single-user application serializes Workspace I/O operation groups through
the runtime. One host I/O Sandbox may remain warm between operations, retaining
its shared admission permit until confirmed deletion. Queueing and preparation
have a finite acquisition deadline; idle expiry and obsolete replacement use
the same cleanup owner. Shutdown drains preparation and active file operations
before deleting their Sandbox. Unconfirmed cleanup blocks replacement and
remains runtime-owned for retry.

Only `/pending` issues host-tool leases; `/result` fences late or duplicate
delivery against the issued lease. Polling dispatches each claimed tool once.
The authenticated `/action` control enables tool admission for the current SDK
execution and its deadline; it accepts no Python code.

The adapter calls one typed backend protocol: `run(code, variables,
on_stdout=...)` returns `BackendExecutionResult`. `_SandboxProcessBackend`
owns one official Daytona interpreter context per invocation, creates it lazily,
executes with `run_code` and SDK stdout/stderr callbacks, and deletes it on
shutdown. The broker only dispatches host tools; it owns no executable namespace
or output stream. Configure its local tool port through
`sandbox_backend(..., broker_port=...)`.

Each SDK action limits ordinary stdout and stderr inside the Sandbox to 65,536
characters per stream, plus one truncation notice. Execution continues after
truncation. A validated `SUBMIT` payload uses a separate bounded final frame
after the original streams are restored. Host callbacks enforce a transport
ceiling before the SDK accumulates further output; an unexpected excess fences
the invocation and taints the root. A failed or uncertain action is never replayed.

Synchronous workers use the official synchronous Daytona SDK with the same host
configuration as `AsyncDaytona`, which owns lifecycle operations. Worker scopes
close their synchronous clients after context and broker cleanup. The pinned
SDK has no public synchronous close method, so the provider module owns a narrow
shutdown compatibility seam. Application host I/O bridges asynchronous SDK
operations onto its composition loop and creates no synchronous SDK clients.
The Run storage view is closed after owned browser, file, and scratch work
settles, before releasing the invocation gate. Closing a view does not close
the composition-owned asynchronous client.

The active semantic-child path requests a Volume-less Sandbox. Fleet resolves
selected child inputs under existing Session authority, stages bounded copies
in private scratch, validates and harvests declared result files, and then
cleans up the child. Children receive no parent Workspace tools, memory, task
checkpoint, publication capability, credentials, or writable parent Volume.
Root verifies child findings and owns any durable updates. An unresolved child
or provider cleanup cannot become a successful committed Turn.

Public page retrieval, Git inspection, and package installation run as ordinary
Sandbox Python or subprocess work. Optional Tavily discovery uses one host-bound
`web_search` tool through the existing broker, with host-only credentials and a
Turn-local credit reservation counter. It returns bounded unverified snippets,
never synthesized answers or arbitrary host-fetched page bodies. The retired host
URL-fetching subsystem is not an alternate execution path.

The bundled `web-research` Skill guides public research. Its HTTP helper retains
bounded text and provenance in Run scratch; agent-browser connects over loopback
CDP to fresh, explicitly sandboxed Chromium owned by that Run. Browser command guidance comes from the installed version’s upstream core Skill;
Fleet’s runtime Skill supplies only its invocation and evidence adapter. The native
launcher belongs to `daytona/`, not model-readable Skill resources. The narrow
`open_browser` tool retains its launch process group on the host; subsequent
actions use the CLI directly. Browser and driver processes carry a Run marker
and must be confirmed stopped before success commit,
scratch removal, and root reuse. Cleanup failure taints the root for retirement.
Browser-free semantic children do not receive the host search tool. Skills grant
no authority and do not implicitly load dependencies under exact selections.
Daytona network and mount policy
requests do not by themselves prove provider enforcement; the recorded Phase 5
network-policy waiver remains in force.

## Events, policy, and optional services

`rlm/event_types.py` defines transport-neutral Runtime Events; `rlm/events.py` observes tools and DSPy callbacks. API projection owns
the public SSE shape and durable replay; the TUI consumes that contract and
generated HTTP types. `observability/` may record MLflow traces and evaluation
data, but tracking failures do not decide execution or commit. `optimization/`
is separate from serving. Bundled Skills provide strategy and manifested
resources without granting tools, permissions, budgets, or scheduling authority.

Optional JSONL Turn captures use one writer owned by `RuntimeInventory` and
closed by the application lifespan. Retention excludes active captures;
capture failures do not affect execution or establish durable commitment.

Non-secret runtime policy comes from `config/fleet.toml` through typed settings
in `config/`. The configuration names the environment variables from which
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
| `docs/reference/configuration-environment.md` | `make config-reference` / `make check-docs` |

Use [AGENTS.md](AGENTS.md#validate-and-deliver-safely) for validation selection
and the [testing strategy](docs/how-to-guides/testing-strategy.md) for test
lanes. `make check-codebase-tree` and `make check-dependency-boundaries` guard
ownership. Local checks do not certify provider behavior, containment,
comparative quality, or release promotion.

Skill selection remains in the Root reasoning loop. The isolated
`optimization/skill_selection.py` comparison can expose advisory Jev recommendations
to a native RLM in an evaluation harness. It is not imported by serving composition;
explicit selections bypass it, and it cannot load Skills or grant capabilities.
