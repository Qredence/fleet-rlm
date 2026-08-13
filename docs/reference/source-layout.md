# Backend source layout

```text
src/fleet_rlm/
├── api/            # HTTP identity, dependencies, schemas, routes, SSE
├── artifacts/      # candidates, validation, persistent/local stores
├── chat/           # Turn context, coordination, and claim policy
├── cli/            # supervised TUI/backend launchers and Daytona doctor
├── daytona/        # Daytona resources, adapters, and Workspace agent
├── files/          # Attachment upload, staging, host tools
├── observability/  # failure diagnostics + opt-in MLflow tracing
├── optimization/   # trusted-host GEPA/evidence lane
├── persistence/    # SQLAlchemy models and repository adapters
├── rlm/            # DSPy signature, models, runner, Runtime Events
├── runtime/        # provider-neutral Sandbox bindings
├── sessions/       # Session/Turn domain and repository interfaces
├── skills/         # immutable bundled catalog, Signatures, and host tools
├── app.py          # FastAPI factory and lifespan
├── composition/    # Daytona, shared, and private testing inventories
├── config.py       # TOML-profile runtime settings
├── config_policy.py # loopback non-secret policy editor
├── json_types.py   # closed JsonScalar/JsonValue contract
├── main.py         # ASGI entrypoint
├── result_snapshot.py # private commit-gated typed-result encoding
└── snapshot_contract.py # immutable Daytona Snapshot name policy
```

See [codebase map](codebase-map.md) for dependency boundaries.

The maintained TypeScript client is separate under `tools/fleet-tui/`; its
generated HTTP types are owned by `make api-sync`.
