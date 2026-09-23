# Fleet RLM Architecture

Fleet RLM is a durable, Session-first system for long-running language-model
work. FastAPI exposes the HTTP/SSE contract, DSPy performs bounded reasoning,
and Daytona supplies execution resources. The maintained interactive client is
the pi-tui application in `tools/fleet-tui/`.

Architecture records the boundaries that protect correctness and evolution. It
does not freeze a product plan or prohibit replacing an implementation. Change
these boundaries with the code, tests, configuration, and generated contracts
that make the new design true.

Canonical Run Environment set: `daytona`.

## Runtime model

```text
User / Workspace
  -> Session -> Turn -> Run preparation and claim
  -> DSPy RLM and authorized tools
  -> typed Runtime Events -> HTTP/SSE client projection
  -> settlement, artifact promotion, Turn Commit, durable replay
```

A Workspace owns Sessions, Skills, Attachments, Artifacts, and workspace-scoped
state. A Session owns ordered Turns and committed history. A Turn is one user
request; a Run is an attempt to execute it. An answer or Artifact is public
only after durable settlement and Turn Commit.

The Daytona interpreter keeps model-authored Python and its namespace inside a
Sandbox. When a Run has authorized Fleet tools, a sandbox-local broker forwards
JSON-only tool requests to the host and returns sanitized results; it does not
move model code into the Fleet process. The broker lifecycle is owned by the
interpreter and closes with its Sandbox lease. Reused root leases reset the
execution namespace and invocation credential between Turns. `DaytonaRuntime`
is the sole application-facing owner of reusable root records, disposable
child leases, and temporary Workspace I/O Sandboxes. It constructs and disposes
the process SDK graph only after remote ownership settles. It coordinates
per-session acquisition without holding a runtime-wide lock across provider
calls, tracks late acquisitions until
their Sandboxes settle, validates retained roots against durable binding
generations, and serializes active invocations across preparation adapters.
Shutdown cannot retire a root while its invocation is active. Persistence
repositories remain the cross-worker generation authority; there is no
separate Daytona session manager. The runtime supplies the invocation-scoped interpreter
factory that native DSPy calls per RLM invocation. DSPy's native
semantic tools use the same broker path as Fleet tools. Consult the
[testing strategy](docs/how-to-guides/testing-strategy.md) for validation lanes
and the [performance budget](docs/reference/performance-budget.md) for dated
performance evidence.

## Ownership map

| Area | Owner and boundary |
| --- | --- |
| HTTP, schemas, OpenAPI, SSE | `src/fleet_rlm/api/` validates and projects transport; routes acquire services from composition. |
| Process wiring | `src/fleet_rlm/composition/` constructs the runtime graph and owns startup/shutdown orchestration. |
| Turn lifecycle | `src/fleet_rlm/chat/` claims Runs, prepares work, orders terminal events, settles results, and coordinates cleanup. |
| Reasoning | `src/fleet_rlm/rlm/` owns DSPy signatures, program construction, budgets, tools, events, and recursive orchestration. |
| Provider integration | `src/fleet_rlm/daytona/` is the Daytona SDK boundary with three core execution modules (`runtime.py`, `interpreter.py`, `broker.py`), `diagnostics.py` for operational doctoring, and `errors.py` for provider error taxonomy. |
| Durable domain data | `sessions/`, `workspace/`, `attachments/`, `artifacts/`, and `persistence/` own their policies and adapters. |
| Policy | `config/fleet.toml` and `src/fleet_rlm/config/` define selected, validated runtime policy. |
| Diagnostics and evaluation | `observability/` and `optimization/` own sanitized telemetry and evaluation contracts; they do not become a second execution path. |
| Terminal client | `tools/fleet-tui/` consumes generated HTTP types and public SSE; it owns interaction, not backend lifecycle. |

## Boundaries that changes must preserve

- API handlers are adapters, not an alternate composition root. Runtime Events
  remain transport-neutral until projected by the API.
- DSPy owns `REPLHistory` and native trajectory behavior. Fleet supplies
  committed history and invocation-local bindings without reconstructing or
  mutating DSPy's internal history.
- Process-scoped LMs are immutable templates. Deadlines, callbacks, retries,
  adapters, tools, and budgets are scoped to a Turn or child invocation.
- A Run retains ownership of its interpreter, sandbox, workers, and cleanup
  through settlement. Cancellation or timeout does not authorize detached work
  to mutate settled state.
- `RunLifecycle.finish()` owns successful durable settlement, Artifact
  publication, result snapshots, and Turn Commit. No failure path may publish
  a successful committed result.
- Daytona-specific SDK imports stay under `src/fleet_rlm/daytona/`. Provider
  exceptions, credentials, private paths, and raw infrastructure details do
  not cross into public events, traces, or API errors.
- Artifact bytes are validated before their public metadata is published.
  Alembic owns live schema migration; test helpers do not define production
  schema policy.
- Configuration names external values but never embeds them. Settings changes
  take effect only through a new runtime composition, not in-flight Turns.

## Dependency direction

```text
API transport
    ↓
application and lifecycle orchestration
    ↓
provider-neutral domain and reasoning
    ↓
provider and persistence adapters
```

Composition is the wiring seam. Depend on typed interfaces across the layers
where practical; keep infrastructure construction and provider-specific details
at their edges. New capabilities should extend the owning layer or introduce a
clear seam, rather than bypassing lifecycle, authorization, or settlement.

## Contracts and generated content

Public contracts originate in backend models and their generators. Never
hand-edit generated artifacts or client types:

| Artifact | Owner workflow |
| --- | --- |
| `openapi.yaml`, `tools/fleet-tui/src/generated/openapi.ts` | `make api-sync`, then `make api-check` |
| TUI stream fixtures and validation tables | `make stream-sync`, then `make stream-check` |
| `docs/reference/profile-matrix.md` | `make profile-matrix`, then `make check-docs` |

Bundled Skill Markdown is package content loaded into model context; preserve
its manifests, catalog, and resource-path contracts when updating it.

## Validation

Tests and generated checks are the executable expression of this architecture.
Use [AGENTS.md](AGENTS.md#validate-proportionally) to choose validation; the
[testing strategy](docs/how-to-guides/testing-strategy.md) explains suite
boundaries. `make check-codebase-tree` and `make check-dependency-boundaries`
verify structural ownership, while `make api-check` and `make stream-check`
guard public contract drift. Passing local checks establishes local evidence,
not provider certification or production rollout approval.
