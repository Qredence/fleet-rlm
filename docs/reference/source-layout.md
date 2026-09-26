# Backend source layout

```text
src/fleet_rlm/
├── api/                  # HTTP identity, dependencies, schemas, routes, and SSE
├── artifacts/            # artifact candidates, validation, and stores
├── attachments/          # Attachment lifecycle, storage, and host tools
├── cli/                  # maintained TUI/backend entry points and Daytona doctor
├── config/               # settings, TOML loading, and policy validation
├── daytona/              # SDK lifecycle, interpreter, broker, diagnostics, errors
├── observability/        # fail-soft tracing, diagnostics, evaluation, and analytics
├── optimization/         # isolated GEPA and evidence workflow
├── persistence/          # SQLAlchemy models and repository adapters
├── rlm/                  # DSPy program, execution, recursion, and events
├── sessions/             # Session history, tasks, lifecycle, and projections
├── skills/               # bundled catalog, resolution, resources, and tools
├── workspace/            # file, project, memory, path, and storage owners
├── app.py                # FastAPI construction and lifespan
├── app_lifecycle.py      # provider resource construction and closure
├── app_services.py       # composed lifespan service inventory
├── main.py               # ASGI entry point
├── paths.py              # provider-neutral Volume layout and path identity
├── result_snapshot.py    # private commit-gated typed-result encoding
├── snapshot_contract.py  # immutable Daytona Snapshot name policy
├── turn_preparation.py   # preparation inputs and orchestration
├── turn_settlement.py    # claim and settlement lifecycle
└── turns.py              # Turn coordinator
```

See the root [architecture](../../ARCHITECTURE.md) for dependency boundaries.

Start lifecycle investigations in `turns.py`, `turn_preparation.py`, and `turn_settlement.py`. Native invocation and observation live in `rlm/execution.py`; transport-neutral event types live in `rlm/events.py`. `rlm/program.py` and `rlm/compat_3_3_1.py` own DSPy construction and its pinned 3.3.1 adaptation. Each Run gets a fresh DSPy program; a healthy broker Root Sandbox may persist across sequential clean Turns. SDK calls remain in `daytona/`. `app.py` owns FastAPI lifespan, `app_lifecycle.py` builds and closes provider resources, and `app_services.py` defines the composed service inventory.

The maintained TypeScript client is separate under `tools/fleet-tui/`; its
generated HTTP types are owned by `make api-sync`.
