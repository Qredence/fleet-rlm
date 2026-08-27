# Backend Reference

- [Configuration](configuration.md) — canonical `FLEET_*` settings and profile prerequisites.
- [Runtime profile matrix](profile-matrix.md) — policy-derived providers, token limits, and environment names.
- [HTTP API](http-api.md) — supported routes and SSE behavior.
- [CLI](cli.md) — supervised, backend-only, diagnostics, and Artifact commands.
- [Database](database.md) — canonical tables and Alembic ownership.
- [Source layout](source-layout.md) — package and client ownership.
- [Codebase map](codebase-map.md) — module boundaries and dependency direction.
- [Performance budget decision](performance-budget.md) — measured Sandbox, Volume, broker, and Run overhead with the KEEP CURRENT DESIGN gate.
- [P41 behavior freeze](behavior-freeze.md) — frozen public behaviors, their owners, and the behavior-over-structure guarantee.
- [P42 module-subtraction ledger](p42-module-subtraction-ledger.md) — planned P44–P51 ownership, adapter, invariant, and parity map; it makes no production change.

`openapi.yaml` is authoritative for HTTP shapes; generated TUI HTTP types are
checked alongside it by `make api-check`.
