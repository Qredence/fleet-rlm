# Backend source layout

```text
src/fleet_rlm/
├── api/                  # HTTP identity, dependencies, schemas, routes, and SSE
├── cli/                  # maintained TUI/backend entry points and Daytona doctor
├── config/               # settings, TOML loading, and policy validation
├── daytona/              # SDK lifecycle, interpreter, broker, diagnostics, errors
├── observability/        # fail-soft tracing, diagnostics, evaluation, and analytics
├── optimization/         # offline GEPA prompt optimization (not on Turn serving path)
├── persistence/          # SQLAlchemy models and repository adapters
├── rlm/                  # DSPy program, execution, recursion, and events
├── sessions/             # Session, Run, and Turn domain contracts, history, and task checkpoint
├── skills/               # bundled catalog, resolution, resources, and tools
├── turns/                # Turn coordinator, preparation, settlement, and stream projection
├── workspace/            # files, projects, memory, artifacts, attachments, and storage
├── app.py                # FastAPI construction and lifespan
├── app_lifecycle.py      # provider resource construction and closure
├── app_services.py       # composed lifespan service inventory
├── main.py               # ASGI entry point
├── paths.py              # provider-neutral Volume layout and path identity
├── result_snapshot.py    # private commit-gated typed-result encoding
└── snapshot_contract.py  # immutable Daytona Snapshot name policy
```

See the root [architecture](../../ARCHITECTURE.md) for dependency boundaries. Layering is `sessions` (contracts) ← `persistence` ← `turns` (orchestration) ← `api`; `rlm` is a sibling that shares event and result value types with `sessions`.

Start lifecycle investigations in `turns/coordinator.py`, `turns/preparation.py`, and `turns/settlement.py`. Native invocation and observation live in `rlm/execution.py` and `rlm/events.py`; `rlm/program.py` constructs the pinned public DSPy API directly. Daytona interpreter result handling lives beside the interpreter in `daytona/interpreter.py`. Each Run gets a fresh DSPy program; a healthy broker Root Sandbox may persist across sequential clean Turns. SDK calls remain in `daytona/`. `app.py` owns FastAPI lifespan, `app_lifecycle.py` builds and closes provider resources, and `app_services.py` defines the composed service inventory.

The maintained TypeScript client is separate under `tools/fleet-tui/`; its
generated HTTP types are owned by `make api-sync`.
