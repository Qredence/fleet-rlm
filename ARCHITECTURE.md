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
areas include `files`, `artifacts`, `memory`, and `skills`.

User Skills are stored locally under `.fleet/skills/` and synchronized to the
session workspace. `.agents/skills/` is reserved for developer-facing agent
skills and is not a Fleet user-Skills directory.

## Reasoning, evaluation, and delegation

DSPy provides the reasoning engine and model calls. Daytona is the execution
boundary for generated Python. TypeSafe routing is optional and configured via
`FLEET_TYPESAFE_API_KEY`. MLflow is used for tracing/evaluation; GEPA optimization is
an explicit operation rather than an automatic CI or release action.

The engine includes a child investigation tool. Do not infer or promise a
particular child-path depth or isolation guarantee from this document; consult
the implementation and provider configuration for the exact behavior.

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
