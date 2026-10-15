# Fleet RLM Lite architecture

Fleet RLM Lite is a small Python system. The current runtime implementation is
under `src/fleet_rlm/`; it is not the former multi-package backend or
TypeScript terminal application.

## Request path

```text
Rich CLI ──HTTP/SSE──> FastAPI server ──> DSPy engine
                              │                 │
                              │                 └── Daytona Python execution
                              └── PostgreSQL VFS, MLflow traces/evaluation
```

`cli.py` provides the Rich client and server command. `server.py` owns the
FastAPI endpoints, SSE streaming, and session runtime registry. A Session
runtime combines a Daytona sandbox manager, PostgreSQL VFS harness, Skill
manager, and DSPy engine. `interpreter.py` creates the per-session Daytona
workspace and runs Python there. `memory_and_eval.py` handles PostgreSQL
workspace/turn persistence, engine composition, MLflow observability and GEPA
optimization. `skills.py` validates and synchronizes Skills. `config.py`
defines the typed `FleetSettings` configuration.

## State and workspace

PostgreSQL holds Session turn history and file content. The `VolumePostgresHarness`
creates the tables it needs at startup and synchronizes workspace data through
the Daytona Volume. There are no Alembic migrations in this Lite runtime.
Daytona uses per-session subpaths for its workspaces. The supported workspace
areas include `files`, `artifacts`, `memory`, `skills`, `buffers`, and `sessions`.
Additive Lite tables retain reviewed source-backed memories, idempotent turn
intents/outcomes, and persisted mode/dataset settings without renaming old tables.
A PostgreSQL session advisory lock gives one worker ownership of a runtime and
is released on close or database disconnect. One connection is held per runtime.

User Skills are stored locally under `.fleet/skills/` and synchronized to the
session workspace. `.agents/skills/` is reserved for developer-facing agent
skills and is not a Fleet user-Skills directory.

## Reasoning, evaluation, and delegation

DSPy provides the reasoning engine and model calls. Daytona is the execution
boundary for generated Python. TypeSafe routing is optional and configured via
`FLEET_TYPESAFE_API_KEY`. MLflow is used for tracing/evaluation; GEPA optimization is
an explicit operation rather than an automatic CI or release action.

Strict `rlm` enters a primary DSPy 3.4.1 RLM; `flex` enters Flex. Hybrid triage
selects direct CoT, Flex, or deep RLM, with primary RLM fallback on routing errors.
Dataset tasks cannot take a context-free direct answer path. Dataset handles
carry source digests and bounded previews; files stay in the sandbox.

DSPy's in-REPL facade constructs host-mediated predictors and agents. Fresh
nested RLM interpreters allocate real child sandboxes, linked to the session
root by default but mounted to distinct `agents/{session}/{agent}` Volume
subpaths. Only input files and Skills are copied; artifacts are promoted with
agent provenance. Namespaces alone are not filesystem isolation. The host
enforces depth, active/total children, call budget, and a remote-stop deadline.
Deletion waits for provider confirmation; ambiguous creation is not blindly retried.
Local allocation journals support expired-allocation reconciliation on the next
root provisioning for that session; provider TTL covers loss of local journals.

The explicit `spawn_agent` host tool accepts a subtask, selected `files/` or
`skills/` input paths, approved delegation tools, and bounded DSPy iteration/query
limits. It copies no input files by default, stages child outputs until success,
and returns bounded findings plus promoted paths, digests, and agent IDs.
`spawn_agents` runs several independent `spawn_agent` children concurrently on a bounded
thread pool (at most the active-child capacity), each with a copy of the caller's context,
and returns one result per subtask in order. A child that fails for ordinary reasons comes
back as `{findings: "", error, artifacts: []}`; deadline, cancellation, and shared-budget
exhaustion still abort the Turn. A child whose reply cannot be parsed is re-asked once by
`FleetRLMChatAdapter` before it fails.
`allowed_tools=[]` disables further Fleet tool delegation; it is not a Python
or network sandbox policy. All generated Python still runs under Daytona's
deployment policy. Native facade-created RLMs retain the compatibility policy
of copying the current parent's `files/` and `skills/`, not its artifacts/memory.

The optimization runner curates approved gold answers, groups by source lineage,
and uses fresh disposable runtimes for each GEPA/evaluation example. Candidate
programs and manifests are separate from operator-approved active pointers.
The reflection LM is explicitly labeled when used as the metric judge; MLflow
judge URIs are never passed as DSPy model IDs. ReAnchor is currently reported
as skipped without a compatible calibration client. Passing unit tests is not
evidence of provider compatibility or a measured quality improvement.

## Deployment and trust boundaries

The HTTP API currently has no caller authentication. It binds to `127.0.0.1`
by default. A non-loopback listener requires the explicit
`FLEET_ALLOW_NON_LOOPBACK_BIND=true` setting, but that is not authentication.
Only expose it behind a separately secured boundary. Model-generated Python
is intended to execute in Daytona, not in the local server process.

Live Daytona behavior depends on valid API key, organization ID, Volume ID,
snapshot, network access, and provider-side policy. Documentation or local CI
does not establish that those external resources work or enforce a security
policy.

## Change ownership

- `config.py`: settings, defaults, validation, and model construction.
- `skills.py`: Skill validation and `.fleet/skills/` synchronization.
- `interpreter.py`: Daytona sandbox and remote Python execution.
- `memory_and_eval.py`: persistence, DSPy engine, MLflow, and GEPA behavior.
- `server.py`: HTTP routes and streaming response.
- `cli.py`: Rich terminal client and command-line entry point.

Keep documentation aligned with implemented routes and settings. Avoid
reintroducing claims about removed migrations, `config/fleet.toml`, a Node TUI,
or retired API surfaces.
