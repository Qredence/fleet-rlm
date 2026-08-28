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
- [P42 session-state behavior freeze](p42-session-state-behavior-freeze.md) — sealed Session-scoped state contract: durable History, resident reuse, taint-and-rotate, fingerprint rotation, and the behavior-over-structure meta-rule.
- [P42 module-subtraction ledger](p42-module-subtraction-ledger.md) — historical P42–P51 ownership migration ledger, retained as the P53 close-out audit record.

`openapi.yaml` is authoritative for HTTP shapes; generated TUI HTTP types are
checked alongside it by `make api-check`.
