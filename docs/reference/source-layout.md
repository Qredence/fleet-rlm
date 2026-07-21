# Backend source layout

```text
src/fleet_rlm/
├── api/            # HTTP identity, dependencies, schemas, routes, SSE
├── artifacts/      # candidates, validation, persistent/local stores
├── chat/           # Turn context and coordination
├── cli/            # supervised TUI/backend launchers and Daytona doctor
├── daytona/        # exclusive Daytona SDK boundary
├── files/          # Attachment upload, staging, host tools
├── observability/  # sanitized Turn records/exporters
├── persistence/    # SQLAlchemy models and repository adapters
├── rlm/            # DSPy signature, models, runner, Runtime Events
├── sessions/       # Session/Turn domain and repository interfaces
├── skills/         # immutable bundled catalog, Signatures, and host tools
├── app.py          # FastAPI factory and lifespan
├── composition/    # Daytona, Deno, shared, and private testing inventories
├── config.py       # FLEET_* settings
└── main.py         # ASGI entrypoint
```

See [codebase map](codebase-map.md) for dependency boundaries.

The maintained TypeScript client is separate under `tools/fleet-tui/`; its
generated HTTP types are owned by `make api-sync`.
