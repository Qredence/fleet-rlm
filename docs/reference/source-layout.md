# Backend source layout

```text
src/fleet_rlm/
├── api/            # HTTP identity, dependencies, schemas, routes, SSE
├── artifacts/      # candidates, validation, persistent/local stores
├── chat/           # Turn context, coordination, and claim policy
├── cli/            # supervised TUI/backend launchers and Daytona doctor
├── daytona/        # Daytona resources, provider adapters, and transport
├── attachments/    # Attachment models, lifecycle, storage, and host tools
├── workspace/      # Workspace, Projects, Memory, URL, and storage domains
├── observability/  # failure diagnostics, MLflow tracing, DSPy callbacks, and posthog
├── optimization/   # trusted-host GEPA/evidence lane
├── persistence/    # SQLAlchemy models and repository adapters
├── rlm/            # DSPy signature, models, runner, Runtime Events
├── runtime/        # provider-neutral Sandbox bindings + Daytona assembly
├── sessions/       # Session/Turn domain and repository interfaces
├── skills/         # immutable bundled catalog, Signatures, and host tools
├── app.py          # FastAPI factory and lifespan
├── composition/    # Daytona, shared, and private testing inventories
├── config/         # settings schema, TOML loader, and loopback-only policy editor
├── json_types.py   # closed JsonScalar/JsonValue contract
├── main.py         # ASGI entrypoint
├── paths.py        # provider-neutral Volume layout and path identity primitives
├── result_snapshot.py # private commit-gated typed-result encoding
└── snapshot_contract.py # immutable Daytona Snapshot name policy
```

See the root [architecture](../../ARCHITECTURE.md) for dependency boundaries.

Start lifecycle investigations in `chat/turn_runtime.py` and
`chat/run_lifecycle.py`; native invocation and observation live in `rlm/runtime.py`
and `rlm/events.py`. `rlm/session_runtime.py` owns resident reuse, while
`rlm/program.py` and `rlm/compat_3_3_1.py` own program construction and pinned
DSPy adaptation. SDK calls remain inside `daytona/`, even when runtime assembly
is exposed through `runtime/daytona/`.

The maintained TypeScript client is separate under `tools/fleet-tui/`; its
generated HTTP types are owned by `make api-sync`.
